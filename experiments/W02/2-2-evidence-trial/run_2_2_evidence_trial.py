# 作業 2-2|「名稱比對 + 寫死篩選規則」判證據,寫不寫得出來、跟逐塊判斷一致多少。
#
# 問題:檢索命中率@k 要定義「一塊資料相關」。候選做法是先要求這塊提到根因實體的名字,
# 再用寫死的規則篩出真的是證據的塊。這支只做一個場景(Scenario-5)的小實驗,
# 同一個人(Claude)先當標註者、再當寫規則的人;規則只看奇數半的標註,偶數半當保留組。
#
# 輸入(純讀檔,不打 API、不跑 embedding、不連資料庫):
#   - Scenario-5 的六種來源,抽樣邏輯複製自 2-1(SCENARIO/SOURCES/PER_SOURCE/SEED),
#     總塊數必須是 10,895,不等就停
#   - Scenario-5/ground_truth.yaml(只用來標註與寫報告,內容只寫進 out/)
# 標註(ANNOTATIONS)是人工看過原文後寫死在本檔的,鍵是 (來源, 行號)。
#
# 跑法:
#     uv run python experiments/W02/2-2-evidence-trial/run_2_2_evidence_trial.py
# 輸出:out/report.md、out/results.json

# ruff: noqa: E501
import json
import random
import re
import sys
from pathlib import Path

import yaml

# §1 輸入常數(複製自 2-1,不 import 它)
REPO = Path(__file__).resolve().parents[3]
SCENARIO = REPO / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7/Scenario-5"
SOURCES = {
    "logs": ["otel_logs_raw.tsv"],
    "traces": ["otel_traces_raw.tsv"],
    "events": ["k8s_events_raw.tsv"],
    "k8s_objects": ["k8s_objects_raw.tsv"],
    "metrics": sorted(p.relative_to(SCENARIO).as_posix() for p in SCENARIO.glob("metrics/*.tsv")),
    "alerts": sorted(p.relative_to(SCENARIO).as_posix() for p in SCENARIO.glob("alerts/*.json")),
}
PER_SOURCE = 2000
SEED = 20260924  # 2-1 語料定案日的種子,重建同一份語料
SAMPLE_SEED = 20260929
PER_SOURCE_SAMPLE = 5
EXPECTED_TOTAL = 10_895
TRUNC = 800
OUT = Path(__file__).parent / "out"


def sample_source(files: list[str]) -> list[tuple[str, int, str]]:
    counts = []
    for f in files:
        with open(SCENARIO / f, "rb") as fh:
            counts.append(sum(1 for _ in fh))
    total = sum(counts)
    picked = set(range(total)) if total <= PER_SOURCE else set(
        random.Random(SEED).sample(range(total), PER_SOURCE)
    )
    rows, offset = [], 0
    for f, n in zip(files, counts, strict=True):
        with open(SCENARIO / f, "rb") as fh:
            for i, raw in enumerate(fh):
                if offset + i in picked:
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    rows.append((f, i + 1, text))
        offset += n
    return rows


# §2 事故描述清單(從 ground_truth.yaml 的 fault / alerts / propagations 整理,編號是本實驗自加)
def incident_items(content: dict) -> list[tuple[str, str]]:
    items = []
    for i, f in enumerate(content["fault"], 1):
        e = f["entity"]
        extra = " (changed: data.demo.flagd.json 內 adFailure defaultVariant off -> on)" if "changed" in f else ""
        items.append((f"F{i}", f"{e['kind']} {e['name']}: {f['condition']}{extra}"))
    for i, a in enumerate(content["alerts"], 1):
        items.append((f"A{i}", f"alert {a['id']} on {a['group_id']}: {a['metadata']['description']}"))
    for i, p in enumerate(content["propagations"], 1):
        items.append((f"P{i}", f"{p['source']} -> {p['target']}: 條件「{p['condition']}」=> 效果「{p['effect']}」"))
    return items


# §3 人工標註(看過完整原文後寫的;鍵 = (來源, 行號))。sup = 補充標註,不在規定的抽樣內
# 值 = (是否證據, 理由, 支持哪條)
CM = "ConfigMap flagd-config 本體,data 內 adFailure.defaultVariant 為 on(原文 JSON 內容),managedFields 顯示 17:47:32Z 被更新"
POD = "Pod flagd 快照,只在 volumes 以名字掛載 configMap flagd-config;phase Running、restartCount 0,沒有顯示設定內容或任何故障現象"
RS = "ReplicaSet flagd 快照,只在 pod template 的 volumes 以名字掛載 configMap flagd-config,沒有顯示設定內容或故障現象"
ANNOTATIONS = {
    ("k8s_objects", 2892): ("是", CM, "F3,P1"),
    ("k8s_objects", 3539): ("否", POD + "(17:53:59,變更後的快照,但看不出變更)", "-"),
    ("k8s_objects", 2091): ("否", RS + "(revision 2)", "-"),
    ("k8s_objects", 2092): ("否", RS + "(revision 1)", "-"),
    ("k8s_objects", 1672): ("否", POD + "(17:38:59,早於 17:47:32Z 的變更)", "-"),
}
SUP_ANNOTATIONS = {  # 抽樣沒抽到的另外 5 塊,同一標準補標
    ("k8s_objects", 3491): ("是", CM, "F3,P1"),
    ("k8s_objects", 244): ("否", POD + "(17:28:59)", "-"),
    ("k8s_objects", 900): ("否", POD + "(17:33:59)", "-"),
    ("k8s_objects", 990): ("否", RS + "(revision 1)", "-"),
    ("k8s_objects", 3630): ("否", RS + "(revision 1,17:54:00)", "-"),
}
HARD = {
    ("k8s_objects", 3539): "變更後的 Pod 快照:flagd 是讀這個 ConfigMap 的服務,P1 說 flagd 因此啟用注入,"
    "但 Pod 物件本身看不出來;判「否」是依「內容本身有沒有顯示條件或效果」,不是依它跟根因的關係遠近。",
    ("k8s_objects", 2091): "ReplicaSet 掛載同一個 ConfigMap,關係與 Pod 相同;revision 2 比 revision 1 新,"
    "但兩者掛載寫法一樣,只靠內容分不出誰相關。",
}


# §4 規則(只看奇數半 #1 #3 #5 寫的;全部 AND,目的是讓「只提到名字」的塊掉出去)
def parse_obj(text: str) -> dict | None:
    m = re.search(r'\t"(\{.*\})"\t', text)
    if not m:
        return None
    try:
        return json.loads(m.group(1).replace('""', '"'))
    except json.JSONDecodeError:
        return None


def rule_source(source: str, text: str) -> bool:
    """R1:來源限 k8s_objects。理由:奇數半裡比得上 flagd-config 的塊全在這個來源。"""
    return source == "k8s_objects"


def rule_object_is_configmap(source: str, text: str) -> bool:
    """R2:物件 kind 是 ConfigMap 且 metadata.name 是 flagd-config(物件本身,不是被別的物件引用)。
    理由:奇數半 #3 ReplicaSet、#5 Pod 只是掛載它而判否,#1 是它本體而判是。"""
    obj = parse_obj(text)
    return bool(obj) and obj.get("kind") == "ConfigMap" and obj.get("metadata", {}).get("name") == "flagd-config"


def rule_adfailure_on(source: str, text: str) -> bool:
    """R3:data 裡 adFailure 旗標的 defaultVariant == on。理由:對應 F3/P1 的 condition「adFailure feature flag set」。"""
    obj = parse_obj(text)
    if not obj:
        return False
    try:
        flags = json.loads(obj["data"]["demo.flagd.json"])
        return flags["flags"]["adFailure"]["defaultVariant"] == "on"
    except (KeyError, TypeError, json.JSONDecodeError):
        return False


RULES = [("R1", rule_source, "F3,P1"), ("R2", rule_object_is_configmap, "F3,P1"), ("R3", rule_adfailure_on, "F3,P1")]


def is_evidence(source: str, text: str) -> bool:
    return all(fn(source, text) for _, fn, _ in RULES)


def cell(s: str, n: int = 110) -> str:
    s = s.replace("\t", "⇥").replace("|", "\\|").replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def main() -> None:
    # §1 重建語料
    corpus = {s: sample_source(f) for s, f in SOURCES.items()}
    total = sum(len(r) for r in corpus.values())
    if total != EXPECTED_TOTAL:
        sys.exit(f"停:總塊數 {total},預期 {EXPECTED_TOTAL},差 {total - EXPECTED_TOTAL}")

    # §2 根因實體比對
    raw = yaml.safe_load(open(SCENARIO / "ground_truth.yaml"))
    content = raw.get("spec", raw)
    rc = next(g for g in content["groups"] if g.get("root_cause"))
    if rc.get("filter"):
        pats = [re.compile(p) for p in rc["filter"]]
        def hit(t: str) -> bool:
            return any(p.search(t) for p in pats)
    else:
        def hit(t: str) -> bool:
            return rc["id"].lower() in t.lower()
    cands = {s: [(s, *r) for r in rows if hit(r[2])] for s, rows in corpus.items()}

    # §3 抽樣
    rng = random.Random(SAMPLE_SEED)
    sample = []
    for c in cands.values():
        sample += c if len(c) <= PER_SOURCE_SAMPLE else rng.sample(c, PER_SOURCE_SAMPLE)
    items = incident_items(content)

    # §4 標註表(缺標註的塊直接報錯,不腦補)
    ann = {(s, ln): ANNOTATIONS[(s, ln)] for s, _, ln, _ in sample}
    sup_keys = [(s, ln) for c in cands.values() for s, _, ln, _ in c if (s, ln) in SUP_ANNOTATIONS]
    if len(sup_keys) + len(sample) != sum(len(c) for c in cands.values()):
        sys.exit("補充標註沒有覆蓋所有候選塊")

    # §5 奇偶分半,規則已固定(只看奇數半寫成),這裡只算一致率
    odd = [b for i, b in enumerate(sample, 1) if i % 2 == 1]
    even = [b for i, b in enumerate(sample, 1) if i % 2 == 0]
    def agree(blocks: list, table: dict) -> tuple[int, list]:
        bad, ok = [], 0
        for s, f, ln, t in blocks:
            pred = "是" if is_evidence(s, t) else "否"
            if pred == table[(s, ln)][0]:
                ok += 1
            else:
                bad.append((s, f, ln, pred, table[(s, ln)][0]))
        return ok, bad
    ok_even, bad_even = agree(even, ann)
    ok_odd, bad_odd = agree(odd, ann)
    all_ann = {**ann, **SUP_ANNOTATIONS}
    all_cands = [b for c in cands.values() for b in c]
    ok_all, bad_all = agree(all_cands, all_ann)
    rule_yes = {s: sum(1 for _, _, _, t in c if is_evidence(s, t)) for s, c in cands.items()}

    # 旁註:根因名字以外,事故描述需要的證據在語料哪裡(只數,不標註)
    side = {
        "flagd 子字串(任何大小寫)": {s: sum(1 for _, _, t in rows if "flagd" in t.lower()) for s, rows in corpus.items()},
        "RequestErrorRate": {s: sum(1 for _, _, t in rows if "RequestErrorRate" in t) for s, rows in corpus.items()},
        "adFailure": {s: sum(1 for _, _, t in rows if "adFailure" in t) for s, rows in corpus.items()},
    }

    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({
        "total": total, "candidates": {s: len(c) for s, c in cands.items()},
        "holdout_agree": [ok_even, len(even)], "odd_agree": [ok_odd, len(odd)],
        "all_candidates_agree": [ok_all, len(all_cands)], "rule_yes": rule_yes, "side": side,
    }, ensure_ascii=False, indent=1))

    L = []
    w = L.append
    w("# 作業 2-2 試驗報告:名稱比對 + 寫死規則判「證據」\n")
    w("## §1 語料重建\n")
    w(f"總塊數 {total}(預期 {EXPECTED_TOTAL},相符)。各來源:" + "、".join(f"{s} {len(r)}" for s, r in corpus.items()) + "。\n")
    w("## §2 根因 group 原文與事故描述清單\n")
    w("### §2.1 根因 group 原文(ground_truth.yaml `groups` 中 `root_cause: true`)\n")
    w("```yaml\n" + yaml.safe_dump(rc, allow_unicode=True, sort_keys=False) + "```\n")
    w("### §2.2 事故描述清單(編號是本實驗自加;來源 = ground_truth.yaml 的 fault / alerts / propagations)\n")
    for k, v in items:
        w(f"- {k}:{v}")
    w("\n## §3 各來源比得上根因名字的塊數\n")
    w(f"篩選條件:`re.search` 任一條 filter = {rc['filter']}。\n")
    w("| 來源 | 語料塊數 | 比得上 |\n|---|---|---|")
    for s, rows in corpus.items():
        w(f"| {s} | {len(rows)} | {len(cands[s])} |")
    w(f"\n合計 {sum(len(c) for c in cands.values())} 塊。抽樣規則是每種來源最多 5 塊,所以實際只有 {len(sample)} 塊(不是 30 塊)。\n")
    w("旁註:同一份語料裡其他事故線索的塊數(只數次數,沒逐塊看):\n")
    w("| 字串 | " + " | ".join(SOURCES) + " |\n|---|" + "---|" * len(SOURCES))
    for k, d in side.items():
        w(f"| {k} | " + " | ".join(str(d[s]) for s in SOURCES) + " |")
    w("\n## §4 標註表\n")
    w(f"### §4.1 規定抽樣的 {len(sample)} 塊(序號依來源順序;奇數 = 寫規則用,偶數 = 保留組)\n")
    w("| # | 來源 | 檔名:行號 | 原文節錄(前 110 字元) | 是否證據 | 理由 | 支持哪條 |\n|---|---|---|---|---|---|---|")
    for i, (s, f, ln, t) in enumerate(sample, 1):
        a, why, sup = ann[(s, ln)]
        w(f"| {i} | {s} | {f}:{ln} | {cell(t)} | {a} | {cell(why, 400)} | {sup} |")
    w(f"\n### §4.2 補充標註:抽樣沒抽到的另外 {len(sup_keys)} 塊(偏離規定,用來讓保留組結果有意義)\n")
    w("| 來源 | 檔名:行號 | 原文節錄 | 是否證據 | 理由 | 支持哪條 |\n|---|---|---|---|---|---|")
    for s, f, ln, t in all_cands:
        if (s, ln) in SUP_ANNOTATIONS:
            a, why, sup = SUP_ANNOTATIONS[(s, ln)]
            w(f"| {s} | {f}:{ln} | {cell(t)} | {a} | {cell(why, 400)} | {sup} |")
    w("\n### §4.3 難判的塊與原因\n")
    for (s, ln), why in HARD.items():
        w(f"- {s} 行 {ln}:{why}")
    w("\n### §4.4 原文(規定抽樣的塊,超過 800 字元截斷,標明原長)\n")
    for i, (s, f, ln, t) in enumerate(sample, 1):
        cut = f"(截斷,原長 {len(t)} 字元)" if len(t) > TRUNC else ""
        w(f"**#{i} {f}:{ln}**{cut}\n\n```\n{t[:TRUNC]}\n```\n")
    w("標註是看過完整原文寫的;#1 的 adFailure 判斷依據在完整 JSON 內,截斷後的 800 字元內不一定看得到。\n")
    w("## §5 規則清單與理由(只看奇數半 #1 #3 #5 寫成;判定 = 全部規則 AND)\n")
    for name, fn, sup in RULES:
        w(f"- **{name}**(對應 {sup}):{(fn.__doc__ or '').strip().split('。', 1)[0]}。" + (fn.__doc__ or "").strip().split("。", 1)[1].strip())
    w("\n## §6 一致率\n")
    w(f"### §6.1 保留組(偶數半 #2 #4)\n\n**{ok_even} / {len(even)}**。不一致:" + ("無。" if not bad_even else str(bad_even)))
    w("\n這個保留組兩塊標註都是「否」,沒有任何一塊該判「是」,所以一致率幾乎沒有資訊量:一條永遠回答「否」的規則也是 2 / 2。\n")
    w(f"### §6.2 補充:規則跑在全部 {len(all_cands)} 個候選塊(含我補標的 {len(sup_keys)} 塊)\n\n**{ok_all} / {len(all_cands)}**。不一致:" + ("無。" if not bad_all else str(bad_all)))
    w(f"\n其中 8 塊標註為「否」、2 塊為「是」(#1 與 3491);奇數半自己(訓練半)一致 {ok_odd} / {len(odd)}。補標的 5 塊是規則寫好之後才標的,但標註者與寫規則者是同一個,標準不可能完全獨立。\n")
    w("### §6.3 規則判成證據的塊數(§2 全部候選塊)\n")
    w("| 來源 | 候選 | 規則判「是」 |\n|---|---|---|")
    for s, c in cands.items():
        w(f"| {s} | {len(c)} | {rule_yes[s]} |")
    w("\n## §7 結論\n")
    w(CONCLUSION)
    w("\n## §8 限制\n")
    w("- 單一場景(Scenario-5)、單一標註者(同時也是寫規則的人)、實際只有 5 塊規定樣本(補標後 10 塊),不能推廣。")
    w("- 規則裡的 `flagd-config`、`adFailure`、`on` 全是這個場景的字面值;換場景要重寫,不是參數化。")
    w("- 標註標準(「內容本身顯示 condition / effect」)是本實驗自訂,沒有第二個人驗過。")
    (OUT / "report.md").write_text("\n".join(L) + "\n")
    print(f"total={total} cands={ {s: len(c) for s, c in cands.items()} }")
    print(f"holdout {ok_even}/{len(even)}  odd {ok_odd}/{len(odd)}  all {ok_all}/{len(all_cands)}  rule_yes={rule_yes}")


CONCLUSION = """\
### §7.1 規則寫得出來嗎

寫得出來,而且在這個場景的 10 個候選塊上跟逐塊判斷完全一致(§6.2)。但這個結論很窄:
- 候選塊只有 10 塊,全在 k8s_objects,其中 8 塊是 Pod / ReplicaSet 只用名字掛載 ConfigMap(標「否」),2 塊是 ConfigMap 本體(標「是」)。規則能分開,靠的是解析 JSON 的 kind 與 name,不是關鍵字。
- 規則裡有 `adFailure` 與 `on` 兩個字面值,是從事故描述反抄進規則的,不是從資料自己長出來的;等於把答案寫進規則。

### §7.2 規則抓不到的證據

名稱比對這一關本身就先漏掉大部分證據:根因名字 `flagd-config` 在 logs、traces、events、metrics、alerts 五種來源的比對數都是 0(§3)。事故描述裡 F1、F2、A1、P2、P3、P4 講的是 frontend / adservice 的錯誤率,證據若存在也在這些來源,而且不會出現 `flagd-config` 這串字。旁註表顯示 `RequestErrorRate`、`adFailure`、`flagd` 在各來源的出現次數,說明線索存在於別的字串下,但沒有逐塊看過,不能說是證據。所以「要求提到根因名字」這個前置條件對這個場景把命中率的分母壓到只剩 k8s_objects。

### §7.3 人(或我)也難判的塊

Pod / ReplicaSet 掛載塊(§4.3):它們跟根因有明確的結構關係(flagd 讀這個 ConfigMap),但內容本身看不出故障。標「否」是套用自訂標準的結果;如果標準改成「跟根因的因果鏈有關」,答案會變。這是標準的取捨,不是規則技術問題。另外同一個 ConfigMap 有 2 個時間點的快照(17:48、17:53),內容相同,判「是」的兩塊互為重複。

### §7.4 propagations / fault 描述夠不夠當標註依據

夠當這 10 塊的依據:F3 有 changed 的 from / to,能直接對到 ConfigMap 內容;P1 的 condition 就是同一句。不夠的地方:propagations 的 condition / effect 是自然語言(例如「getads endpoint Error Rate is Above Threshold」),沒有指定要看哪個來源的哪個欄位、多高算超過,所以 F1、F2、P2–P4 的標註本次沒有樣本可驗證,不知道逐塊標註在那些來源會有多穩。
"""

if __name__ == "__main__":
    main()
