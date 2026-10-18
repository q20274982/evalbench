"""W01 §7.5 附帶實驗|第二層題目會不會太簡單。

問題正本是 W01 工作簿 §7.5 的引言塊(來源 [[W00 拆掉 AI 系統的魔法]] §8.7,
2026-09-09 Calvin 決定)。那一格要的是**一個是非答案加一句觀察**,不是一個分數。

跟作業 1-3 的關係:同一個模型、同一組請求參數、同樣粗暴截頭、同樣要求 JSON 輸出。
**只有輸入段不同**——第二層沒有告警也沒有 K8s events(demo 跑在 docker compose),
手上只有 trace。SPEC 第 12 章已定「第二層不做欄位格式對齊,不寫 Jaeger JSON 轉第一層
TSV 的轉換器」,所以這裡不轉格式,只把 span 攤成文字行塞進 prompt。

  §7.5 引言塊:不造第二層的正式題目(那是 W3)、不定 schema、不改進系統。
  所以這支腳本不套 src/scoring.py 的評分層——第二層沒有 ground_truth.yaml,
  標準答案是「注入的旗標是 adFailure,所以根因是 ad 服務」這個場景常數。

兩個版本一起跑,因為快照裡有一條 trace 的 flagd span 直接寫著旗標名 `adFailure`:
  with-flagd    — 不動,旗標名可能出現在 prompt 裡(等於把答案餵給它)
  without-flagd — 把 flagd 這個服務的 span 全部拿掉

跑法:
    docker compose run --rm app uv run python experiments/W01/7-5-layer2-probe/run_7_5_layer2_probe.py dry
    docker compose run --rm app uv run python experiments/W01/7-5-layer2-probe/run_7_5_layer2_probe.py live
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from config import settings  # noqa: E402
from parsing import inspect  # noqa: E402

# §1 常數
REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = REPO_ROOT / "experiments/W00/otel_demo_smoke/ad_service_traces.json"
OUT_DIR = Path(__file__).parent / "out"

# 跟作業 1-3 同一組(W01 §7.3 / W00 §8.2、§8.3 ①)。
N_ROUNDS = 3
SEED = 42
TEMPERATURE = 0
MAX_TOKENS = 600
PROVIDER = {"order": ["Alibaba"], "allow_fallbacks": False}
REASONING = {"enabled": False}

# 不截頭:§7.5 引言塊寫的是「不檢索、把資料整包塞進 prompt」,所以 492 個 span 全進。
# 跟作業 1-3 的差別要記在工作簿:第一層截頭截的是**告警**,那是已經被 Prometheus 篩過
# 一輪的異常訊號;第二層的 trace 是原始流量,截頭會把唯一那幾條出錯的 trace 截掉,
# 截出來的題目量到的是切法,不是題目難不難。

# 場景常數 = 標準答案的來源(/scaffold 慣例 6 的第二種合法基準)。
# W0 撈這份快照時開的旗標是 adFailure,受害的服務是 ad。
INJECTED_FLAG = "adFailure"
EXPECTED_ENTITY = "ad"

# 跟作業 1-3 的 PROMPT_TEMPLATE 只差輸入段的標題與內容。
PROMPT_TEMPLATE = """你是值班的 SRE。下面是一個微服務系統在事故當下的觀測快照。

請判斷這次事故的根因,只輸出一個 JSON 物件,不要有其他文字:
{{"root_cause": "<一句話講根因>", "root_cause_entity": "<根因資源的名稱,只填名稱>",\
 "confidence": <0 到 1 的小數>, "evidence": ["<證據一>"]}}

=== 分散式追蹤(span)===
{spans}
"""


# §2 造輸入切片
# 把 Jaeger JSON 的 span 整包攤成文字行,照時間排序,不截頭。
#
# 一行的內容:時間、服務名、操作名、耗時、以及有值的話帶上狀態與錯誤旗標。
# 不做欄位對齊,不轉 TSV(SPEC 第 12 章已定)。
def build_slice(drop_flagd: bool) -> str:
    traces = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["data"]
    rows = []
    for tr in traces:
        pmap = {k: v.get("serviceName", "?") for k, v in tr.get("processes", {}).items()}
        for sp in tr.get("spans", []):
            svc = pmap.get(sp.get("processID"), "?")
            if drop_flagd and svc == "flagd":
                continue
            tags = {t["key"]: t["value"] for t in sp.get("tags", [])}
            extra = []
            if tags.get("error"):
                extra.append("error=true")
            for k in ("http.status_code", "rpc.grpc.status_code", "otel.status_code"):
                if k in tags:
                    extra.append(f"{k}={tags[k]}")
            rows.append(
                (
                    sp.get("startTime", 0),
                    f"- svc={svc} op={sp.get('operationName', '?')} "
                    f"duration_us={sp.get('duration', 0)} {' '.join(extra)}".rstrip(),
                )
            )
    rows.sort(key=lambda x: x[0])
    return "\n".join(line for _, line in rows)


# §3 打模型(跟作業 1-3 §4 同一支)
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
    return payload["choices"][0]["message"]["content"] or "", payload.get("usage", {})


# §4 判是非(不套評分層,§7.5 只要一個是非答案)
# 答對的定義:答的名字就是被注入旗標打的那個服務。
#
# 比對基準是 EXPECTED_ENTITY 這個場景常數(W0 開的旗標是 adFailure),
# 不是模型自己的輸出,也不是系統算出來的任何東西。
# 比法放寬到「名字裡含 ad 這個詞」,因為 demo 的服務名在不同地方寫成
# ad / adservice / ad-service(資料集導覽 §4.8 記過同型的事)。
def verdict(entity: str | None) -> bool:
    if not entity:
        return False
    norm = entity.lower().replace("_", "-")
    return norm == EXPECTED_ENTITY or norm.startswith("ad-") or norm in {"adservice", "ad-service"}


# §5 跑一個版本
def run_variant(name: str, drop_flagd: bool) -> list[dict]:
    spans = build_slice(drop_flagd)
    prompt = PROMPT_TEMPLATE.format(spans=spans)
    leaked = INJECTED_FLAG in prompt
    print(f"\n=== 版本 {name}|span 行數 {len(spans.splitlines())}"
          f"|prompt 裡出現 {INJECTED_FLAG}:{leaked}")
    out = []
    for rnd in range(1, N_ROUNDS + 1):
        try:
            raw, usage = call_model(prompt)
        except urllib.error.HTTPError as e:
            print(f"  [{rnd}] ✗ HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
            continue
        obs = inspect(raw)
        diag = obs["diagnosis"]
        entity = diag.root_cause_entity if diag is not None else None
        ok = verdict(entity)
        out.append({
            "variant": name, "round": rnd, "leaked_flag_in_prompt": leaked,
            "schema_ok": obs["schema_ok"], "entity": entity, "correct": ok,
            "cost": float(usage.get("cost") or 0), "raw": raw,
        })
        print(f"  [{rnd}] {'答對' if ok else '答錯'}  entity={entity!r}")
    return out


# §6 live
def run_live() -> int:
    print(f"model={settings.openrouter_model} provider={PROVIDER}"
          f"|標準答案={EXPECTED_ENTITY}(注入旗標 {INJECTED_FLAG})")
    records = run_variant("with-flagd", drop_flagd=False)
    records += run_variant("without-flagd", drop_flagd=True)

    print("\n--- §6.1 是非結果(三輪並列,不取平均)---")
    for name in ("with-flagd", "without-flagd"):
        rows = [r for r in records if r["variant"] == name]
        if rows:
            print(f"  {name}:{' / '.join('對' if r['correct'] else '錯' for r in rows)}"
                  f"|答的 entity:{[r['entity'] for r in rows]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "7_5_records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total = sum(r["cost"] for r in records)
    print(f"\n原始輸出:{OUT_DIR / '7_5_records.json'}|本次花費 ${total:.4f}")
    return 0


# §7 dry(不打 API,只看 prompt 長什麼樣)
def run_dry() -> int:
    for name, drop in (("with-flagd", False), ("without-flagd", True)):
        spans = build_slice(drop)
        prompt = PROMPT_TEMPLATE.format(spans=spans)
        print(f"=== 版本 {name}|span 行數 {len(spans.splitlines())}"
              f"|字元數 {len(prompt)}|出現 {INJECTED_FLAG}:{INJECTED_FLAG in prompt}")
        print(prompt[:1500])
        print("  ...(截斷)\n")
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
