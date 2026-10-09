"""探針:OpenRouter 鎖 DeepInfra 跑 bge-m3,作業 2-6 整份語料要算多久、花多少。

來源:Calvin 2026-10-09 要求(作業 2-6 的 embedding 要走本機還是 OpenRouter,本機估 6–15 小時,
OpenRouter 的時間沒有任何紀錄)。不屬於任何作業的本體,量完就沒有用途。

量四件事:
  §2 每個 token 幾個字元:用 OpenRouter 回傳的 usage 算,取代先前借用的「約 2 字元」
  §3 一次送一個請求時,短 / 中 / 長三組各自每秒幾個 token
  §4 同時送 4 個請求,總速度有沒有變快、會不會被擋(429)
  §5 最長的一塊(16,625 字元)送得過去嗎——bge-m3 上限 8192 token
最後 §6 用量到的比例與速度,換算 run1.jsonl 全部 145,786 塊的時間與花費。

輸入:experiments/W02/2-3-chunking/out/run1.jsonl(作業 2-3 的輸出,調整用 25 題)。
跑法:uv run python experiments/W02/openrouter-embed-probe/run_openrouter_embed_probe.py
"""

import json
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from config import settings  # noqa: E402

CORPUS = Path(__file__).resolve().parents[1] / "2-3-chunking" / "out" / "run1.jsonl"
OUT = Path(__file__).resolve().parent / "out"
MODEL = "baai/bge-m3"
PROVIDER = {"only": ["DeepInfra"], "allow_fallbacks": False}  # 同 W02 工作簿環境前置的鎖法
PRICE_PER_M = 0.010  # US$ / 百萬 token,W02 工作簿 `W02 檢索.md:38`(2026-09-24 查)

# 三組按字元數分;每組一次送幾段,讓每個請求大約都是 3–6 萬字元
GROUPS = {
    "短(<800 字元)": (0, 800, 128),
    "中(800–6,000)": (800, 6000, 32),
    "長(≥6,000)": (6000, 10**9, 8),
}
# 長組抽樣只抽 15,000 字元以下:約 2 字元 / token,更長的會撞 8192 token 上限、整個請求被退(§5 另測)
SAMPLE_MAX_CHARS = 15000
REPEAT = 3
CONCURRENCY = 4


# §1 送一個請求
def embed(texts: list[str]) -> dict:
    body = json.dumps({"model": MODEL, "input": texts, "provider": PROVIDER}).encode()
    req = urllib.request.Request(
        f"{settings.openrouter_base_url}/embeddings",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.openrouter_api_key}",
        },
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "sec": time.perf_counter() - t0,
            "status": e.code,
            "detail": e.read().decode()[:300],
        }
    sec = time.perf_counter() - t0
    return {
        "ok": True,
        "sec": sec,
        "tokens": d["usage"]["prompt_tokens"],
        "chars": sum(map(len, texts)),
        "n": len(d["data"]),
        "provider": d.get("provider"),
        "dim": len(d["data"][0]["embedding"]),
    }


def main() -> None:
    texts = [json.loads(line)["text"] for line in CORPUS.open()]
    rng = random.Random(0)
    by_group = {g: [t for t in texts if lo <= len(t) < hi] for g, (lo, hi, _) in GROUPS.items()}
    results: dict = {"groups": {}}

    # §2 + §3 一次一個請求
    for g, (_, _, size) in GROUPS.items():
        pool = by_group[g]
        sample_pool = [x for x in pool if len(x) < SAMPLE_MAX_CHARS]
        runs = [embed(rng.sample(sample_pool, size)) for _ in range(REPEAT)]
        bad = [r for r in runs if not r["ok"]]
        if bad:
            sys.exit(f"{g} 有請求失敗:{bad}")
        tok = sum(r["tokens"] for r in runs)
        results["groups"][g] = {
            "塊數": len(pool),
            "字元數": sum(map(len, pool)),
            "每 token 字元": sum(r["chars"] for r in runs) / tok,
            "每秒 token(各次)": [round(r["tokens"] / r["sec"]) for r in runs],
            "每次秒數": [round(r["sec"], 2) for r in runs],
            "provider": {r["provider"] for r in runs}.pop(),
        }
        print(g, json.dumps(results["groups"][g], ensure_ascii=False))

    # §4 同時送 4 個中組請求
    g_mid = "中(800–6,000)"
    batches = [rng.sample(by_group[g_mid], GROUPS[g_mid][2]) for _ in range(CONCURRENCY)]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        par = list(ex.map(embed, batches))
    wall = time.perf_counter() - t0
    ok = [r for r in par if r["ok"]]
    results["concurrent"] = {
        "同時幾個": CONCURRENCY,
        "成功": len(ok),
        "失敗": [r for r in par if not r["ok"]],
        "總秒數": round(wall, 2),
        "合計每秒 token": round(sum(r["tokens"] for r in ok) / wall) if ok else None,
    }
    print("同時送", json.dumps(results["concurrent"], ensure_ascii=False))

    # §5 最長的一塊
    longest = max(texts, key=len)
    r = embed([longest])
    results["longest"] = {"字元": len(longest), **{k: v for k, v in r.items() if k != "chars"}}
    print("最長一塊", json.dumps(results["longest"], ensure_ascii=False))

    # §6 換算整份語料:每組 token = 字元 ÷ 該組實測比例;時間 = token ÷ 該組一次一個請求的中位速度
    total_tok = total_sec = 0.0
    for g, v in results["groups"].items():
        t = v["字元數"] / v["每 token 字元"]
        total_tok += t
        total_sec += t / sorted(v["每秒 token(各次)"])[1]
    speedup = (results["concurrent"]["合計每秒 token"] or 0) / sorted(
        results["groups"][g_mid]["每秒 token(各次)"]
    )[1]
    results["estimate"] = {
        "總 token(百萬)": round(total_tok / 1e6, 1),
        "花費 US$": round(total_tok / 1e6 * PRICE_PER_M, 2),
        "一次一個請求(小時)": round(total_sec / 3600, 2),
        "同時 4 個的倍數(中組實測)": round(speedup, 2),
    }
    print("換算", json.dumps(results["estimate"], ensure_ascii=False))

    OUT.mkdir(exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1, default=list)
    )


if __name__ == "__main__":
    main()
