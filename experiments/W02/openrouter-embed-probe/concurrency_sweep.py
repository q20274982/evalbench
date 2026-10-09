"""探針續:同時送 8 / 16 / 32 個請求,看哪一級開始被擋(429)或總速度不再增加。

來源:Calvin 2026-10-09 要求。OpenRouter 官方 limits 頁只寫付費模型「has no platform-level
request cap」,沒有數字;DeepInfra 那側有沒有上限只能實測。

每一級送「同時幾個」個請求,每個請求 32 段中組(800–6,000 字元)的塊,只跑一次。
換算整份語料用 run_openrouter_embed_probe.py 量到的 7,280 萬 token(out/result.json)。
跑法:uv run python experiments/W02/openrouter-embed-probe/concurrency_sweep.py [8,16,32]
"""

import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from run_openrouter_embed_probe import CORPUS, OUT, embed

LEVELS = [int(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "8,16,32").split(",")]
BATCH = 32
TOTAL_TOKENS = json.loads((OUT / "result.json").read_text())["estimate"]["總 token(百萬)"] * 1e6


def main() -> None:
    texts = [
        t for t in (json.loads(line)["text"] for line in CORPUS.open()) if 800 <= len(t) < 6000
    ]
    rng = random.Random(1)
    rows = []
    for n in LEVELS:
        batches = [rng.sample(texts, BATCH) for _ in range(n)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(n) as ex:
            res = list(ex.map(embed, batches))
        wall = time.perf_counter() - t0
        ok = [r for r in res if r["ok"]]
        tps = sum(r["tokens"] for r in ok) / wall
        row = {
            "同時幾個": n,
            "成功": len(ok),
            "失敗狀態碼": sorted({r["status"] for r in res if not r["ok"]}),
            "總秒數": round(wall, 2),
            "單一請求秒數(最短–最長)": [
                round(min(r["sec"] for r in ok), 2),
                round(max(r["sec"] for r in ok), 2),
            ]
            if ok
            else None,
            "合計每秒 token": round(tps),
            "整份語料換算(分鐘)": round(TOTAL_TOKENS / tps / 60, 1) if tps else None,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        time.sleep(5)
    (OUT / "concurrency_sweep.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
