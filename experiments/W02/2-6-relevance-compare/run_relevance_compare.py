"""作業 2-6 前置:「一塊算不算證據」的兩種算法,各自在 25 題裡標出幾塊。

來源:TA 提議、Calvin 2026-10-11 同意「先兩種都量,看數字再決定」。工作簿沒有指到這一步。
要決定的事:SPEC §8.2「第一步:取出實體 (kind, name)」沒寫一塊要取哪個實體。

兩種算法(第二步「怎麼比」兩邊相同,照 R01 §11 的官方推導設定):
  甲 只算發出者:一塊講的實體 = 發出這一塊的東西(POC run_poc.py 的取法,改寫成新切塊的格式)
  乙 發出者 + 被提到的:甲,再加上「塊的文字裡出現根因 alias 組的名字」。
     文字裡的名字看不出 kind,所以被提到的這一半不比 kind;但 Namespace 型的 group 不算被提到
     (SPEC §8.2「namespace: 只算 namespace 物件本身」)。名稱規則無法編譯的 group 也不算被提到。

獨立性:本檔不 import src/(量尺不跟被測系統共用判斷程式)。
輸入:作業 2-3 輸出 run1.jsonl(調整用 25 題)、各場景 ground_truth.yaml。純讀檔,不打 API、不連 DB。
跑法:uv run python experiments/W02/2-6-relevance-compare/run_relevance_compare.py
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
CORPUS = REPO / "experiments/W02/2-3-chunking/out/run1.jsonl"
OUT = Path(__file__).resolve().parent / "out"
# R01 §12 排除版的第一個排除條件:答案檔名稱規則無法編譯或與資料對不上
EXCLUDED = {"Scenario-38", "Scenario-29"}


# §1 甲:每種來源取「發出這一塊的東西」
def _line(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}: (.*)$", text, re.M)
    return m.group(1).strip() if m else None


def _res_attr(text: str, key: str) -> str | None:
    m = re.search(r"'" + re.escape(key) + r"': '([^']*)'", text)
    return m.group(1) if m else None


def emitter_otel(c: dict) -> list[tuple[str, str]]:
    """logs / traces:ServiceName(Service)、k8s.pod.name(Pod)、k8s.deployment.name(Deployment)。"""
    t, out = c["text"], []
    svc = _line(t, "ServiceName") or _res_attr(t, "service.name")
    for kind, val in (
        ("Service", svc),
        ("Pod", _res_attr(t, "k8s.pod.name")),
        ("Deployment", _res_attr(t, "k8s.deployment.name")),
    ):
        if val:
            out.append((kind, val))
    return out


def emitter_events(c: dict) -> list[tuple[str, str]]:
    """Event 的主角是被指到的資源(regarding / involvedObject),不是 Event 自己的名字。"""
    o = json.loads(c["text"]).get("object", {})
    io = o.get("regarding") or o.get("involvedObject") or {}
    return [(io["kind"], io["name"])] if io.get("kind") and io.get("name") else []


def emitter_objects(c: dict) -> list[tuple[str, str]]:
    """切塊時的合併依據 key = [kind, namespace, name, resourceVersion, (欄位路徑)]。"""
    k = c["key"]
    return [(k[0], k[2])] if len(k) >= 3 and k[0] and k[2] else []


def emitter_metrics(c: dict) -> list[tuple[str, str]]:
    """pod 檔的 pod_name(Pod)、service 檔的 service_name(Service);不取 tags 裡的 pod / service。"""
    out = []
    for kind, key in (("Pod", "pod_name"), ("Service", "service_name")):
        v = _line(c["text"], key)
        if v:
            out.append((kind, v))
    return out


AL_DESC = [
    (re.compile(r"in service (\S+) in namespace"), "Service", 1),
    (re.compile(r"in pod (\S+) on "), "Pod", 1),
    (re.compile(r"for container (\S+) in pod"), "Container", 1),
    (re.compile(r"^(Pod|Deployment|HPA|StatefulSet|DaemonSet|Job)\s+[^/\s]+/(\S+)"), None, 2),
    (re.compile(r"^Service (\S+) in namespace"), "Service", 1),
    (re.compile(r"^Namespace (\S+) is"), "Namespace", 1),
]


def emitter_alerts(c: dict) -> list[tuple[str, str]]:
    """labels 的 service_name / pod / container 與 description 句型(照 POC);不取 labels.service。"""
    t, out = c["text"], []
    for kind, key in (
        ("Service", "labels.service_name"),
        ("Pod", "labels.pod"),
        ("Container", "labels.container"),
    ):
        v = _line(t, key)
        if v:
            out.append((kind, v))
    desc = _line(t, "annotations.description") or ""
    for pat, kind, grp in AL_DESC:
        m = pat.search(desc)
        if m:
            k = kind or {"HPA": "HorizontalPodAutoscaler"}.get(m.group(1), m.group(1))
            out.append((k, m.group(grp).rstrip(".,")))
    return out


EMITTER = {
    "logs": emitter_otel,
    "traces": emitter_otel,
    "events": emitter_events,
    "objects": emitter_objects,
    "metrics": emitter_metrics,
    "alerts": emitter_alerts,
}


# §2 答案檔、alias 組、官方推導的比對(照 POC run_poc.py §3、§4 複製)
@dataclass
class Group:
    id: str
    kind: str
    filters: list[str]
    name: str | None
    root: bool


def load_alias_set(scenario: str) -> list[Group]:
    raw = yaml.safe_load(open(BASE / scenario / "ground_truth.yaml"))
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
        if ids & set(lst):
            ids |= set(lst)
    return [g for g in groups if g.id in ids]


def entity_test(g: Group):
    """官方推導:filter 原樣 re.match;無 filter 用 name(不分大小寫完全相符);無法編譯只比 kind。"""
    if g.filters:
        try:
            pats = [re.compile(p) for p in g.filters]
        except re.error:
            return lambda n: True
        return lambda n: any(p.match(n[:500]) for p in pats)
    key = (g.name or g.id).lower()
    return lambda n: n[:500].lower() == key


def mention_test(g: Group):
    """乙的「被提到」:文字裡任何位置出現符合名稱規則的字。Namespace 型與無法編譯的 group 不算。"""
    if g.kind == "Namespace":
        return None
    if g.filters:
        try:
            pats = [re.compile(p.removeprefix(".*")) for p in g.filters]
        except re.error:
            return None
        return lambda t: any(p.search(t) for p in pats)
    key = re.escape(g.name or g.id)
    pat = re.compile(rf"(?<![\w-]){key}(?![\w])", re.I)
    return lambda t: bool(pat.search(t))


# §3 主流程
def main() -> None:
    by_sc: dict[str, list[dict]] = defaultdict(list)
    for line in CORPUS.open():
        c = json.loads(line)
        by_sc[c["scenario"]].append(c)

    rows, examples, no_emitter = [], {}, Counter()
    for sc in sorted(by_sc, key=lambda s: int(s.split("-")[1])):
        aset = load_alias_set(sc)
        e_tests = [(g, entity_test(g)) for g in aset]
        m_tests = [t for t in (mention_test(g) for g in aset) if t]
        a = b = 0
        a_src, b_src = Counter(), Counter()
        only_b = []
        for c in by_sc[sc]:
            ents = EMITTER[c["source"]](c)
            if not ents:
                no_emitter[c["source"]] += 1
            hit_a = any(k == g.kind and t(n) for k, n in ents for g, t in e_tests)
            hit_b = hit_a or any(t(c["text"]) for t in m_tests)
            a += hit_a
            b += hit_b
            a_src[c["source"]] += hit_a
            b_src[c["source"]] += hit_b
            if hit_b and not hit_a:
                only_b.append(c["id"])
        n = len(by_sc[sc])
        root = next(g for g in aset if g.root)
        rows.append(
            {
                "場景": sc,
                "根因 kind": root.kind,
                "排除版": "排除" if sc in EXCLUDED else "",
                "塊數": n,
                "甲": a,
                "甲 %": round(100 * a / n, 1),
                "乙": b,
                "乙 %": round(100 * b / n, 1),
                "乙多出來的(按來源)": {
                    s: b_src[s] - a_src[s] for s in b_src if b_src[s] - a_src[s]
                },
            }
        )
        examples[sc] = only_b[:3]

    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    print("抽不出發出者的塊(按來源):", dict(no_emitter))
    OUT.mkdir(exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(
            {"rows": rows, "乙多出來的例子": examples, "抽不出發出者": no_emitter},
            ensure_ascii=False,
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
