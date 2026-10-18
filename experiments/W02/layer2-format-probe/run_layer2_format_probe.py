# 資料集導覽 §10.4|第二層(OTel demo)另外三種來源與切塊的實測(2026-09-24)。
#
# 來源:Calvin 2026-09-24 委託 TA 做,不屬於任何一週的作業。目的是 W3 之前先知道
# 第二層的格式會不會卡住切塊。結論與數字寫在 [[EvalBench 資料集導覽]] §10.4。
#
# POC(TA 決定的切法,不是 Calvin 作業 2-3 的切法):一筆記錄一塊,log 一行一塊、
# trace 一個 TraceId 一塊。只問一件事:同一支切塊函式,兩層資料各要多寫多少轉接才吃得下。
#
# 輸入:第一層 = 命令列給的場景資料夾;第二層 = 同資料夾 demo_export/
# (demo commit fcedca7,旗標 adFailure,2026-09-24 撈的,demo 狀態無法重現所以存檔)。
#
# 跑法:
#     python3 experiments/W02/layer2-format-probe/run_layer2_format_probe.py \
#         data/ITBench-Lite/snapshots/sre/<版本>/Scenario-105

import ast
import collections
import csv
import itertools
import json
import pathlib
import sys

csv.field_size_limit(sys.maxsize)
SCENARIO = pathlib.Path(sys.argv[1])
DEMO = pathlib.Path(__file__).parent / "demo_export"


# 切塊函式本體:兩層共用
def chunk_log(svc, ts, sev, body, trace_id, pod):
    return {"text": f"[{ts}] {svc} {sev}: {body}", "service": svc,
            "trace_id": trace_id, "pod": pod}


# spans: list of (service, span 名, 奈秒, 狀態, pod 名)
def chunk_trace(trace_id, spans):
    lines = [f"{s} {n} {d / 1e6:.1f}ms {st}" for s, n, d, st, _ in spans]
    return {"text": f"trace {trace_id}\n" + "\n".join(lines), "trace_id": trace_id,
            "services": sorted({s for s, *_ in spans}),
            "pods": sorted({p for *_, p in spans if p})}


# 第一層轉接:ITBench TSV(Body 含換行,一定走 CSV 解析器)
def l1_logs(n=2000):
    with open(SCENARIO / "otel_logs_raw.tsv", newline="") as f:
        for r in itertools.islice(csv.DictReader(f, delimiter="\t"), n):
            pod = ast.literal_eval(r["ResourceAttributes"]).get("k8s.pod.name")
            yield chunk_log(r["ServiceName"], r["Timestamp"], r["SeverityText"],
                            r["Body"], r["TraceId"], pod)


def l1_traces(n=5000):
    groups = collections.defaultdict(list)
    with open(SCENARIO / "otel_traces_raw.tsv", newline="") as f:
        for r in itertools.islice(csv.DictReader(f, delimiter="\t"), n):
            pod = ast.literal_eval(r["ResourceAttributes"]).get("k8s.pod.name")
            groups[r["TraceId"]].append(
                (r["ServiceName"], r["SpanName"], int(r["Duration"]), r["StatusCode"], pod))
    return [chunk_trace(t, s) for t, s in groups.items()]


# 第二層轉接:OpenSearch JSON / Jaeger JSON
def l2_logs():
    for h in json.loads((DEMO / "logs.json").read_text())["hits"]["hits"]:
        s = h["_source"]
        yield chunk_log(s["resource"].get("service.name"), s["@timestamp"],
                        s.get("severity", {}).get("text"), s.get("body"),
                        s.get("traceId"), s["resource"].get("k8s.pod.name"))


def l2_traces():
    out = []
    for t in json.loads((DEMO / "traces_ad.json").read_text())["data"]:
        procs = {k: v["serviceName"] for k, v in t["processes"].items()}
        spans = []
        for sp in t["spans"]:
            tags = {x["key"]: x["value"] for x in sp["tags"]}
            status = "Error" if tags.get("error") else tags.get("otel.status_code", "Unset")
            # Jaeger 的 duration 是微秒,乘 1000 對齊第一層的奈秒
            spans.append((procs[sp["processID"]], sp["operationName"],
                          sp["duration"] * 1000, status, None))
        out.append(chunk_trace(t["traceID"], spans))
    return out


for name, logs, traces in [("L1 Scenario-105", l1_logs, l1_traces),
                           ("L2 demo", l2_logs, l2_traces)]:
    L = list(logs())
    T = traces()
    no_svc = sum(not c["service"] for c in L)
    no_sev = sum(" None:" in c["text"] for c in L)
    with_pod = sum(bool(c["pod"]) for c in L)
    print(f"== {name}: log 塊 {len(L)}(缺 service {no_svc}、缺 severity {no_sev}、"
          f"有 pod 名 {with_pod}); trace 塊 {len(T)}"
          f"(有 pod 名 {sum(bool(c['pods']) for c in T)})")
    print("   log 範例:", next(c["text"] for c in L if c["service"])[:140].replace("\n", " "))
    print("   trace 範例:", T[0]["text"][:160].replace("\n", " | "))
