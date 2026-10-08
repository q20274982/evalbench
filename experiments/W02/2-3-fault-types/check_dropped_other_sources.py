"""作業 2-3|alerts / metrics / objects 打算丟的欄位:各空幾成、根因名字有沒有只出現在那裡。

問題:這三種來源的去留,Calvin 2026-10-08 交給 TA 判斷。每一筆紀錄先攤成「欄位路徑 → 字串」,
分成暫定留與打算丟兩組;這支替打算丟的每一組算「名字只在這裡」:這一組比對到名稱規則,
而暫定留下的所有欄位都比對不到。0 = 名字層面丟了不會少。

一筆紀錄是什麼:
  - alerts:一則告警(alerts_at_*.json 的陣列元素;alerts_in_alerting_state_*.json 取 data.alerts,§7 7-2;
    S11 的告警檔在場景根目錄,§7 7-1)
  - metrics:pod_* / service_* TSV 的一列;tags 拆成 tags.<key>。同一檔裡除了 timestamp / value 以外全相同的列只算一筆
    (數字欄比對不到名字,逐列檢查要跑超過 10 分鐘),所以 metrics 的「筆數」是數列數,不是列數
  - objects:k8s_objects_raw.tsv 的一列;外層 16 欄各自一組,Body 的 JSON 攤到第二層(例 metadata.managedFields)

輸入(純讀檔,不打 API、不連資料庫):25 題調整用場景(排除留出 10 題,同 count_fault_types.py);
名稱規則取法同 check_dropped_resource_keys.py,用 re.search 在整個字串裡找,比 SPEC §8.2 寬,只會多報。
2026-10-08 跑出的誤報:objects spec[CustomResourceDefinition] S33 56 筆是 schema 說明文字的 `read-only` 恰好含 `ad-`。
不做的事:只檢查名字,不檢查「錯成什麼樣」的證據;不決定要不要過濾整筆紀錄或去重。

跑法:
    uv run python experiments/W02/2-3-fault-types/check_dropped_other_sources.py [alerts] [metrics] [objects]
    (不給參數 = 三種都跑)
"""

# ruff: noqa: E501
import collections
import csv
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import yaml
from check_dropped_resource_keys import compile_filter
from count_fault_types import BASE, HELD_OUT

csv.field_size_limit(sys.maxsize)

DROP = {
    "alerts": {"labels.instance", "labels.endpoint", "labels.job", "labels.alertstate", "annotations.runbook_url", "state"},
    "metrics": {"tags.prometheus", "tags.prometheus_replica", "tags.endpoint", "tags.instance", "tags.job", "tags.metrics_path",
                "tags.exported_instance", "tags.exported_namespace", "tags.otel_scope_name", "tags.le"},
    "objects": {"TimestampTime", "TraceId", "SpanId", "TraceFlags", "SeverityText", "SeverityNumber", "ServiceName",
                "ResourceSchemaUrl", "ResourceAttributes", "ScopeSchemaUrl", "ScopeName", "ScopeVersion", "ScopeAttributes",
                "LogAttributes", "EventName", "metadata.generation",
                "metadata.annotations.last-applied", "spec[CustomResourceDefinition]"},
}
TAG = re.compile(r"'([^']+)': '((?:[^'\\]|\\.)*)'")  # tags 是 Python dict 字串(§7 7-7),值都是字串
LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"


def alerts(scn: Path) -> Iterator[dict[str, str]]:
    for p in sorted(scn.glob("alerts/*.json")) + sorted(scn.glob("alerts_in_alerting_state_*.json")):
        d = json.loads(p.read_text())
        for a in d if isinstance(d, list) else d["data"]["alerts"]:
            rec = {f"labels.{k}": str(v) for k, v in a.get("labels", {}).items()}
            rec |= {f"annotations.{k}": str(v) for k, v in a.get("annotations", {}).items()}
            rec |= {k: str(a.get(k, "")) for k in ("state", "activeAt", "value")}
            yield rec


def metrics(scn: Path) -> Iterator[dict[str, str]]:
    for p in sorted(scn.glob("metrics/*.tsv")):
        with open(p) as f:
            seen: set[tuple] = set()
            for row in csv.DictReader(f, delimiter="\t"):
                # timestamp / value 是數字,不會比對到名字;其餘欄位相同的列只檢查一次(同一條數列有上千個時間點)
                key = tuple(v for k, v in row.items() if k not in ("timestamp", "value"))
                if key in seen:
                    continue
                seen.add(key)
                tags = row.pop("tags") or ""
                row |= {f"tags.{k}": v for k, v in TAG.findall(tags)}
                yield row


def objects(scn: Path) -> Iterator[dict[str, str]]:
    with open(scn / "k8s_objects_raw.tsv") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            o = json.loads(row.pop("Body"))
            o = o.get("object", o)
            for k, v in o.items():
                if k == "metadata":
                    meta = dict(v)
                    ann = dict(meta.pop("annotations", {}) or {})
                    if LAST_APPLIED in ann:
                        row["metadata.annotations.last-applied"] = ann.pop(LAST_APPLIED)
                    row["metadata.annotations"] = json.dumps(ann)
                    row |= {f"metadata.{mk}": json.dumps(mv) for mk, mv in meta.items()}
                elif k == "spec" and o.get("kind") == "CustomResourceDefinition":
                    row["spec[CustomResourceDefinition]"] = json.dumps(v)
                else:
                    row[k] = json.dumps(v)
            yield row


EMPTY = re.compile(r"^([\[\]{}'\", ]*|None|null|0+)$")


def main() -> None:
    nums = sorted(int(d.name.split("-")[1]) for d in BASE.iterdir() if d.name.startswith("Scenario-"))
    tune = [n for n in nums if n not in HELD_OUT]
    assert len(nums) == 35 and len(tune) == 25, (len(nums), len(tune))

    want = set(sys.argv[1:]) or {"alerts", "metrics", "objects"}
    for src, reader in (("alerts", alerts), ("metrics", metrics), ("objects", objects)):
        if src not in want:
            continue
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
            # 開頭的 .* 對 re.search 是多餘的,而且在 objects 好幾 MB 的字串上會慢到跑不完;拿掉不影響「找不找得到」
            rx = [compile_filter(re.sub(r"^(\.\*|\*\.\*|\*)+", "", p) or ".") for p in pats]
            for rec in reader(BASE / f"Scenario-{n}"):
                total += 1
                kept = "\n".join(v for k, v in rec.items() if k not in DROP[src])
                in_kept = any(r.search(kept) for r in rx)
                for c in DROP[src]:
                    v = rec.get(c, "")
                    if EMPTY.match(v):
                        empty[c] += 1
                    elif not in_kept and any(r.search(v) for r in rx):
                        only_here[c] += 1
                        only_where[c].add(n)
        print(f"\n== {src}:25 題合計 {total} 筆", flush=True)
        print(f"{'打算丟的欄位':<36}{'空值比例':>10}{'只在這裡':>10}  出現在哪幾題")
        for c in sorted(DROP[src]):
            print(f"{c:<36}{empty[c] / total:>10.2%}{only_here[c]:>10}  {sorted(only_where[c])}", flush=True)


if __name__ == "__main__":
    main()
