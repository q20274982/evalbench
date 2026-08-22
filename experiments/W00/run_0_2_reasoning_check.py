"""W00 作業 0-2 的附帶驗證|把 reasoning 打開,分數會不會變。

**來源與範圍**(鐵律 7):2026-08-23 Calvin 問「0 分是模型太弱,還是因為關掉了
reasoning」。關 reasoning 是 TA 為了拿乾淨 JSON 自己加的設定(工作簿 §8.3),
所以這是驗證 TA 引入的變數,不是擴大作業 0-2。
換模型比較留在 W1,餵更多觀測資料留在 W2,本檔都不碰。

跟 run_0_2_score_variance.py 的差別只有一個:`reasoning` 從關改成開到 high。
題目、prompt、判定規則、temperature 與 seed 全部沿用,單一變數。

只跑一輪 5 題。這一輪要回答的是「分數會不會變」,不是「分數穩不穩」。

⚠️ 這個模型的思考過程是直接寫在 `message.content` 的純文字裡,不是走 OpenRouter
的 reasoning 欄位,所以 `exclude: true` 沒有作用(2026-08-23 實測)。答案要從
content 的尾端抓最後一個 JSON 物件出來。

成本:5 次請求,每次輸出約 5000 token,估計 $0.007。

跑法:
    docker compose run --rm app uv run --with pyyaml python experiments/W00/run_0_2_reasoning_check.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_0_2_score_variance as base  # 題目、prompt、判定規則全部沿用

REASONING = {"effort": "high"}
MAX_TOKENS = 8000
OUT_DIR = "experiments/W00/out/0-2-reasoning"

JSON_RE = re.compile(r"\{[^{}]*\"root_cause_id\"[^{}]*\}")


def extract_answer(text: str) -> str:
    """從一長串思考過程的尾端,抓最後一個帶 root_cause_id 的 JSON 物件。"""
    hits = JSON_RE.findall(text)
    if not hits:
        return ""
    try:
        return json.loads(hits[-1])["root_cause_id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return ""


def ask(case: dict) -> tuple[str, dict]:
    prompt = base.PROMPT_TEMPLATE.format(
        candidates="\n".join(f"- {c}" for c in case["candidates"]),
        alerts=case["alerts"],
    )
    body = {
        "model": os.environ["OPENROUTER_MODEL"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "provider": base.PROVIDER,
        "reasoning": REASONING,
        "temperature": 0,
        "seed": base.SEED,
    }
    import urllib.request

    req = urllib.request.Request(
        os.environ["OPENROUTER_BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        payload = json.load(resp)
    text = payload["choices"][0]["message"]["content"]
    return extract_answer(text), payload


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    cases = [base.load_case(s) for s in base.SCENARIOS]
    print(f"模型:{os.environ['OPENROUTER_MODEL']}|reasoning={REASONING}")
    print("對照組是 run_0_2_score_variance.py 的 B 組(reasoning 關,三輪都 0 分)\n")

    score = 0
    off_list = 0
    cost = 0.0
    for case in cases:
        answer, payload = ask(case)
        hit = base.is_correct(answer, case["root_cause_id"])
        off = base.is_off_list(answer, case["candidates"])
        score += 1 if hit else 0
        off_list += 1 if off else 0
        cost += payload["usage"].get("cost", 0)
        mark = "對" if hit else ("錯(清單外)" if off else "錯")
        print(
            f"  {case['scenario']:<14} 模型答 {answer or '(抓不到 JSON)':<44}"
            f" 標準答案 {case['root_cause_id']:<44} {mark}"
            f"  輸出 {payload['usage']['completion_tokens']} token"
        )
        with open(os.path.join(OUT_DIR, f"{case['scenario']}.json"), "w", encoding="utf-8") as f:
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

    print(f"\n這一輪總分:{score} / {len(cases)}|答了清單外的東西 {off_list} 次")
    print(f"對照:reasoning 關的 B 組三輪是 [0, 0, 0]")
    print(f"這一輪花費 ${cost:.4f}")
    print("\n⚠️ 這是一輪的單一數字,不是可以拿來報的分數。要報分數得跑多輪(INV-6)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
