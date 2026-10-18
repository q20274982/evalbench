# 作業 2-6 第一步:把作業 2-3 切好的塊算成向量,存進 retrieval.chunks。一次跑一種來源。
#
# Calvin 2026-10-09 的做法:先跑一種來源,看同時 16 個請求的速度與花費在不在預期內,
# 都沒問題再跑其他來源。預期值用 W02 探針量到的數字換算(§1),超過停止門檻就停下回報。
#
# 跑法(從 repo 根目錄,DB 要先起來):
#     POSTGRES_HOST=localhost uv run python experiments/W02/2-6-embed/run_2_6_embed.py events
# 可中斷:已存且原文沒變的塊不重送,重跑從斷點接著算。

import json
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import chunk_store  # noqa: E402
from embedding import embed_batch  # noqa: E402

# §1 輸入與預期值(探針:code repo 4807b00、424cf97;W02 工作簿環境前置)
CORPUS = Path(__file__).resolve().parents[1] / "2-3-chunking" / "out" / "run1.jsonl"
OUT = Path(__file__).resolve().parent / "out"
SOURCES = ["events", "traces", "logs", "alerts", "metrics", "objects"]
CHARS_PER_TOKEN = (1.86, 2.13)  # 探針三組實測的最低與最高
TOKENS_PER_SEC = 42_603  # 同時 16 個請求,只量過 1 次
PRICE_PER_M = 0.010  # US$ / 百萬 token
STOP_COST_FACTOR = 1.5  # 花費超過預期上限的 1.5 倍就停
STOP_TIME_FACTOR = 2.0  # 時間超過預期上限的 2 倍就停

# §2 怎麼送:同時 16 個請求;長度相近的塊裝同一批
CONCURRENCY = 16
BATCH_MAX_ITEMS = 32
BATCH_MAX_CHARS = 64_000


def batches(chunks: list[dict]) -> list[list[dict]]:
    out, cur, chars = [], [], 0
    for c in sorted(chunks, key=lambda c: len(c["text"])):
        if cur and (len(cur) == BATCH_MAX_ITEMS or chars + len(c["text"]) > BATCH_MAX_CHARS):
            out.append(cur)
            cur, chars = [], 0
        cur.append(c)
        chars += len(c["text"])
    return out + ([cur] if cur else [])


# §3 主流程
def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else ""
    if source not in SOURCES:
        sys.exit(f"用法:run_2_6_embed.py <{'|'.join(SOURCES)}>")

    chunks = [c for c in map(json.loads, CORPUS.open()) if c["source"] == source]
    chars = sum(len(c["text"]) for c in chunks)
    exp_tok = (chars / CHARS_PER_TOKEN[1], chars / CHARS_PER_TOKEN[0])
    exp_cost = tuple(t / 1e6 * PRICE_PER_M for t in exp_tok)
    exp_sec = tuple(t / TOKENS_PER_SEC for t in exp_tok)
    print(
        f"[預期] {source}:{len(chunks):,} 塊、{chars:,} 字元 → "
        f"{exp_tok[0] / 1e6:.2f}–{exp_tok[1] / 1e6:.2f} 百萬 token、"
        f"US${exp_cost[0]:.3f}–{exp_cost[1]:.3f}、{exp_sec[0] / 60:.1f}–{exp_sec[1] / 60:.1f} 分鐘"
    )

    conn = chunk_store.connect()
    have = chunk_store.done(conn, source)
    todo = [c for c in chunks if have.get(c["id"]) != chunk_store.md5(c["text"])]
    plan = batches(todo)
    print(f"[續跑] 已存 {len(chunks) - len(todo):,} 塊,待算 {len(todo):,} 塊、{len(plan):,} 批")

    tok = retries = truncated = n_done = 0
    truncated_ids: list[str] = []
    stopped = ""
    t0 = time.perf_counter()
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        pending = {ex.submit(embed_batch, [c["text"] for c in b]): b for b in plan}
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for f in finished:
                b = pending.pop(f)
                r = f.result()
                chunk_store.upsert(
                    conn, b, [x.vector for x in r.items], [x.sent_chars for x in r.items]
                )
                tok += r.tokens
                retries += r.retries
                truncated += r.truncated
                truncated_ids += [
                    c["id"]
                    for c, x in zip(b, r.items, strict=True)
                    if x.sent_chars < len(c["text"])
                ]
                n_done += 1
            dt = time.perf_counter() - t0
            cost = tok / 1e6 * PRICE_PER_M
            if n_done % 50 == 0 or not pending:
                print(
                    f"[進度] {n_done:,}/{len(plan):,} 批 {tok:,} tok US${cost:.3f} "
                    f"{dt:,.0f}s {tok / dt:,.0f} tok/s"
                )
            if not stopped and cost > exp_cost[1] * STOP_COST_FACTOR:
                stopped = f"花費 US${cost:.3f} 超過預期上限 {STOP_COST_FACTOR} 倍"
            if not stopped and dt > exp_sec[1] * STOP_TIME_FACTOR:
                stopped = f"時間 {dt:.0f}s 超過預期上限 {STOP_TIME_FACTOR} 倍"
            if stopped:
                for f in pending:
                    f.cancel()
                pending = {f: b for f, b in pending.items() if not f.cancelled()}

    dt = time.perf_counter() - t0
    summary = {
        "來源": source,
        "塊數": len(chunks),
        "本次送出塊數": sum(len(b) for b in plan),
        "停止原因": stopped or None,
        "token": tok,
        "每 token 字元(本次)": round(sum(len(c["text"]) for c in todo) / tok, 3)
        if tok and not stopped
        else None,
        "花費 US$": round(tok / 1e6 * PRICE_PER_M, 4),
        "預期花費 US$": [round(x, 4) for x in exp_cost],
        "秒數": round(dt, 1),
        "預期秒數": [round(x, 1) for x in exp_sec],
        "每秒 token": round(tok / dt) if dt else None,
        "重試次數": retries,
        "截短塊數": truncated,
        "截短的塊": truncated_ids,
    }
    print(
        "[結果]",
        json.dumps({k: v for k, v in summary.items() if k != "截短的塊"}, ensure_ascii=False),
    )
    print("[截短的塊]", truncated_ids[:20], "…" if len(truncated_ids) > 20 else "")
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (OUT / f"{source}-{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
