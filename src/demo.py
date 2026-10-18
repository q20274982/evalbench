# W01 §7.7|一鍵起 demo:快照進 → 切片 → 模型 → 結構化輸出 → 落一張 case + 存 trace。
#
# 規格正本是 W01 工作簿 §7.7 與 §7.7.1(Calvin 2026-09-21 定案)。
#
#   ① 起點含容器:`docker compose up -d db` 起 Postgres,再跑這支
#   ② 第 2 段叫「切片」不叫「檢索」——本週就是寫死的切法(src/pipeline.py),W2 才換
#   ④ 固定 Scenario-1;預設跑 1 題,--all 跑 35 題 × 3 輪
#   ⑤ 題內停、題間繼續;呼叫失敗的那一筆仍然落 trace(§7.6.1 ③a)
#
# **成功判準不看分數**(§7.7 ④):五段各自走完沒有、case 有沒有落、trace 檔在不在,
# 那些才是成功燈。分數另外印,不進成功判定——挑一題會答對的來當綠燈的話,「管線通」
# 與「答對了」就共用一盞燈,變紅時分不出是哪一個壞了。
#
# 住 src/ 不住 experiments/ 的理由:這是系統的入口,每週都要跑,不是某一份作業的
# 一次性紀錄(/scaffold 慣例 8,2026-09-21)。
#
# 跑法:
#     docker compose up -d db
#     docker compose run --rm app uv run python src/demo.py
#     docker compose run --rm app uv run python src/demo.py --all
#
# INV-13:ground_truth.yaml 不進 prompt。切片只讀 alerts/ 與 k8s_events_raw.tsv;
# 答案檔只在 §3 評分那一側被讀。

import sys
import time
import urllib.error
from pathlib import Path
from typing import Any

import psycopg

from cases import alert_fingerprint, ensure_schema, open_case
from config import settings
from parsing import inspect
from pipeline import DATASET_ROOT, build_prompt, build_request_body, call_model, slice_params
from pipeline import pick_scenarios as all_scenarios
from scoring import load_ground_truth, score_one
from tracing import build_trace, new_run_id, write_trace

# §1 常數
DEMO_SCENARIO = "Scenario-1"
N_ROUNDS = 3

# §7.4.2 定案:刻意保留的那個會動手的動作。本週每一筆 case 都是它。
PROPOSED_ACTION = "apply_remediation"

# 預期範圍,拿 §7.6.2 的實測當基準(Scenario-1 存檔那次是 prompt 4,189 tokens)。
# 落在範圍外不會讓 demo 失敗,只印一行提醒——它是「數字對不對得上」的哨兵,不是判準。
PROMPT_TOKENS_EXPECTED = (3000, 6000)


# §2 挑場景
def pick_scenarios(run_all: bool) -> list[str]:
    return all_scenarios() if run_all else [DEMO_SCENARIO]


# §3 評分(不進成功判定,只印出來。⑤:解析失敗算 0 分、留在分母裡)
def judge(raw: str, scenario: str) -> dict:
    obs = inspect(raw)
    diag = obs["diagnosis"]
    entity = diag.root_cause_entity if diag is not None else None
    gt = load_ground_truth(DATASET_ROOT / scenario)
    res = score_one(entity, gt)
    return {
        "scenario": scenario,
        "verdict": obs["verdict"],
        "schema_ok": obs["schema_ok"],
        "diagnosis": diag,
        "entity": entity,
        "score": res.score,
        "stage": res.stage,
    }


# §4 跑一題(題內停:任何一段壞掉就回傳失敗,不往下寫 case)
def run_one(conn: psycopg.Connection, run_id: str, scenario: str, round_no: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scenario": scenario,
        "round": round_no,
        "ok": False,
        "stopped_at": None,
        "reason": None,
        "score": 0,
        "case_id": None,
        "trace_path": None,
    }

    # 第 1–2 段
    try:
        prompt, fed_alerts = build_prompt(scenario)
    except Exception as e:  # noqa: BLE001 — 讀檔與解析的任何失敗都算這一段壞掉
        result.update(stopped_at="1-2 讀快照/切片", reason=repr(e))
        return result
    if not fed_alerts:
        # Scenario-11 沒有告警快照。沒有告警就算不出指紋,case 落不下去。
        result.update(stopped_at="2 切片", reason="這個場景沒有 firing 告警,算不出指紋")
        return result

    body = build_request_body(prompt)
    trace_kw = dict(
        run_id=run_id, scenario=scenario, round_no=round_no,
        prompt=prompt, slice_params=slice_params(), request_body=body,
    )

    # 第 3 段
    try:
        raw, usage = call_model(body)
    except (urllib.error.URLError, TimeoutError, KeyError) as e:
        detail = ""
        if isinstance(e, urllib.error.HTTPError):
            detail = " " + e.read().decode("utf-8", "replace")[:200]
        # §7.6.1 ③a:失敗的呼叫也落一份,否則少掉的題之後查不出原因。
        result["trace_path"] = str(write_trace(build_trace(**trace_kw, error=f"{e!r}{detail}")))
        result.update(stopped_at="3 打模型", reason=f"{e!r}{detail}")
        return result

    result["trace_path"] = str(write_trace(build_trace(**trace_kw, raw=raw, usage=usage)))
    result["prompt_tokens"] = usage.get("prompt_tokens")
    result["cost"] = float(usage.get("cost") or 0)

    # 第 4 段
    rec = judge(raw, scenario)
    result["score"] = rec["score"]
    result["entity"] = rec["entity"]
    if not rec["schema_ok"] or rec["diagnosis"] is None:
        # §7.7 ⑤:解析失敗不落 case(Calvin 2026-09-21)。這一題仍然留在分母裡拿 0 分。
        result.update(stopped_at="4 解析", reason=f"schema 沒過:{rec['verdict']}")
        return result

    # 第 5 段
    d = rec["diagnosis"]
    try:
        result["case_id"] = open_case(
            conn,
            fingerprint=alert_fingerprint(fed_alerts),
            proposed_action=PROPOSED_ACTION,
            run_id=run_id,
            scenario=scenario,
            round_no=round_no,
            root_cause=d.root_cause,
            root_cause_entity=d.root_cause_entity,
            confidence=d.confidence,
            evidence=d.evidence,
        )
    except psycopg.Error as e:
        result.update(stopped_at="5 落 case", reason=repr(e))
        return result

    result["ok"] = True
    return result


# §5 入口
def main(argv: list[str]) -> int:
    run_all = "--all" in argv[1:]
    scenarios = pick_scenarios(run_all)
    rounds = N_ROUNDS if run_all else 1
    run_id = new_run_id()

    print(f"\n=== §5.1 demo run_id={run_id} 場景 {len(scenarios)} 個 × {rounds} 輪 ===")

    try:
        conn = psycopg.connect(settings.postgres_dsn, connect_timeout=10)
    except psycopg.Error as e:
        print(f"✗ 連不上 Postgres:{e!r}\n  先跑 docker compose up -d db")
        return 2
    with conn:
        ensure_schema(conn)

        started = time.monotonic()
        results: list[dict[str, Any]] = []
        for rnd in range(1, rounds + 1):
            for sc in scenarios:
                r = run_one(conn, run_id, sc, rnd)
                results.append(r)
                if r["ok"]:
                    print(
                        f"  [{rnd}/{sc}] ✓ 五段全通 case={r['case_id']} "
                        f"score={r['score']} entity={r.get('entity')!r}"
                    )
                else:
                    # 題間繼續:標成失敗、記原因,下一題照跑。
                    print(f"  [{rnd}/{sc}] ✗ 停在「{r['stopped_at']}」:{r['reason']}")
        elapsed = time.monotonic() - started

    # §5.2 成功判定(不看分數)
    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    print(f"\n--- §5.2 管線 ---\n  五段全通 {len(ok)}/{len(results)} 題,耗時 {elapsed:.1f}s")
    if bad:
        print("  停下來的題:")
        for r in bad:
            print(f"    [{r['round']}/{r['scenario']}] {r['stopped_at']} — {r['reason']}")
    missing = [r for r in results if r["trace_path"] and not Path(r["trace_path"]).is_file()]
    print(
        f"  trace 檔:{len([r for r in results if r['trace_path']])} 份,"
        f"不存在的 {len(missing)} 份"
    )
    toks = [r["prompt_tokens"] for r in results if r.get("prompt_tokens")]
    if toks:
        lo, hi = PROMPT_TOKENS_EXPECTED
        outside = [t for t in toks if not lo <= t <= hi]
        print(
            f"  prompt tokens:{min(toks)}–{max(toks)},預期 {lo}–{hi},"
            f"落在範圍外 {len(outside)} 題"
        )
    print(f"  花費:${sum(r.get('cost', 0) for r in results):.4f}")

    # §5.3 分數(另外印,不進成功判定。⑤:解析失敗算 0 分、留在分母裡)
    print("\n--- §5.3 分數(解析失敗算 0 分,留在分母裡)---")
    if run_all:
        scores = []
        for rnd in range(1, rounds + 1):
            rows = [r for r in results if r["round"] == rnd]
            hit = sum(r["score"] for r in rows)
            rate = hit / len(rows)
            scores.append(rate)
            print(f"  第 {rnd} 輪:{hit}/{len(rows)} = {rate:.0%}")
        spread = max(scores) - min(scores)
        print(f"  三輪:{' / '.join(f'{s:.0%}' for s in scores)}|全距 {spread:.0%}")
    else:
        r = results[0]
        print(f"  單題 {r['scenario']}:score={r['score']}(一題沒有區間可報,區間要 --all)")

    # 退出碼:基礎設施層錯誤 = 2(上面已 return);一題都沒通 = 1;其餘 0。
    # 題級的解析失敗不改退出碼——§7.2.1 量到 5%–10% 會壞,那是本週的已知狀態。
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
