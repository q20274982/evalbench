# 作業 2-3|logs 16 欄:各空幾成、根因名字有沒有只出現在這一欄、嚴重程度欄位在 25 題裡用上沒有。
#
# 問題:logs 前處理要決定 16 欄的去留(Calvin 2026-10-08 交給 TA 判斷)。TA 暫定留
# Timestamp / TraceId / SpanId / ServiceName / Body / LogAttributes / ResourceAttributes 的 k8s.* 與 service.*;
# 這支替每一欄算「名字只在這一欄」:這一欄比對到名稱規則,而暫定留下的其他欄都比對不到。
# 暫定留下的欄也算,用來看它是不是唯一的來源。
#
# 輸入(純讀檔,不打 API、不連資料庫):
#   - 25 題調整用場景(排除留出 10 題,同 count_fault_types.py)
#   - 根因與故障實體的名稱規則,取法同 check_dropped_resource_keys.py
#   - 每題 otel_logs_raw.tsv 的每一筆(走 CSV 解析器,Body 有換行;資料集導覽 §4.4)
# 算法:
#   - 「空」= 值是 ''、'[]'、'{}'、'None',或 TraceId / SpanId 全是 0
#   - ResourceAttributes 拆成兩欄:留下的 k8s.* / service.*(記為 ResourceAttributes[keep])與其餘(ResourceAttributes[drop])
#   - 名稱規則用 re.search 在整個字串裡找,比 SPEC §8.2 寬,只會多報
# 2026-10-08 跑出的 ResourceAttributes[drop] S33 2,653 筆是誤報:`process.command` 的 `thread-stream` 裡恰好有 `ad-`
# 另外印:SeverityNumber >= 17(ERROR 以上,一般知識)的筆數與出現在哪幾題
#
# 跑法:
#     uv run python experiments/W02/2-3-fault-types/check_dropped_log_columns.py

# ruff: noqa: E501
import ast
import collections
import csv
import re
import sys

import yaml
from check_dropped_resource_keys import compile_filter
from count_fault_types import BASE, HELD_OUT

csv.field_size_limit(sys.maxsize)
KEEP = ["Timestamp", "TraceId", "SpanId", "ServiceName", "Body", "LogAttributes", "ResourceAttributes[keep]"]
OTHERS = ["TimestampTime", "TraceFlags", "SeverityText", "SeverityNumber", "ResourceSchemaUrl",
          "ScopeSchemaUrl", "ScopeName", "ScopeVersion", "ScopeAttributes", "ResourceAttributes[drop]"]
EMPTY = re.compile(r"^([\[\]{}'\", ]*|None|0+)$")


def main() -> None:
    nums = sorted(int(d.name.split("-")[1]) for d in BASE.iterdir() if d.name.startswith("Scenario-"))
    tune = [n for n in nums if n not in HELD_OUT]
    assert len(nums) == 35 and len(tune) == 25, (len(nums), len(tune))

    total = 0
    empty: collections.Counter = collections.Counter()
    only_here: collections.Counter = collections.Counter()
    only_where: dict[str, set[int]] = collections.defaultdict(set)
    severe: collections.Counter = collections.Counter()
    for n in tune:
        raw = yaml.safe_load((BASE / f"Scenario-{n}" / "ground_truth.yaml").read_text())
        spec = raw.get("spec", raw)
        fault_gids = {(f.get("entity") or {}).get("group_id") for f in spec.get("fault") or []}
        groups = [g for g in spec.get("groups") or [] if g.get("root_cause") or g.get("id") in fault_gids]
        pats = {p for g in groups for p in g.get("filter") or []} | {re.escape(g["name"]) for g in groups if g.get("name")}
        rx = [compile_filter(p) for p in pats]

        with open(BASE / f"Scenario-{n}" / "otel_logs_raw.tsv") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                total += 1
                res = ast.literal_eval(row["ResourceAttributes"]) if row["ResourceAttributes"] else {}
                row["ResourceAttributes[keep]"] = str({k: v for k, v in res.items() if k.startswith(("k8s.", "service."))})
                row["ResourceAttributes[drop]"] = str({k: v for k, v in res.items() if not k.startswith(("k8s.", "service."))})
                if row["SeverityNumber"].isdigit() and int(row["SeverityNumber"]) >= 17:
                    severe[n] += 1
                hit = {c: any(r.search(row[c]) for r in rx) for c in KEEP + OTHERS}
                for c in KEEP + OTHERS:
                    if EMPTY.match(row[c]):
                        empty[c] += 1
                    elif hit[c] and not any(hit[k] for k in KEEP if k != c):
                        only_here[c] += 1
                        only_where[c].add(n)

    print(f"25 題合計 {total} 筆 log\n")
    print(f"{'欄位':<26}{'空值比例':>10}{'只在這一欄':>12}  出現在哪幾題")
    for c in KEEP + OTHERS:
        tag = "暫定留" if c in KEEP else "待決"
        print(f"{tag} {c:<22}{empty[c] / total:>10.2%}{only_here[c]:>12}  {sorted(only_where[c])}")
    print(f"\nSeverityNumber >= 17:共 {sum(severe.values())} 筆,{dict(severe)}")


if __name__ == "__main__":
    main()
