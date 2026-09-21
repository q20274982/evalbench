"""W01 §7.6.2|同一題重放 30 次,量 root_cause_entity 的寫法分布。

規格正本是 W01 工作簿 `§7.6.2`(Calvin 2026-09-21 決定:量哪一層選 (a)
同一題重放 N 次,N = 30)。要回答的是:可控變數全部沒動時,抖動有多大——
沒有這個數字,「分數落在預期區間內就可以接受」那句話沒辦法檢查。

跟 replay_trace.py 的分工:那一支是單次重放、判定一致不一致(`§7.6.2` ①b);
這一支重複呼叫同一包 body,只統計分布,不做一致性判定。

可控變數在這 30 次裡全部固定:同一個 trace 檔的 request body(model、
temperature=0、seed=42、provider 鎖 Alibaba、不 fallback)、同一個 commit、
同一份 src/scoring.py。所以量到的抖動只剩模型那一側。

跑法:
    docker compose run --rm app uv run python experiments/W01/run_7_6_replay_spread.py \
        traces/<run_id>/Scenario-1_1.json [次數]

成本:一次約 $0.0003,30 次約 $0.009(依據 2026-09-21 單題實測)。
"""

import json
import sys
import urllib.error
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from pipeline import call_model  # noqa: E402

from parsing import inspect  # noqa: E402
from tracing import read_trace  # noqa: E402

OUT_DIR = Path(__file__).parent / "out"
DEFAULT_N = 30


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_N
    trace = read_trace(path)
    body = trace["request"]
    print(f"重放 {trace['trace_id']} × {n}|model={body.get('model')} "
          f"temperature={body.get('temperature')} seed={body.get('seed')}")

    rows: list[dict] = []
    cost = 0.0
    for i in range(1, n + 1):
        try:
            raw, usage = call_model(body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:120]
            print(f"  [{i}] ✗ HTTP {e.code} {detail}")
            rows.append({"i": i, "entity": None, "error": f"HTTP {e.code}"})
            continue
        cost += float(usage.get("cost") or 0)
        diag = inspect(raw)["diagnosis"]
        entity = diag.root_cause_entity if diag else None
        rows.append({"i": i, "entity": entity, "raw": raw})
        print(f"  [{i}] {entity!r}")

    # §1 分布:可控變數沒動,剩下的差異全部來自模型那一側。
    counts = Counter(r["entity"] for r in rows)
    ok = [r for r in rows if "error" not in r]
    print(f"\n§1 {len(ok)}/{n} 次有回應。root_cause_entity 的寫法分布:")
    for entity, c in counts.most_common():
        print(f"  {c:>3} 次 ({c / max(len(ok), 1):.0%})  {entity!r}")

    then = trace["response"]["raw"]
    then_diag = inspect(then)["diagnosis"] if then else None
    print(f"\n§2 當初存的那一次:{then_diag.root_cause_entity if then_diag else None!r}")
    print(f"§3 相異寫法 {len(counts)} 種|本次花費 ${cost:.4f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "7_6_replay_spread.json"
    out.write_text(json.dumps(
        {"trace_id": trace["trace_id"], "n": n, "rows": rows,
         "counts": {str(k): v for k, v in counts.items()}, "cost": cost},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"原始輸出:{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
