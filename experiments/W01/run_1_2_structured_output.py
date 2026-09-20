"""W01 作業 1-2|prompt 寫得再清楚,下游解析得動幾題?

20 個場景各問一題,連跑 3 輪,記錄每一題在解析鏈上死在哪一層。

⚠️ 這支腳本**只觀察,不做策略**。解析失敗時它不丟例外、不填預設值、不重試,
只把每一層看到什麼記下來。少欄位 / 型別不對 / 多包圍欄各自該在哪一層爆掉,
是 Calvin 看完這批真實壞輸出之後才做的決定(工作簿 §7.2),
腳本先替他選了,那個決定就不是他的了。

兩個模式:
  live      — 打 API 跑 20 題 × 3 輪,產出成敗表。要花錢。
  malformed — 不打 API。三個手造的壞輸出走同一條解析鏈,看各層分別報什麼。
              「模型這次剛好都吐對」的時候,三個決定仍然有材料可做。

跑法:
    docker compose run --rm app uv run python experiments/W01/run_1_2_structured_output.py malformed
    docker compose run --rm app uv run python experiments/W01/run_1_2_structured_output.py live

INV-13:ground_truth.yaml 不進 prompt。§3 只讀 alerts/ 與 k8s_events_raw.tsv,
不碰場景目錄下的答案檔。
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

# §1 常數
REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = (
    REPO_ROOT / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
)
OUT_DIR = Path(__file__).parent / "out"

N_ROUNDS = 3
N_SCENARIOS = 20
SEED = 42
TEMPERATURE = 0
MAX_TOKENS = 600
# 定案見 W00 工作簿 §8.2:主模型鎖 Alibaba、不 fallback;reasoning 不關的話
# 模型會先寫一長串英文思考過程,JSON 被推到 max_tokens 之外(W00 §8.3 ①)。
PROVIDER = {"order": ["Alibaba"], "allow_fallbacks": False}
REASONING = {"enabled": False}

# 每題餵進去的量。醜版沒有檢索,就是粗暴地截頭(§7.1 已證整包塞不進 context)。
N_ALERTS = 12
N_EVENTS = 20
EVENT_BODY_CHARS = 400

PROMPT_TEMPLATE = """你是值班的 SRE。下面是一個 Kubernetes 叢集在事故當下的觀測快照。

請判斷這次事故的根因,只輸出一個 JSON 物件,不要有其他文字:
{{"root_cause": "<一句話講根因>", "confidence": <0 到 1 的小數>, "evidence": ["<證據一>"]}}

=== 告警 ===
{alerts}

=== K8s events ===
{events}
"""


# §2 挑場景
def pick_scenarios():
    """取前 N 個場景,照名稱排序固定住,換一台機器跑到的是同一批。"""
    names = sorted(p.name for p in DATASET_ROOT.iterdir() if p.is_dir())
    return names[:N_SCENARIOS]


# §3 造輸入切片(不碰 ground_truth.yaml)
def build_slice(scenario: str) -> tuple[str, str]:
    """回傳 (告警文字, events 文字)。刻意不整理格式——真實輸入本來就髒。"""
    root = DATASET_ROOT / scenario

    # §3.1 告警:取時間最後的那份快照,只留 firing 的
    alerts_text = "(這個場景沒有告警快照)"
    snaps = sorted((root / "alerts").glob("alerts_at_*.json")) if (root / "alerts").is_dir() else []
    if snaps:
        with snaps[-1].open(encoding="utf-8") as f:
            items = json.load(f)
        firing = [a for a in items if a.get("state") == "firing"][:N_ALERTS]
        lines = []
        for a in firing:
            labels = a.get("labels", {})
            ann = a.get("annotations", {})
            lines.append(
                f"- {labels.get('alertname', '?')} "
                f"[severity={labels.get('severity', '?')} ns={labels.get('namespace', '-')}] "
                f"{ann.get('summary', '')} {ann.get('description', '')}"
            )
        if lines:
            alerts_text = "\n".join(lines)

    # §3.2 k8s events:前 N 列的 Body 欄,長的截斷
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


# §5 打模型
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
    usage = payload.get("usage", {})
    return text, usage


# §6 live 模式
def run_live() -> int:
    scenarios = pick_scenarios()
    print(
        f"場景 {len(scenarios)} 個 × {N_ROUNDS} 輪|"
        f"model={settings.openrouter_model} provider={PROVIDER}"
    )
    records = []
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
            obs = inspect(raw)
            obs.update({"round": rnd, "scenario": sc, "raw": raw})
            records.append(obs)
            print(
                f"  [{rnd}/{sc}] {obs['verdict']}  "
                f"fence={obs['has_fence']} prefix={obs['prefix_chars']}"
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "1_2_records.json").write_text(
        json.dumps(
            [json_safe(r) for r in records], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    # §6.1 每輪各自的成功率,三個數字擺在一起報(鐵律 18)
    print("\n--- §6.1 每輪成功率(嚴格解析 + schema 都過才算成功)---")
    rates = []
    for rnd in range(1, N_ROUNDS + 1):
        rows = [r for r in records if r["round"] == rnd]
        ok = sum(1 for r in rows if r["verdict"] == "成功")
        rate = ok / len(rows) if rows else 0.0
        rates.append(rate)
        print(f"  第 {rnd} 輪:{ok}/{len(rows)} = {rate:.0%}")
    if rates:
        print(f"  三輪區間:{min(rates):.0%} – {max(rates):.0%}"
              f"(全距 {max(rates) - min(rates):.0%}"
              f"{'、標準差 ' + format(statistics.stdev(rates), '.3f') if len(rates) > 1 else ''})")

    # §6.2 失敗原因分布
    print("\n--- §6.2 分類分布(三輪合計)---")
    for v in ["成功", "差一點點(外殼)", "差一點點(欄位)", "差一點點(括號)", "完全不像"]:
        n = sum(1 for r in records if r["verdict"] == v)
        print(f"  {v}:{n}")

    # §6.3 20 列表(第 1 輪),貼進工作簿 §7.2 用
    print("\n--- §6.3 成敗表(第 1 輪)|貼進工作簿 §7.2 ---")
    print("| 場景 | 判定 | 圍欄 | JSON 前多餘字元 | 括號差 | 缺欄位 | 型別不對 |")
    print("|---|---|---|---|---|---|---|")
    for r in [r for r in records if r["round"] == 1]:
        print(f"| {r['scenario']} | {r['verdict']} | {'有' if r['has_fence'] else '無'} "
              f"| {r['prefix_chars']} | {r['brace_delta']:+d} "
              f"| {','.join(r['missing_fields']) or '-'} "
              f"| {','.join(r['type_errors']) or '-'} |")

    print(f"\n原始輸出:{OUT_DIR / '1_2_records.json'}|本次花費 ${cost:.4f}")
    return 0


def json_safe(rec: dict) -> dict:
    """落檔用:解析層回傳的 Diagnosis 物件換成 dict,其餘原樣。

    物件本身是給評測層用的(規格 §7.2.2 ②),json 存不進去,所以只在寫檔這一步
    攤平。攤平的是同一筆資料,沒有補值也沒有丟欄位。
    """
    out = dict(rec)
    diag = out.get("diagnosis")
    out["diagnosis"] = diag.model_dump() if diag is not None else None
    return out


# §7 malformed 模式(不打 API)
CRAFTED = [
    (
        "多包 markdown 圍欄",
        '好的,這是診斷結果:\n```json\n{"root_cause": "payment 服務 OOM", '
        '"confidence": 0.8, "evidence": ["pod 反覆重啟"]}\n```\n希望有幫助。',
    ),
    (
        "少一個欄位",
        '{"root_cause": "payment 服務 OOM", "evidence": ["pod 反覆重啟"]}',
    ),
    (
        "少一個收尾大括號(2026-09-14 live 實測的主要壞法)",
        '{"root_cause": "payment 服務 OOM", "confidence": 0.8, "evidence": ["pod 反覆重啟"]',
    ),
    (
        "型別不對",
        '{"root_cause": "payment 服務 OOM", "confidence": "high", "evidence": "pod 反覆重啟"}',
    ),
    (
        "完全正常(對照組,驗 §4.4 有沒有把 Diagnosis 物件附上來)",
        '{"root_cause": "payment 服務 OOM", "confidence": 0.8, "evidence": ["pod 反覆重啟"]}',
    ),
]


def run_malformed() -> int:
    print("手造的樣本走同一條解析鏈(§4),每層只報看到什麼,不做處置。\n")
    for name, raw in CRAFTED:
        obs = inspect(raw)
        print(f"=== {name} ===")
        print(f"  §4.1 raw     圍欄={obs['has_fence']} JSON 前多餘字元={obs['prefix_chars']}"
              f" 括號差={obs['brace_delta']:+d}")
        print(f"  §4.2 strict  {'過' if obs['strict_ok'] else '不過:' + obs['strict_err']}")
        print(f"  §4.3 salvage {'過' if obs['salvage_ok'] else '不過:' + obs['salvage_err']}")
        print(f"  §4.4 schema  {'過' if obs['schema_ok'] else '不過'}"
              f" 缺欄位={obs['missing_fields'] or '-'} 型別不對={obs['type_errors'] or '-'}")
        diag = obs["diagnosis"]
        print(f"  §4.4 物件    {'有,評測層可直接取值' if diag else '無'}")
        print(f"  §4.5 判定    {obs['verdict']}\n")
    print("這幾種各自要在哪一層爆掉,是工作簿 §7.2 要填的決定。腳本不替它選。")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "live":
        return run_live()
    if mode == "malformed":
        return run_malformed()
    print("用法:python experiments/W01/run_1_2_structured_output.py [live|malformed]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
