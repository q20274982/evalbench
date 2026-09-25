"""作業 2-1 跑完後的速度探針:哪些改法能縮短建向量的時間,各省多少(2026-09-25)。

來源:Calvin 2026-09-25 要求,估「縮短每段長度」與「只改腳本的小優化」兩類改法的效果,
允許送實驗請求到 M1。不屬於任何作業;結論寫在 vault `Ops/m1pro-embedding-server.md` 最後一節。
放在這一包,是因為要重建 run_2_1_string_queries.py 送出的同一批請求(同一包內可 import)。

不寫 w02_2_1.chunks、不動 out/results.json 與 out/run.txt;每量完一項就追加一行到
out/probe.jsonl,中途斷掉已量的還在。

量五件事:
  §2 段長 vs 速度:真實 k8s_objects 長行重切成 256…8000 token 的段,照正式腳本的分批規則送,各 3 次
  §3 一次請求幾段:段長固定約 4096 token,一次 16 段 vs 48 段,各 2 次(驗證手冊 §9.4 的「試跑」)
  §4 400 重送的成本:重建正式跑的批次,有截短的批各送一次原文、量 400 來回,乘上該批截短數
  §5 批與批之間的空檔:128 段一個字的請求(float 與 base64 各 5 次),加上客戶端寫 DB 的時間
  §6 重複的段:只查 DB,不打 M1

跑法(M1 的 start.sh 要先開):
    docker compose run --rm -T app uv run python -u \\
        experiments/W02/2-1-string-queries/probe_throughput.py
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings  # noqa: E402
from run_2_1_string_queries import (  # noqa: E402
    SCHEMA,
    SOURCES,
    batches,
    sample_source,
    vec,
)

OUT = Path(__file__).parent / "out" / "probe.jsonl"


# §1 工具:原始請求,不做截短重送,回 (HTTP 狀態, 秒數, token 數或錯誤訊息)
def post(texts: list[str], fmt: str = "float") -> tuple[int, float, object]:
    body = json.dumps({"model": settings.embedding_model, "input": texts,
                       "encoding_format": fmt}).encode()
    req = urllib.request.Request(f"{settings.embedding_base_url}/embeddings", data=body,
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            d = json.load(r)
        return 200, time.time() - t, d["usage"]["total_tokens"]
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t, e.read().decode()


def record(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)
    with open(OUT, "a") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def token_count(text: str) -> int:
    """借 server 的 400 訊息數 token:只數不算,不佔 GPU。text 要超過 8192 token。"""
    code, _, detail = post([text])
    assert code == 400, (code, detail)
    return int(str(detail).split(" has ")[1].split(" tokens")[0])


# §2 段長 vs 速度
def probe_length(long_text: str, chars_per_tok: float) -> None:
    for target in [256, 512, 1024, 2048, 4096, 8000]:
        c = int(target * chars_per_tok)
        n_seg = max(1, min(128, 300_000 // c))           # 正式腳本的分批上限
        n_req = max(1, round(128_000 / (n_seg * target)))  # 每次約 12.8 萬 token
        need = n_seg * n_req * c
        assert need <= len(long_text), (target, need, len(long_text))
        segs = [long_text[i * c:(i + 1) * c] for i in range(n_seg * n_req)]
        for rep in range(3):
            tok = sec = 0
            for r in range(n_req):
                code, dt, t = post(segs[r * n_seg:(r + 1) * n_seg])
                assert code == 200, t
                tok += t
                sec += dt
            record(probe="length", target=target, rep=rep, segs_per_req=n_seg, requests=n_req,
                   avg_tok_per_seg=round(tok / (n_seg * n_req)), tokens=tok,
                   seconds=round(sec, 2), tok_per_s=round(tok / sec))


# §3 一次請求幾段(段長固定約 4096 token)
def probe_segs_per_request(long_text: str, chars_per_tok: float) -> None:
    c = int(4096 * chars_per_tok)
    for n in [16, 48]:
        segs = [long_text[i * c:(i + 1) * c] for i in range(n)]
        for rep in range(2):
            code, dt, tok = post(segs)
            assert code == 200, tok
            record(probe="segs_per_request", segs=n, rep=rep, tokens=tok,
                   seconds=round(dt, 2), tok_per_s=round(tok / dt))


# §4 400 重送的成本
def probe_retry(conn: psycopg.Connection, rows: list[tuple]) -> None:
    trunc = {tuple(r) for r in conn.execute(
        f"SELECT source, file, line_no FROM {SCHEMA}.chunks WHERE truncated")}
    total_est = 0.0
    for k, b in enumerate(batches(rows), 1):
        n_trunc = sum((r[0], r[1], r[2]) in trunc for r in b)
        if not n_trunc:
            continue
        code, dt, _ = post([r[3] for r in b])
        assert code == 400
        total_est += dt * n_trunc
        record(probe="retry", batch=k, segs=len(b), truncated=n_trunc,
               chars=sum(len(r[3]) for r in b), seconds_per_400=round(dt, 2),
               est_retry_seconds=round(dt * n_trunc, 1))
    record(probe="retry_total", est_retry_seconds=round(total_est, 1))


# §5 批與批之間的空檔:網路 + 序列化 + 客戶端寫 DB
def probe_gap(conn: psycopg.Connection) -> None:
    for fmt in ["float", "base64"]:
        for rep in range(5):
            code, dt, tok = post(["a"] * 128, fmt)
            assert code == 200, tok
            record(probe="gap_request", fmt=fmt, rep=rep, seconds=round(dt, 3))
    v = [0.123456789] * 1024
    for rep in range(3):
        t = time.time()
        with conn.transaction(force_rollback=True):
            conn.execute("CREATE TEMP TABLE probe_t (t text, e vector(1024))")
            for _ in range(128):
                conn.execute("INSERT INTO probe_t VALUES (%s, %s::vector)", ("x" * 500, vec(v)))
        record(probe="gap_db_insert_128", rep=rep, seconds=round(time.time() - t, 3))


# §6 重複的段(只查 DB)
def probe_dedup(conn: psycopg.Connection) -> None:
    total, distinct, dup_chars, all_chars = conn.execute(f"""
        SELECT count(*), count(DISTINCT text),
               sum(length(text)) - (SELECT sum(length(t)) FROM
                   (SELECT DISTINCT text AS t FROM {SCHEMA}.chunks) d),
               sum(length(text))
        FROM {SCHEMA}.chunks""").fetchone()
    top = conn.execute(f"""SELECT left(text, 60), count(*) FROM {SCHEMA}.chunks
                           GROUP BY text HAVING count(*) > 1 ORDER BY 2 DESC LIMIT 5""").fetchall()
    record(probe="dedup", rows=total, distinct=distinct, duplicate_rows=total - distinct,
           duplicate_chars=dup_chars, all_chars=all_chars, top=top)


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    rows = []
    for source, files in SOURCES.items():
        rows += [(source, f, n, t) for f, n, t in sample_source(files)]
    # 長文字來源:這次抽到的 k8s_objects 行,由長到短接起來(真實文字,跟正式跑同一種內容)
    k8s = sorted((r[3] for r in rows if r[0] == "k8s_objects"), key=len, reverse=True)
    long_text = "\n".join(k8s)
    probe_text = long_text[:60_000]
    chars_per_tok = len(probe_text) / token_count(probe_text)
    record(probe="calibrate", chars=len(probe_text), chars_per_tok=round(chars_per_tok, 3),
           long_text_chars=len(long_text))

    with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
        probe_dedup(conn)
        probe_gap(conn)
        probe_retry(conn, rows)
        probe_segs_per_request(long_text, chars_per_tok)
        probe_length(long_text, chars_per_tok)
    record(probe="done")


if __name__ == "__main__":
    main()
