"""W02 環境前置|INV-9 smoke test:模型會不會寫出觀測資料裡沒出現過的實體名稱。

只讀 W1 基準線的 trace(traces/20260921T115647Z-6ce5d6/,102 份),不打 API、不花錢。
Calvin 2026-09-26 同意這個做法(W02 工作簿 §3)。

逐份檢查:模型回答的 root_cause_entity,有沒有**逐字**(大小寫也要一樣的子字串)
出現在同一份 prompt 的觀測段落裡。
  - 觀測段落 = prompt 從第一個 "=== " 標題起到結尾;前面的指示文字不算,
    因為 INV-9 要的是「出現在原文」,不是出現在 TA 寫的 prompt 模板。
  - 回應解析不出 root_cause_entity 的,另外計數,不算進對得上 / 對不上。
  - 「不分大小寫才對得上」那一欄只是幫忙分類對不上的原因,不改變判定。

驗收:對不上 ≥ 1 份 = 災難打得出來,不用走 §3 的替代路徑。

跑法:
    docker compose run --rm app uv run python experiments/W02/inv9-smoke/run_inv9_smoke.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TRACE_DIR = REPO_ROOT / "traces/20260921T115647Z-6ce5d6"
OBS_MARKER = "=== "


def observation_text(prompt: str) -> str:
    i = prompt.find(OBS_MARKER)
    if i < 0:
        raise ValueError("prompt 裡找不到觀測段落的標題")
    return prompt[i:]


def answered_entity(raw: str) -> str | None:
    try:
        entity = json.loads(raw).get("root_cause_entity")
    except (json.JSONDecodeError, AttributeError):
        return None
    return entity if isinstance(entity, str) and entity.strip() else None


def main() -> int:
    files = sorted(TRACE_DIR.glob("*.json"))
    matched, mismatched, unparsed = [], [], []

    for f in files:
        d = json.loads(f.read_text())
        entity = answered_entity(d["response"]["raw"] or "")
        if entity is None:
            unparsed.append(f.stem)
            continue
        obs = observation_text(d["input"]["prompt"])
        if entity in obs:
            matched.append(f.stem)
        else:
            ci = entity.lower() in obs.lower()
            mismatched.append((f.stem, entity, ci))

    print(f"trace 份數:{len(files)}")
    print(f"逐字對得上:{len(matched)}")
    print(f"對不上:{len(mismatched)}")
    print(f"解析不出實體:{len(unparsed)} {unparsed}")
    print()
    print("對不上的實例(trace|回答的實體|不分大小寫是否對得上):")
    for stem, entity, ci in mismatched:
        print(f"  {stem}|{entity}|{'是' if ci else '否'}")

    return 0 if mismatched else 1


if __name__ == "__main__":
    sys.exit(main())
