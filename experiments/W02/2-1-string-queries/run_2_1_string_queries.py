"""作業 2-1|四個字串丟進純向量搜尋,各查回什麼(W02 工作簿 §14.1)。

預測在工作簿 §4.1、§4.5,查之前就寫好了;這支只負責「實際查回的前幾名」那一欄。

語料照 §14.1 定案(Calvin 2026-09-24;「一行」的讀法 TA 2026-09-25 補):
  - Scenario-5,六種來源各隨機抽 2000 行(不足全收,固定種子 SEED)
  - 一行 = 檔案的實體行,一行一塊,不做前處理(只去掉行尾換行字元)
  - alerts 44 個 JSON 檔、metrics 36 個 TSV 檔各自併成一種來源再抽
  - 超過 8192 token 的行先截短:server 回 400 並告知 token 數,按比例砍字元重送
  - ground_truth.yaml 不放
embedding:M1 Pro 本機 bge-m3(settings.embedding_base_url),一次送至多 128 段、
按長度排序分組(手冊 Ops/m1pro-embedding-server.md §10:長短混批會掉速)。
向量存自己的 schema `w02_2_1`,不借別人的表。搜尋用 `<=>`(cosine 距離)全表掃,
不建索引——近似索引會多一個變數。

可以重跑:已算過向量的行會跳過,只補沒算完的,再重查四個字串。

跑法(M1 Pro 的 start.sh 要先開):
    docker compose run --rm app uv run python \\
        experiments/W02/2-1-string-queries/run_2_1_string_queries.py
輸出:終端印每個字串的前 5 名(每塊印前 300 字元);全文寫進同包 out/results.json。
"""

import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from config import settings  # noqa: E402

# §1 輸入常數
REPO = Path(__file__).resolve().parents[3]
SCENARIO = REPO / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7/Scenario-5"
SOURCES = {
    "logs": ["otel_logs_raw.tsv"],
    "traces": ["otel_traces_raw.tsv"],
    "events": ["k8s_events_raw.tsv"],
    "k8s_objects": ["k8s_objects_raw.tsv"],
    "metrics": sorted(p.relative_to(SCENARIO).as_posix() for p in SCENARIO.glob("metrics/*.tsv")),
    "alerts": sorted(p.relative_to(SCENARIO).as_posix() for p in SCENARIO.glob("alerts/*.json")),
}
PER_SOURCE = 2000
SEED = 20260924  # 語料定案日;固定種子,重跑抽到同一批行
QUERIES = [  # 預測寫在工作簿 §4.1(前三個)、§4.5(第四個)
    "checkoutservice-7d4f9b",
    "ECONNREFUSED",
    "503",
    "checkout-5dccddf8bb-2ggcw",
]
TOP_K = 5
BATCH_MAX_ITEMS = 128
BATCH_MAX_CHARS = 300_000  # 長段(k8s_objects 一行上萬字元)不要一次塞 128 段
SCHEMA = "w02_2_1"
OUT = Path(__file__).parent / "out"


# §2 抽樣:兩趟讀檔,第一趟數行、第二趟只留抽中的行,不把 400MB 的 traces 整份讀進記憶體
def sample_source(files: list[str]) -> list[tuple[str, int, str]]:
    counts = []
    for f in files:
        with open(SCENARIO / f, "rb") as fh:
            counts.append(sum(1 for _ in fh))
    total = sum(counts)
    picked = set(range(total)) if total <= PER_SOURCE else set(
        random.Random(SEED).sample(range(total), PER_SOURCE)
    )
    rows, offset = [], 0
    for f, n in zip(files, counts, strict=True):
        with open(SCENARIO / f, "rb") as fh:
            for i, raw in enumerate(fh):
                if offset + i in picked:
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    rows.append((f, i + 1, text))  # 行號從 1 起,對得上 sed -n / 編輯器
        offset += n
    return rows


# §3 embedding:超過 8192 token 的行,照 server 回報的 token 數按比例截短後重送
TOO_LONG = re.compile(r"input\[(\d+)\] has (\d+) tokens")


def embed(texts: list[str]) -> tuple[list[list[float]], int, dict[int, str]]:
    """回傳 (向量, token 數, 被截短的 {批內位置: 截短後文字})。"""
    texts, truncated = list(texts), {}
    while True:
        body = json.dumps({"model": settings.embedding_model, "input": texts}).encode()
        req = urllib.request.Request(
            f"{settings.embedding_base_url}/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                d = json.load(r)
            return [e["embedding"] for e in d["data"]], d["usage"]["total_tokens"], truncated
        except urllib.error.HTTPError as e:
            detail = e.read().decode()
            m = TOO_LONG.search(detail)
            if e.code != 400 or not m:
                raise RuntimeError(f"embedding server {e.code}: {detail}") from e
            i, n = int(m.group(1)), int(m.group(2))
            texts[i] = texts[i][: int(len(texts[i]) * 8000 / n)]
            truncated[i] = texts[i]


def batches(rows: list[tuple]) -> list[list[tuple]]:
    rows = sorted(rows, key=lambda r: len(r[3]))  # r = (source, file, line_no, text)
    out, cur, chars = [], [], 0
    for r in rows:
        if cur and (len(cur) == BATCH_MAX_ITEMS or chars + len(r[3]) > BATCH_MAX_CHARS):
            out.append(cur)
            cur, chars = [], 0
        cur.append(r)
        chars += len(r[3])
    return out + ([cur] if cur else [])


def vec(v: list[float]) -> str:
    return "[" + ",".join(map(str, v)) + "]"


# §4 主流程:建表 → 抽樣 → 補算向量 → 查四個字串
def main() -> None:
    if not settings.embedding_base_url:
        sys.exit(".env 缺 EMBEDDING_BASE_URL(M1 Pro 本機 server,見 Ops/m1pro-embedding-server.md)")

    with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.chunks (
                source    text    NOT NULL,
                file      text    NOT NULL,
                line_no   int     NOT NULL,
                text      text    NOT NULL,  -- 送進 embedding 的字(截短過就是截短後的)
                truncated boolean NOT NULL,
                embedding vector(1024) NOT NULL,
                PRIMARY KEY (source, file, line_no)
            )""")
        done = {tuple(r) for r in conn.execute(f"SELECT source, file, line_no FROM {SCHEMA}.chunks")}

        # §4.1 抽樣
        pending = []
        for source, files in SOURCES.items():
            rows = sample_source(files)
            todo = [(source, f, n, t) for f, n, t in rows if (source, f, n) not in done]
            print(f"[sample] {source:12s} 抽到 {len(rows):5d} 行,待算 {len(todo):5d}")
            pending += todo

        # §4.2 補算向量(每批寫進 DB,中斷後重跑從斷點接著算)
        todo_batches = batches(pending)
        t0, tok_total = time.time(), 0
        for k, b in enumerate(todo_batches, 1):
            vecs, tok, trunc = embed([r[3] for r in b])
            tok_total += tok
            with conn.transaction():
                for j, (r, v) in enumerate(zip(b, vecs, strict=True)):
                    conn.execute(
                        f"INSERT INTO {SCHEMA}.chunks VALUES (%s, %s, %s, %s, %s, %s::vector)",
                        (r[0], r[1], r[2], trunc.get(j, r[3]), j in trunc, vec(v)),
                    )
            dt = time.time() - t0
            print(f"[embed] 批 {k}/{len(todo_batches)} {len(b)} 段 截短 {len(trunc)} "
                  f"累計 {tok_total:,} tok {dt:,.0f}s {tok_total / dt:,.0f} tok/s", flush=True)

        n, n_trunc = conn.execute(
            f"SELECT count(*), count(*) FILTER (WHERE truncated) FROM {SCHEMA}.chunks").fetchone()
        print(f"[corpus] 共 {n} 塊,其中截短 {n_trunc} 塊")

        # §4.3 查詢:全表精確排序,距離 = 1 - cosine 相似度,越小越像
        results = {}
        qvecs, _, _ = embed(QUERIES)
        for q, qv in zip(QUERIES, qvecs, strict=True):
            hits = conn.execute(
                f"""SELECT source, file, line_no, embedding <=> %s::vector AS dist, text
                    FROM {SCHEMA}.chunks ORDER BY dist LIMIT {TOP_K}""",
                (vec(qv),),
            ).fetchall()
            results[q] = [
                {"rank": i, "source": s, "file": f, "line_no": ln, "cosine_distance": round(d, 4),
                 "text": t}
                for i, (s, f, ln, d, t) in enumerate(hits, 1)
            ]
            print(f"\n=== {q}")
            for h in results[q]:
                print(f"#{h['rank']} {h['cosine_distance']:.4f} {h['source']} "
                      f"{h['file']}:{h['line_no']}\n    {h['text'][:300]}")

    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(
        {"seed": SEED, "per_source": PER_SOURCE, "corpus_size": n, "truncated": n_trunc,
         "model": settings.embedding_model, "results": results},
        ensure_ascii=False, indent=2))
    print(f"\n[out] {OUT / 'results.json'}")


if __name__ == "__main__":
    main()
