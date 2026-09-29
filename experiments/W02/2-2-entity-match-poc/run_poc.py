"""作業 2-2 POC|先抽「這一塊描述的實體 (kind, name)」,再照 alias 組判相關,和整行比對並排。

要回答的四個問題(結論在 out/report.md):
  Q1 六種來源各自能不能穩定抽出 (kind, name)
  Q2 用抽出的實體比對,候選塊數和整行比對差多少
  Q3 特殊場景(filter 無法編譯、沒有 filter、根因是故障注入物件)有哪些處理方式
  Q4 答案檔的 kind 跟各來源抽出的東西怎麼對應(每一條都是待 Calvin 決定的假設)

輸入(純讀檔,不打 API、不跑 embedding、不連資料庫):
  - 每個場景照 2-1 的語料定義抽樣:六種來源各抽 2000 行(不足全收),一行一塊,種子 20260924。
    抽樣函式從 2-1 複製(不 import 其他實驗);標頭行與多行紀錄的續行都留在語料裡,不濾掉。
  - 各場景 ground_truth.yaml(答案檔只在 out/ 出現,本檔不寫任何場景的答案字面值)。

獨立性(量尺不能跟被測系統共用判斷程式):
  本檔不 import src/ 的任何東西。實體抽取與 filter 比對全部在本檔另寫。
  src/scoring.py 有類似功能(literal_prefix / same_service_members / _match_any / is_entity_like),
  src/parsing.py 有 inspect(),都沒用;路徑記在報告 §9。

「這一塊描述的實體」怎麼定(Q4 的核心假設,報告 §3 逐條列):
  只算「發出這一塊的主體」與它自己的層級:
    logs / traces   : 發出者的 service.name(Service)、k8s.pod.name(Pod)、k8s.deployment.name(Deployment)
    events          : involvedObject 或 regarding 的 (kind, name)
    k8s_objects     : 物件自己最外層的 kind 與 metadata.name
    metrics         : pod 檔的 pod_name 欄(Pod)、service 檔的 service_name 欄(Service)
    alerts          : 該行寫著的 service_name(Service)、pod(Pod)、container(Container)與 description 句型
  不算:呼叫對象(span 的 net.peer.name)、Event 自己的名字、ownerReferences、
       log 內文裡「提到」的別的實體、metrics 的 tags 裡的 pod / service(那是抓指標的 otel-collector 或 Prometheus)。
  namespace 是否算 Namespace 實體:主指標「不算」,另算一欄「算」(看 kind 是 Namespace 的場景差多少)。

比對規則(主指標與變體都輸出,不選定):
  主:kind 完全相同 且 filter 以 re.search 比實體 name(開頭的 `.*` 先去掉)
  變體 fullmatch:filter 以 re.fullmatch 比 name(不去掉 `.*`)
  變體 kind-blind:只比 name,不看 kind
  變體 root-only:只用根因 group,不展開 alias 組
  變體 ns-entity:主指標再加上 (Namespace, 該塊的 namespace) 這個實體

alias 組 = 根因 group,加上「aliases 內層清單裡含根因 id」的那些清單的所有成員(只看直接含根因的清單,不做傳遞閉包)。

跑法:
    uv run python experiments/W02/2-2-entity-match-poc/run_poc.py
輸出:out/results.json、out/report.md(內文散文段落讀 out/prose.json)
"""

# ruff: noqa: E501
import csv
import fnmatch
import json
import pickle
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

csv.field_size_limit(10**9)

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
OLD_OUT = REPO / "experiments/W02/2-2-evidence-trial/out"
OUT = Path(__file__).parent / "out"
CACHE = Path("/tmp/poc_entity_cache")
PER_SOURCE = 2000
SEED = 20260924
SAMPLE_SEED = 20260929
ORDER = ["logs", "traces", "events", "k8s_objects", "metrics", "alerts"]
SCENARIOS = [5, 18, 102, 33, 1, 12, 38, 20, 29]


# §1 抽樣(複製自 2-1 的 sample_source,行號從 1 起;檔案不存在就跳過)
def source_files(sc: Path) -> dict[str, list[Path]]:
    return {
        "logs": [sc / "otel_logs_raw.tsv"],
        "traces": [sc / "otel_traces_raw.tsv"],
        "events": [sc / "k8s_events_raw.tsv"],
        "k8s_objects": [sc / "k8s_objects_raw.tsv"],
        "metrics": sorted(sc.glob("metrics/*.tsv")),
        "alerts": sorted(sc.glob("alerts/*.json")),
    }


def sample_source(sc: Path, files: list[Path]) -> list[tuple[str, int, str]]:
    files = [f for f in files if f.exists()]
    counts = []
    for f in files:
        with open(f, "rb") as fh:
            counts.append(sum(1 for _ in fh))
    total = sum(counts)
    picked = set(range(total)) if total <= PER_SOURCE else set(random.Random(SEED).sample(range(total), PER_SOURCE))
    rows, offset = [], 0
    for f, n in zip(files, counts, strict=True):
        with open(f, "rb") as fh:
            for i, raw in enumerate(fh):
                if offset + i in picked:
                    rows.append((str(f.relative_to(sc)), i + 1, raw.decode("utf-8", errors="replace").rstrip("\r\n")))
        offset += n
    return rows


def load_corpus(sc: Path) -> dict[str, list[tuple[str, int, str]]]:
    CACHE.mkdir(exist_ok=True)
    p = CACHE / f"{sc.name}.pkl"
    if p.exists():
        return pickle.loads(p.read_bytes())
    corpus = {src: sample_source(sc, files) for src, files in source_files(sc).items()}
    p.write_bytes(pickle.dumps(corpus))
    return corpus


# §2 六種來源的實體抽取。回傳 Ext:entities=[(kind, name, role)], ns=該塊的 namespace, status="ok" 或失敗標籤
@dataclass
class Ext:
    entities: list[tuple[str, str, str]] = field(default_factory=list)
    ns: str | None = None
    status: str = "ok"
    path: str = ""  # 走哪條路抽出來的:attr / column / field / body / line


HEADER = re.compile(r"^(Timestamp|metric_name)\t")
TS_START = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")


def _attr(key: str) -> re.Pattern[str]:
    return re.compile(r"'" + re.escape(key) + r"': '([^']*)'")


A_SVC, A_POD, A_DEP, A_NS = _attr("service.name"), _attr("k8s.pod.name"), _attr("k8s.deployment.name"), _attr("k8s.namespace.name")


def _otel_row(text: str) -> Ext:
    """logs 與 traces 共用。
    抽 ResourceAttributes 裡的 service.name / k8s.pod.name / k8s.deployment.name / k8s.namespace.name。
    為什麼用整行 regex 不用欄位位置:logs 的 Body 有換行(導覽 §4.4),一則紀錄會被切成多個物理行,
    ResourceAttributes 落在最後一行,位置切法會失敗,regex 找 `'key': 'value'` 都找得到。
    起始行若 ResourceAttributes 在下一行,退而取第 8 欄 ServiceName(logs 與 traces 都是第 8 欄)。
    """
    if HEADER.match(text):
        return Ext(status="header")
    ents: list[tuple[str, str, str]] = []
    path = ""
    m = A_SVC.search(text)
    if m and m.group(1):
        ents.append(("Service", m.group(1), "self"))
        path = "attr"
        if TS_START.match(text):  # 自我檢查:起始行的 ServiceName 欄應該等於 ResourceAttributes 的 service.name
            cols = text.split("\t")
            if len(cols) > 7 and cols[7] and cols[7] != m.group(1):
                path = "attr_but_column_differs"
    elif TS_START.match(text):
        cols = text.split("\t")
        if len(cols) > 7 and cols[7]:
            ents.append(("Service", cols[7], "self"))
            path = "column"
    for pat, kind, role in ((A_POD, "Pod", "pod"), (A_DEP, "Deployment", "deployment")):
        m = pat.search(text)
        if m and m.group(1):
            ents.append((kind, m.group(1), role))
            path = path or "attr"
    ns_m = A_NS.search(text)
    ns = ns_m.group(1) if ns_m and ns_m.group(1) else None
    if not ents:
        return Ext(ns=ns, status="start_line_no_fields" if TS_START.match(text) else "continuation_no_fields")
    return Ext(ents, ns, "ok", path)


def ex_logs(rel: str, text: str) -> Ext:
    return _otel_row(text)


def ex_traces(rel: str, text: str) -> Ext:
    return _otel_row(text)


def _body(text: str) -> tuple[dict | None, str]:
    """events 與 k8s_objects 共用:第 9 欄 Body 是 CSV 跳脫過的 JSON。"""
    try:
        row = next(csv.reader([text], delimiter="\t", quotechar='"'))
    except Exception:
        return None, "csv_error"
    if len(row) < 9:
        return None, "short_row"
    try:
        b = json.loads(row[8])
    except Exception:
        return None, "json_error"
    return (b, "ok") if isinstance(b, dict) else (None, "body_not_object")


def ex_events(rel: str, text: str) -> Ext:
    """抽 object.involvedObject(舊版 events 用 regarding)的 kind 與 name。
    為什麼:Event 是「某個資源發生了什麼」的紀錄,主角是被指到的資源,不是 Event 自己的名字。"""
    if HEADER.match(text):
        return Ext(status="header")
    b, st = _body(text)
    if b is None:
        return Ext(status=st)
    o = b.get("object")
    if not isinstance(o, dict):
        return Ext(status="no_object")
    io = o.get("involvedObject") or o.get("regarding")
    if not isinstance(io, dict):
        return Ext(status="no_involvedObject")
    kind, name = io.get("kind"), io.get("name")
    if not kind or not name:
        return Ext(status="no_kind_or_name")
    return Ext([(kind, name, "involved")], io.get("namespace"), "ok", "body")


def ex_k8s_objects(rel: str, text: str) -> Ext:
    """抽物件最外層的 kind 與 metadata.name。為什麼:快照的每一行就是一個資源,自我描述。"""
    if HEADER.match(text):
        return Ext(status="header")
    b, st = _body(text)
    if b is None:
        return Ext(status=st)
    kind = b.get("kind")
    md = b.get("metadata")
    name = md.get("name") if isinstance(md, dict) else None
    if not kind or not name:
        return Ext(status="no_kind_or_name")
    ns = md.get("namespace") or (name if kind == "Namespace" else None)
    return Ext([(kind, name, "object")], ns, "ok", "body")


def ex_metrics(rel: str, text: str) -> Ext:
    """pod 檔取 pod_name 欄、service 檔取 service_name 欄(都是第 4 欄),namespace 是第 5 欄。
    為什麼不用 tags:service 檔 tags 裡的 pod 是 otel-collector,pod 檔 tags 裡的 service 是 Prometheus 的抓取端點,
    都不是這條指標在量的對象。種類由檔名前綴決定(這是一個假設,見報告 §3)。"""
    cols = text.split("\t")
    if cols[0] == "metric_name":
        return Ext(status="header")
    base = rel.split("/")[-1]
    kind = "Pod" if base.startswith("pod_") else "Service" if base.startswith("service_") else None
    if kind is None or len(cols) < 5 or not cols[3]:
        return Ext(status="short_row_or_unknown_file")
    return Ext([(kind, cols[3], "self")], cols[4] or None, "ok", "column")


AL_KV = re.compile(r'^\s*"(service_name|pod|container|namespace)":\s*"([^"]*)"')
AL_DESC_SVC = re.compile(r"in service (\S+) in namespace (\S+)")
AL_DESC_POD = re.compile(r"in pod (\S+) on ")
AL_DESC_CTR = re.compile(r"for container (\S+) in pod")
AL_DESC_NSNAME = re.compile(r'"description":\s*"(Pod|Deployment|HPA|StatefulSet|DaemonSet|Job)\s+([^/\s]+)/(\S+)')
AL_DESC_SVC2 = re.compile(r'"description":\s*"Service (\S+) in namespace (\S+)')
AL_DESC_NS = re.compile(r'"description":\s*"Namespace (\S+) is')
AL_KIND = {"HPA": "HorizontalPodAutoscaler"}


def ex_alerts(rel: str, text: str) -> Ext:
    """告警 JSON 是排版過的,一個物理行只有一個欄位。這裡只看單行(一行一塊):
    `"service_name": "x"` 取 Service、`"pod": "x"` 取 Pod、`"container": "x"` 取 Container、
    `"namespace"` 只記 ns;description 行用句型「in service X in namespace Y」「in pod X on」「for container X in pod」
    「Pod|Deployment|HPA ns/name」「Service X in namespace Y」「Namespace X is」(句型是看過九個場景的告警文字後補的,見報告 §7)。
    不取 `"service"`(那是 Prometheus 抓取端點的標籤,不是被告警的服務)。"""
    ents: list[tuple[str, str, str]] = []
    ns = None
    m = AL_KV.match(text)
    if m:
        key, val = m.groups()
        if val:
            if key == "namespace":
                ns = val
            else:
                ents.append(({"service_name": "Service", "pod": "Pod", "container": "Container"}[key], val, "self"))
    elif text.lstrip().startswith('"description"'):
        for pat, kind in ((AL_DESC_SVC, "Service"), (AL_DESC_POD, "Pod"), (AL_DESC_CTR, "Container")):
            mm = pat.search(text)
            if mm:
                ents.append((kind, mm.group(1), "self"))
        mm = AL_DESC_SVC.search(text)
        if mm:
            ns = mm.group(2)
        mm = AL_DESC_NSNAME.search(text)
        if mm:
            ents.append((AL_KIND.get(mm.group(1), mm.group(1)), mm.group(3), "self"))
            ns = mm.group(2)
        mm = AL_DESC_SVC2.search(text)
        if mm:
            ents.append(("Service", mm.group(1), "self"))
            ns = mm.group(2)
        mm = AL_DESC_NS.search(text)
        if mm:
            ents.append(("Namespace", mm.group(1), "self"))
    if ents:
        return Ext(ents, ns, "ok", "line")
    if ns:
        return Ext(ns=ns, status="namespace_only")
    s = text.strip()
    if s in ("[", "]", "{", "}", "},", "],") or s.endswith(("{", "[")):
        return Ext(status="structural_line")
    return Ext(status="other_field_line")


EXTRACT = {"logs": ex_logs, "traces": ex_traces, "events": ex_events, "k8s_objects": ex_k8s_objects, "metrics": ex_metrics, "alerts": ex_alerts}


def alert_object_stats(sc: Path) -> dict:
    """對照用:若一塊 = 一個完整告警物件(不是一行),抽得出實體的比例。讀該場景全部告警檔,不抽樣。"""
    files = sorted(sc.glob("alerts/*.json")) + sorted(sc.glob("alerts_in_alerting_state_*.json"))
    total = with_ent = 0
    missing = Counter()
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        items = d if isinstance(d, list) else (d.get("data", {}).get("alerts", []) if isinstance(d, dict) else [])
        for a in items:
            if not isinstance(a, dict):
                continue
            total += 1
            lab = a.get("labels", {}) or {}
            desc = (a.get("annotations", {}) or {}).get("description", "") or ""
            if lab.get("service_name") or lab.get("pod") or AL_DESC_SVC.search(desc) or AL_DESC_POD.search(desc):
                with_ent += 1
            else:
                missing[lab.get("alertname", "?")] += 1
    return {"files": len(files), "objects": total, "with_entity": with_ent, "missing_by_alertname": missing.most_common(5)}


# §3 答案檔與比對
@dataclass
class Group:
    id: str
    kind: str
    filters: list[str]
    name: str | None
    namespace: str | None
    root: bool


def load_gt(sc: Path) -> tuple[list[Group], list[list[str]]]:
    raw = yaml.safe_load(open(sc / "ground_truth.yaml"))
    c = raw.get("spec", raw)
    groups = [
        Group(g["id"], str(g.get("kind") or ""), [str(f) for f in (g.get("filter") or [])], g.get("name"), g.get("namespace"), bool(g.get("root_cause")))
        for g in c["groups"]
    ]
    return groups, c.get("aliases") or []


def alias_groups(groups: list[Group], aliases: list[list[str]], transitive: bool = False) -> list[Group]:
    root = next(g for g in groups if g.root)
    ids = {root.id}
    for _ in range(5 if transitive else 1):
        for lst in aliases:
            if ids & set(lst):
                ids |= set(lst)
    return [g for g in groups if g.id in ids]


# 沒有 filter(或 filter 無法編譯)時的備援選項;主指標的預設是「有 name 欄用 name,否則用 id」,只是 POC 預設,不是決定
def build_tests(g: Group, fallback: str | None = None) -> dict | None:
    """回傳 {"search": fn, "full": fn};None = 無法比對(無效 filter 且沒選備援)。"""
    if g.filters and fallback is None:
        try:
            srch = [re.compile(p.removeprefix(".*")) for p in g.filters]
            full = [re.compile(p) for p in g.filters]
        except re.error:
            return None
        return {"search": lambda n: any(p.search(n) for p in srch), "full": lambda n: any(p.fullmatch(n) for p in full)}
    fb = fallback or ("name" if g.name else "id")
    if fb == "name" and g.name:
        f = lambda n: n == g.name  # noqa: E731
    elif fb == "id_stripped":
        key = re.sub(r"-\d+$", "", g.id).lower()
        f = lambda n: key in n.lower()  # noqa: E731
    elif fb == "kind_only":
        f = lambda n: True  # noqa: E731
    elif fb == "glob":
        pat = g.filters[0]
        f = lambda n: fnmatch.fnmatchcase(n, pat)  # noqa: E731
    else:  # id
        key = g.id.lower()
        f = lambda n: key in n.lower()  # noqa: E731
    return {"search": f, "full": f}


def hit_group(ext: Ext, tests: list[tuple[Group, dict | None]], mode: str = "search", kind_gate: bool = True, ns_entity: bool = False) -> str | None:
    ents = list(ext.entities)
    if ns_entity and ext.ns:
        ents.append(("Namespace", ext.ns, "ns"))
    for g, t in tests:
        if t is None:
            continue
        for kind, name, _ in ents:
            if kind_gate and kind != g.kind:
                continue
            if t[mode](name):
                return g.id
    return None


def old_matcher(g: Group):
    """整行比對(複製 2-2 check_root_filters.matcher 的邏輯):filter 以 re.search 比整行;沒有 filter 用 id 子字串。"""
    if g.filters:
        try:
            pats = [re.compile(p.removeprefix(".*")) for p in g.filters]
        except re.error:
            return None
        return lambda t: next((m for p in pats if (m := p.search(t))), None)
    key = g.id.lower()

    class _M:
        def __init__(self, s: int):
            self._s = s

        def start(self) -> int:
            return self._s

    return lambda t: (_M(t.lower().find(key)) if key in t.lower() else None)


def parse_old_txt() -> dict[str, dict]:
    """從 out/root_filters*.txt 抄整行比對的數字,做交叉驗算。"""
    res: dict[str, dict] = {}
    for f in ("root_filters.txt", "root_filters_rest.txt"):
        for line in (OLD_OUT / f).read_text().splitlines():
            m = re.match(r"^(Scenario-\d+)\s+.*\|\s+(\d+) / (\d+)$", line)
            if m:
                left = line.rsplit(" | ", 1)[0].split()[-6:]
                if all(x.isdigit() for x in left):
                    res[m.group(1)] = {"per_source": dict(zip(ORDER, map(int, left), strict=True)), "total": int(m.group(2)), "corpus": int(m.group(3))}
    return res


def clip(s: str, n: int) -> str:
    s = s.replace("\t", "⇥").replace("\n", "↵")
    return s if len(s) <= n else s[:n] + "…"


def window(text: str, needle: str, before: int = 90, after: int = 110) -> str:
    i = text.find(needle) if needle else -1
    head = clip(text[:100], 100)
    if i < 0:
        return head
    seg = text[max(0, i - before) : i + len(needle) + after]
    return f"{head} … {clip(seg, before + len(needle) + after)}"


def round_robin(cands: dict[str, list], k: int, rng: random.Random) -> list:
    """每種來源先各自洗牌,再輪流各取一個,直到取滿 k 個。"""
    pools = {s: rng.sample(v, len(v)) for s, v in cands.items() if v}
    out = []
    while pools and len(out) < k:
        for s in list(pools):
            if len(out) >= k:
                break
            out.append((s, pools[s].pop()))
            if not pools[s]:
                del pools[s]
    return out


def run_scenario(n: int, old_txt: dict) -> dict:
    sc = BASE / f"Scenario-{n}"
    name = sc.name
    groups, aliases = load_gt(sc)
    root = next(g for g in groups if g.root)
    aset = alias_groups(groups, aliases)
    aset_tr = alias_groups(groups, aliases, transitive=True)
    corpus = load_corpus(sc)
    t_alias = [(g, build_tests(g)) for g in aset]
    t_root = [(root, build_tests(root))]
    t_trans = [(g, build_tests(g)) for g in aset_tr]
    old = old_matcher(root)
    exts = {s: [(rel, ln, text, EXTRACT[s](rel, text)) for rel, ln, text in corpus[s]] for s in ORDER}

    r: dict = {
        "scenario": name,
        "root": {"id": root.id, "kind": root.kind, "filters": root.filters, "name": root.name, "namespace": root.namespace},
        "alias_group": [{"id": g.id, "kind": g.kind, "filters": g.filters, "name": g.name} for g in aset],
        "alias_group_transitive_extra": [g.id for g in aset_tr if g.id not in {x.id for x in aset}],
        "filter_compiles": build_tests(root) is not None or not root.filters,
        "corpus": {s: len(exts[s]) for s in ORDER},
        "sources": {},
        "alert_object_level": alert_object_stats(sc),
    }
    search_only: Counter = Counter()  # 只有 re.search 認為相關、fullmatch 不認為的實體名(kind:name,pod 去掉尾巴)
    samples_new: dict[str, list] = {}
    samples_old: dict[str, list] = {}
    for s in ORDER:
        rows = exts[s]
        st = Counter(e.status for *_, e in rows)
        ok = st["ok"]
        hdr = st["header"]
        kinds, paths = Counter(), Counter()
        for *_, e in rows:
            if e.status == "ok":
                for k in {k for k, _, _ in e.entities}:
                    kinds[k] += 1
                paths[e.path] += 1
        fails: dict[str, list] = {}
        for rel, ln, text, e in rows:
            if e.status not in ("ok", "header") and len(fails.setdefault(e.status, [])) < 3:
                fails[e.status].append({"file": f"{rel}:{ln}", "text": text[:200]})
        cnt = Counter()
        c_new, c_old = [], []
        for rel, ln, text, e in rows:
            flags = {
                "main": hit_group(e, t_alias),
                "full": hit_group(e, t_alias, "full"),
                "blind": hit_group(e, t_alias, kind_gate=False),
                "root": hit_group(e, t_root),
                "ns": hit_group(e, t_alias, ns_entity=True),
                "trans": hit_group(e, t_trans),
            }
            for k, v in flags.items():
                cnt[k] += bool(v)
            m = old(text) if old else None
            cnt["old"] += bool(m)
            if flags["main"] and not flags["full"]:
                for g, tt in t_alias:
                    for k, nm, _ in e.entities:
                        if tt and k == g.kind and tt["search"](nm) and not tt["full"](nm):
                            search_only[f"{k}:{nm}"] += 1
            if flags["main"]:
                c_new.append((rel, ln, text, e, flags["main"]))
            if m and not flags["main"]:
                c_old.append((rel, ln, text, e, m.start()))
        samples_new[s], samples_old[s] = c_new, c_old
        r["sources"][s] = {
            "n": len(rows), "ok": ok, "header": hdr, "ok_rate": ok / len(rows) if rows else None,
            "ok_rate_excl_header": ok / (len(rows) - hdr) if len(rows) - hdr else None,
            "fail_tags": {k: v for k, v in st.items() if k not in ("ok",)},
            "kinds": dict(kinds.most_common()), "paths": dict(paths), "fail_examples": fails,
            "distinct_names_by_kind": {k: len({nm for *_, e in rows for kk, nm, _ in e.entities if kk == k}) for k in kinds},
            "candidates": {k: cnt[k] for k in ("old", "main", "full", "blind", "root", "ns", "trans")},
        }
    # 根因 kind 在資料裡實際叫什麼名字(前 6)
    names, in_ns = Counter(), Counter()
    for s in ORDER:
        for *_, e in exts[s]:
            for k, nm, _ in e.entities:
                if k == root.kind:
                    names[nm] += 1
                    if not root.namespace or e.ns == root.namespace:
                        in_ns[nm] += 1
    r["search_only_entities"] = search_only.most_common(6)
    r["root_kind_names_in_data"] = {"distinct_all": len(names), "distinct_in_root_ns": len(in_ns), "top_in_root_ns": in_ns.most_common(6)}
    # 備援選項:沒有 filter 或 filter 無法編譯時,各選項的候選塊數
    opts: dict[str, dict] = {}
    root_only_total = sum(r["sources"][s]["candidates"]["root"] for s in ORDER)
    invalid = bool(root.filters) and build_tests(root) is None
    if not root.filters or invalid or root_only_total == 0:
        choices = ["name", "id", "id_stripped", "kind_only"] if (not root.filters or invalid) else ["kind_only"]
        choices += ["glob"] if invalid else []
        for fb in choices:
            if fb == "name" and not root.name:
                continue
            tt = [(root, build_tests(root, fb))]
            opts[fb] = {s: sum(1 for *_, e in exts[s] if hit_group(e, tt)) for s in ORDER}
            opts[fb]["total"] = sum(opts[fb][s] for s in ORDER)
        if invalid:
            opts["skip(視為無法比對)"] = {s: 0 for s in ORDER} | {"total": 0}
        elif root.filters:
            opts["原 filter(現況)"] = {s: r["sources"][s]["candidates"]["root"] for s in ORDER} | {"total": root_only_total}
    r["fallback_options"] = opts
    # 抽樣目視檢查
    rng = random.Random(SAMPLE_SEED + n)
    picks_new = round_robin(samples_new, 5, rng)
    picks_old = round_robin(samples_old, 5, rng)
    r["inspect_new"] = [
        {"source": s, "where": f"{rel}:{ln}", "entities": [f"{k}:{nm}({ro})" for k, nm, ro in e.entities], "ns": e.ns, "matched_group": gid,
         "excerpt": window(text, next((nm for k, nm, _ in e.entities if any(g.id == gid and g.kind == k for g in aset)), ""))}
        for s, (rel, ln, text, e, gid) in picks_new
    ]
    r["inspect_old_only"] = [
        {"source": s, "where": f"{rel}:{ln}", "entities": [f"{k}:{nm}({ro})" for k, nm, ro in e.entities] or ["(抽不出)"], "ns": e.ns, "status": e.status,
         "excerpt": (lambda t, pos: f"{clip(t[:100], 100)} … {clip(t[max(0, pos - 90) : pos + 110], 200)}")(text, pos)}
        for s, (rel, ln, text, e, pos) in picks_old
    ]
    # 對照舊數字
    ot = old_txt.get(name)
    if ot:
        mine = {s: r["sources"][s]["candidates"]["old"] for s in ORDER}
        r["old_crosscheck"] = {"txt": ot["per_source"], "mine": mine, "equal": ot["per_source"] == mine, "corpus_txt": ot["corpus"], "corpus_mine": sum(r["corpus"].values())}
    return r


def all_scenarios_kinds() -> dict:
    """35 個場景:根因 group 的 kind 分布,與 alias 組(含根因)裡所有 group 的 kind 分布(Q4:要對應的 kind 有哪些)。"""
    root_kinds, alias_kinds, no_filter_root = Counter(), Counter(), 0
    for sc in sorted(BASE.glob("Scenario-*")):
        groups, aliases = load_gt(sc)
        root = next(g for g in groups if g.root)
        root_kinds[root.kind] += 1
        no_filter_root += not root.filters
        for g in alias_groups(groups, aliases):
            alias_kinds[g.kind] += 1
    return {"scenarios": len(list(BASE.glob("Scenario-*"))), "root_kinds": dict(root_kinds.most_common()), "alias_group_kinds": dict(alias_kinds.most_common()), "root_without_filter": no_filter_root}


# §4 「官方推導」設定(§10):名字本身、filter 保留原樣(不去 `.*`)、re.match;無 filter 用 name 欄(不分大小寫完全相符)再退 id;
# filter 無法編譯只比 kind。kind 仍須相同,alias 組照舊。
OFFICIAL_SCENARIOS = SCENARIOS + [14, 15]
LONG_NAMES: list[int] = []


def build_official(g: Group) -> dict:
    def cut(n: str) -> str:
        if len(n) > 500:
            LONG_NAMES.append(len(n))
            return n[:500]
        return n

    if g.filters:
        try:
            pats = [re.compile(p) for p in g.filters]
        except re.error:
            f = lambda n: True  # noqa: E731  只比 kind
        else:
            f = lambda n: any(p.match(cut(n)) for p in pats)  # noqa: E731
    else:
        key = (g.name or g.id).lower()
        f = lambda n: cut(n).lower() == key  # noqa: E731
    return {"search": f, "full": f}


def run_official(n: int) -> dict:
    sc = BASE / f"Scenario-{n}"
    groups, aliases = load_gt(sc)
    root = next(g for g in groups if g.root)
    aset = alias_groups(groups, aliases)
    corpus = load_corpus(sc)
    t_main = [(g, build_tests(g)) for g in aset]
    t_off = [(g, build_official(g)) for g in aset]
    old = old_matcher(root)
    LONG_NAMES.clear()
    per: dict = {}
    diff: Counter = Counter()
    cand_off: dict[str, list] = {}
    for src in ORDER:
        c = Counter()
        cand_off[src] = []
        for rel, ln, text in corpus[src]:
            e = EXTRACT[src](rel, text)
            m_main = hit_group(e, t_main)
            m_full = hit_group(e, t_main, "full")
            m_off = hit_group(e, t_off)
            c["old"] += bool(old and old(text))
            c["main"] += bool(m_main)
            c["full"] += bool(m_full)
            c["off"] += bool(m_off)
            if m_off:
                cand_off[src].append((rel, ln, text, e, m_off))
            # 差異實體:各 group 逐一比,只有一邊判中的實體名
            for (g, ts), (_, to) in zip(t_main, t_off, strict=True):
                for k, nm, _ in e.entities:
                    if k != g.kind:
                        continue
                    a = bool(ts and ts["search"](nm))
                    b = bool(to["search"](nm))
                    if a != b:
                        diff[("只有search中" if a else "只有官方推導中", f"{k}:{nm}")] += 1
        per[src] = dict(c)
    rng = random.Random(SAMPLE_SEED + 1000 + n)
    picks = round_robin(cand_off, 5, rng)
    return {
        "scenario": f"Scenario-{n}", "root": {"id": root.id, "kind": root.kind, "filters": root.filters, "name": root.name},
        "filter_compiles": not root.filters or build_tests(root) is not None,
        "per_source": per, "total": {k: sum(per[s][k] for s in ORDER) for k in ("old", "main", "full", "off")},
        "long_names": list(LONG_NAMES),
        "diff_top5": [(a, b, c) for (a, b), c in diff.most_common(5)],
        "diff_top5_by_direction": {d: [(nm, c) for (dd, nm), c in diff.most_common() if dd == d][:5] for d in ("只有search中", "只有官方推導中")},
        "inspect": [{"source": s_, "where": f"{rel}:{ln}", "entities": [f"{k}:{nm}({ro})" for k, nm, ro in e.entities], "matched_group": gid,
                     "excerpt": window(text, next((nm for k, nm, _ in e.entities if any(g.id == gid and g.kind == k for g in aset)), ""))}
                    for s_, (rel, ln, text, e, gid) in picks],
    }


def render_official(off: list[dict], prose: dict[str, str]) -> str:
    L: list[str] = []
    w = L.append
    w("\n## §10 官方推導設定重跑\n")
    w("### §10.1 設定說明\n")
    w(prose.get("s10_1", ""))
    w("\n### §10.2 候選塊數並列(六種來源合計)\n")
    w("| 場景 | 根因 kind | 整行 | 先前主指標(search) | fullmatch | **官方推導** | 官方推導各來源(logs/traces/events/k8s_objects/metrics/alerts) |")
    w("|---|---|---|---|---|---|---|")
    for r in off:
        t = r["total"]
        w(f"| {r['scenario']} | {r['root']['kind']} | {t['old']} | {t['main']} | {t['full']} | **{t['off']}** | {' / '.join(str(r['per_source'][s]['off']) for s in ORDER)} |")
    lens = {r["scenario"]: r["long_names"] for r in off if r["long_names"]}
    w(f"\n名字超過 500 字元而被截斷的次數:{lens if lens else '全部場景 0 次'}。")
    w("\n### §10.3 官方推導與 search 差異最大的實體(每場景前 5,計塊數;一塊可能算到多個 group)\n")
    for r in off:
        w(f"- {r['scenario']}:{r['diff_top5'] or '無差異'}")
    w("\n各方向分列(前 5):\n")
    for r in off:
        d = r["diff_top5_by_direction"]
        w(f"- {r['scenario']}:只有 search 中 {d['只有search中']};只有官方推導中 {d['只有官方推導中']}")
    w(prose.get("s10_3", ""))
    w("\n### §10.4 目視檢查:官方推導判相關的塊(每場景最多 5 塊,每種來源輪流取)\n")
    for r in off:
        w(f"\n**{r['scenario']}**\n")
        if not r["inspect"]:
            w("- (無)")
        for i, x in enumerate(r["inspect"], 1):
            w(f"{i}. {x['source']} `{x['where']}` 實體 {x['entities']} → 中 group `{x['matched_group']}`\n   ```\n   {x['excerpt']}\n   ```")
    w("\n### §10.5 結論\n")
    w(prose.get("s10_5", ""))
    return "\n".join(L) + "\n"


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


def render(results: list[dict], prose: dict[str, str], kinds35: dict) -> str:
    L: list[str] = []
    w = L.append
    w("# 作業 2-2 POC 報告|先抽實體 (kind, name) 再判相關\n")
    w(prose.get("intro", ""))
    # §1
    w("\n## §1 範圍與語料驗收\n")
    w(prose.get("s1_scope", ""))
    w("\n語料驗收(重算整行比對,與 `2-2-evidence-trial/out/root_filters*.txt` 逐來源比對):\n")
    w("| 場景 | 語料塊數(本 POC) | 語料塊數(舊 txt) | 六來源整行比中數是否逐格相同 | 舊 txt 各來源 | 本 POC 各來源 |")
    w("|---|---|---|---|---|---|")
    for r in results:
        c = r.get("old_crosscheck")
        if c:
            w(f"| {r['scenario']} | {c['corpus_mine']} | {c['corpus_txt']} | {'相同' if c['equal'] else '**不同**'} | {' / '.join(str(c['txt'][s]) for s in ORDER)} | {' / '.join(str(c['mine'][s]) for s in ORDER)} |")
        else:
            w(f"| {r['scenario']} | {sum(r['corpus'].values())} | (舊 txt 無此場景或無效 filter) | - | - | {' / '.join(str(r['sources'][s]['candidates']['old']) for s in ORDER)} |")
    w("\n各場景各來源塊數(順序 logs / traces / events / k8s_objects / metrics / alerts):\n")
    for r in results:
        w(f"- {r['scenario']}:{' / '.join(str(r['corpus'][s]) for s in ORDER)}(合計 {sum(r['corpus'].values())})")
    # §2
    w("\n## §2 各來源抽取方式與成功率(Q1)\n")
    w(prose.get("s2_methods", ""))
    w("\n### §2.1 六來源合併成功率(9 個場景加總)\n")
    w("成功 = 至少抽出一個 (kind, name)。「扣標頭」= 分母扣掉 TSV 標頭行(每個檔一行,語料定義裡本來就算一塊)。\n")
    w("| 來源 | 總塊數 | 抽得出 | 成功率 | 標頭行 | 扣標頭後成功率 | 抽出的 kind(塊數) | 抽不出的標籤(塊數) |")
    w("|---|---|---|---|---|---|---|---|")
    for s in ORDER:
        tot = sum(r["sources"][s]["n"] for r in results)
        ok = sum(r["sources"][s]["ok"] for r in results)
        hd = sum(r["sources"][s]["header"] for r in results)
        kinds, fails = Counter(), Counter()
        for r in results:
            kinds.update(r["sources"][s]["kinds"])
            fails.update({k: v for k, v in r["sources"][s]["fail_tags"].items() if k != "header"})
        kk = "、".join(f"{k} {v}" for k, v in kinds.most_common(12))
        ff = "、".join(f"{k} {v}" for k, v in fails.most_common()) or "無"
        w(f"| {s} | {tot} | {ok} | {100 * ok / tot:.1f}% | {hd} | {100 * ok / (tot - hd):.1f}% | {kk} | {ff} |")
    w("\n### §2.2 各場景成功率(扣標頭後)\n")
    w("| 場景 | " + " | ".join(ORDER) + " |")
    w("|---|" + "---|" * len(ORDER))
    for r in results:
        w(f"| {r['scenario']} | " + " | ".join(f"{100 * (r['sources'][s]['ok_rate_excl_header'] or 0):.1f}%" for s in ORDER) + " |")
    w("\n### §2.3 抽取路徑(logs / traces:attr = ResourceAttributes regex;column = 第 8 欄 ServiceName 後備)\n")
    for s in ("logs", "traces"):
        p = Counter()
        for r in results:
            p.update(r["sources"][s]["paths"])
        w(f"- {s}:{dict(p)}")
    w("\n### §2.4 告警:單行與整個告警物件的差別\n")
    w("一塊 = 一個物理行時(本 POC 的語料定義),告警檔是排版過的 JSON,一行只有一個欄位。下表對照「若一塊 = 一個完整告警物件」抽得出實體的比例(讀全部告警檔,不抽樣):\n")
    w("| 場景 | 告警檔數 | 告警物件數 | 有 service_name 或 pod 或 description 句型 | 抽不出的 alertname(前 5) |")
    w("|---|---|---|---|---|")
    for r in results:
        a = r["alert_object_level"]
        w(f"| {r['scenario']} | {a['files']} | {a['objects']} | {pct(a['with_entity'], a['objects'])} | {a['missing_by_alertname']} |")
    w("\n### §2.5 抽不出的範例(每個來源每種標籤前 3 個,取第一個出現該標籤的場景;截 200 字元;全部場景見 results.json)\n")
    for s in ORDER:
        seen: dict[str, dict] = {}
        for r in results:
            for tag, ex in r["sources"][s]["fail_examples"].items():
                seen.setdefault(tag, {"scenario": r["scenario"], "ex": ex})
        w(f"**{s}**\n")
        if not seen:
            w("- (九個場景都沒有抽不出的塊)\n")
        for tag, v in seen.items():
            for e in v["ex"]:
                w(f"- `{tag}` {v['scenario']} {e['file']}:`{clip(e['text'], 200)}`")
        w("")
    w(prose.get("s2_findings", ""))
    # §3
    w("\n## §3 kind 對應表與假設(Q4)\n")
    w(prose.get("s3", ""))
    w(f"\n全部 {kinds35['scenarios']} 個場景:根因 group 的 kind 分布 {kinds35['root_kinds']};根因所在 alias 組(含根因)裡所有 group 的 kind 分布 {kinds35['alias_group_kinds']};根因沒有 filter 的場景 {kinds35['root_without_filter']} 個。\n")
    w("\n各場景根因 group 的 kind,以及該 kind 在資料裡實際出現的名字(前 6,包含來源計數):\n")
    w("| 場景 | 根因 kind | 根因 filter / name | alias 組(id:kind) | 資料裡該 kind 的名字(名字:出現塊數) |")
    w("|---|---|---|---|---|")
    for r in results:
        rt = r["root"]
        cond = f"filter={rt['filters']}" if rt["filters"] else f"(無 filter)name={rt['name']}"
        rk = r["root_kind_names_in_data"]
        w(f"| {r['scenario']} | {rt['kind']} | {cond} | {'、'.join(f'{g['id']}:{g['kind']}' for g in r['alias_group'])} | 該 kind 共 {rk['distinct_all']} 個名字,其中在根因 namespace 的 {rk['distinct_in_root_ns']} 個;前 6:{rk['top_in_root_ns']} |")
    # §4
    w("\n## §4 候選塊數:新做法 vs 整行比對(Q2)\n")
    w(prose.get("s4_intro", ""))
    w("\n欄位說明:整行 = 舊做法(根因 filter 比整行);root-only = 新做法只用根因 group;**主** = 新做法用 alias 組、kind 相同、re.search;fullmatch / kind-blind / ns-entity 是變體(見檔頭定義)。\n")
    for r in results:
        w(f"\n**{r['scenario']}**(根因 {r['root']['id']}:{r['root']['kind']};alias 組 {len(r['alias_group'])} 個 group)\n")
        w("| 來源 | 塊數 | 整行 | root-only | **主** | fullmatch | kind-blind | ns-entity |")
        w("|---|---|---|---|---|---|---|---|")
        tot = Counter()
        for s in ORDER:
            c = r["sources"][s]["candidates"]
            n = r["sources"][s]["n"]
            tot.update({**c, "n": n})
            w(f"| {s} | {n} | {c['old']} | {c['root']} | **{c['main']}** | {c['full']} | {c['blind']} | {c['ns']} |")
        w(f"| **合計** | {tot['n']} | {tot['old']} | {tot['root']} | **{tot['main']}** | {tot['full']} | {tot['blind']} | {tot['ns']} |")
        if r["search_only_entities"]:
            w(f"\n只有 re.search 判相關、fullmatch 不判相關的實體(前 6,計塊數;一塊可能算到多個 group):{r['search_only_entities']}")
        if r["alias_group_transitive_extra"]:
            w(f"\n(若 alias 組做傳遞閉包,多納入 {r['alias_group_transitive_extra']},候選合計 {sum(r['sources'][s]['candidates']['trans'] for s in ORDER)}。)")
    w(prose.get("s4_findings", ""))
    # §5
    w("\n## §5 目視檢查樣本\n")
    w("每個場景:最多 5 塊「新做法判相關」、最多 5 塊「整行比中但新做法判不相關」。挑法:每種來源先各自洗牌,輪流各取一塊(種子 20260929 + 場景號),所以不是純隨機比例抽樣,是為了每種來源都看得到。excerpt = 行首 100 字元 + 該實體名(或整行比中處)前後的原文;⇥ = tab,↵ = 換行。\n")
    for r in results:
        w(f"\n### §5.{SCENARIOS.index(int(r['scenario'].split('-')[1])) + 1} {r['scenario']}\n")
        w("**新做法判相關**\n")
        if not r["inspect_new"]:
            w("- (無)")
        for i, x in enumerate(r["inspect_new"], 1):
            w(f"{i}. {x['source']} `{x['where']}` 實體 {x['entities']} ns={x['ns']} → 中 group `{x['matched_group']}`\n   ```\n   {x['excerpt']}\n   ```")
        w("\n**整行比中、新做法判不相關**\n")
        if not r["inspect_old_only"]:
            w("- (無)")
        for i, x in enumerate(r["inspect_old_only"], 1):
            w(f"{i}. {x['source']} `{x['where']}` 抽出實體 {x['entities']} ns={x['ns']} 狀態 {x['status']}\n   ```\n   {x['excerpt']}\n   ```")
    w(prose.get("s5_findings", ""))
    # §6
    w("\n## §6 特殊場景的可行處理與後果(Q3,不選定)\n")
    w(prose.get("s6_intro", ""))
    for r in results:
        if r["fallback_options"]:
            w(f"\n**{r['scenario']} 各處理方式的候選塊數**(根因 {r['root']['id']}:{r['root']['kind']};alias 組只有 {len(r['alias_group'])} 個 group)\n")
            w("| 處理方式 | " + " | ".join(ORDER) + " | 合計 |")
            w("|---|" + "---|" * (len(ORDER) + 1))
            for k, v in r["fallback_options"].items():
                w(f"| {k} | " + " | ".join(str(v[s]) for s in ORDER) + f" | {v['total']} |")
    w(prose.get("s6", ""))
    w("\n## §7 工作量與格式坑\n")
    w(prose.get("s7", ""))
    w("\n## §8 結論\n")
    w(prose.get("s8", ""))
    w("\n## §9 限制\n")
    w(prose.get("s9", ""))
    return "\n".join(L) + "\n"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    old_txt = parse_old_txt()
    results = []
    for n in SCENARIOS:
        print(f"Scenario-{n} ...", flush=True)
        r = run_scenario(n, old_txt)
        results.append(r)
        print("  ", {s: (r["sources"][s]["ok"], r["sources"][s]["candidates"]["old"], r["sources"][s]["candidates"]["main"]) for s in ORDER}, flush=True)
        if n == 5:
            tot = sum(r["sources"][s]["candidates"]["old"] for s in ORDER)
            if tot != 10:
                sys.exit(f"自我檢查失敗:Scenario-5 整行比對應為 10 塊,實得 {tot}")
    kinds35 = all_scenarios_kinds()
    (OUT / "results.json").write_text(json.dumps({"scenarios": results, "all_scenarios_kinds": kinds35}, ensure_ascii=False, indent=1))
    pp = OUT / "prose.json"
    prose = json.loads(pp.read_text()) if pp.exists() else {}
    official = [run_official(n) for n in OFFICIAL_SCENARIOS]
    data = json.loads((OUT / "results.json").read_text())
    data["official_rerun"] = official
    (OUT / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (OUT / "report.md").write_text(render(results, prose, kinds35) + render_official(official, prose))
    print("done")


if __name__ == "__main__":
    main()
