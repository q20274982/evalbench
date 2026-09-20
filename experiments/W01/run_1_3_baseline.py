"""W01 作業 1-3|最笨版本的基準線:不檢索、把截斷的快照塞進 prompt,跑分。

規格正本是 W01 工作簿 §7.3.1 ①–⑧(Calvin 2026-09-20 定案)。這支腳本只負責
把規格跑起來:餵題、收模型輸出、交給 src/parsing.py 解析、交給 src/scoring.py
評分、把三輪的分數與全距印出來。所有判斷規則都在 scoring.py,這裡不做裁決。

  ⑥ 全部 35 個場景 × 3 輪,固定這一批,之後每週對同一批報數
  ⑤ 解析失敗算 0 分、留在分母裡——分母永遠是當輪跑掉的題數
  ⑥ 三輪的分數並列 + 全距,不報平均(鐵律 18)

兩個模式:
  dry   — 不打 API。拿手造的答案走評分層,看三階段各自命中什麼。不花錢。
  live  — 打 API 跑 35 題 × 3 輪。要花錢(估計 $0.14)。

跑法:
    docker compose run --rm app uv run python experiments/W01/run_1_3_baseline.py dry
    docker compose run --rm app uv run python experiments/W01/run_1_3_baseline.py live

INV-13:ground_truth.yaml 不進 prompt。§3 只讀 alerts/ 與 k8s_events_raw.tsv;
答案檔只在 §5 評分那一側被讀,方向是單向的。
"""

import csv
import json
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config import settings  # noqa: E402
from parsing import inspect  # noqa: E402
from scoring import load_ground_truth, score_one  # noqa: E402

# §1 常數
REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = (
    REPO_ROOT / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
)
OUT_DIR = Path(__file__).parent / "out"

N_ROUNDS = 3
SEED = 42
TEMPERATURE = 0
MAX_TOKENS = 600
# 定案見 W00 工作簿 §8.2 與 §8.3 ①,跟作業 1-2 同一組設定。
PROVIDER = {"order": ["Alibaba"], "allow_fallbacks": False}
REASONING = {"enabled": False}

# 餵進去的量跟作業 1-2 一樣,只有輸出格式多一個欄位。醜版沒有檢索,就是粗暴地截頭。
N_ALERTS = 12
N_EVENTS = 20
EVENT_BODY_CHARS = 400

# 比作業 1-2 的 template 多要一個 root_cause_entity(§7.3.1 ①:評分只看這一欄)。
PROMPT_TEMPLATE = """你是值班的 SRE。下面是一個 Kubernetes 叢集在事故當下的觀測快照。

請判斷這次事故的根因,只輸出一個 JSON 物件,不要有其他文字:
{{"root_cause": "<一句話講根因>", "root_cause_entity": "<根因資源的名稱,只填名稱>",\
 "confidence": <0 到 1 的小數>, "evidence": ["<證據一>"]}}

=== 告警 ===
{alerts}

=== K8s events ===
{events}
"""


# §2 挑場景(⑥:全部 35 個,照名稱排序固定住)
def pick_scenarios() -> list[str]:
    return sorted(p.name for p in DATASET_ROOT.iterdir() if p.is_dir())


# §3 造輸入切片(不碰 ground_truth.yaml)
def build_slice(scenario: str) -> tuple[str, str]:
    """回傳 (告警文字, events 文字)。跟作業 1-2 的 §3 同一套切法。"""
    root = DATASET_ROOT / scenario

    alerts_text = "(這個場景沒有 alerts 快照)"
    alert_dir = root / "alerts"
    if alert_dir.is_dir():
        snaps = sorted(alert_dir.glob("*.json"))
        if snaps:
            data = json.loads(snaps[-1].read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else data.get("data", [])
            lines = []
            for a in items:
                if (a.get("state") or a.get("status", {}).get("state")) != "firing":
                    continue
                labels = a.get("labels", {})
                ann = a.get("annotations", {})
                lines.append(
                    f"- {labels.get('alertname', '?')} "
                    f"severity={labels.get('severity', '?')} "
                    f"{ann.get('description') or ann.get('summary') or ''}"
                )
                if len(lines) >= N_ALERTS:
                    break
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

    return alerts_text, events_text


# §4 打模型
def call_model(prompt: str) -> tuple[str, dict]:
    body = json.dumps(
        {
            "model": settings.openrouter_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "seed": SEED,
            "provider": PROVIDER,
            "reasoning": REASONING,
        }
    ).encode()
    req = urllib.request.Request(
        settings.openrouter_base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + settings.openrouter_api_key,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.load(resp)
    text = payload["choices"][0]["message"]["content"] or ""
    return text, payload.get("usage", {})


# §5 評一題(解析 → 評分)
def judge(raw: str, scenario: str) -> dict:
    """把一次原始輸出走完解析與評分,回傳一筆紀錄。

    ⑤:schema 沒過就沒有 Diagnosis 物件,entity 傳 None 進評分層,拿 0 分,
    但這一題仍然留在分母裡——分母是跑掉的題數,不是解析成功的題數。
    """
    obs = inspect(raw)
    diag = obs["diagnosis"]
    entity = diag.root_cause_entity if diag is not None else None
    gt = load_ground_truth(DATASET_ROOT / scenario)
    res = score_one(entity, gt)
    return {
        "scenario": scenario,
        "verdict": obs["verdict"],
        "schema_ok": obs["schema_ok"],
        "entity": entity,
        "score": res.score,
        "stage": res.stage,
        "detail": res.detail,
        "gt_id": gt.root.id,
        "gt_filter": gt.root.filters,
        "raw": raw,
    }


# §6 報分(⑥:三個分數並列 + 全距,不報平均)
def report(records: list[dict]) -> None:
    print("\n--- §6.1 每輪分數(解析失敗算 0 分,留在分母裡)---")
    scores = []
    for rnd in range(1, N_ROUNDS + 1):
        rows = [r for r in records if r["round"] == rnd]
        if not rows:
            continue
        hit = sum(r["score"] for r in rows)
        rate = hit / len(rows)
        scores.append(rate)
        print(f"  第 {rnd} 輪:{hit}/{len(rows)} = {rate:.0%}")
    if scores:
        spread = max(scores) - min(scores)
        line = f"  三輪:{' / '.join(f'{s:.0%}' for s in scores)}|全距 {spread:.0%}"
        if len(scores) > 1:
            line += f"|標準差 {statistics.stdev(scores):.3f}"
        print(line)

    print("\n--- §6.2 命中階段分布(三輪合計)---")
    for stage in ["①根因 filter", "②同服務 filter", "③根因 id", "不命中", "沒回答"]:
        n = sum(1 for r in records if r["stage"] == stage)
        print(f"  {stage}:{n}")

    print("\n--- §6.3 逐題(第 1 輪)|貼進工作簿 §7.3.2 用 ---")
    print("| 場景 | 分數 | 命中階段 | 模型答的 entity | 標準答案 id |")
    print("|---|---|---|---|---|")
    for r in [r for r in records if r["round"] == 1]:
        print(f"| {r['scenario']} | {r['score']} | {r['stage']} "
              f"| {r['entity'] if r['entity'] is not None else '(解析失敗)'} | {r['gt_id']} |")


# §7 live 模式
def run_live() -> int:
    scenarios = pick_scenarios()
    print(f"場景 {len(scenarios)} 個 × {N_ROUNDS} 輪|"
          f"model={settings.openrouter_model} provider={PROVIDER}")
    records: list[dict] = []
    cost = 0.0
    for rnd in range(1, N_ROUNDS + 1):
        for sc in scenarios:
            alerts, events = build_slice(sc)
            prompt = PROMPT_TEMPLATE.format(alerts=alerts, events=events)
            try:
                raw, usage = call_model(prompt)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:200]
                print(f"  [{rnd}/{sc}] ✗ HTTP {e.code} {detail}")
                continue
            cost += float(usage.get("cost") or 0)
            rec = judge(raw, sc)
            rec["round"] = rnd
            records.append(rec)
            print(f"  [{rnd}/{sc}] {rec['score']} {rec['stage']}  entity={rec['entity']!r}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "1_3_records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report(records)
    print(f"\n原始輸出:{OUT_DIR / '1_3_records.json'}|本次花費 ${cost:.4f}")
    return 0


# §8 dry 模式(不打 API,看評分層在真實答案檔上怎麼判)
def run_dry() -> int:
    """拿手造的答案走評分層。

    每一筆的期望值來自 ground_truth.yaml 或規格,不是任何模型的輸出。
    """
    cases = [
        ("Scenario-1", "load-generator-pod-1", "標準答案本身,第 1 階段就該中"),
        ("Scenario-1", "load-generator-service-1",
         "同服務成員,得分——但命中的是第 1 階段:部分命中語意下根因的 filter 已經涵蓋它"),
        ("Scenario-20", "productcatalog-pod-1",
         "規格②說要收的同服務成員,前兩階段收不到(filter 帶連字號、id 不帶),靠 (c) 案的第 3 階段"),
        ("Scenario-1", "frontend-proxy-service-1", "下游受害者,規格②明說不算對"),
        ("Scenario-20", "productcatalog-deployment-1", "filter 比不到自己的 id,靠第 3 階段"),
        ("Scenario-38", "otel-demo-horizontal-pod-autoscalers", "filter 是無效正則,跳過後靠 id"),
        ("Scenario-102", "otel-demo-frontend", "已知的假陽性:filter 是 otel-demo"),
        ("Scenario-1", "load-generator-pod-1 (Pod in otel-demo)", "帶括號說明 → 沒回答"),
        ("Scenario-1", "", "空字串 → 沒回答"),
    ]
    print("手造的答案走評分層(§7.3.1 的三階段)。期望值來自答案檔與規格,不是模型輸出。\n")
    for scenario, entity, note in cases:
        gt = load_ground_truth(DATASET_ROOT / scenario)
        res = score_one(entity, gt)
        print(f"=== {scenario} | {entity!r}")
        print(f"  分數 {res.score}|階段 {res.stage}|{res.detail}")
        print(f"  為什麼看這一筆:{note}\n")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "live":
        return run_live()
    if mode == "dry":
        return run_dry()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
