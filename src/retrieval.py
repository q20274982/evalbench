"""檢索層(被測系統):從一題的告警與 events 組查詢,在同一題的塊裡做向量搜尋。

規格出處:R01 `§12` 的查詢設計(Calvin 2026-09-29),加上 2026-10-11 的兩個決定:
  - 改動前的起點先不用時間窗:events 不看時間,Warning 優先、由新到舊取 5 筆
  - events 進查詢的字,兩種都跑:"raw" = 塊的原文;"fields" = reason、note、regarding 的 kind 與 name
查詢的組法也兩種都跑(R01 §12 的對照實驗):"one" = 壓成一個查詢;"each" = 一筆一個查詢再用 RRF 合併。

本檔不讀答案檔(INV-13),只讀 retrieval.chunks 裡的塊。
查詢向量算一次就存進 retrieval.query_vectors:DeepInfra 同一段字每次的向量不完全一樣,
不存的話「每個設定跑 1 次」的前提(R01 §12「重跑結果相同」)就不成立。
"""

import json
import re

import psycopg

import chunk_store
from config import settings
from embedding import embed_batch

# §1 常數
N_EVENTS = 5
ALERT_FIELDS = ["annotations.description", "labels.alertname", "labels.service_name"]
RRF_K = 60  # RRF 的常用預設常數(一般知識)
PER_QUERY_DEPTH = 100  # "each" 模式每個查詢取前幾名進 RRF
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


def event_texts(conn: psycopg.Connection, scenario: str, mode: str) -> tuple[list[str], list[str]]:
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
    out = []
    for *_, t, o in evs[:N_EVENTS]:
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
    return out, [e[2] for e in evs[:N_EVENTS]]


def build_queries(
    conn: psycopg.Connection, scenario: str, events_mode: str, combine: str
) -> tuple[list[str], list[str]]:
    """回傳 (查詢, 組查詢用到的塊 id)。"""
    a_txt, a_ids = alert_texts(conn, scenario)
    e_txt, e_ids = event_texts(conn, scenario, events_mode)
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
) -> list[str]:
    """exclude_query_chunks:搜尋結果排除組查詢用到的塊(告警全部 + 挑出的 events),
    因為那些 agent 本來就看得到(Calvin 2026-10-11 選 A)。False = code repo d5123d0 的行為。"""
    queries, used = build_queries(conn, scenario, events_mode, combine)
    exclude = used if exclude_query_chunks else []
    vecs = query_vectors(conn, queries)
    if combine == "one":
        return vector_search(conn, scenario, vecs[queries[0]], k, exclude)
    return rrf(
        [vector_search(conn, scenario, vecs[q], PER_QUERY_DEPTH, exclude) for q in queries], k
    )
