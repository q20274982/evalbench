# 作業 2-4 執行腳本:調整用 25 題 × 1 輪,跑一次存一批 trace、落一批 case。
#
# 規格正本:W02 工作簿 §7(五項 + 引言塊各條決定)、§8 設定。
#   搜尋設定 = B + 同 regarding kind 只留一筆(§8 設定,同 2-6 的 1013/1016-en)
#   prompt   = src/pipeline.py CITATION_TEMPLATE(破和修共用,只差引用檢查開或關)
#   防線     = src/citation_check.py,開關讀 CITATION_CHECK(on / off,沒設就報錯)
#   只跑 1 輪(§7 2026-10-18 決定:成本),數字沒有區間
#   只跑不允許說不知道那一組(§7 第 5 項決定 ④);prompt 不提 abstain / ruled_out
# 留出的 10 題不跑(§3,同 2-6)。
#
# 兩個模式:
#   dry  — 不打 API、不寫 DB 以外的東西:組好 25 份 prompt,印長度、估計花費、檢查 retrieval
#          跟 prompt 一字不差
#   live — 打 API 25 次,一題一份 trace(src/tracing.py),一題一筆 case
#
# 跑法(從 repo 根目錄,DB 要先起來):
#     export CITATION_CHECK=off POSTGRES_HOST=localhost
#     uv run python experiments/W02/2-4-citation/run_2_4.py dry
#     uv run python experiments/W02/2-4-citation/run_2_4.py live
# 跑完印出 run_id,並追加一行到 out/runs.jsonl(run_id、開關、時間)。
# INV-13:ground_truth.yaml 不進 prompt;這支腳本不評分,評分在 compare_2_4.py。

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cases_2_4  # noqa: E402

import chunk_store  # noqa: E402
import citation_check  # noqa: E402
import tracing  # noqa: E402
from parsing import inspect  # noqa: E402
from pipeline import (  # noqa: E402
    DATASET_ROOT,
    RETRIEVAL_K,
    build_citation_prompt,
    build_request_body,
    call_model,
)

# §1 常數
OUT = Path(__file__).resolve().parent / "out"
HELD_OUT = {6, 9, 12, 15, 16, 20, 23, 29, 31, 35}  # 同 2-6-agent/run_agent.py
ROUND_NO = 1
# B + dedup_kind(§8 設定)。events_mode / ts_config 是 build_citation_prompt 寫死的值,
# 一起存進 slice_params,重建 prompt 才不用回頭讀碼。
SLICE = {
    "combine": "each",
    "split": True,
    "fuse": "flat",
    "pick": "dedup_kind",
    "k": RETRIEVAL_K,
    "events_mode": "raw",
    "ts_config": "simple",
}
PRICE_PROMPT, PRICE_COMPLETION = 0.065, 0.26  # US$ / 百萬 token,同 2-6
CHARS_PER_TOKEN = 2.0  # 估計用,同 2-6
STOP_COST = 0.5  # US$
WORKERS = 8


def tune_scenarios() -> list[str]:
    names = [p.name for p in DATASET_ROOT.iterdir() if p.is_dir()]
    names = [n for n in names if int(n.split("-")[1]) not in HELD_OUT]
    return sorted(names, key=lambda n: int(n.split("-")[1]))


# §2 組 prompt
def build_all(conn, scenarios: list[str]) -> dict[str, tuple[str, list[dict]]]:
    return {
        s: build_citation_prompt(
            conn, s, SLICE["combine"], SLICE["split"], SLICE["fuse"], SLICE["k"], SLICE["pick"]
        )
        for s in scenarios
    }


def main(mode: str) -> None:
    switch = citation_check.resolve_mode()  # 沒設或打錯在這裡就停
    scenarios = tune_scenarios()
    conn = chunk_store.connect()
    prompts = build_all(conn, scenarios)

    # dry 的自我檢查:retrieval 每一塊原文都原樣出現在 prompt 裡(§7 第 2 項)
    mismatch = [s for s, (p, bl) in prompts.items() if any(b["text"] not in p for b in bl)]
    lens = sorted(len(p) for p, _ in prompts.values())
    est = sum(lens) / CHARS_PER_TOKEN * PRICE_PROMPT / 1e6
    print(f"[§2] {len(scenarios)} 題,開關 {switch}")
    print(f"[§2] prompt 字元 中位數 {lens[len(lens) // 2]} 最長 {lens[-1]};估計 US${est:.3f}")
    print(f"[§2] retrieval 塊數 {sorted({len(bl) for _, bl in prompts.values()})};")
    print(f"[§2] retrieval 原文不在 prompt 裡的題:{mismatch or '無'}")
    if mode != "live":
        return

    cases_2_4.ensure_schema(conn)
    fps = {s: cases_2_4.fingerprint(conn, s) for s in scenarios}
    run_id = tracing.new_run_id()
    lock, spent = threading.Lock(), [0.0]

    # 一題:打模型 → 存 trace → 跑防線
    def one(s: str) -> dict:
        prompt, blocks = prompts[s]
        body = build_request_body(prompt)
        kw = dict(
            run_id=run_id,
            scenario=s,
            round_no=ROUND_NO,
            prompt=prompt,
            slice_params=SLICE,
            request_body=body,
            retrieval=blocks,
        )
        with lock:
            over = spent[0] > STOP_COST
        if over:
            raw, usage, err = None, {}, "超過停止門檻,沒打"
        else:
            try:
                raw, usage = call_model(body)
                err = None
            except (OSError, KeyError) as e:  # 同 2-6:OSError 含 URLError、TimeoutError
                raw, usage, err = None, {}, repr(e)
        c = usage.get("prompt_tokens", 0) * PRICE_PROMPT / 1e6
        c += usage.get("completion_tokens", 0) * PRICE_COMPLETION / 1e6
        with lock:
            spent[0] += c
        trace = tracing.build_trace(**kw, raw=raw, usage=usage, error=err)
        tracing.write_trace(trace)
        obs = inspect(raw) if raw is not None else None
        result = citation_check.check(blocks, obs)
        return {
            "trace_id": trace["trace_id"],
            "scenario": s,
            "verdict": obs["verdict"] if obs else "call_failed",
            "status": result["status"],
            "diag": obs["diagnosis"] if obs and result["status"] != "parse_failed" else None,
        }

    with ThreadPoolExecutor(WORKERS) as ex:
        rows = list(ex.map(one, scenarios))
    for r in rows:
        cases_2_4.open_case(
            conn,
            fp=fps[r["scenario"]],
            run_id=run_id,
            scenario=r["scenario"],
            round_no=ROUND_NO,
            status=r["status"],
            diag=r["diag"],
        )

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"[§3] run_id {run_id},花費約 US${spent[0]:.3f}(usage × 公開價)")
    print(f"[§3] status 題數(1 輪,沒有區間):{json.dumps(counts, ensure_ascii=False)}")
    print(f"[§3] trace:traces/{run_id}/;case:{cases_2_4.SCHEMA}.cases")
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as f:
        line = {"run_id": run_id, "citation_check": switch, "at": datetime.now(UTC).isoformat()}
        f.write(json.dumps(line) + "\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dry")
