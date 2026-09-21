"""W01 §7.7|一鍵起 demo:快照進 → 切片 → 模型 → 結構化輸出 → 落一張 case + 存 trace。

規格正本是 W01 工作簿 §7.7 與 §7.7.1(Calvin 2026-09-21 定案)。

  ① 起點含容器:`docker compose up -d db` 起 Postgres,再跑這支
  ② 第 2 段叫「切片」不叫「檢索」——本週就是寫死的切法,W2 才換成檢索
  ④ 固定 Scenario-1;預設跑 1 題,--all 跑 35 題 × 3 輪
  ⑤ 題內停、題間繼續;呼叫失敗的那一筆仍然落 trace(§7.6.1 ③a)

**成功判準不看分數**(§7.7 ④):五段各自走完沒有、case 有沒有落、trace 檔在不在,
那些才是成功燈。分數另外印,不進成功判定——挑一題會答對的來當綠燈的話,「管線通」
與「答對了」就共用一盞燈,變紅時分不出是哪一個壞了。

切片與打模型的碼是從 experiments/W01/run_1_3_baseline.py **複製**過來的
(Calvin 2026-09-21 選 b)。代價寫在這裡:那支腳本改了切法,這支不會跟著變,
而 §7.7 ② 寫的是「切法照 §7.3.2」。兩邊要一起改。

跑法:
    docker compose up -d db
    docker compose run --rm app uv run python experiments/W01/run_demo.py
    docker compose run --rm app uv run python experiments/W01/run_demo.py --all

INV-13:ground_truth.yaml 不進 prompt。§3 只讀 alerts/ 與 k8s_events_raw.tsv;
答案檔只在 §6 評分那一側被讀。
"""

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import psycopg  # noqa: E402
from cases import alert_fingerprint, ensure_schema, open_case  # noqa: E402
from config import settings  # noqa: E402
from parsing import inspect  # noqa: E402
from scoring import load_ground_truth, score_one  # noqa: E402
from tracing import build_trace, new_run_id, write_trace  # noqa: E402

# §1 常數(跟 run_1_3_baseline.py 同一組,複製過來)
REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = (
    REPO_ROOT / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
)

DEMO_SCENARIO = "Scenario-1"
N_ROUNDS = 3
SEED = 42
TEMPERATURE = 0
MAX_TOKENS = 600
PROVIDER = {"order": ["Alibaba"], "allow_fallbacks": False}
REASONING = {"enabled": False}

SLICE_PARAMS_KEYS = ("N_ALERTS", "N_EVENTS", "EVENT_BODY_CHARS")
N_ALERTS = 12
N_EVENTS = 20
EVENT_BODY_CHARS = 400

# §7.4.2 定案:刻意保留的那個會動手的動作。本週每一筆 case 都是它。
PROPOSED_ACTION = "apply_remediation"

# 預期範圍,拿 §7.6.2 的實測當基準(Scenario-1 存檔那次是 prompt 4,189 tokens)。
# 落在範圍外不會讓 demo 失敗,只印一行提醒——它是「數字對不對得上」的哨兵,不是判準。
PROMPT_TOKENS_EXPECTED = (3000, 6000)

PROMPT_TEMPLATE = """你是值班的 SRE。下面是一個 Kubernetes 叢集在事故當下的觀測快照。

請判斷這次事故的根因,只輸出一個 JSON 物件,不要有其他文字:
{{"root_cause": "<一句話講根因>", "root_cause_entity": "<根因資源的名稱,只填名稱>",\
 "confidence": <0 到 1 的小數>, "evidence": ["<證據一>"]}}

=== 告警 ===
{alerts}

=== K8s events ===
{events}
"""


# §2 挑場景
def pick_scenarios(run_all: bool) -> list[str]:
    if not run_all:
        return [DEMO_SCENARIO]
    return sorted(p.name for p in DATASET_ROOT.iterdir() if p.is_dir())


# §3 第 1–2 段|讀快照 + 切片(不碰 ground_truth.yaml)
def build_slice(scenario: str) -> tuple[str, str, list[dict[str, Any]]]:
    """回傳 (告警文字, events 文字, 實際餵進去的那批告警原文)。

    第三個回傳值是 run_1_3_baseline.py 沒有的:§7.7.1 的指紋要拿「這一題實際餵進去
    那批告警」去算,光有文字算不出來。切法本身一模一樣。
    """
    root = DATASET_ROOT / scenario

    alerts_text = "(這個場景沒有告警快照)"
    fed_alerts: list[dict[str, Any]] = []
    snaps = sorted((root / "alerts").glob("alerts_at_*.json")) if (root / "alerts").is_dir() else []
    if snaps:
        items = json.loads(snaps[-1].read_text(encoding="utf-8"))
        fed_alerts = [x for x in items if x.get("state") == "firing"][:N_ALERTS]
        lines = []
        for a in fed_alerts:
            labels = a.get("labels", {})
            ann = a.get("annotations", {})
            lines.append(
                f"- {labels.get('alertname', '?')} "
                f"severity={labels.get('severity', '?')} "
                f"{ann.get('description') or ann.get('summary') or ''}"
            )
        if lines:
            alerts_text = "\n".join(lines)

    events_text = "(這個場景沒有 k8s events)"
    ev_path = root / "k8s_events_raw.tsv"
    if ev_path.is_file():
        rows = []
        with ev_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t", quotechar='"')
            for row in reader:
                body = (row.get("Body") or "").replace("\n", " ")
                rows.append(f"- {row.get('TimestampTime', '')} {body[:EVENT_BODY_CHARS]}")
                if len(rows) >= N_EVENTS:
                    break
        if rows:
            events_text = "\n".join(rows)

    return alerts_text, events_text, fed_alerts


# §4 第 3 段|打模型
def build_request_body(prompt: str) -> dict:
    return {
        "model": settings.openrouter_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "seed": SEED,
        "provider": PROVIDER,
        "reasoning": REASONING,
    }


def call_model(body: dict) -> tuple[str, dict]:
    req = urllib.request.Request(
        settings.openrouter_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + settings.openrouter_api_key,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.load(resp)
    text = payload["choices"][0]["message"]["content"] or ""
    return text, payload.get("usage", {})


# §5 第 4 段|解析成 Diagnosis
# §6 評分(不進成功判定,只印出來。⑤:解析失敗算 0 分、留在分母裡)
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


# §7 跑一題(題內停:任何一段壞掉就回傳失敗,不往下寫 case)
def run_one(
    conn: psycopg.Connection, run_id: str, scenario: str, round_no: int
) -> dict[str, Any]:
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
        alerts_text, events_text, fed_alerts = build_slice(scenario)
    except Exception as e:  # noqa: BLE001 — 讀檔與解析的任何失敗都算這一段壞掉
        result.update(stopped_at="1-2 讀快照/切片", reason=repr(e))
        return result
    if not fed_alerts:
        # Scenario-11 沒有告警快照。沒有告警就算不出指紋,case 落不下去。
        result.update(stopped_at="2 切片", reason="這個場景沒有 firing 告警,算不出指紋")
        return result

    prompt = PROMPT_TEMPLATE.format(alerts=alerts_text, events=events_text)
    body = build_request_body(prompt)
    slice_params = {k: globals()[k] for k in SLICE_PARAMS_KEYS}
    trace_kw = dict(
        run_id=run_id, scenario=scenario, round_no=round_no,
        prompt=prompt, slice_params=slice_params, request_body=body,
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


# §8 入口
def main(argv: list[str]) -> int:
    run_all = "--all" in argv[1:]
    scenarios = pick_scenarios(run_all)
    rounds = N_ROUNDS if run_all else 1
    run_id = new_run_id()

    print(f"\n=== §8.1 demo run_id={run_id} 場景 {len(scenarios)} 個 × {rounds} 輪 ===")

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

    # §8.2 成功判定(不看分數)
    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    print(f"\n--- §8.2 管線 ---\n  五段全通 {len(ok)}/{len(results)} 題,耗時 {elapsed:.1f}s")
    if bad:
        print("  停下來的題:")
        for r in bad:
            print(f"    [{r['round']}/{r['scenario']}] {r['stopped_at']} — {r['reason']}")
    missing = [r for r in results if r["trace_path"] and not Path(r["trace_path"]).is_file()]
    print(f"  trace 檔:{len([r for r in results if r['trace_path']])} 份,不存在的 {len(missing)} 份")
    toks = [r["prompt_tokens"] for r in results if r.get("prompt_tokens")]
    if toks:
        lo, hi = PROMPT_TOKENS_EXPECTED
        outside = [t for t in toks if not lo <= t <= hi]
        print(
            f"  prompt tokens:{min(toks)}–{max(toks)},預期 {lo}–{hi},"
            f"落在範圍外 {len(outside)} 題"
        )
    print(f"  花費:${sum(r.get('cost', 0) for r in results):.4f}")

    # §8.3 分數(另外印,不進成功判定。⑤:解析失敗算 0 分、留在分母裡)
    print("\n--- §8.3 分數(解析失敗算 0 分,留在分母裡)---")
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
