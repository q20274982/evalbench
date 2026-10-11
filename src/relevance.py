"""量尺(評測台):一塊算不算證據,以及檢索命中率@k 怎麼算。

規格出處(Calvin 定,TA 照著實作):
  - SPEC §8.2 相關怎麼標:第一步只取「發出這一塊的東西」(2026-10-11),第二步照官方推導設定比對
    根因所在的 alias 組(R01 §11、§12)
  - SPEC §8.1 分數怎麼算:hit@k、recall@k(macro / micro)、precision@k(分母一律 k)
  - R01 §12 排除版:去掉 38、29、105(名稱規則與資料對不上)與「沒有任何相關塊」的題
  - W03 §15.1 下 2026-09-29 的決定:根因是 Chaos Mesh 實驗物件的題獨立成一類分開報

獨立性:本檔不 import retrieval.py,判斷程式不跟被測系統共用(/scaffold 慣例 6)。
小數:SPEC §8.1 的小數處理還沒統一,這裡一律保留完整精度,報數字時再決定。
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = (
    REPO_ROOT / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
)
KS = [1, 5, 10, 20]  # R01 §12
EXCLUDED = {"Scenario-38", "Scenario-29", "Scenario-105"}  # R01 §12 排除版的第一個條件


# §1 第一步:每種來源取「發出這一塊的東西」(SPEC §8.2 第 3 項)
def _line(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}: (.*)$", text, re.M)
    return m.group(1).strip() if m else None


def _res_attr(text: str, key: str) -> str | None:
    m = re.search(r"'" + re.escape(key) + r"': '([^']*)'", text)
    return m.group(1) if m else None


def _otel(text: str, key: list) -> list[tuple[str, str]]:
    svc = _line(text, "ServiceName") or _res_attr(text, "service.name")
    pairs = (
        ("Service", svc),
        ("Pod", _res_attr(text, "k8s.pod.name")),
        ("Deployment", _res_attr(text, "k8s.deployment.name")),
    )
    return [(k, v) for k, v in pairs if v]


def _events(text: str, key: list) -> list[tuple[str, str]]:
    o = json.loads(text).get("object", {})
    r = o.get("regarding") or o.get("involvedObject") or {}
    return [(r["kind"], r["name"])] if r.get("kind") and r.get("name") else []


def _objects(text: str, key: list) -> list[tuple[str, str]]:
    # 切塊的合併依據 key = [kind, namespace, name, resourceVersion, (欄位路徑)]
    return [(key[0], key[2])] if len(key) >= 3 and key[0] and key[2] else []


def _metrics(text: str, key: list) -> list[tuple[str, str]]:
    pairs = (("Pod", _line(text, "pod_name")), ("Service", _line(text, "service_name")))
    return [(k, v) for k, v in pairs if v]


_AL_DESC = [
    (re.compile(r"in service (\S+) in namespace"), "Service", 1),
    (re.compile(r"in pod (\S+) on "), "Pod", 1),
    (re.compile(r"for container (\S+) in pod"), "Container", 1),
    (re.compile(r"^(Pod|Deployment|HPA|StatefulSet|DaemonSet|Job)\s+[^/\s]+/(\S+)"), None, 2),
    (re.compile(r"^Service (\S+) in namespace"), "Service", 1),
    (re.compile(r"^Namespace (\S+) is"), "Namespace", 1),
]


def _alerts(text: str, key: list) -> list[tuple[str, str]]:
    out = []
    for kind, k in (
        ("Service", "labels.service_name"),
        ("Pod", "labels.pod"),
        ("Container", "labels.container"),
    ):
        v = _line(text, k)
        if v:
            out.append((kind, v))
    desc = _line(text, "annotations.description") or ""
    for pat, kind, grp in _AL_DESC:
        m = pat.search(desc)
        if m:
            k = kind or {"HPA": "HorizontalPodAutoscaler"}.get(m.group(1), m.group(1))
            out.append((k, m.group(grp).rstrip(".,")))
    return out


EMITTER = {
    "logs": _otel,
    "traces": _otel,
    "events": _events,
    "objects": _objects,
    "metrics": _metrics,
    "alerts": _alerts,
}


# §2 第二步:答案檔的 alias 組與官方推導的比對(R01 §11)
@dataclass
class Group:
    id: str
    kind: str
    filters: list[str]
    name: str | None
    root: bool


def alias_set(scenario: str) -> list[Group]:
    """根因 group,加上 aliases 裡「直接含根因 id」的那些清單的所有成員。"""
    raw = yaml.safe_load(open(DATASET_ROOT / scenario / "ground_truth.yaml"))
    c = raw.get("spec", raw)
    groups = [
        Group(
            g["id"],
            str(g.get("kind") or ""),
            [str(f) for f in (g.get("filter") or [])],
            g.get("name"),
            bool(g.get("root_cause")),
        )
        for g in c["groups"]
    ]
    root = next(g for g in groups if g.root)
    ids = {root.id}
    for lst in c.get("aliases") or []:
        if root.id in lst:
            ids |= set(lst)
    return [g for g in groups if g.id in ids]


def name_test(g: Group):
    """名稱規則原樣從開頭比(re.match);沒有名稱規則:name 欄位完全相同;規則無法編譯:只比 kind。"""
    if g.filters:
        try:
            pats = [re.compile(p) for p in g.filters]
        except re.error:
            return lambda n: True
        return lambda n: any(p.match(n) for p in pats)
    return lambda n: n == (g.name or g.id)


def is_chaos(scenario: str) -> bool:
    root = next(g for g in alias_set(scenario) if g.root)
    return root.kind.endswith("Chaos") or root.kind == "Schedule"


def relevant_ids(scenario: str, chunks: list[tuple[str, str, str, list]]) -> set[str]:
    """chunks = [(id, source, text, key)]。回傳算相關的塊 id。"""
    tests = [(g.kind, name_test(g)) for g in alias_set(scenario)]
    out = set()
    for cid, source, text, key in chunks:
        ents = EMITTER[source](text, key)
        if any(ek == gk and t(en) for ek, en in ents for gk, t in tests):
            out.add(cid)
    return out


# §3 分數(SPEC §8.1)
def per_scenario(ranked: list[str], relevant: set[str]) -> dict:
    out = {"相關塊數": len(relevant)}
    for k in KS:
        top = ranked[:k]
        n = sum(c in relevant for c in top)
        out[f"hit@{k}"] = 1.0 if n else 0.0
        out[f"precision@{k}"] = n / k
        out[f"recall@{k}"] = n / len(relevant) if relevant else None
        out[f"中幾塊@{k}"] = n
    return out


def aggregate(rows: dict[str, dict]) -> dict:
    """rows = {場景: per_scenario 的結果}。hit、precision 每題平均;recall 報 macro 與 micro,
    沒有相關塊的題不計,另報不計的題數。"""
    if not rows:
        return {}
    out: dict = {"題數": len(rows)}
    with_rel = {s: r for s, r in rows.items() if r["相關塊數"]}
    out["recall 不計的題數"] = len(rows) - len(with_rel)
    for k in KS:
        out[f"hit@{k}"] = sum(r[f"hit@{k}"] for r in rows.values()) / len(rows)
        out[f"precision@{k}"] = sum(r[f"precision@{k}"] for r in rows.values()) / len(rows)
        if with_rel:
            out[f"recall@{k} macro"] = sum(r[f"recall@{k}"] for r in with_rel.values()) / len(
                with_rel
            )
            out[f"recall@{k} micro"] = sum(r[f"中幾塊@{k}"] for r in with_rel.values()) / sum(
                r["相關塊數"] for r in with_rel.values()
            )
    return out


def report_groups(rows: dict[str, dict]) -> dict[str, dict]:
    """全算版、排除版,以及排除版裡的非 chaos / chaos 兩類。"""
    kept = {s: r for s, r in rows.items() if s not in EXCLUDED and r["相關塊數"]}
    return {
        "全算版": aggregate(rows),
        "排除版": aggregate(kept),
        "排除版|非 chaos": aggregate({s: r for s, r in kept.items() if not is_chaos(s)}),
        "排除版|chaos": aggregate({s: r for s, r in kept.items() if is_chaos(s)}),
    }
