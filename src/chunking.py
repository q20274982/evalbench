"""切塊層:把一個場景的六種觀測資料切成要算 embedding 的塊。

規格正本是 Product SPEC `§8.3`(前處理:每種來源切之前丟什麼)與 `§8.4`(每種來源怎麼切,
含「照著切的補充規則」)。本檔每個判斷分支旁邊的 `§8.x` 註記指到規格裡對應的那一句。

這一層只「切」:不存資料庫、不算 embedding(SPEC §8.4 補充規則「範圍」之外,下一步再做)。
輸出的每一塊帶四樣東西:
  - text:塊的文字 = 規格說的「原句」。只放前處理留下的欄位,值一個字都不改
  - key:合併依據。key 相同的紀錄併成一塊,保留第一次出現的那一筆
  - count / first_seen / last_seen:合併後的附帶欄位,不進 text(§8.4「合併後的附帶資訊」)
  - namespace:附帶欄位,不進 text(§8.3 namespace 那條)

「第一次出現」= 先照時間排序,時間相同再照檔案裡的列順序(§8.4)。Python 的 sort 是穩定排序,
所以先按讀檔順序收進 list、再只用時間當排序鍵,同時間的列自然維持檔案順序。
"""

import ast
import csv
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

csv.field_size_limit(sys.maxsize)

# §1 共用工具


@dataclass
class Chunk:
    source: str
    key: tuple
    text: str
    namespace: str = ""
    count: int | None = None
    first_seen: str = ""
    last_seen: str = ""
    extra: dict = field(default_factory=dict)


def norm_time(s: str) -> str:
    """把兩種時間寫法統一成可直接比大小的字串:YYYY-MM-DDTHH:MM:SS.nnnnnnnnn。

    資料裡有 `2025-12-15 17:17:22.019415781`(traces / logs / objects)與
    `2025-12-14T18:14:29Z`(events)。小數位數不固定,補到 9 位才能逐字比大小。
    """
    s = s.replace(" ", "T").rstrip("Z")
    base, _, frac = s.partition(".")
    return f"{base}.{frac.ljust(9, '0')}"


# 原檔的 ResourceAttributes / SpanAttributes / tags 是 Python dict 字串(資料集導覽 §7 7-7)。
# 要「只留部分 key、保留原檔寫法」(§8.4 補充規則「塊的文字」),所以不解析再重寫,
# 而是把每一對 'key': 'value' 的原始字面切出來,挑完再接回去。
_Q = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
_PAIR = re.compile(rf"({_Q}): ({_Q})")


@lru_cache(maxsize=200_000)
def dict_pairs(raw: str) -> tuple[tuple[str, str, str], ...]:
    """回傳 (key, value, 原始字面) 的序列。切不回原字串就報錯,不靜默吞掉格式意外。"""
    if raw in ("", "{}"):
        return ()
    pairs = [(m.group(0), m.group(1), m.group(2)) for m in _PAIR.finditer(raw)]
    if "{" + ", ".join(p[0] for p in pairs) + "}" != raw:
        raise ValueError(f"dict 字串切不回原樣,格式假設不成立:{raw[:200]}")
    return tuple((ast.literal_eval(k), ast.literal_eval(v), lit) for lit, k, v in pairs)


def dict_subset(raw: str, keep) -> str:
    """只留 keep(key) 為真的那幾對,原始字面與順序不動。"""
    if raw == "":
        return raw
    return "{" + ", ".join(lit for k, _, lit in dict_pairs(raw) if keep(k)) + "}"


def dict_get(raw: str, key: str) -> str:
    for k, v, _ in dict_pairs(raw):
        if k == key:
            return v
    return ""


def lines(cols: list[str], row: dict[str, str]) -> str:
    """一欄一行寫成 `欄位名: 值`,欄位順序照 cols(§8.4 補充規則「塊的文字」)。"""
    return "\n".join(f"{c}: {row[c]}" for c in cols)


def merge(source: str, items: list[tuple[str, tuple, str, str]]) -> list[Chunk]:
    """items = [(時間, key, text, namespace)],已照檔案順序排好。

    照時間穩定排序後,key 相同的併成一塊,保留第一筆的 text,記次數與首末時間。
    """
    items.sort(key=lambda x: x[0])
    out: dict[tuple, Chunk] = {}
    for t, key, text, ns in items:
        c = out.get(key)
        if c is None:
            out[key] = Chunk(source, key, text, ns, 1, t, t)
        else:
            c.count += 1
            c.last_seen = t
    return list(out.values())


def read_tsv(path: Path) -> Iterator[dict[str, str]]:
    with open(path, newline="") as f:
        yield from csv.DictReader(f, delimiter="\t")


def k8s_or_service(k: str) -> bool:
    # §8.3 traces / logs:ResourceAttributes 只留 k8s.*、service.*
    return k.startswith("k8s.") or k.startswith("service.")


# §2 events(§8.3 events、§8.4 events)

EV_NEW = "events.k8s.io/v1"


def chunk_events(scn: Path) -> list[Chunk]:
    rows = list(read_tsv(scn / "k8s_events_raw.tsv"))
    objs = [json.loads(r["Body"])["object"] for r in rows]
    versions: dict[str, set[str]] = {}
    for o in objs:
        versions.setdefault(o["metadata"]["uid"], set()).add(o["apiVersion"])

    items = []
    for r, o in zip(rows, objs):
        if o["apiVersion"] == EV_NEW:
            reg, note = o.get("regarding") or {}, o.get("note", "")
            t = (
                o.get("deprecatedFirstTimestamp")
                or o.get("eventTime")
                or o.get("deprecatedLastTimestamp")
            )
        elif versions[o["metadata"]["uid"]] == {"v1"}:
            # §8.4 補充規則:只有舊版的 uid 原樣收進來,message 當 note、involvedObject 當 regarding。
            # 時間欄位的對照(firstTimestamp ↔ deprecatedFirstTimestamp)是 TA 的讀法,規則沒寫。
            reg, note = o.get("involvedObject") or {}, o.get("message", "")
            t = o.get("firstTimestamp") or o.get("eventTime") or o.get("lastTimestamp")
        else:
            continue  # §8.3:兩版都有的 uid 只留新版
        if not t:
            raise ValueError(f"event 三個時間欄位都是空的:{o['metadata']['uid']}")
        # §8.4 補充規則:同一個物件 = regarding 的 kind + namespace + name;同一句話 = reason + note
        key = (
            reg.get("kind", ""),
            reg.get("namespace", ""),
            reg.get("name", ""),
            o.get("reason", ""),
            note,
        )
        # 塊的文字 = Body 原字串;events 的前處理不丟 Body 裡的鍵,所以不重新序列化
        items.append((norm_time(t), key, r["Body"], reg.get("namespace", "")))
    return merge("events", items)


# §3 traces(§8.3 traces、§8.4 traces)

TRACE_KEEP = {
    "TraceId",
    "SpanId",
    "ParentSpanId",
    "ServiceName",
    "Timestamp",
    "Duration",
    "StatusCode",
    "StatusMessage",
    "SpanAttributes",
    "ResourceAttributes",
    "Events.Name",
    "Events.Attributes",
    "SpanName",
    "Links.TraceId",
    "Links.SpanId",
}
# §8.4 補充規則:四個狀態碼欄位有幾個放幾個,沒有的當空值
TRACE_STATUS = (
    "http.response.status_code",
    "http.status_code",
    "rpc.grpc.status_code",
    "grpc.status_code",
)


def chunk_traces(scn: Path) -> list[Chunk]:
    path = scn / "otel_traces_raw.tsv"
    with open(path, newline="") as f:
        cols = [c for c in csv.DictReader(f, delimiter="\t").fieldnames if c in TRACE_KEEP]
    items = []
    for r in read_tsv(path):
        ra = r["ResourceAttributes"]
        r = {c: r[c] for c in cols} | {"ResourceAttributes": dict_subset(ra, k8s_or_service)}
        sa = r["SpanAttributes"]
        key = (
            r["ServiceName"],
            dict_get(ra, "k8s.pod.name"),
            r["SpanName"],
            r["StatusCode"],
            *(dict_get(sa, k) for k in TRACE_STATUS),
        )
        items.append(
            (norm_time(r["Timestamp"]), key, lines(cols, r), dict_get(ra, "k8s.namespace.name"))
        )
    return merge("traces", items)


# §4 logs(§8.3 logs、§8.4 logs)

LOG_KEEP = {
    "TraceId",
    "SpanId",
    "Timestamp",
    "ServiceName",
    "Body",
    "LogAttributes",
    "ResourceAttributes",
    "SeverityText",
}
# §8.4 補充規則「logs 遮罩的正規表示式」,順序即套用順序
LOG_STATUS = re.compile(r'(HTTP/[\d.]+"\s)(\d{3})')
LOG_MASKS = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?Z?"), "<TIME>"),
    (
        re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
        "<UUID>",
    ),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b"), "<IP>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HEX>"),
    (re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b"), "<ID>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<N>"),
]


def mask_log(body: str) -> tuple[str, tuple[str, ...]]:
    """回傳 (遮罩後的 Body, 依序抽出的 HTTP 狀態碼)。只用來分組,不進 text。"""
    codes = tuple(m.group(2) for m in LOG_STATUS.finditer(body))
    for rx, tag in LOG_MASKS:
        body = rx.sub(tag, body)
    return body, codes


def chunk_logs(scn: Path) -> list[Chunk]:
    path = scn / "otel_logs_raw.tsv"
    with open(path, newline="") as f:
        cols = [c for c in csv.DictReader(f, delimiter="\t").fieldnames if c in LOG_KEEP]
    items = []
    for r in read_tsv(path):
        ra = r["ResourceAttributes"]
        r = {c: r[c] for c in cols} | {"ResourceAttributes": dict_subset(ra, k8s_or_service)}
        masked, codes = mask_log(r["Body"])
        # §8.4 logs:服務 + pod + 嚴重程度(轉大寫,補充規則)+ 遮罩後的 Body + HTTP 狀態碼
        key = (
            r["ServiceName"],
            dict_get(ra, "k8s.pod.name"),
            r["SeverityText"].upper(),
            masked,
            codes,
        )
        items.append(
            (norm_time(r["Timestamp"]), key, lines(cols, r), dict_get(ra, "k8s.namespace.name"))
        )
    return merge("logs", items)


# §5 alerts(§8.3 alerts、§8.4 alerts)

ALERT_LABELS = ("alertname", "severity", "namespace", "pod", "container", "service", "service_name")
ALERT_ANNOTATIONS = ("description", "summary")
_AT = re.compile(r"alerts_at_(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})\.(\d+)\.json")
_STATE = re.compile(
    r"alerts_in_alerting_state_(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})\.(\d+)Z\.json"
)


def snapshot_time(name: str) -> str:
    """§8.4 補充規則:兩種檔名都當 UTC。"""
    m = _AT.fullmatch(name) or _STATE.fullmatch(name)
    if not m:
        raise ValueError(f"告警快照檔名格式沒見過:{name}")
    d, hh, mm, ss, frac = m.groups()
    return norm_time(f"{d}T{hh}:{mm}:{ss}.{frac}")


def alert_text(a: dict) -> str:
    """欄位名用路徑,順序照原檔的鍵順序(§8.4 補充規則「塊的文字」)。"""
    out = []
    for k, v in a.items():
        if k == "labels":
            out += [f"labels.{lk}: {lv}" for lk, lv in v.items() if lk in ALERT_LABELS]
        elif k == "annotations":
            out += [f"annotations.{ak}: {av}" for ak, av in v.items() if ak in ALERT_ANNOTATIONS]
        elif k in ("activeAt", "value"):
            out.append(f"{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}")
    return "\n".join(out)


def chunk_alerts(scn: Path) -> list[Chunk]:
    # S11 的告警檔在場景根目錄(資料集導覽 §7 7-1);其餘場景兩種檔都在 alerts/
    files = list(scn.glob("alerts/*.json")) + list(scn.glob("alerts_in_alerting_state_*.json"))
    files.sort(key=lambda p: (snapshot_time(p.name), p.name))
    out: dict[tuple, Chunk] = {}
    for p in files:
        t = snapshot_time(p.name)
        d = json.loads(p.read_text())
        seen_here: set[tuple] = set()
        for a in d if isinstance(d, list) else d["data"]["alerts"]:
            labels = a.get("labels", {})
            key = tuple(labels.get(x, "") for x in ALERT_LABELS) + (a.get("activeAt", ""),)
            c = out.get(key)
            if c is None:
                out[key] = Chunk("alerts", key, alert_text(a), labels.get("namespace", ""), 1, t, t)
            elif key not in seen_here:
                # §8.4 alerts:附帶欄位記的是「出現在幾張快照」,同一張快照重複出現只算一次
                c.count += 1
                c.last_seen = t
            seen_here.add(key)
    return list(out.values())


# §6 metrics(§8.3 metrics、§8.4 metrics)

METRIC_DROP_TAGS = {
    "prometheus",
    "prometheus_replica",
    "endpoint",
    "instance",
    "job",
    "metrics_path",
    "exported_instance",
    "exported_namespace",
    "otel_scope_name",
    "le",
}
# §8.4 metrics:一條數列的身分 = metric_name + pod_name 或 service_name + 留下的 tags +
# bucket_le + status_code。內容只放身分,不放數值
METRIC_ID = {"metric_name", "pod_name", "service_name", "bucket_le", "tags", "status_code"}


def chunk_metrics(scn: Path) -> list[Chunk]:
    out: dict[tuple, Chunk] = {}
    for p in sorted(scn.glob("metrics/*.tsv")):
        with open(p, newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            cols = [c for c in reader.fieldnames if c in METRIC_ID]
            for r in reader:
                r["tags"] = dict_subset(r["tags"], lambda k: k not in METRIC_DROP_TAGS)
                key = tuple(r[c] for c in cols)
                if key not in out:
                    # 合併後的附帶資訊不適用 metrics(§8.4),count 與時間留空
                    out[key] = Chunk("metrics", key, lines(cols, r), r.get("namespace", ""))
    return list(out.values())


# §7 objects(§8.3 objects、§8.4 objects)

LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"
OBJ_LIMIT = 16_384  # §8.4 補充規則:門檻與每段最長都是 16,384 字元
OBJ_OVERLAP = 1_024  # §8.4 補充規則:相鄰重疊 1,024 字元(TA 估)


def dumps(o) -> str:
    # 原檔 Body 是緊湊 JSON;鍵照原順序(dict 本來就保序),中文等字元不轉 \u 碼。
    # 原檔(Go 輸出)把 < > & 寫成 \u003c 等跳脫碼,這裡寫成字元本身(TA 決定,Calvin 授權,2026-10-09)
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def go_escape(s: str) -> str:
    """把 dumps 的輸出轉回 Go 的跳脫寫法,只用來檢查「除了這幾個字元,重新序列化沒動到別的」。"""
    for ch, esc in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        s = s.replace(ch, esc)
    return s


def windows(s: str) -> list[str]:
    """按長度切開、相鄰重疊。"""
    if len(s) <= OBJ_LIMIT:
        return [s]
    out, i, step = [], 0, OBJ_LIMIT - OBJ_OVERLAP
    while True:
        out.append(s[i : i + OBJ_LIMIT])
        if i + OBJ_LIMIT >= len(s):
            return out
        i += step


def split_object(o: dict) -> list[tuple[str, str]]:
    """超長的版本 → [(欄位路徑, 段的內容)]。§8.4 objects + 補充規則「objects 按長度切」。"""
    scalar = [k for k, v in o.items() if not isinstance(v, dict | list)]
    segs: list[tuple[str, str]] = []
    for k, v in o.items():
        if k in scalar:
            continue
        if k == "metadata":
            # 第一層的單一值欄位(例 apiVersion)不自成一段,併進 metadata 那段
            segs.append(("metadata", dumps({x: o[x] for x in o if x in scalar or x == k})))
        elif k == "data" and o.get("kind") == "ConfigMap" and len(dumps({k: v})) > OBJ_LIMIT:
            segs += [(f"data.{dk}", dumps({k: {dk: dv}})) for dk, dv in v.items()]
        else:
            segs.append((k, dumps({k: v})))
    out = []
    for path, content in segs:
        parts = windows(content)
        for i, part in enumerate(parts, 1):
            out.append((path if len(parts) == 1 else f"{path} ({i}/{len(parts)})", part))
    return out


def chunk_objects(scn: Path) -> tuple[list[Chunk], dict]:
    """回傳 (塊, 統計)。

    統計:列數、版本數、超長版本數;reserialize_escape_only = 重新序列化後只差在 < > & 寫法的列數,
    reserialize_other = 連轉回 Go 跳脫寫法都還對不上原字串的列數(應為 0,不是 0 代表動到了值)。
    """
    items = []
    stats = {"rows": 0, "reserialize_escape_only": 0, "reserialize_other": 0}
    for r in read_tsv(scn / "k8s_objects_raw.tsv"):
        stats["rows"] += 1
        o = json.loads(r["Body"])
        d = dumps(o)
        if d != r["Body"]:
            stats[
                "reserialize_escape_only" if go_escape(d) == r["Body"] else "reserialize_other"
            ] += 1
        md = o.get("metadata", {})
        # §8.3 objects:Body 裡丟 CRD 的 spec、metadata.generation、last-applied-configuration
        if o.get("kind") == "CustomResourceDefinition":
            o.pop("spec", None)
        md.pop("generation", None)
        md.get("annotations", {}).pop(LAST_APPLIED, None)
        key = (
            o.get("kind", ""),
            md.get("namespace", ""),
            md.get("name", ""),
            md.get("resourceVersion", ""),
        )
        items.append((norm_time(r["Timestamp"]), key, o, md.get("namespace", "")))

    versions = merge("objects", [(t, k, "", ns) for t, k, _, ns in items])
    first: dict[tuple, dict] = {}
    for t, k, o, _ in sorted(items, key=lambda x: x[0]):
        first.setdefault(k, o)

    out = []
    stats |= {"versions": len(versions), "long_versions": 0}
    for v in versions:
        o = first[v.key]
        text = dumps(o)
        if len(text) <= OBJ_LIMIT:
            out.append(Chunk("objects", v.key, text, v.namespace))
            continue
        stats["long_versions"] += 1
        kind, ns, name, rv = v.key
        for path, content in split_object(o):
            # 每段開頭加 kind、namespace、name、resourceVersion 與欄位路徑,
            # 讓 §8.2 取得出 (kind, name)
            head = (
                f"kind: {kind}\nnamespace: {ns}\nname: {name}\nresourceVersion: {rv}\npath: {path}"
            )
            out.append(Chunk("objects", v.key + (path,), f"{head}\n{content}", v.namespace))
    return out, stats


# §8 一個場景全部切完

SOURCES = ("events", "traces", "logs", "alerts", "metrics", "objects")


def chunk_scenario(scn: Path) -> tuple[list[Chunk], dict]:
    chunks: list[Chunk] = []
    for fn in (chunk_events, chunk_traces, chunk_logs, chunk_alerts, chunk_metrics):
        chunks += fn(scn)
    obj, stats = chunk_objects(scn)
    return chunks + obj, stats
