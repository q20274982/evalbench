"""W00 作業 0-2|讓分數騙你一次:固定 5 題連跑 3 輪,看三個總分差多少。

判定規則是 Calvin 定的,正本在工作簿 §5.2.0。這支腳本只負責跑與統計,
**判定那一步留給 Calvin 自己實作**(見下面的 §2 TODO 1)——
評測台的評分是這個專案的本體,TA 不代寫(鐵律 6)。

跑兩組,各 3 輪,每輪 5 題:
  A 組 = 供應商預設(不設 temperature、不設 seed)
  B 組 = temperature=0 且 seed 固定  ← 主組
兩組合計 30 次請求,估計不到 $0.01。

⚠️ prompt 裡的候選清單抄自 `ground_truth.yaml`,這在 W0 之外是洩漏標準答案。
理由與有效期見工作簿 §5.2.0 的 ⚠️ 那段。

跑法:
    docker compose run --rm app uv run --with pyyaml python experiments/W00/run_0_2_score_variance.py
"""

import glob
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

import yaml

# §1 常數
N_ROUNDS = 3
SEED = 42
MAX_TOKENS = 200
PROVIDER = {"order": ["Alibaba"], "allow_fallbacks": False}
REASONING = {"enabled": False}  # 不關的話模型會寫一長串思考過程,見工作簿 §8.3
OUT_DIR = "experiments/W00/out/0-2"
SRE_ROOT = "data/ITBench-Lite/snapshots/sre"

# 這 5 題的挑法:候選數由多到少排,五題的標準答案彼此不重複,而且都有
# alerts_in_alerting_state 快照。候選越多,亂猜猜中的機率越低;答案不重複,
# 才不會因為模型偏好某個字串而系統性多拿分。
# 候選數依序是 19 / 13 / 11 / 10 / 10。
SCENARIOS = ["Scenario-105", "Scenario-14", "Scenario-102", "Scenario-17", "Scenario-19"]

PROMPT_TEMPLATE = """你是值班的 SRE。下面是一個 Kubernetes 叢集在事故當下的告警快照。

從候選清單裡挑出這次事故的根因,只輸出一個 JSON 物件,不要有其他文字:
{{"root_cause_id": "<候選清單裡的其中一個 id>"}}

候選清單:
{candidates}

告警快照:
{alerts}
"""


# §2 判定|規則是 Calvin 定的,正本在工作簿 §5.2.0
def normalize(s: str) -> str:
    """判定前的正規化:去掉前後空白、轉成小寫。

    §5.2.0 的判斷分支 1 與 2:候選清單全是小寫,區分大小寫或計較空白,
    會把「模型照不照抄格式」混進分數,而這一題量的是診斷。
    """
    return s.strip().lower()


def is_correct(model_answer: str, root_cause_id: str) -> bool:
    """模型挑的 id 算不算對。§5.2.0:正規化之後完全相同才算對。"""
    return normalize(model_answer) == normalize(root_cause_id)


def is_off_list(model_answer: str, candidates: list[str]) -> bool:
    """模型是不是答了候選清單以外的東西。

    §5.2.0 的判斷分支 3:這種情況照樣算錯,但要另外統計次數——
    這個數字看得出模型有沒有在編造不存在的東西。
    """
    return normalize(model_answer) not in {normalize(c) for c in candidates}


# §3 讀題
def load_case(scenario: str) -> dict:
    """讀一題:候選 id 清單、標準答案、告警快照。"""
    hits = glob.glob(f"{SRE_ROOT}/*/{scenario}/ground_truth.yaml")
    if not hits:
        raise SystemExit(f"✗ 找不到 {scenario} 的 ground_truth.yaml")
    gt_path = hits[0]
    doc = yaml.safe_load(open(gt_path, encoding="utf-8"))
    spec = doc.get("spec", doc)  # 35 個場景有兩種包法,有的多一層 spec

    groups = spec.get("groups") or []
    candidates = [g["id"] for g in groups]
    root = [g["id"] for g in groups if g.get("root_cause")]
    if len(root) != 1:
        raise SystemExit(f"✗ {scenario} 的 root_cause 不是恰好一個:{root}")

    alerts_hits = sorted(
        glob.glob(os.path.join(os.path.dirname(gt_path), "alerts", "alerts_in_alerting_state_*.json"))
    )
    if not alerts_hits:
        raise SystemExit(f"✗ {scenario} 沒有 alerts_in_alerting_state 快照")

    return {
        "scenario": scenario,
        "candidates": candidates,
        "root_cause_id": root[0],
        "alerts_path": alerts_hits[0],
        "alerts": open(alerts_hits[0], encoding="utf-8").read(),
    }


# §4 打一次 API
def call_once(prompt: str, pinned: bool) -> dict:
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


def ask(case: dict, pinned: bool) -> tuple[str, dict]:
    """問一題,回傳 (模型挑的 id, 完整回應)。挑不出 id 時回空字串。"""
    prompt = PROMPT_TEMPLATE.format(
        candidates="\n".join(f"- {c}" for c in case["candidates"]),
        alerts=case["alerts"],
    )
    for attempt in (1, 2, 3):
        try:
            payload = call_once(prompt, pinned)
            break
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            detail = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else e.reason
            print(f"    第 {attempt} 次失敗:{detail}")
            if attempt == 3:
                raise SystemExit(f"✗ {case['scenario']} 連三次失敗,停手。")
            time.sleep(3 * attempt)

    text = payload["choices"][0]["message"]["content"]
    try:
        answer = json.loads(text)["root_cause_id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        answer = ""  # 模型沒吐出合法 JSON,這題算錯
    return answer, payload


# §5 跑一組
def run_group(label: str, pinned: bool, cases: list[dict]) -> tuple[list[int], list[int]]:
    """跑 N_ROUNDS 輪,每輪 5 題,回傳(每輪總分, 每輪答了清單外東西的次數)。"""
    out_dir = os.path.join(OUT_DIR, label)
    os.makedirs(out_dir, exist_ok=True)
    scores = []
    off_lists = []

    for rnd in range(1, N_ROUNDS + 1):
        score = 0
        off_list = 0
        print(f"  第 {rnd} 輪")
        for case in cases:
            answer, payload = ask(case, pinned)
            hit = is_correct(answer, case["root_cause_id"])
            off = is_off_list(answer, case["candidates"])
            score += 1 if hit else 0
            off_list += 1 if off else 0
            mark = "對" if hit else ("錯(清單外)" if off else "錯")
            print(
                f"    {case['scenario']:<14} 模型答 {answer or '(不是合法 JSON)':<44}"
                f" 標準答案 {case['root_cause_id']:<44} {mark}"
            )
            with open(
                os.path.join(out_dir, f"round{rnd}_{case['scenario']}.json"), "w", encoding="utf-8"
            ) as f:
                json.dump(
                    {
                        "scenario": case["scenario"],
                        "root_cause_id": case["root_cause_id"],
                        "model_answer": answer,
                        "is_correct": hit,
                        "is_off_list": off,
                        "response": payload,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        print(f"  第 {rnd} 輪總分:{score} / {len(cases)}|答了清單外的東西 {off_list} 次")
        scores.append(score)
        off_lists.append(off_list)

    return scores, off_lists


def report(label: str, scores: list[int], off_lists: list[int], n_cases: int) -> None:
    """§6 三輪的分數一起報,不報單一數字(鐵律 18)。"""
    pct = [s / n_cases * 100 for s in scores]
    print(f"\n[{label}] 三輪總分:{scores}(滿分 {n_cases})")
    print(f"        換算百分比:{[f'{p:.0f}%' for p in pct]}")
    print(f"        最高與最低差 {max(pct) - min(pct):.0f} 個百分點")
    if len(scores) > 1:
        print(f"        三輪的標準差 {statistics.stdev(pct):.1f} 個百分點")
    print(f"        三輪各答了清單外的東西 {off_lists} 次")


def main() -> int:
    missing = [
        k
        for k in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL")
        if not os.environ.get(k)
    ]
    if missing:
        print(f"✗ .env 缺這幾個變數:{', '.join(missing)}")
        return 1

    cases = [load_case(s) for s in SCENARIOS]
    print(f"模型:{os.environ['OPENROUTER_MODEL']}|provider 鎖 {PROVIDER['order'][0]}")
    print(f"題目 {len(cases)} 題,每組跑 {N_ROUNDS} 輪:")
    for c in cases:
        print(f"  {c['scenario']:<14} 候選 {len(c['candidates']):>2} 個|標準答案 {c['root_cause_id']}")

    print(f"\n--- B 組(主組):temperature=0、seed={SEED} ---")
    b, b_off = run_group("B", True, cases)

    print("\n--- A 組(對照):不設 temperature、不設 seed ---")
    a, a_off = run_group("A", False, cases)

    report("B 組", b, b_off, len(cases))
    report("A 組", a, a_off, len(cases))
    print(f"\n每題的完整回應存在 {OUT_DIR}/B/ 與 {OUT_DIR}/A/。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
