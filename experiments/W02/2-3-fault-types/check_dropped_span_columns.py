# 作業 2-3|traces 還沒決定去留的 12 欄:各空幾成、有沒有根因名字只出現在這一欄。
#
# 問題:traces 前處理已決定留 10 欄(TraceId / SpanId / ParentSpanId / ServiceName / Timestamp / Duration /
# StatusCode / StatusMessage / SpanAttributes / ResourceAttributes 的 k8s.* 與 service.*)。
# 剩下 12 欄要丟之前,先用機器篩:全空、或根因名字從不只出現在這一欄的,名字層面有證據可丟;
# 其餘留給 Calvin 判斷。
#
# 輸入(純讀檔,不打 API、不連資料庫):
#   - 25 題調整用場景(排除留出 10 題,同 count_fault_types.py)
#   - 根因與故障實體的名稱規則,取法同 check_dropped_resource_keys.py
#   - 每題 otel_traces_raw.tsv 的每個 span
# 算法:
#   - 「空」= 值是 ''、'[]'、'{}',或陣列欄的元素全是空(例:Links.TraceState 的 [''])
#   - 「只在這一欄」= 這一欄的原字串比對到名稱規則,而留下的 10 欄(ResourceAttributes 只取 k8s.* / service.*)都比對不到
# 不做的事:
#   - 只檢查名字,不檢查「錯成什麼樣」的證據(例:stacktrace)會不會丟,那要 Calvin 判斷
#   - S38 的名稱規則 `*.*` 比對到一切,它對「只在這一欄」永遠是 0,看不出東西
#   - 名稱規則用 re.search 在整個字串裡找,比 SPEC §8.2「從開頭比對、kind 也要對得上」寬,所以只會多報不會漏報;
#     2026-10-08 跑出的 S33 Events.Attributes 2 筆是 UUID 裡恰好有 `ad-`(例 `80ad-46f6`)的誤報
#
# 跑法:
#     uv run python experiments/W02/2-3-fault-types/check_dropped_span_columns.py
# 輸出:每欄一行:25 題合計空值比例、「只在這一欄」的 span 數與出現在哪幾題

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
KEEP = ["TraceId", "SpanId", "ParentSpanId", "ServiceName", "Timestamp", "Duration", "StatusCode", "StatusMessage", "SpanAttributes"]
CANDIDATES = ["TraceState", "SpanName", "SpanKind", "ScopeName", "ScopeVersion",
              "Events.Timestamp", "Events.Name", "Events.Attributes",
              "Links.TraceId", "Links.SpanId", "Links.TraceState", "Links.Attributes"]
EMPTY = re.compile(r"^[\[\]{}'\", ]*$")  # '', '[]', '{}', "['']", '[{}]' 這類


def main() -> None:
    nums = sorted(int(d.name.split("-")[1]) for d in BASE.iterdir() if d.name.startswith("Scenario-"))
    tune = [n for n in nums if n not in HELD_OUT]
    assert len(nums) == 35 and len(tune) == 25, (len(nums), len(tune))

    total = 0
    empty: collections.Counter = collections.Counter()
    only_here: collections.Counter = collections.Counter()
    only_where: dict[str, set[int]] = collections.defaultdict(set)
    for n in tune:
        raw = yaml.safe_load((BASE / f"Scenario-{n}" / "ground_truth.yaml").read_text())
        spec = raw.get("spec", raw)
        fault_gids = {(f.get("entity") or {}).get("group_id") for f in spec.get("fault") or []}
        groups = [g for g in spec.get("groups") or [] if g.get("root_cause") or g.get("id") in fault_gids]
        pats = {p for g in groups for p in g.get("filter") or []} | {re.escape(g["name"]) for g in groups if g.get("name")}
        rx = [compile_filter(p) for p in pats]

        with open(BASE / f"Scenario-{n}" / "otel_traces_raw.tsv") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                total += 1
                res = {k: v for k, v in ast.literal_eval(row["ResourceAttributes"]).items() if k.startswith(("k8s.", "service."))}
                kept_text = "\n".join([row[c] for c in KEEP] + [str(v) for v in res.values()])
                in_kept = any(r.search(kept_text) for r in rx)
                for c in CANDIDATES:
                    v = row[c]
                    if EMPTY.match(v):
                        empty[c] += 1
                    elif not in_kept and any(r.search(v) for r in rx):
                        only_here[c] += 1
                        only_where[c].add(n)

    print(f"25 題合計 {total} 個 span\n")
    print(f"{'欄位':<20}{'空值比例':>10}{'只在這一欄':>12}  出現在哪幾題")
    for c in CANDIDATES:
        print(f"{c:<20}{empty[c] / total:>10.2%}{only_here[c]:>12}  {sorted(only_where[c])}")


if __name__ == "__main__":
    main()
