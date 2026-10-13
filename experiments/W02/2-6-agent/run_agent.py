"""作業 2-6 最後一步:混合搜尋接進 agent,調整用 25 題 × 3 輪,跟 W1 基準線同 25 題比。

設定出處:W02 工作簿 §9.2「接進 agent 的設定」(Calvin 2026-10-12)
  prompt  = 組查詢用到的東西(告警去重後 + 那 5 筆 events 原文)+ 混合搜尋前 10 塊
  搜尋設定兩組:A = one/raw 不換 simple;B = each/raw 換 simple 一次合併
  判準(跑之前定):逐題比,一題 3 輪對 2 輪以上算對(多數決),淨贏 3 題以上才算變好
對照組:experiments/W01/1-3-baseline/out/1_3_records.json 裡同 25 題(不重跑 W1)。
模型、provider、temperature、seed、max_tokens 跟 W1 相同(src/pipeline.py build_request_body)。
評分照 W1:src/parsing.py 解析、src/scoring.py 評分;解析失敗或呼叫失敗算 0 分、留在分母。

設定組(第二個參數,預設 1012):
  1012 — 上面兩組,events 用起點挑法(code repo 262229f 的結果)
  1013 — events 換挑法(W02 §9.2,Calvin 2026-10-13 照 TA 建議):
         B + dedup_kind(同 regarding kind 只留一筆);A + warning_all(Warning 全部放)

兩個模式:
  dry  — 不打 API。組好 50 份 prompt,印長度與估計花費。
  live — 打 API,2 組 × 25 題 × 3 輪 = 150 次。花費累計超過 STOP_COST 就停。

跑法(從 repo 根目錄,DB 要先起來):
    POSTGRES_HOST=localhost uv run python experiments/W02/2-6-agent/run_agent.py dry
    POSTGRES_HOST=localhost uv run python experiments/W02/2-6-agent/run_agent.py live
    POSTGRES_HOST=localhost uv run python experiments/W02/2-6-agent/run_agent.py live 1013
INV-13:ground_truth.yaml 不進 prompt;只在 §3 評分那一側被讀。
"""

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import chunk_store  # noqa: E402
from parsing import inspect  # noqa: E402
from pipeline import (  # noqa: E402
    DATASET_ROOT,
    build_request_body,
    build_retrieval_prompt,
    call_model,
)
from scoring import load_ground_truth, score_one  # noqa: E402

# §1 常數
OUT = Path(__file__).resolve().parent / "out"
W1_RECORDS = Path(__file__).resolve().parents[2] / "W01/1-3-baseline/out/1_3_records.json"
HELD_OUT = {6, 9, 12, 15, 16, 20, 23, 29, 31, 35}  # 同 2-3-chunking/run_chunking.py
N_ROUNDS = 3
CONFIG_SETS = {
    "1012": {
        "A one/raw 不換 simple": ("one", False, "flat", "warning5"),
        "B each/raw 換 simple 一次合併": ("each", True, "flat", "warning5"),
    },
    "1013": {
        "B + dedup_kind": ("each", True, "flat", "dedup_kind"),
        "A + warning_all": ("one", False, "flat", "warning_all"),
    },
}
CONFIGS = CONFIG_SETS["1012"]
PRICE_PROMPT, PRICE_COMPLETION = 0.065, 0.26  # US$ / 百萬 token,OpenRouter 2026-10-12 公開價
CHARS_PER_TOKEN = 2.0  # 估計用,W02 轉向量時量到每 token 1.9–3.3 字元
STOP_COST = 1.0  # US$,dry 估計約 0.3
WORKERS = 8
NET_WIN_BAR = 3


def tune_scenarios() -> list[str]:
    names = [p.name for p in DATASET_ROOT.iterdir() if p.is_dir()]
    names = [n for n in names if int(n.split("-")[1]) not in HELD_OUT]
    return sorted(names, key=lambda n: int(n.split("-")[1]))


# §2 組 prompt(每組設定、每題一份,3 輪共用;搜尋沒有隨機成分)
def build_all(scenarios: list[str]) -> dict[str, dict[str, tuple[str, list[str]]]]:
    conn = chunk_store.connect()
    return {
        name: {s: build_retrieval_prompt(conn, s, c, sp, fz, pick=pk) for s in scenarios}
        for name, (c, sp, fz, pk) in CONFIGS.items()
    }


# §3 評一題(跟 W1 run_1_3_baseline.py §5 同一套)
def judge(raw: str, scenario: str) -> dict:
    obs = inspect(raw)
    diag = obs["diagnosis"]
    entity = diag.root_cause_entity if diag is not None else None
    res = score_one(entity, load_ground_truth(DATASET_ROOT / scenario))
    return {"verdict": obs["verdict"], "entity": entity, "score": res.score, "stage": res.stage}


# §4 多數決與逐題比(判準:W02 §9.2)
def majority(records: list[dict], scenarios: list[str]) -> dict[str, int]:
    by: dict[str, int] = {s: 0 for s in scenarios}
    for r in records:
        if r["scenario"] in by:
            by[r["scenario"]] += r["score"]
    return {s: int(n * 2 > N_ROUNDS) for s, n in by.items()}


def compare(new: dict[str, int], old: dict[str, int]) -> dict:
    win = [s for s in new if new[s] and not old[s]]
    lose = [s for s in new if old[s] and not new[s]]
    return {
        "W1 多數決對幾題": sum(old.values()),
        "新多數決對幾題": sum(new.values()),
        "贏": win,
        "輸": lose,
        "淨贏": len(win) - len(lose),
        f"達到淨贏 {NET_WIN_BAR} 題": len(win) - len(lose) >= NET_WIN_BAR,
    }


def main(mode: str) -> None:
    scenarios = tune_scenarios()
    prompts = build_all(scenarios)
    est = 0.0
    for name, per in prompts.items():
        lens = sorted(len(p) for p, _ in per.values())
        cost = sum(len(p) / CHARS_PER_TOKEN for p, _ in per.values()) * N_ROUNDS
        cost = cost * PRICE_PROMPT / 1e6
        est += cost
        print(
            f"[§2] {name}:prompt 字元 中位數 {lens[len(lens) // 2]} 最長 {lens[-1]};"
            f"3 輪估計 US${cost:.3f}"
        )
    print(f"[§2] 合計估計 US${est:.3f}(prompt 端,字元÷{CHARS_PER_TOKEN} 換算)")
    if mode != "live":
        return

    lock, spent = threading.Lock(), [0.0]

    def one(job: tuple[str, str, int]) -> dict:
        name, s, rnd = job
        prompt, ids = prompts[name][s]
        rec = {"config": name, "scenario": s, "round": rnd, "retrieved": ids}
        with lock:
            if spent[0] > STOP_COST:
                return {**rec, "error": "超過停止門檻,沒打", "score": 0}
        try:
            raw, usage = call_model(build_request_body(prompt))
        except (OSError, KeyError) as e:  # OSError 含 URLError、TimeoutError、ConnectionResetError
            return {**rec, "error": repr(e), "score": 0, "verdict": "呼叫失敗"}
        c = usage.get("prompt_tokens", 0) * PRICE_PROMPT / 1e6
        c += usage.get("completion_tokens", 0) * PRICE_COMPLETION / 1e6
        with lock:
            spent[0] += c
        return {**rec, **judge(raw, s), "usage": usage, "raw": raw}

    jobs = [(n, s, r) for n in CONFIGS for r in range(1, N_ROUNDS + 1) for s in scenarios]
    with ThreadPoolExecutor(WORKERS) as ex:
        records = list(ex.map(one, jobs))
    print(f"[§5] 打完 {len(records)} 次,花費約 US${spent[0]:.3f}(usage × 公開價)")

    w1 = [r for r in json.loads(W1_RECORDS.read_text()) if r["scenario"] in scenarios]
    old = majority(w1, scenarios)
    summary = {}
    for name in CONFIGS:
        recs = [r for r in records if r["config"] == name]
        per_round = [
            sum(r["score"] for r in recs if r["round"] == k) for k in range(1, N_ROUNDS + 1)
        ]
        errors = sum(1 for r in recs if r.get("error"))
        summary[name] = {
            "每輪答對(/25)": per_round,
            "呼叫失敗": errors,
            **compare(majority(recs, scenarios), old),
        }
        print(f"[§4] {name}:{json.dumps(summary[name], ensure_ascii=False)}")
    OUT.mkdir(exist_ok=True)
    (
        OUT / ("records.json" if CONFIGS is CONFIG_SETS["1012"] else f"records-{SET}.json")
    ).write_text(json.dumps({"records": records, "summary": summary}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    SET = sys.argv[2] if len(sys.argv) > 2 else "1012"
    CONFIGS = CONFIG_SETS[SET]
    main(sys.argv[1] if len(sys.argv) > 1 else "dry")
