# 作業 2-3|traces 的 ResourceAttributes 只留 k8s.* 與 service.*,丟掉的 key 裡有沒有根因名字。
#
# 問題:traces 前處理打算丟掉 ResourceAttributes 的 host.* / os.* / telemetry.* / container.* / process.*。
# 丟之前要說得出「怎麼知道答案不在裡面」。這支逐題檢查:根因或故障實體的名字,
# 有沒有「只出現在被丟掉的 key、留下的 key 裡沒有」的 span。
#
# 輸入(純讀檔,不打 API、不連資料庫):
#   - 35 個場景資料夾,先排除 W2 留出不看的 10 題(同 count_fault_types.py)
#   - 剩下 25 題的 ground_truth.yaml:root_cause: true 的 group,加上 fault 的 entity.group_id 指到的 group;
#     取它們的 filter(名稱規則)與 name(沒有 filter 時用,逐字比)
#   - 同一題的 otel_traces_raw.tsv 每個 span 的 ResourceAttributes
# 不做的事:
#   - 只檢查名字(三道檢查的第三道),不檢查「錯成什麼樣」的證據在不在被丟的 key 裡
#   - filter 寫成 glob 的 `*xxx` 無法編譯時,把開頭的 `*` 換成 `.*`;S38 的 `*.*` 換完會比對到一切,那一列看不出東西
#
# 跑法:
#     uv run python experiments/W02/2-3-fault-types/check_dropped_resource_keys.py
# 輸出:終端每題一行;最後一欄「只在丟掉的 key」是要看的數字,0 = 丟掉不會丟到根因名字

# ruff: noqa: E501
import ast
import collections
import csv
import re
import sys

import yaml
from count_fault_types import BASE, HELD_OUT

csv.field_size_limit(sys.maxsize)
DROP = ("host.", "os.", "telemetry.", "container.", "process.")


def compile_filter(p: str) -> re.Pattern:
    try:
        return re.compile(p)
    except re.error:
        return re.compile(re.sub(r"^\*", ".*", p))


def main() -> None:
    nums = sorted(int(d.name.split("-")[1]) for d in BASE.iterdir() if d.name.startswith("Scenario-"))
    tune = [n for n in nums if n not in HELD_OUT]
    assert len(nums) == 35 and len(tune) == 25, (len(nums), len(tune))

    for n in tune:
        raw = yaml.safe_load((BASE / f"Scenario-{n}" / "ground_truth.yaml").read_text())
        spec = raw.get("spec", raw)  # 資料集導覽 §5.1:兩種外殼
        fault_gids = {(f.get("entity") or {}).get("group_id") for f in spec.get("fault") or []}
        groups = [g for g in spec.get("groups") or [] if g.get("root_cause") or g.get("id") in fault_gids]
        pats = sorted({p for g in groups for p in g.get("filter") or []} | {re.escape(g["name"]) for g in groups if g.get("name")})
        rx = [compile_filter(p) for p in pats]

        spans = keep_hit = drop_only = 0
        drop_keys: collections.Counter = collections.Counter()
        with open(BASE / f"Scenario-{n}" / "otel_traces_raw.tsv") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                spans += 1
                in_keep = in_drop = False
                for k, v in ast.literal_eval(row["ResourceAttributes"]).items():
                    if any(r.search(str(v)) for r in rx):
                        if k.startswith(DROP):
                            in_drop = True
                            drop_keys[k] += 1
                        else:
                            in_keep = True
                keep_hit += in_keep
                drop_only += in_drop and not in_keep
        print(f"S{n:<4} spans={spans:<7} 名稱規則={pats} | 留下的 key 命中={keep_hit} | 丟掉的 key 命中={dict(drop_keys)} | 只在丟掉的 key={drop_only}")


if __name__ == "__main__":
    main()
