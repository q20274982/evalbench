"""醜版管線的前三段:讀快照 → 切片 → 打模型。

規格正本是 W01 工作簿 §7.3.1(切法與模型設定)與 §7.7 ②(五段的前三段)。
本週的「切片」就是寫死的截頭,不是檢索——W2 把 build_slice 換掉的就是這裡。

搬進 src/ 的理由(/scaffold 慣例 8,2026-09-21):這是系統的一部分,不是某一份
作業的一次性腳本。experiments/W01/1-3-baseline/run_1_3_baseline.py 裡有一份一模一樣的複本,
那份**刻意不動**——它是作業 1-3 的紀錄,§7.3.2 的數字釘在它的 commit 上,
而慣例 8 (c) 說實驗跟正式碼重複是可以接受的。

兩份要一起改的情況只有一種:切法本身變了,而且要讓 replay 的 check 模式繼續
比得出「切法被改掉了」。W2 換檢索時會碰到這件事。

W2 作業 2-6(§6,W02 工作簿 §9.2「接進 agent 的設定」,Calvin 2026-10-12):
另加「用搜尋結果」的切法 build_retrieval_prompt,W1 的截頭 build_slice / build_prompt 原樣保留。
prompt = 組查詢用到的東西(告警去重後 + 那 5 筆 events)+ 混合搜尋前 k 塊。
"""

import csv
import json
import urllib.request
from pathlib import Path
from typing import Any

import retrieval
from config import settings

# §1 常數
REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = (
    REPO_ROOT / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
)

SEED = 42
TEMPERATURE = 0
MAX_TOKENS = 600
# 定案見 W00 工作簿 §8.2 與 §8.3 ①。
PROVIDER = {"order": ["Alibaba"], "allow_fallbacks": False}
REASONING = {"enabled": False}

SLICE_PARAMS_KEYS = ("N_ALERTS", "N_EVENTS", "EVENT_BODY_CHARS")
N_ALERTS = 12
N_EVENTS = 20
EVENT_BODY_CHARS = 400

PROMPT_TEMPLATE = """You are the on-call SRE. Below is an observability snapshot of a \
Kubernetes cluster taken during an incident.

Determine the root cause of this incident. Output only a single JSON object, with no other text:
{{"root_cause": "<the root cause in one sentence>", \
"root_cause_entity": "<name of the root-cause resource, name only>",\
 "confidence": <a decimal between 0 and 1>, "evidence": ["<evidence 1>"]}}

=== Alerts ===
{alerts}

=== K8s events ===
{events}
"""


# §2 切法參數與場景清單
def slice_params() -> dict[str, int]:
    """存進 trace 的切法參數(§7.6.1 ①)。"""
    return {k: globals()[k] for k in SLICE_PARAMS_KEYS}


def pick_scenarios() -> list[str]:
    """全部 35 個場景,照名稱排序固定住。"""
    return sorted(p.name for p in DATASET_ROOT.iterdir() if p.is_dir())


# §3 切片(W2 要換成檢索的就是這一段)
def build_slice(scenario: str) -> tuple[str, str, list[dict[str, Any]]]:
    """回傳 (告警文字, events 文字, 實際餵進去的那批告警原文)。

    第三個回傳值給 §7.7.1 的指紋用:指紋要拿「這一題實際餵進去那批告警」去算,
    光有文字算不出來。前兩個跟作業 1-2 / 1-3 的切法一模一樣。

    INV-13:只讀 alerts/ 與 k8s_events_raw.tsv,不碰 ground_truth.yaml。
    """
    root = DATASET_ROOT / scenario

    # 取時間最後的那份快照,只留 firing 的。檔名樣式寫死成 alerts_at_*.json:
    # alerts/ 底下還有別的 json,結構是 {"data": {"alerts": [...]}},混進來會炸。
    alerts_text = "(no alert snapshot for this scenario)"
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

    events_text = "(no k8s events for this scenario)"
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


# §4 組 prompt
def build_prompt(scenario: str) -> tuple[str, list[dict[str, Any]]]:
    """切片 + 套進 template。回傳 (prompt 全文, 餵進去的那批告警)。"""
    alerts_text, events_text, fed = build_slice(scenario)
    return PROMPT_TEMPLATE.format(alerts=alerts_text, events=events_text), fed


# §5 打模型
def build_request_body(prompt: str) -> dict:
    """送出去的整包 body。抽成函式是因為 §7.6.1 ② 要把它原樣存進 trace。"""
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


# §6 用搜尋結果的切法(W2 作業 2-6)
RETRIEVAL_K = 10

RETRIEVAL_TEMPLATE = """You are the on-call SRE. Below is an observability snapshot of a \
Kubernetes cluster taken during an incident.

Determine the root cause of this incident. Output only a single JSON object, with no other text:
{{"root_cause": "<the root cause in one sentence>", \
"root_cause_entity": "<name of the root-cause resource, name only>",\
 "confidence": <a decimal between 0 and 1>, "evidence": ["<evidence 1>"]}}

=== Alerts ===
{alerts}

=== K8s events ===
{events}

=== Retrieved context ===
{retrieved}
"""


def build_retrieval_prompt(
    conn,
    scenario: str,
    combine: str,
    split: bool,
    fuse: str = "flat",
    k: int = RETRIEVAL_K,
    pick: str = "warning5",
) -> tuple[str, list[str]]:
    """回傳 (prompt 全文, 搜尋結果的塊 id)。events 用原文、字根用 simple(W02 §9.2)。

    INV-13:只讀 retrieval.chunks,不碰 ground_truth.yaml。"""
    alerts, _ = retrieval.alert_texts(conn, scenario)
    events, _ = retrieval.event_texts(conn, scenario, "raw", pick)
    ids = retrieval.hybrid_search(
        conn, scenario, "raw", combine, k, "simple", split, fuse, pick=pick
    )
    texts = dict(
        conn.execute("SELECT id, text FROM retrieval.chunks WHERE id = ANY(%s)", (ids,)).fetchall()
    )
    blocks = [f"--- [{i}] {cid} ---\n{texts[cid]}" for i, cid in enumerate(ids, 1)]
    prompt = RETRIEVAL_TEMPLATE.format(
        alerts="\n\n".join(alerts) or "(no alerts for this scenario)",
        events="\n\n".join(events) or "(no k8s events for this scenario)",
        retrieved="\n\n".join(blocks) or "(no retrieval results)",
    )
    return prompt, ids
