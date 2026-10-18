# 作業 2-3|25 個調整用場景的故障類型分布(選來源前的參考)。
#
# 問題:Scenario-5 一題代表不了全部。選「收哪幾種來源」之前,先知道調整用的 25 題裡
# 注入的故障有哪幾類、各有幾題,才能一類一類問「它的痕跡留在哪幾種來源」。
#
# 輸入(純讀檔,不打 API、不連資料庫):
#   - 35 個場景資料夾,先排除 W2 留出不看的 10 題(工作簿 W02 §3,2026-10-04 抽定),
#     排除發生在讀檔之前,留出題的答案檔不會被打開
#   - 剩下 25 題的 ground_truth.yaml 的 `fault` 欄位(資料集導覽 §5.7:每項有 entity /
#     category / condition / fault_mechanism;一題 1–4 項)
# 不做的事:
#   - 不把 category 的同義寫法合併(哪些算同義是分類決定,留給 Calvin),原值照印
#   - 不把 fault 當答案(資料集導覽 §5.7:fault 不一定等於 root_cause group)
#
# 跑法:
#     uv run python experiments/W02/2-3-fault-types/count_fault_types.py
# 輸出:終端表格

# ruff: noqa: E501
import collections
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
# 工作簿 W02 §3:random.Random(20261004).sample(去掉 Scenario-5 的 34 題, 10)
HELD_OUT = {6, 9, 12, 15, 16, 20, 23, 29, 31, 35}


def main() -> None:
    nums = sorted(int(d.name.split("-")[1]) for d in BASE.iterdir() if d.name.startswith("Scenario-"))
    tune = [n for n in nums if n not in HELD_OUT]
    assert len(nums) == 35 and len(tune) == 25, (len(nums), len(tune))

    by_cat: dict[str, set[int]] = collections.defaultdict(set)
    by_row: dict[tuple, set[int]] = collections.defaultdict(set)
    for n in tune:
        raw = yaml.safe_load((BASE / f"Scenario-{n}" / "ground_truth.yaml").read_text())
        spec = raw.get("spec", raw)  # 資料集導覽 §5.1:兩種外殼
        for f in spec.get("fault") or []:
            ent = f.get("entity") or {}
            cat = str(f.get("category"))
            by_cat[cat].add(n)
            by_row[(cat, str(f.get("fault_mechanism")), str(ent.get("kind")), str(f.get("condition")))].add(n)

    print(f"調整用 {len(tune)} 題(已排除留出 {sorted(HELD_OUT)})\n")
    print("== category 原值:題數(一題可有多項 fault,所以加總可能 > 25)")
    for cat, s in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        print(f"{len(s):>3}  {cat:<28} 場景 {sorted(s)}")
    print("\n== category × fault_mechanism × entity.kind × condition")
    for (cat, mech, kind, cond), s in sorted(by_row.items(), key=lambda x: (x[0][0], -len(x[1]))):
        print(f"{len(s):>3}  {cat} | {mech} | {kind} | {cond[:70]}  場景 {sorted(s)}")


if __name__ == "__main__":
    main()
