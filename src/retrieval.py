"""檢索層(被測系統):從一題的告警與 events 組查詢,在同一題的塊裡做向量搜尋。

規格出處:R01 `§12` 的查詢設計(Calvin 2026-09-29),加上 2026-10-11 的兩個決定:
  - 改動前的起點先不用時間窗:events 不看時間,Warning 優先、由新到舊取 5 筆
  - events 進查詢的字,兩種都跑:"raw" = 塊的原文;"fields" = reason、note、regarding 的 kind 與 name
查詢的組法也兩種都跑(R01 §12 的對照實驗):"one" = 壓成一個查詢;"each" = 一筆一個查詢再用 RRF 合併。
混合搜尋(§5,W02 §9.2「兩份名次表怎麼合併」「關鍵字搜尋的設定」,Calvin 2026-10-11):
  - 關鍵字搜尋:任一個字就算命中,Postgres 內建 ts_rank 打分
  - 兩種都跑:拆字前把 / . : 換成空白與否(split);english / simple(ts_config)
  - 向量與關鍵字的名次表用 RRF 合併(常數 60,每份取前 100);"each" 時兩種合併都跑:
    "flat" = 2N 份一次丟進 RRF;"two_level" = 向量、關鍵字各自先合成一份再 RRF

本檔不讀答案檔(INV-13),只讀 retrieval.chunks 裡的塊。
查詢向量算一次就存進 retrieval.query_vectors:DeepInfra 同一段字每次的向量不完全一樣,
不存的話「每個設定跑 1 次」的前提(R01 §12「重跑結果相同」)就不成立。
events 怎麼挑(pick,W02 §9.2,Calvin 2026-10-13),預設 "warning5" = 上面的起點:
  - "warning_all":Warning 全部放,不設上限、不用 Normal 補(沒有 Warning 的題,events 就是空的)
  - "dedup_reason" / "dedup_kind":照起點的順序往下走,reason(或 regarding 的 kind)
    已經挑過就跳過,湊滿 5 筆
"""

import json
import re

import psycopg

import chunk_store
from config import settings
from embedding import embed_batch

# §1 常數
N_EVENTS = 5
PICKS = ("warning5", "warning_all", "dedup_reason", "dedup_kind")
ALERT_FIELDS = ["annotations.description", "labels.alertname", "labels.service_name"]
RRF_K = 60  # RRF 的常用預設常數(一般知識)
PER_QUERY_DEPTH = 100  # "each" 模式每個查詢取前幾名進 RRF
SPLIT_RE = re.compile(r"[/.:]")  # split=True 時查詢與塊兩邊都把這三個字元換成空白
ALERT_FIELD_RE = re.compile(r"^(labels\.\w+|annotations\.\w+|activeAt|value): ", re.M)

QUERY_DDL = f"""
CREATE TABLE IF NOT EXISTS {chunk_store.SCHEMA}.query_vectors (
    text_md5    text PRIMARY KEY,
    text        text NOT NULL,
    embedding   vector(1024) NOT NULL,
    model       text NOT NULL,
    provider    text NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now()
);
"""


# §2 組查詢
def _alert_fields(text: str) -> dict[str, str]:
    """告警塊一欄一行寫成 `欄位: 值`,description 可能跨好幾行,值一路收到下一個欄位名為止。"""
    marks = list(ALERT_FIELD_RE.finditer(text))
    out = {}
    for m, nxt in zip(marks, marks[1:] + [None], strict=False):
        out[m.group(1)] = text[m.end() : nxt.start() if nxt else len(text)].strip()
    return out


def alert_texts(conn: psycopg.Connection, scenario: str) -> tuple[list[str], list[str]]:
    """回傳 (查詢文字, 用到的塊 id)。每一塊告警都用到了,文字相同的只是不重複放進查詢。"""
    rows = conn.execute(
        f"SELECT id, text FROM {chunk_store.SCHEMA}.chunks "
        "WHERE scenario = %s AND source = 'alerts' ORDER BY id",
        (scenario,),
    ).fetchall()
    seen, out = set(), []
    for _, t in rows:
        f = _alert_fields(t)
        q = "\n".join(f[k] for k in ALERT_FIELDS if f.get(k))
        if (
            q and q not in seen
        ):  # 同一則告警在不同快照、不同 activeAt 會有好幾塊,查詢文字相同只留一次
            seen.add(q)
            out.append(q)
    return out, [r[0] for r in rows]


def event_texts(
    conn: psycopg.Connection, scenario: str, mode: str, pick: str = "warning5"
) -> tuple[list[str], list[str]]:
    """回傳 (查詢文字, 用到的塊 id)。"""
    rows = conn.execute(
        f"SELECT id, text FROM {chunk_store.SCHEMA}.chunks "
        "WHERE scenario = %s AND source = 'events'",
        (scenario,),
    ).fetchall()
    evs = []
    for cid, t in rows:
        o = json.loads(t).get("object", {})
        when = o.get("deprecatedLastTimestamp") or o.get("eventTime") or ""
        evs.append((o.get("type") == "Warning", when, cid, t, o))
    # 穩定排序疊三次:先照塊 id,再由新到舊,最後 Warning 優先
    evs.sort(key=lambda e: e[2])
    evs.sort(key=lambda e: e[1], reverse=True)
    evs.sort(key=lambda e: e[0], reverse=True)
    chosen = _pick(evs, pick)
    out = []
    for *_, t, o in chosen:
        if mode == "raw":
            out.append(t)
        else:
            r = o.get("regarding") or {}
            parts = [
                o.get("reason"),
                o.get("note"),
                " ".join(x for x in (r.get("kind"), r.get("name")) if x),
            ]
            out.append("\n".join(p for p in parts if p))
    return out, [e[2] for e in chosen]


def _pick(evs: list[tuple], pick: str) -> list[tuple]:
    """evs 已照起點排好(Warning 優先、由新到舊、塊 id)。"""
    if pick == "warning5":
        return evs[:N_EVENTS]
    if pick == "warning_all":
        return [e for e in evs if e[0]]
    if pick in ("dedup_reason", "dedup_kind"):
        key = (
            (lambda o: o.get("reason"))
            if pick == "dedup_reason"
            else (lambda o: (o.get("regarding") or {}).get("kind"))
        )
        seen, out = set(), []
        for e in evs:
            k = key(e[4])
            if k in seen:
                continue
            seen.add(k)
            out.append(e)
            if len(out) == N_EVENTS:
                break
        return out
    raise ValueError(f"pick 只能是 {PICKS}:{pick}")


def build_queries(
    conn: psycopg.Connection,
    scenario: str,
    events_mode: str,
    combine: str,
    pick: str = "warning5",
) -> tuple[list[str], list[str]]:
    """回傳 (查詢, 組查詢用到的塊 id)。"""
    a_txt, a_ids = alert_texts(conn, scenario)
    e_txt, e_ids = event_texts(conn, scenario, events_mode, pick)
    items = a_txt + e_txt
    return (["\n\n".join(items)] if combine == "one" else items), a_ids + e_ids


# §3 查詢向量(算一次就存)
def query_vectors(conn: psycopg.Connection, texts: list[str]) -> dict[str, str]:
    """回傳 {查詢文字: pgvector 字串}。"""
    conn.execute(QUERY_DDL)
    keys = {t: chunk_store.md5(t) for t in texts}
    have = dict(
        conn.execute(
            f"SELECT text_md5, embedding::text FROM {chunk_store.SCHEMA}.query_vectors "
            "WHERE text_md5 = ANY(%s)",
            (list(keys.values()),),
        ).fetchall()
    )
    todo = [t for t in dict.fromkeys(texts) if keys[t] not in have]
    for i in range(0, len(todo), 16):
        batch = todo[i : i + 16]
        r = embed_batch(batch)
        for t, e in zip(batch, r.items, strict=True):
            v = "[" + ",".join(map(str, e.vector)) + "]"
            conn.execute(
                f"INSERT INTO {chunk_store.SCHEMA}.query_vectors "
                "(text_md5, text, embedding, model, provider) VALUES (%s, %s, %s::vector, %s, %s) "
                "ON CONFLICT (text_md5) DO NOTHING",
                (
                    keys[t],
                    t,
                    v,
                    settings.openrouter_embedding_model,
                    settings.openrouter_embedding_provider,
                ),
            )
            have[keys[t]] = v
    return {t: have[keys[t]] for t in texts}


# §4 搜尋
def vector_search(
    conn: psycopg.Connection, scenario: str, qvec: str, k: int, exclude: list[str]
) -> list[str]:
    """同一題的塊裡,cosine 距離由近到遠的前 k 個塊 id。全表精確排序(不建近似索引),
    同距離照塊 id 排(R01 §12「同分時加次要鍵」)。exclude 裡的塊不回傳。"""
    rows = conn.execute(
        f"SELECT id FROM {chunk_store.SCHEMA}.chunks WHERE scenario = %s AND NOT (id = ANY(%s)) "
        "ORDER BY embedding <=> %s::vector, id LIMIT %s",
        (scenario, exclude, qvec, k),
    ).fetchall()
    return [r[0] for r in rows]


def rrf(rankings: list[list[str]], k: int) -> list[str]:
    score: dict[str, float] = {}
    for ranking in rankings:
        for pos, cid in enumerate(ranking, 1):
            score[cid] = score.get(cid, 0.0) + 1 / (RRF_K + pos)
    return sorted(score, key=lambda c: (-score[c], c))[:k]


def search(
    conn: psycopg.Connection,
    scenario: str,
    events_mode: str,
    combine: str,
    k: int,
    exclude_query_chunks: bool = True,
    pick: str = "warning5",
) -> list[str]:
    """exclude_query_chunks:搜尋結果排除組查詢用到的塊(告警全部 + 挑出的 events),
    因為那些 agent 本來就看得到(Calvin 2026-10-11 選 A)。False = code repo d5123d0 的行為。"""
    queries, used = build_queries(conn, scenario, events_mode, combine, pick)
    exclude = used if exclude_query_chunks else []
    vecs = query_vectors(conn, queries)
    if combine == "one":
        return vector_search(conn, scenario, vecs[queries[0]], k, exclude)
    return rrf(
        [vector_search(conn, scenario, vecs[q], PER_QUERY_DEPTH, exclude) for q in queries], k
    )


# §5 關鍵字搜尋與混合搜尋
def _tsquery_any(lexemes: list[str]) -> str:
    """把已正規化的字組成「任一個就算」的 tsquery 文字。單引號內 \\ 與 '' 是 tsquery 的跳脫寫法。"""
    return " | ".join("'" + x.replace("\\", "\\\\").replace("'", "''") + "'" for x in lexemes)


def doc_expr(split: bool) -> str:
    """塊這一側送進 to_tsvector 的 SQL 運算式;先算好 tsvector 的表也要用同一條。"""
    return "regexp_replace(text, '[/.:]', ' ', 'g')" if split else "text"


def keyword_rankings(
    conn: psycopg.Connection,
    scenario: str,
    queries: list[str],
    depth: int,
    exclude: list[str],
    ts_config: str,
    split: bool,
    tsv_table: str | None = None,
) -> list[list[str]]:
    """每個查詢一份名次表:同一題的塊裡,含查詢任一個字的塊照 ts_rank 由高到低,同分照塊 id,
    取前 depth 個。exclude 裡的塊不回傳。不建索引(W02 §9.2)。
    塊的 tsvector 預設每次現算;tsv_table 給了就改讀那張表裡先算好的
    (欄位 id, scenario, ts_config, split, v,算法須與這裡現算的相同),只為了省時間。"""
    tqs = []
    for q in queries:
        text = SPLIT_RE.sub(" ", q) if split else q
        lex = conn.execute(
            "SELECT tsvector_to_array(to_tsvector(%s::regconfig, %s))", (ts_config, text)
        ).fetchone()[0]
        tqs.append(_tsquery_any(lex) if lex else None)
    live = [t for t in tqs if t]
    if not live:
        return [[] for _ in queries]
    if tsv_table:
        d_sql = (
            f"SELECT id, v FROM {tsv_table} WHERE scenario = %(s)s AND ts_config = %(cfg)s "
            "AND split = %(split)s AND NOT (id = ANY(%(ex)s))"
        )
    else:
        d_sql = (
            f"SELECT id, to_tsvector(%(cfg)s::regconfig, {doc_expr(split)}) AS v "
            f"FROM {chunk_store.SCHEMA}.chunks WHERE scenario = %(s)s AND NOT (id = ANY(%(ex)s))"
        )
    rows = conn.execute(
        f"""
        WITH d AS ({d_sql}), q AS (
            SELECT n, t::tsquery AS tq FROM unnest(%(tqs)s::text[]) WITH ORDINALITY AS u(t, n)
        ), r AS (
            SELECT q.n, d.id,
                   row_number() OVER (
                       PARTITION BY q.n ORDER BY ts_rank(d.v, q.tq) DESC, d.id
                   ) AS pos
            FROM q JOIN d ON d.v @@ q.tq
        )
        SELECT n, id FROM r WHERE pos <= %(depth)s ORDER BY n, pos
        """,
        {
            "cfg": ts_config,
            "s": scenario,
            "split": split,
            "ex": exclude,
            "tqs": live,
            "depth": depth,
        },
    ).fetchall()
    by_n: dict[int, list[str]] = {}
    for n, cid in rows:
        by_n.setdefault(n, []).append(cid)
    out, n = [], 0
    for t in tqs:  # 沒有任何字的查詢(全被丟掉)給空名次表,其餘依序對回 live 的編號
        if t:
            n += 1
            out.append(by_n.get(n, []))
        else:
            out.append([])
    return out


def hybrid_search(
    conn: psycopg.Connection,
    scenario: str,
    events_mode: str,
    combine: str,
    k: int,
    ts_config: str,
    split: bool,
    fuse: str = "flat",
    tsv_table: str | None = None,
    pick: str = "warning5",
) -> list[str]:
    """向量 + 關鍵字,RRF 合併。排除組查詢用到的塊,做法同 search()。
    fuse 只在 combine="each" 有差:"flat" / "two_level"(見檔頭)。"""
    queries, used = build_queries(conn, scenario, events_mode, combine, pick)
    vecs = query_vectors(conn, queries)
    vec_lists = [vector_search(conn, scenario, vecs[q], PER_QUERY_DEPTH, used) for q in queries]
    kw_lists = keyword_rankings(
        conn, scenario, queries, PER_QUERY_DEPTH, used, ts_config, split, tsv_table
    )
    if combine == "each" and fuse == "two_level":
        return rrf([rrf(vec_lists, PER_QUERY_DEPTH), rrf(kw_lists, PER_QUERY_DEPTH)], k)
    return rrf(vec_lists + kw_lists, k)
