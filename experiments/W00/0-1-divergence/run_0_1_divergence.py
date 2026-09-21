"""W00 作業 0-1|同一個問題問二十次,量輸出分歧。

跑兩組,每組 20 次,同一個 prompt、同一份輸入:
  A 組 = 不設 temperature、不設 seed(供應商預設)
  B 組 = temperature=0 且 seed 固定
兩組的差別只有這兩個旋鈕,其他一律相同。

輸入是 ITBench-Lite Scenario-105 的一份告警快照(2.5KB),
不是整包 574MB 的場景——這一題量的是「同樣輸入下輸出穩不穩」,
輸入越小,分歧越不可能來自「模型讀到不一樣的東西」。

只用 Python 標準庫(作業 0-4 才會建 uv 專案,見 check_api.py 檔頭)。
provider 鎖 Alibaba、allow_fallbacks=false,理由見 W00 工作簿 §3。

成本估計:40 次請求,每次輸入約 0.9k token、輸出實測約 30 token,
以 $0.065 / $0.260 每百萬 token 計,合計估計不到 $0.01(實測見工作簿 §5.1)。

跑法:
    docker compose run --rm app python experiments/W00/0-1-divergence/run_0_1_divergence.py
"""

import glob
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

# §1 常數
N_RUNS = 20
SEED = 42
MAX_TOKENS = 400
PROVIDER = {"order": ["Alibaba"], "allow_fallbacks": False}
# qwen3.5-flash 預設會先寫一長串思考過程,4000 token 都寫不完,拿不到 JSON。
# 關掉 reasoning 之後輸出剩 26 token 且直接是 JSON,見工作簿 §8.3。
REASONING = {"enabled": False}
OUT_DIR = "experiments/W00/0-1-divergence/out"
SCENARIO_GLOB = "data/ITBench-Lite/snapshots/sre/*/Scenario-105"

PROMPT_TEMPLATE = """你是值班的 SRE。下面是一個 Kubernetes 叢集在事故當下的告警快照。

請判斷這次事故的根因。直接輸出一個 JSON 物件,不要寫思考過程,不要有其他文字:
{{"root_cause_service": "<服務名>", "reason": "<一句話,不超過 30 字>"}}

告警快照:
{alerts}
"""


def load_alerts() -> tuple[str, str]:
    """回傳 (檔案路徑, 檔案內容)。挑 alerts_in_alerting_state 那一份。"""
    hits = sorted(glob.glob(f"{SCENARIO_GLOB}/alerts/alerts_in_alerting_state_*.json"))
    if not hits:
        raise SystemExit(
            f"✗ 找不到告警快照,對過的路徑:{SCENARIO_GLOB}/alerts/\n"
            "  資料還沒下載完的話,先等 hf download 跑完。"
        )
    path = hits[0]
    return path, open(path, encoding="utf-8").read()


def call_once(prompt: str, pinned: bool) -> dict:
    """打一次 API。pinned=True 時送 temperature=0 與固定 seed。"""
    body = {
        "model": os.environ["OPENROUTER_MODEL"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "provider": PROVIDER,
        "reasoning": REASONING,
    }
    if pinned:
        body["temperature"] = 0
        body["seed"] = SEED

    req = urllib.request.Request(
        os.environ["OPENROUTER_BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def run_group(label: str, prompt: str, pinned: bool) -> list[str]:
    """跑一組 N_RUNS 次,每次的完整回應存成一個檔,回傳 20 份輸出文字。"""
    out_dir = os.path.join(OUT_DIR, label)
    os.makedirs(out_dir, exist_ok=True)
    texts = []

    for i in range(1, N_RUNS + 1):
        for attempt in (1, 2, 3):
            try:
                payload = call_once(prompt, pinned)
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                print(f"  [{label} {i:02d}] HTTP {e.code} 第 {attempt} 次:{detail}")
                if attempt == 3:
                    raise SystemExit(f"✗ {label} 第 {i} 次連三次失敗,停手。")
                time.sleep(3 * attempt)
            except urllib.error.URLError as e:
                print(f"  [{label} {i:02d}] 連線失敗第 {attempt} 次:{e.reason}")
                if attempt == 3:
                    raise SystemExit(f"✗ {label} 第 {i} 次連三次失敗,停手。")
                time.sleep(3 * attempt)

        text = payload["choices"][0]["message"]["content"]
        texts.append(text)
        with open(os.path.join(out_dir, f"run_{i:02d}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  [{label} {i:02d}] provider={payload.get('provider')!r} 長度={len(text)}")

    return texts


def report(label: str, texts: list[str]) -> None:
    """§3 只報「幾份逐字相同」。差在哪不由腳本判斷,由 Calvin 自己看檔案。"""
    groups: dict[str, list[int]] = {}
    for i, t in enumerate(texts, 1):
        groups.setdefault(hashlib.sha256(t.encode()).hexdigest(), []).append(i)

    sizes = sorted((len(v) for v in groups.values()), reverse=True)
    print(f"\n[{label}] {len(texts)} 份輸出裡有 {len(groups)} 種不同的字串")
    print(f"        最大的一群有 {sizes[0]} 份|各群大小 {sizes}")
    for h, idxs in groups.items():
        print(f"        {h[:8]} → run {idxs}")


def main() -> int:
    missing = [
        k
        for k in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL")
        if not os.environ.get(k)
    ]
    if missing:
        print(f"✗ .env 缺這幾個變數:{', '.join(missing)}")
        return 1

    path, alerts = load_alerts()
    prompt = PROMPT_TEMPLATE.format(alerts=alerts)
    print(f"輸入:{path}({len(alerts)} bytes)")
    print(f"模型:{os.environ['OPENROUTER_MODEL']}|provider 鎖 {PROVIDER['order'][0]}")
    print(f"輸出存到:{OUT_DIR}/A/ 與 {OUT_DIR}/B/\n")

    # §2 A 組:供應商預設
    print("--- A 組:不設 temperature、不設 seed ---")
    a = run_group("A", prompt, pinned=False)

    # §3 B 組:temperature=0 且 seed 固定
    print(f"\n--- B 組:temperature=0、seed={SEED} ---")
    b = run_group("B", prompt, pinned=True)

    # §4 兩組各自的逐字相同狀況
    report("A 組", a)
    report("B 組", b)
    print("\n下一步:打開 out/0-1/A/ 與 out/0-1/B/ 的檔案,自己看不同的那幾份差在哪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
