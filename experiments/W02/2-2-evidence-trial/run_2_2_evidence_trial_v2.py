"""作業 2-2 第二輪|第一關放寬成「因果鏈上的實體」,規則禁止場景字面值。

問題:第一輪只比對根因實體的名字,症狀側的證據全被擋掉;規則也含 adFailure、on 這種
從答案檔照抄的字面值。這輪只改兩件事:
  1. 第一關 = 因果鏈上所有實體(root_cause 那個 group + propagations 裡的 source/target),
     各自用 groups 裡的 filter(re.search,任一條比得上);沒有 filter 用 id 子字串(不分大小寫)。
  2. 規則不准含任何場景字面值(實體名、flag 名、設定鍵名);程式最後會自動檢查。

輸入(純讀檔,不打 API、不跑 embedding、不連資料庫):
  - 第一輪的 run_2_2_evidence_trial.py(同資料夾 import:SCENARIO/SOURCES/sample_source,
    語料同抽樣同種子,總塊數必須是 10,895,不等就停)
  - Scenario-5/ground_truth.yaml(只用來找實體與寫報告,內容只寫進 out/)
  - out/results.json(第一輪的數字,只用在對照)
標註(ANNOTATIONS)是人工看過原文後寫死在本檔的,鍵是 (檔名, 行號)。

跑法:
    uv run python experiments/W02/2-2-evidence-trial/run_2_2_evidence_trial_v2.py
輸出:out/report_v2.md、out/results_v2.json
"""

# ruff: noqa: E501
import inspect
import json
import random
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import run_2_2_evidence_trial as v1  # noqa: E402  (同資料夾的第一輪)

OUT = Path(__file__).parent / "out"
SAMPLE_SEED = 20260930
PER_SOURCE_SAMPLE = 5
MAX_SAMPLE = 30
TRUNC = 800

# §3 事故描述:重用第一輪編號(F1.. / A1.. / P1..),由第一輪的 incident_items 產生


# §4 人工標註(看過原文後寫的;鍵 = (檔名, 行號);值 = (標籤, 理由, 支持哪條))
N = "-"
ANNOTATIONS = {
    ("otel_logs_raw.tsv", 78147): ("否", "kafka 的 INFO 日誌「Rolled new log segment」,正常運作", N),
    ("otel_logs_raw.tsv", 67822): ("否", "shipping 的 INFO 日誌「Requesting quote」,正常運作", N),
    ("otel_logs_raw.tsv", 108972): ("否", "這塊是 envoy access log 的尾段(多行紀錄的續行),只有 url.path 與 upstream.cluster 等欄位,本塊沒有狀態碼或錯誤字樣", N),
    ("otel_logs_raw.tsv", 38238): ("否", "load-generator 的 INFO「adding 1 of product … to cart」,正常運作", N),
    ("otel_logs_raw.tsv", 116120): ("否", "load-generator 的 INFO「User getting ads for category」,只是請求動作紀錄,沒有錯誤", N),
    ("otel_traces_raw.tsv", 105073): ("否", "frontend 呼叫 GetProduct 的 span,StatusCode Unset、rpc.grpc.status_code 0", N),
    ("otel_traces_raw.tsv", 226520): ("否", "checkout 呼叫 GetProduct 的 span,StatusCode Unset、grpc status 0", N),
    ("otel_traces_raw.tsv", 176444): ("否", "cart 的 GetCart span,http 狀態碼 200、StatusCode Unset", N),
    ("otel_traces_raw.tsv", 253120): ("否", "product-catalog GetProduct span,grpc status 0,事件「Product Found」", N),
    ("otel_traces_raw.tsv", 254415): ("否", "product-catalog GetProduct span,grpc status 0,事件「Product Found」", N),
    ("k8s_events_raw.tsv", 661): ("否", "event type Normal、reason Pulled(shipping pod),type 欄位 DELETED 是 watch 動作不是故障", N),
    ("k8s_events_raw.tsv", 430): ("否", "event type Normal、reason SuccessfulCreate(email replicaset 建 pod)", N),
    ("k8s_events_raw.tsv", 843): ("否", "event type Normal、reason Pulled(17:47:34,ad 系列 pod 拉映像檔);時間貼近設定變更但內容本身是正常事件", N),
    ("k8s_events_raw.tsv", 24): ("否", "event type Normal、reason SuccessfulCreate(kube-system 的 StatefulSet)", N),
    ("k8s_events_raw.tsv", 53): ("否", "event type Normal、reason ExternalProvisioning(kube-system 等待磁碟),與應用無關", N),
    ("k8s_objects_raw.tsv", 2245): ("否", "CustomResourceDefinition 快照(原長 246,457 字元),conditions 是 Established True,正常", N),
    ("k8s_objects_raw.tsv", 2821): ("否", "ClusterRole(RBAC 規則)快照,與故障現象無關", N),
    ("k8s_objects_raw.tsv", 3004): ("否", "CustomResourceDefinition 快照(原長 246,457 字元),Established True,正常", N),
    ("k8s_objects_raw.tsv", 1127): ("否", "ClusterRole(otel collector 的 RBAC 規則)快照,與故障現象無關", N),
    ("k8s_objects_raw.tsv", 1881): ("否", "VolumeAttachment 快照,status.attached true,正常", N),
    ("metrics/service_frontend_raw.tsv", 21588): ("拿不準", "frontend 的 span 指標:duration histogram 的 bucket,span_name tcp.connect、span_kind INTERNAL,label 有 status_code STATUS_CODE_ERROR;有錯誤狀態的 span,但是延遲分布的一格,不是請求錯誤率,也看不出是不是事故造成", "F1(弱)"),
    ("metrics/pod_recommendation-5f45f75855-fg87f_raw.tsv", 7346): ("否", "recommendation pod 的 container_memory_usage_bytes 一個取樣值,正常量測", N),
    ("metrics/pod_otel-collector-564d9c7987-chphh_raw.tsv", 37603): ("否", "otel-collector pod 的 container_memory_working_set_bytes 一個取樣值,正常量測", N),
    ("metrics/pod_accounting-58d4b6c958-jhgbl_raw.tsv", 11849): ("否", "accounting pod 的 CPU throttled periods 計數器(567),與請求錯誤無關", N),
    ("metrics/service_frontend_raw.tsv", 48306): ("是", "frontend 的 span 指標 calls_total = 82,span_kind SERVER、span_name POST,label status_code STATUS_CODE_ERROR(17:56,設定變更之後):frontend 有伺服端請求以錯誤狀態結束", "F1,A1,P4"),
    ("alerts/alerts_at_2025-12-15T17-35-58.847937.json", 22): ("否", "alert JSON 的一行 description,內容是「這個告警永遠 firing,用來確認告警管線活著」,不是故障", N),
    ("alerts/alerts_at_2025-12-15T17-56-58.851024.json", 64): ("否", "同上,告警管線心跳的 description", N),
    ("alerts/alerts_at_2025-12-15T17-40-58.848558.json", 108): ("否", "alert JSON 的一行 `\"container\": \"frontend\",`,這一行只有 label 值,沒有錯誤字樣;同一個 alert 物件上下文是 CPUThrottlingHigh,severity info,也不是錯誤率", N),
    ("alerts/alerts_at_2025-12-15T17-44-58.849025.json", 39): ("否", "alert JSON 的一行 `\"service_name\": \"ad\",`,這一行沒有錯誤字樣;同一個 alert 物件的上下文是錯誤率告警(相鄰的 description 行寫 Request error rate … above 0),但那是別的行,本塊本身看不出", N),
    ("alerts/alerts_at_2025-12-15T17-59-58.851462.json", 69): ("否", "告警管線心跳的 description", N),
}
HARD = {
    ("metrics/service_frontend_raw.tsv", 21588): "拿不準:有 STATUS_CODE_ERROR 但是 duration bucket、內部 tcp.connect span;判準說「錯誤狀態」算故障現象,但它不是請求層錯誤率,也不知道是不是背景雜訊。",
    ("metrics/service_frontend_raw.tsv", 48306): "判「是」也有保留:區塊只顯示 frontend 有 82 個錯誤狀態的 POST,沒有跟事故前的基準比,所以只能說「顯示錯誤現象」,不能說「顯示異常增加」。",
    ("alerts/alerts_at_2025-12-15T17-44-58.849025.json", 39): "告警檔一行一塊,切碎了物件:真正有證據力的錯誤率告警被拆成 alertname、service_name、description 多行,只有含 description 的那行看得到錯誤字樣;本塊是沒有訊號的那行,判「否」是依塊內容,但整個 alert 物件其實是症狀側證據。",
    ("alerts/alerts_at_2025-12-15T17-40-58.848558.json", 108): "同上:切碎後只剩 `container: frontend` 一行,內容是否相關要看同一物件的鄰行。",
    ("otel_logs_raw.tsv", 108972): "多行日誌紀錄被行切開,這塊是續行,狀態碼在前一行(不在本塊)。",
}


# §5 規則(禁止場景字面值;只看奇數半寫成)。判定 = 任一條成立即「證據」;前提 = 這塊已通過 §2 第一關
def _fields(text: str) -> list[str]:
    return text.split("\t")


def rule_log_severity(source: str, text: str) -> bool:
    """R1:logs,severity 欄(第 6 欄)是 error / fatal / critical / warn 系列。理由:日誌的故障現象最通用的訊號就是 severity;奇數半 3 塊 logs 全是 INFO,規則不含來自奇數半的正例。"""
    if source != "logs":
        return False
    f = _fields(text)
    return len(f) > 8 and f[5].strip().lower() in {"error", "err", "fatal", "critical", "warn", "warning"}


def rule_span_error(source: str, text: str) -> bool:
    """R2:traces,span 的 StatusCode 欄(第 14 欄)是 Error,或 grpc 狀態碼不是 0,或 http 狀態碼 >= 500。理由:span 錯誤的通用標記;奇數半 2 塊 traces 全是正常(Unset / status 0 / 200),不含來自奇數半的正例。"""
    if source != "traces":
        return False
    f = _fields(text)
    if len(f) > 13 and f[13] == "Error":
        return True
    g = re.search(r"'rpc\.grpc\.status_code': '(\d+)'", text)
    if g and g.group(1) != "0":
        return True
    h = re.search(r"'http\.(?:response\.)?status_code': '(\d+)'", text)
    return bool(h) and int(h.group(1)) >= 500


def rule_metric_error_label(source: str, text: str) -> bool:
    """R3:metrics,label 含 status_code 為 STATUS_CODE_ERROR(OpenTelemetry 通用的 span 錯誤狀態值)。理由:奇數半 #25 判「是」的依據就是這個 label;#21 也有這個 label(拿不準)。"""
    return source == "metrics" and "'status_code': 'STATUS_CODE_ERROR'" in text


def rule_event_warning(source: str, text: str) -> bool:
    """R4:events,K8s event 的 type 是 Warning。理由:K8s 用 Warning 標非正常事件;奇數半 3 塊 events 全是 Normal,不含來自奇數半的正例。"""
    return source == "events" and '""type"":""Warning""' in text


def rule_alert_error_word(source: str, text: str) -> bool:
    """R5:alerts,這一行(alert JSON 一行一塊)含 error 或 exception(不分大小寫)。理由:告警描述用通用字表達錯誤;奇數半 alerts 2 塊(1 塊心跳、1 塊只有 label 值)都不含這兩個字。"""
    return source == "alerts" and re.search(r"error|exception", text, re.I) is not None


RULES = [
    ("R1", rule_log_severity),
    ("R2", rule_span_error),
    ("R3", rule_metric_error_label),
    ("R4", rule_event_warning),
    ("R5", rule_alert_error_word),
]


def is_evidence(source: str, text: str) -> bool:
    return any(fn(source, text) for _, fn in RULES)


def fired(source: str, text: str) -> list[str]:
    return [n for n, fn in RULES if fn(source, text)]


def scenario_literals(content: dict) -> set[str]:
    """從 ground_truth 收集場景字面值:group id / filter / 實體名 / 告警 id / 被改設定的 JSON 鍵名。"""
    lit: set[str] = set()
    for g in content["groups"]:
        lit.add(g["id"])
        lit.update(g.get("filter", []))
    for f in content["fault"]:
        lit.add(f["entity"]["name"])
        if "changed" in f:
            try:
                lit.update(json.loads(f["changed"]["to"])["flags"].keys())
            except (json.JSONDecodeError, KeyError):
                pass
            lit.add(str(f["changed"]["element"]).replace("\\", ""))
    lit.update(a["id"] for a in content["alerts"])
    lit.add("demo.flagd.json")
    return {x for x in lit if x}


def literal_hits(lits: set[str]) -> list[tuple[str, str]]:
    """整個規則函式原始碼(含 docstring)逐一用「單詞邊界」比對字面值。"""
    hits = []
    for name, fn in RULES:
        src = inspect.getsource(fn)
        for x in lits:
            if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(x) + r"(?![A-Za-z0-9_-])", src, re.I):
                hits.append((name, x))
    return hits


def cell(s: str, n: int = 100) -> str:
    s = s.replace("\t", "⇥").replace("|", "\\|").replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def main() -> None:
    # §1 語料(同第一輪)
    corpus = {s: v1.sample_source(f) for s, f in v1.SOURCES.items()}
    total = sum(len(r) for r in corpus.values())
    if total != v1.EXPECTED_TOTAL:
        sys.exit(f"停:總塊數 {total},預期 {v1.EXPECTED_TOTAL},差 {total - v1.EXPECTED_TOTAL}")

    # §2 因果鏈實體
    raw = yaml.safe_load(open(v1.SCENARIO / "ground_truth.yaml"))
    content = raw.get("spec", raw)
    groups = {g["id"]: g for g in content["groups"]}
    ids: list[str] = [g["id"] for g in content["groups"] if g.get("root_cause")]
    for p in content["propagations"]:
        for k in ("source", "target"):
            if p[k] not in ids:
                ids.append(p[k])
    conds: dict[str, str] = {}
    matchers = {}
    missing = []
    for i in ids:
        g = groups.get(i)
        if g is None:
            missing.append(i)
            continue
        if g.get("filter"):
            pats = [re.compile(p) for p in g["filter"]]
            conds[i] = f"re.search 任一條:{g['filter']}"
            matchers[i] = (lambda t, pats=pats: any(p.search(t) for p in pats))
        else:
            conds[i] = f"id 子字串(不分大小寫):{i}"
            matchers[i] = (lambda t, i=i: i.lower() in t.lower())
    matrix = {i: {s: sum(1 for r in rows if matchers[i](r[2])) for s, rows in corpus.items()} for i in matchers}
    cands = {}
    for s, rows in corpus.items():
        cands[s] = []
        for f, ln, t in rows:
            hit = [i for i in matchers if matchers[i](t)]
            if hit:
                cands[s].append((s, f, ln, t, hit))

    # §3 抽樣
    rng = random.Random(SAMPLE_SEED)
    sample = []
    for c in cands.values():
        sample += c if len(c) <= PER_SOURCE_SAMPLE else rng.sample(c, PER_SOURCE_SAMPLE)
    sample = sample[:MAX_SAMPLE]
    for s, f, ln, t, h in sample:
        if (f, ln) not in ANNOTATIONS:
            sys.exit(f"停:缺標註 {f}:{ln}")
    items = v1.incident_items(content)

    # §5 奇偶分半
    odd = [b for i, b in enumerate(sample, 1) if i % 2 == 1]
    even = [b for i, b in enumerate(sample, 1) if i % 2 == 0]

    def agree(blocks: list) -> tuple[int, int, list, list]:
        ok, bad, skipped = 0, [], []
        for s, f, ln, t, h in blocks:
            lab = ANNOTATIONS[(f, ln)][0]
            pred = "是" if is_evidence(s, t) else "否"
            if lab == "拿不準":
                skipped.append((f, ln, pred))
            elif pred == lab:
                ok += 1
            else:
                bad.append((f, ln, pred, lab, fired(s, t)))
        return ok, len(blocks) - len(skipped), bad, skipped

    ok_e, n_e, bad_e, skip_e = agree(even)
    ok_o, n_o, bad_o, skip_o = agree(odd)
    ok_a, n_a, bad_a, skip_a = agree(sample)
    dist_e = {k: sum(1 for b in even if ANNOTATIONS[(b[1], b[2])][0] == k) for k in ("是", "否", "拿不準")}
    dist_all = {k: sum(1 for b in sample if ANNOTATIONS[(b[1], b[2])][0] == k) for k in ("是", "否", "拿不準")}
    all_c = [b for c in cands.values() for b in c]
    rule_yes = {s: sum(1 for b in c if is_evidence(b[0], b[3])) for s, c in cands.items()}
    rule_by = {s: {n: sum(1 for b in c if fn(b[0], b[3])) for n, fn in RULES} for s, c in cands.items()}
    lits = scenario_literals(content)
    lit_hits = literal_hits(lits)

    v1res = json.loads((OUT / "results.json").read_text())
    cand_n = {s: len(c) for s, c in cands.items()}

    OUT.mkdir(exist_ok=True)
    (OUT / "results_v2.json").write_text(json.dumps({
        "total": total, "entities": ids, "missing": missing, "matrix": matrix, "candidates": cand_n,
        "sample_n": len(sample), "even_agree": [ok_e, n_e], "even_dist": dist_e,
        "odd_agree": [ok_o, n_o], "all30_agree": [ok_a, n_a], "all30_dist": dist_all,
        "rule_yes": rule_yes, "rule_by": rule_by, "literal_hits": lit_hits,
    }, ensure_ascii=False, indent=1))

    L: list[str] = []
    w = L.append
    w("# 作業 2-2 第二輪報告:第一關放寬成因果鏈實體 + 規則禁止場景字面值\n")
    w("## §1 語料驗收\n")
    w(f"總塊數 {total}(預期 {v1.EXPECTED_TOTAL},相符;同第一輪的抽樣與種子)。各來源:" + "、".join(f"{s} {len(r)}" for s, r in corpus.items()) + "。\n")
    w("事故描述清單沿用第一輪報告 §2.2 的編號(由第一輪程式的 `incident_items` 產生):\n")
    for k, v in items:
        w(f"- {k}:{v}")
    w("\n## §2 因果鏈實體與比對條件\n")
    w("實體 = `groups` 裡 `root_cause: true` 的 group,加上 `propagations` 裡所有出現在 `source` 或 `target` 的 group id(依出現順序):\n")
    w("| 實體 | kind | 比對條件 |\n|---|---|---|")
    for i in ids:
        w(f"| {i} | {groups[i]['kind'] if i in groups else '?'} | {conds.get(i, '找不到')} |")
    w("\n在 `groups` 找不到的實體:" + ("、".join(missing) if missing else "無(全部找得到)。"))
    w("\n注意:filter 是 `re.search`,即「子字串」語意。`ad` 這種短 filter 會比中 `load`、`header`、`address` 之類含 ad 的字(見 §3 的數字與 §4 比中處)。這是 ground_truth 給的條件,本輪照用、不改。\n")
    w("## §3 實體 × 來源的候選塊數\n")
    w("| 實體 | " + " | ".join(corpus) + " |\n|---|" + "---|" * len(corpus))
    for i in matchers:
        w(f"| {i} | " + " | ".join(str(matrix[i][s]) for s in corpus) + " |")
    w("| **合計(一塊比中多個實體只算一次)** | " + " | ".join(f"**{cand_n[s]}**" for s in corpus) + " |")
    w(f"\n各來源候選塊數合計 {sum(cand_n.values())} / {total}。候選佔語料比例:" + "、".join(f"{s} {cand_n[s]}/{len(corpus[s])}" for s in corpus) + "。")
    w(f"\n抽樣(種子 {SAMPLE_SEED},每來源最多 {PER_SOURCE_SAMPLE}、總共最多 {MAX_SAMPLE}):實際抽到 {len(sample)} 塊(每種來源候選都 ≥ 5,所以每來源 5 塊)。\n")
    w("## §4 30 塊標註表\n")
    w("標準(沿用第一輪):這塊內容本身有沒有顯示事故描述裡的 condition / effect / 故障現象(錯誤狀態、例外、錯誤率告警、設定被改);只是提到實體、內容是正常運作 → 否。序號奇數 = 寫規則用,偶數 = 保留組。\n")
    w("| # | 來源 | 檔名:行號 | 比中實體 | 原文節錄(前 100 字元) | 是否證據 | 理由 | 支持哪條 |\n|---|---|---|---|---|---|---|---|")
    for n, (s, f, ln, t, h) in enumerate(sample, 1):
        a, why, sup = ANNOTATIONS[(f, ln)]
        w(f"| {n} | {s} | {f}:{ln} | {'、'.join(h)} | {cell(t)} | {a} | {cell(why, 300)} | {sup} |")
    w("\n### §4.1 比中處(每塊第一個實體比中的前後文,說明為什麼會進候選)\n")
    for n, (s, f, ln, t, h) in enumerate(sample, 1):
        ex = []
        for i in h[:2]:
            for p in [re.compile(x) for x in groups[i]["filter"]]:
                m = p.search(t)
                if m:
                    ex.append(f"{i}:`…{t[max(0, m.start() - 15):m.end() + 15]}…`".replace("\t", "⇥").replace("\n", " "))
                    break
        w(f"- #{n}({f}:{ln}):" + ";".join(ex))
    w("\n### §4.2 原文(超過 800 字元截斷,標明原長)\n")
    for n, (s, f, ln, t, h) in enumerate(sample, 1):
        cut = f"(截斷,原長 {len(t)} 字元)" if len(t) > TRUNC else ""
        w(f"**#{n} {f}:{ln}**{cut}\n\n```\n{t[:TRUNC]}\n```\n")
    w("標註是看過完整原文寫的;截斷的塊,判斷依據可能在截斷處之後。\n")
    w("## §5 難判的塊與原因\n")
    for (f, ln), why in HARD.items():
        idx = [n for n, b in enumerate(sample, 1) if (b[1], b[2]) == (f, ln)]
        w(f"- #{idx[0] if idx else '?'} {f}:{ln}:{why}")
    w("\n## §6 規則清單與理由(只看奇數半寫成;判定 = 任一條成立,前提是這塊已通過 §2 第一關)\n")
    w("奇數半 15 塊的標註分布:是 " + str(sum(1 for b in odd if ANNOTATIONS[(b[1], b[2])][0] == '是')) + "、否 " + str(sum(1 for b in odd if ANNOTATIONS[(b[1], b[2])][0] == '否')) + "、拿不準 " + str(sum(1 for b in odd if ANNOTATIONS[(b[1], b[2])][0] == '拿不準')) + "。只有 1 個正例(#25),所以 R1、R2、R4、R5 不是從奇數半的正例長出來的,而是用任務指定「允許」的通用錯誤訊號(severity、HTTP/gRPC 狀態碼、K8s Warning、error/exception 字)為每種來源各寫一條;它們在奇數半上只被驗證「不會誤報」,沒被驗證「抓得到」。k8s_objects 沒有規則(奇數半 2 塊全是正常快照,也找不到一個通用、機械的訊號),所以 k8s_objects 一律判「否」。\n")
    for name, fn in RULES:
        d = (fn.__doc__ or "").strip()
        w(f"- **{name}**:{d}")
    w("\n### §6.1 逐條說明為什麼不含場景字面值\n")
    for name, why in [
        ("R1", "只用來源種類(logs)、欄位位置(severity 欄)與通用的 severity 字。"),
        ("R2", "只用來源種類(traces)、StatusCode 欄位、gRPC / HTTP 通用狀態碼(0、>= 500)。"),
        ("R3", "只用來源種類(metrics)與 OpenTelemetry 通用的 status_code 錯誤值,沒有任何實體名。"),
        ("R4", "只用來源種類(events)與 K8s event type `Warning`。"),
        ("R5", "只用來源種類(alerts)與通用字 error / exception。"),
    ]:
        w(f"- **{name}**:{why}")
    w(f"\n自動檢查:程式從 ground_truth 收集 {len(lits)} 個場景字面值(group id、filter、實體名、告警 id、被改設定的 JSON 鍵名),用單詞邊界逐一比對五條規則的原始碼(含 docstring),命中 {len(lit_hits)} 個" + ("。" if not lit_hits else f":{lit_hits}。"))
    w("限制:這個檢查只擋「字面值原樣出現」;`STATUS_CODE_ERROR`、`Warning` 這類通用值是任務明文允許的。R5 的 `error` 是通用字,不是告警 id,但它和告警 id 內含的字相同,讀者要知道這點。\n")
    w("## §7 一致率\n")
    w(f"### §7.1 偶數半(保留組,{len(even)} 塊)\n")
    w(f"標註分布:是 {dist_e['是']}、否 {dist_e['否']}、拿不準 {dist_e['拿不準']}。")
    w(f"\n**一致 {ok_e} / {n_e}**。不一致:" + ("無。" if not bad_e else str(bad_e)))
    if dist_e["是"] == 0 or dist_e["否"] == 0:
        w("\n**偶數半標註全是同一個標籤(否),所以這個一致率沒有鑑別力**:永遠回答「否」的規則也是同樣的成績。")
    w(f"\n### §7.2 全部 {len(sample)} 塊(含寫規則用的奇數半)\n")
    w(f"標註分布:是 {dist_all['是']}、否 {dist_all['否']}、拿不準 {dist_all['拿不準']}(拿不準不計入分母)。")
    w(f"\n**一致 {ok_a} / {n_a}**;其中奇數半 {ok_o} / {n_o}。不一致:" + ("無。" if not bad_a else str(bad_a)))
    if skip_a:
        w("\n拿不準的塊,規則的判定:" + "、".join(f"{f}:{ln} → {p}" for f, ln, p in skip_a) + "(不計入一致率)。")
    w("\n這個數字含奇數半,是規則的「訓練成績」,不是保留成績;而且全體只有 1 個「是」,即使全對,鑑別「是」的能力只被一個例子驗證。\n")
    w("## §8 全部候選塊的規則判定\n")
    w("| 來源 | 候選塊 | 規則判「是」 | 各規則命中(R1..R5) |\n|---|---|---|---|")
    for s, c in cands.items():
        w(f"| {s} | {len(c)} | {rule_yes[s]} | " + "、".join(f"{n}={v}" for n, v in rule_by[s].items() if v) + " |")
    w(f"| 合計 | {len(all_c)} | {sum(rule_yes.values())} | |")
    w("\n這些「是」沒有逐塊人工檢查,只有抽樣的 30 塊有標註;所以判「是」的塊裡有多少是真證據是**不知道**(缺:人工標註)。\n")
    w("## §9 與第一輪對照\n")
    v1c = sum(v1res["candidates"].values())
    w("| 項目 | 第一輪 | 第二輪 |\n|---|---|---|")
    w(f"| 第一關 | 只比根因實體名字 | 因果鏈 {len(ids)} 個實體 |")
    w(f"| 候選塊數 | {v1c}(全在 k8s_objects) | {len(all_c)}(" + "、".join(f"{s} {cand_n[s]}" for s in corpus) + ") |")
    w(f"| 判成證據的塊數 | {sum(v1res['rule_yes'].values())} | {sum(rule_yes.values())}(" + "、".join(f"{s} {rule_yes[s]}" for s in corpus if rule_yes[s]) + ") |")
    w("| 規則含場景字面值 | 是(flagd-config、adFailure、on) | " + ("否(自動檢查 0 命中)" if not lit_hits else f"有 {lit_hits}") + " |")
    w("| 標註樣本 | 規定 5 塊 + 補標 5 塊 | 30 塊 |")
    w("| 保留組一致率 | 2 / 2(全為否) | " + f"{ok_e} / {n_e}(全為否)" + " |")
    w("\n## §10 結論\n")
    w(CONCLUSION.format(
        cand=len(all_c), tot=total, ok_e=ok_e, n_e=n_e, ok_a=ok_a, n_a=n_a,
        ryes=sum(rule_yes.values()),
        ry=", ".join(f"{s} {rule_yes[s]}/{cand_n[s]}" for s in corpus),
    ))
    w("\n## §11 限制\n")
    w("- 單一場景(Scenario-5),結論不能推到別的場景。")
    w("- 標註者與寫規則者是同一個(Claude),標準沒有第二個人驗過;偶數半不是獨立的第二個人。")
    w("- 樣本 30 塊,只有 1 個「是」、1 個「拿不準」,對「規則抓不抓得到證據」幾乎沒有驗證力。")
    w("- 每來源抽的 5 塊來自各自很大的候選池(見 §3),抽樣只覆蓋池子的一小部分;§8 的規則判定沒有逐塊人工核對。")
    w("- 標註用「塊內容本身」為準;alerts 一行一塊、logs 多行紀錄被切開,會讓塊內容不完整(§5)。")
    (OUT / "report_v2.md").write_text("\n".join(L) + "\n")
    print(f"total={total} cands={cand_n} sample={len(sample)}")
    print(f"even {ok_e}/{n_e} dist={dist_e}; all {ok_a}/{n_a} dist={dist_all}; odd {ok_o}/{n_o}")
    print(f"rule_yes={rule_yes} literal_hits={lit_hits} missing={missing}")


CONCLUSION = """\
### §10.1 放寬後症狀側的證據進得來嗎

**進得來,但代價是分母變成語料的大宗(約七成)。** 候選塊從第一輪的 10 塊變成 {cand} / {tot} 塊,其中 `ad`(子字串)一個實體就比中 events 894/895、k8s_objects 1999/2000(§3),`frontend` 比中 logs、traces、metrics、alerts 的一部分。有真證據的來源(metrics 的 frontend 錯誤狀態、alerts 的錯誤率告警)確實進了候選(§4 #25;alerts 見 §5),所以「症狀側被擋掉」的問題解了;但同時 30 塊樣本裡 28 塊是「否」(§7.2),名稱比對這一關在大多數來源已經不擋任何東西。

### §10.2 通用規則分得開證據與雜訊嗎

**這次的資料只能說「沒有反例」,不能說「分得開」。** 偶數半 {ok_e} / {n_e} 但偶數半全是「否」,沒有鑑別力(§7.1);全部 30 塊 {ok_a} / {n_a}(含寫規則的奇數半),而且只有 1 個「是」。規則在全部候選塊判「是」共 {ryes} 塊(各來源:{ry},§8),這些沒逐塊人工檢查,所以規則的精準度與召回率都是**不知道**。能確定的只有:五條規則沒有場景字面值(§6.1),而且在 30 塊標註樣本上沒有對「否」的塊誤報。

### §10.3 哪類塊規則分不開

- **切碎的塊**:alerts 一行一塊、logs 多行紀錄被切成續行(§5),證據力在同一物件的鄰行,單塊規則看不到。alerts 的 R5 靠「這一行含 error」,同一個錯誤率告警的 alertname 行與 description 行會各算一塊,而 `service_name` 那行判不出來。
- **K8s 物件快照**:k8s_objects 沒有通用機械訊號,一律判「否」;第一輪那類「設定本體被改」的證據(第一輪判 2 塊「是」),在這輪的規則下會被漏掉,而且無法在不使用設定鍵名的前提下補回。這是規則目前的已知缺口,不是已驗證的結論(缺:k8s_objects 正例的更多樣本)。
- **有錯誤標記但不是請求層錯誤率**:如 #21 的 duration bucket,是否算證據取決於標準,不是規則技術問題。
- **只有錯誤存在、沒有跟基準比較**:#25 的「82 個錯誤狀態 POST」無從判斷是背景雜訊還是事故造成,單塊規則本質上分不出來。
"""

if __name__ == "__main__":
    main()
