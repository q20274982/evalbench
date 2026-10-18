# 作業 2-4 比對腳本:本週實驗的量尺,不擋答案(W02 §7 第 3 項)。
#
# 只讀 trace 的兩個欄位:retrieval(比對基準)與 response.raw(先經 parsing.py 解析)。
# 「逐字」照 W02 §3:大小寫一樣的子字串;空字串算找不到(實作用決定 ④)。
# 比對函式自己寫,不 import src/citation_check.py(§7 第 5 項決定:各寫各的)。
#
# 產出三樣,放 out/<run_id>/:
#   evidence.csv      — 一條 evidence 一列,label / note 留空給人工標
#   per_question.csv  — 一題一列,8 欄
#   counts.json       — 計數表(對到 §8.1.1),1 輪,沒有區間
#
# 跑法(從 repo 根目錄):
#     uv run python experiments/W02/2-4-citation/compare_2_4.py <run_id>
# INV-13:ground_truth.yaml 只在 score 那一欄被讀,不進 evidence 表(刻意不放答對沒有)。

import csv
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from parsing import inspect  # noqa: E402
from pipeline import DATASET_ROOT  # noqa: E402
from scoring import load_ground_truth, score_one  # noqa: E402
from tracing import TRACES_ROOT, read_trace  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
EVIDENCE_COLS = [
    "trace_id",
    "evidence_no",
    "evidence",
    "found",
    "located_at",
    "nearest",
    "root_cause",
    "entity",
    "label",
    "note",
]
QUESTION_COLS = [
    "trace_id",
    "verdict",
    "score",
    "all_evidence_found",
    "entity_in_evidence",
    "entity_in_retrieved",
    "verbatim_citation",
    "status",
]


# §1 逐字比對(量尺自己的一份)
def find_first(text: str, blocks: list[dict]) -> str | None:
    if not text:
        return None
    for b in blocks:
        i = b["text"].find(text)
        if i >= 0:
            return f"{b['id']} [{i}:{i + len(text)}]"
    return None


# 找不到時附最相近的一行:10 塊逐行比 difflib 相似度,取最高的。
# 只當人工翻找起點,相似度不進計數表(§7 第 3 項)。以「行」為單位是 TA 決定:語料是 log /
# events / 物件描述,一行通常是一筆紀錄。
def nearest(text: str, blocks: list[dict]) -> str:
    best = (0.0, "", "")
    for b in blocks:
        for line in b["text"].splitlines():
            r = difflib.SequenceMatcher(None, text, line).ratio()
            if r > best[0]:
                best = (r, b["id"], line)
    return json.dumps(
        {"id": best[1], "ratio": round(best[0], 3), "text": best[2]}, ensure_ascii=False
    )


# §2 一題
def one(trace: dict) -> tuple[dict, list[dict]]:
    blocks = trace["retrieval"] or []
    raw = trace["response"]["raw"]
    tid = trace["trace_id"]
    if raw is None:
        q = {"trace_id": tid, "verdict": "call_failed", "score": 0, "status": "call_failed"}
        return {**q, "verbatim_citation": 0}, []
    obs = inspect(raw)
    if obs["verdict"] != "ok":
        q = {"trace_id": tid, "verdict": obs["verdict"], "score": 0, "status": "parse_failed"}
        return {**q, "verbatim_citation": 0}, []

    d = obs["diagnosis"]
    rows = []
    for n, e in enumerate(d.evidence, 1):
        at = find_first(e, blocks)
        rows.append(
            {
                "trace_id": tid,
                "evidence_no": n,
                "evidence": e,
                "found": at is not None,
                "located_at": at or "",
                "nearest": "" if at else nearest(e, blocks),
                "root_cause": d.root_cause,
                "entity": d.root_cause_entity,
                "label": "",
                "note": "",
            }
        )
    ent = d.root_cause_entity
    all_found = all(r["found"] for r in rows)
    in_ev = ent != "" and any(ent in e for e in d.evidence)
    in_ret = ent != "" and any(ent in b["text"] for b in blocks)
    score = score_one(ent, load_ground_truth(DATASET_ROOT / trace["scenario"])).score
    if d.abstain:
        status, score, vc = "abstained", 0, 0
    else:
        status = "passed" if all_found and in_ev else "check_failed"
        vc = int(all_found and in_ev)
    q = {
        "trace_id": tid,
        "verdict": obs["verdict"],
        "score": score,
        "all_evidence_found": all_found,
        "entity_in_evidence": in_ev,
        "entity_in_retrieved": in_ret,
        "verbatim_citation": vc,
        "status": status,
    }
    return q, rows


def write_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def main(run_id: str) -> None:
    paths = sorted((TRACES_ROOT / run_id).glob("*.json"))
    if not paths:
        sys.exit(f"traces/{run_id}/ 底下沒有 trace")
    qs, evs = [], []
    for p in paths:
        q, rows = one(read_trace(p))
        qs.append(q)
        evs += rows

    def n_status(s: str) -> int:
        return sum(1 for q in qs if q["status"] == s)

    counts = {
        "說明": "1 輪,沒有區間",
        "題數": len(qs),
        "① 找不到的 evidence 條數": sum(1 for r in evs if not r["found"]),
        "evidence 總條數": len(evs),
        "④ all_evidence_found = 否 的題數": sum(
            1 for q in qs if q.get("all_evidence_found") is False
        ),
        "④ entity_in_retrieved = 否 的題數": sum(
            1 for q in qs if q.get("entity_in_retrieved") is False
        ),
        "verbatim_citation 合計": sum(q["verbatim_citation"] for q in qs),
        "score 合計": sum(q["score"] for q in qs),
        "解析失敗題數": n_status("parse_failed"),
        "呼叫失敗題數": n_status("call_failed"),
        "說不知道題數": n_status("abstained"),
        "三類各幾條": "待人工標 label",
    }
    out = OUT / run_id
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "evidence.csv", EVIDENCE_COLS, evs)
    write_csv(out / "per_question.csv", QUESTION_COLS, qs)
    (out / "counts.json").write_text(json.dumps(counts, ensure_ascii=False, indent=1))
    print(json.dumps(counts, ensure_ascii=False, indent=1))
    print(f"→ {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")


if __name__ == "__main__":
    main(sys.argv[1])
