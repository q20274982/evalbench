# 作業 2-4 標註腳本:替 evidence.csv 的每條 evidence 填 label(W02 §7 2026-10-30「標註換分工」)。
#
# 只看出處就能決定的,由這支程式分類:
#   檢索結果裡逐字找得到           → 留給人判斷(撐得起 / 存在但不支持)
#   檢索結果裡只差格式             → 格式差異
#   告警段或 events 段,逐字        → 越界引用
#   告警段或 events 段,只差格式    → 越界引用,note 記「只差格式」
#   整份 prompt 都找不到           → 留給人判斷(編造 / 改寫)
# 「只差格式」= 兩邊都把 \" 換成 "、字面的 \n 換成換行、連續空白併成一格之後,
# 是子字串(W02 §8.1 更正)。
# 「逐字」照 W02 §3:大小寫一樣的子字串。檢索結果用 trace 的 retrieval(比對基準,§7 第 3 項)。
# 告警段與 events 段 = prompt 從 "\n=== Alerts ===\n"
# 到最後一個 "\n=== Retrieved context ===\n" 之間
# (§8.1 引言塊:規則文字本身含 "=== Retrieved context ===",所以取最後一處)。
#
# 留給人判斷的,由 Calvin 親自判,寫在下面的 HUMAN;TA 不標(§7 同日決定)。note 記「Calvin 判」。
#
# 產出放 out/<run_id>/,不改 compare_2_4.py 的產出:
#   labels.csv          — evidence.csv 同欄位,label / note 已填
#   counts_labels.json  — 計數表(對到 §8.1.1),1 輪,沒有區間
#
# 跑法(從 repo 根目錄,先跑過 compare_2_4.py):
#     uv run python experiments/W02/2-4-citation/label_2_4.py <run_id>

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from tracing import TRACES_ROOT, read_trace  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
ALERTS = "\n=== Alerts ===\n"
RETRIEVED = "\n=== Retrieved context ===\n"
HUMAN_SUPPORT = "待人判斷(撐得起 / 存在但不支持)"
HUMAN_SOURCE = "待人判斷(編造 / 改寫)"

# Calvin 親自判的結果,鍵是 (trace 檔名, evidence_no)。空的 = 還沒判。
HUMAN: dict[tuple[str, str], str] = {}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace('\\"', '"').replace("\\n", "\n")).strip()


# 一條 evidence 的出處分類,回傳 (label, note)
def classify(evidence: str, blocks: list[str], background: str) -> tuple[str, str]:
    if evidence and any(evidence in b for b in blocks):
        return HUMAN_SUPPORT, ""
    if evidence and norm(evidence) in "\n".join(norm(b) for b in blocks):
        return "格式差異", ""
    if evidence and evidence in background:
        return "越界引用", ""
    if evidence and norm(evidence) in norm(background):
        return "越界引用", "只差格式"
    return HUMAN_SOURCE, ""


def background_of(trace: dict) -> str:
    prompt = trace["request"]["messages"][-1]["content"]
    start, end = prompt.find(ALERTS), prompt.rfind(RETRIEVED)
    if start < 0 or end < start:
        sys.exit(f"{trace['trace_id']}:prompt 裡找不到告警段或檢索結果段的切點")
    return prompt[start:end]


def main(run_id: str) -> None:
    out = OUT / run_id
    with (out / "evidence.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with (out / "per_question.csv").open(encoding="utf-8") as f:
        qs = list(csv.DictReader(f))
    cols = list(rows[0].keys())

    traces: dict[str, dict] = {}
    for r in rows:
        name = r["trace_id"].split("/")[-1]
        if name not in traces:
            traces[name] = read_trace(TRACES_ROOT / run_id / f"{name}.json")
        t = traces[name]
        blocks = [b["text"] for b in t["retrieval"]]
        label, note = classify(r["evidence"], blocks, background_of(t))
        if label.startswith("待人判斷"):
            judged = HUMAN.get((name, r["evidence_no"]))
            if judged:
                label, note = judged, "Calvin 判"
        r["label"], r["note"] = label, note

    with (out / "labels.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    counts = {
        "說明": "1 輪,沒有區間",
        "evidence 總條數": len(rows),
        "① 找不到的 evidence 條數": sum(1 for r in rows if r["found"] == "False"),
        "各類各幾條": dict(Counter(r["label"] for r in rows)),
        "④ all_evidence_found = 否 的題數": sum(
            1 for q in qs if q["all_evidence_found"] == "False"
        ),
        "④ entity_in_retrieved = 否 的題數": sum(
            1 for q in qs if q["entity_in_retrieved"] == "False"
        ),
        "待人判斷": [
            f"{r['trace_id'].split('/')[-1]} #{r['evidence_no']}"
            for r in rows
            if r["label"].startswith("待人判斷")
        ],
    }
    (out / "counts_labels.json").write_text(
        json.dumps(counts, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(counts, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
