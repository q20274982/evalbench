# Demo 站的資料匯出:把 traces/ 裡的每一筆診斷紀錄轉成網站讀的 JSON。
#
# 來源:Calvin 2026-11-10 要求一個沒跑過這個專案的人也能直接點開看的展示站(過渡版,W8 儀表板做完後取代)。
# 介面設計交給 TA;這支腳本只搬資料,不做任何新的判斷:
#
#   - 解析     → 原樣呼叫 src/parsing.py 的 inspect
#   - 評分     → 原樣呼叫 src/scoring.py 的 load_ground_truth / score_one
#                (取答案的寫法照 src/demo.py 的 judge:解析失敗時 entity = None)
#   - 引用檢查 → 原樣呼叫 src/citation_check.py 的 check,固定 mode="on"。
#                trace 沒存當時的檢查結果,所以這一欄是匯出時重算的,網站上有標明。
#                沒有檢索結果的 trace(W1 時期)不跑,留 None。
#
# 來源有兩種:
#
#   一、traces/<run_id>/*.json            src/tracing.py 存的完整紀錄(輸入、檢索、回答都在)
#   二、experiments/W02/2-6-agent/out/records*.json
#       2026-10-12 到 10-16 的三次檢索實驗。那時實驗腳本還沒改用 src/tracing.py,
#       只存了回答、分數與檢索段落的編號,沒存輸入全文與段落內容。網站照實顯示
#       「這一次沒有存下輸入」,不回頭重建。分數用現在的評分程式重算,並跟檔案裡
#       當時存的分數比對,不一致會印出來。
#
# 網站不連資料庫、不呼叫模型。它只讀這支腳本寫出來的檔案:
#
#   web/site/snapshot/index.json                    每一批的彙總 + 每題每輪一列 + 每題的標題
#   web/site/snapshot/traces/<run_id>/<檔名>.json   單筆的完整內容(點進去才載入)
#
# 分數的報法:每一批按輪次分開數「答對幾題 / 共幾題」,不合併成單一數字。
# 只跑一輪的批次沒有區間,網站上照實寫「1 round」。
#
# 跑法:
#     docker compose run --rm app uv run python web/export_demo.py
#     docker compose up -d demo        # http://localhost:8080
#
# INV-13:這支腳本會讀 ground_truth.yaml,因為它站在評分那一側。答案只寫進網站的
# 資料檔,不回到 prompt 或索引。

import json
import re
import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from citation_check import check as citation_check  # noqa: E402
from parsing import inspect  # noqa: E402
from pipeline import DATASET_ROOT  # noqa: E402
from scoring import load_ground_truth, same_service_members, score_one  # noqa: E402

TRACES_ROOT = REPO_ROOT / "traces"
RECORDS_DIR = REPO_ROOT / "experiments/W02/2-6-agent/out"
OUT_ROOT = REPO_ROOT / "web/site/snapshot"

# prompt 裡的分段標記,例:`=== Alerts ===`
SECTION_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)
RETRIEVED_TITLE = "Retrieved context"


# §1 把 prompt 切成「開頭指示 + 各分段」
# 有檢索結果的 trace,檢索那一段的文字已經逐塊存在 retrieval 欄,這裡不重複存。
def split_prompt(prompt: str, has_retrieval: bool) -> dict:
    marks = list(SECTION_RE.finditer(prompt))
    if not marks:
        return {"instructions": prompt, "sections": []}
    sections = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(prompt)
        title = m.group(1)
        text = prompt[m.end() : end].strip()
        if has_retrieval and title == RETRIEVED_TITLE:
            sections.append({"title": title, "chars": len(text), "text": None})
        else:
            sections.append({"title": title, "chars": len(text), "text": text})
    return {"instructions": prompt[: marks[0].start()].strip(), "sections": sections}


# §2 標準答案在網站上顯示的部分:根因 group 與評分規格收進來的同服務成員
def ground_truth_view(gt) -> dict:
    return {
        "root": {"id": gt.root.id, "kind": gt.root.kind, "filters": gt.root.filters},
        "accepted_members": [
            {"id": m.id, "kind": m.kind, "filters": m.filters} for m in same_service_members(gt)
        ],
    }


# §3 一筆 trace → 網站用的一筆
def export_one(trace: dict, gt_cache: dict) -> dict:
    scenario = trace["scenario"]
    if scenario not in gt_cache:
        gt_cache[scenario] = load_ground_truth(DATASET_ROOT / scenario)
    gt = gt_cache[scenario]

    retrieval = trace.get("retrieval")
    response = trace["response"]
    raw = response.get("raw") if response.get("ok") else None

    # 呼叫失敗的那一筆沒有輸出可解析:obs = None,分數照規格算 0 分、留在分母
    obs = inspect(raw) if raw is not None else None
    diag = obs["diagnosis"] if obs is not None else None
    entity = diag.root_cause_entity if diag is not None else None
    res = score_one(entity, gt)

    citation = None
    if retrieval:
        citation = citation_check(retrieval, obs, mode="on")

    req = trace["request"]
    usage = response.get("usage") or {}
    return {
        "trace_id": trace["trace_id"],
        "run_id": trace["run_id"],
        "scenario": scenario,
        "round": trace["round"],
        "created_at": trace["created_at"],
        "request": {
            "model": req.get("model"),
            "temperature": req.get("temperature"),
            "seed": req.get("seed"),
            "max_tokens": req.get("max_tokens"),
            "provider": (req.get("provider") or {}).get("order"),
        },
        "slice_params": trace["input"].get("slice_params"),
        "versions": {
            "commit": trace["versions"]["code"]["commit"],
            "scoring_sha12": trace["versions"]["scoring_sha12"],
        },
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost": usage.get("cost"),
        },
        "prompt": split_prompt(trace["input"]["prompt"], bool(retrieval)),
        "prompt_chars": len(trace["input"]["prompt"]),
        "retrieval": retrieval,
        "response": {"ok": response.get("ok"), "raw": raw, "error": response.get("error")},
        "parse": None
        if obs is None
        else {
            "verdict": obs["verdict"],
            "strict_ok": obs["strict_ok"],
            "salvage_ok": obs["salvage_ok"],
            "schema_ok": obs["schema_ok"],
            "has_fence": obs["has_fence"],
            "parsed": obs["parsed"],
        },
        "score": {"score": res.score, "stage": res.stage, "detail": res.detail},
        "ground_truth": ground_truth_view(gt),
        "citation": citation,
    }


# §3.1 一筆舊實驗紀錄 → 網站用的一筆
# 欄位跟 export_one 的輸出同形狀;當時沒存的欄位填 None,另外帶 retrieval_ids。
def export_record(rec: dict, run_id: str, created_at: str, label: str, gt_cache: dict) -> dict:
    scenario = rec["scenario"]
    if scenario not in gt_cache:
        gt_cache[scenario] = load_ground_truth(DATASET_ROOT / scenario)
    gt = gt_cache[scenario]

    raw = rec.get("raw")
    obs = inspect(raw) if raw else None
    diag = obs["diagnosis"] if obs is not None else None
    entity = diag.root_cause_entity if diag is not None else None
    res = score_one(entity, gt)

    usage = rec.get("usage") or {}
    return {
        "trace_id": None,
        "run_id": run_id,
        "scenario": scenario,
        "round": rec["round"],
        "created_at": created_at,
        "request": {
            "model": None,
            "temperature": None,
            "seed": None,
            "max_tokens": None,
            "provider": None,
        },
        "slice_params": {"config": label},
        "versions": {"commit": None, "scoring_sha12": None},
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost": usage.get("cost"),
        },
        "prompt": None,
        "prompt_chars": None,
        "retrieval": None,
        "retrieval_ids": rec.get("retrieved") or [],
        "response": {"ok": raw is not None, "raw": raw, "error": None},
        "parse": None
        if obs is None
        else {
            "verdict": obs["verdict"],
            "strict_ok": obs["strict_ok"],
            "salvage_ok": obs["salvage_ok"],
            "schema_ok": obs["schema_ok"],
            "has_fence": obs["has_fence"],
            "parsed": obs["parsed"],
        },
        "score": {"score": res.score, "stage": res.stage, "detail": res.detail},
        "stored_score": rec.get("score"),
        "ground_truth": ground_truth_view(gt),
        "citation": None,
    }


# §3.2 每一題在網站上的標題:答案檔裡的告警描述與故障類別
# 這些欄位只給網站顯示,不進 prompt(INV-13)。
def issue_view(scenario: str) -> dict:
    raw = yaml.safe_load((DATASET_ROOT / scenario / "ground_truth.yaml").read_text("utf-8"))
    body = raw.get("spec", raw)
    alerts = []
    seen = set()
    for a in body.get("alerts") or []:
        key = (a.get("id"), (a.get("metadata") or {}).get("description"))
        if key in seen:
            continue
        seen.add(key)
        alerts.append({"id": key[0], "description": key[1]})
    fault = (body.get("fault") or [{}])[0]
    return {
        "alerts": alerts,
        "fault": {
            "category": fault.get("category"),
            "mechanism": fault.get("fault_mechanism"),
            "condition": fault.get("condition"),
        },
    }


# §4 一批的彙總。每一輪分開數,不合併。
def summarize(run_id: str, items: list[dict]) -> dict:
    rounds = sorted({it["round"] for it in items})
    per_round = []
    for r in rounds:
        rows = [it for it in items if it["round"] == r]
        per_round.append(
            {"round": r, "correct": sum(it["score"]["score"] for it in rows), "total": len(rows)}
        )
    first = items[0]
    cite = Counter(it["citation"]["status"] for it in items if it["citation"] is not None)
    return {
        "run_id": run_id,
        "created_at": min(it["created_at"] for it in items),
        "model": first["request"]["model"],
        "commit": first["versions"]["commit"],
        "scoring_sha12": first["versions"]["scoring_sha12"],
        "slice_params": first["slice_params"],
        "label": (first["slice_params"] or {}).get("config"),
        "has_input": first["prompt"] is not None,
        "has_retrieval": any(it["retrieval"] or it.get("retrieval_ids") for it in items),
        "n_traces": len(items),
        "n_scenarios": len({it["scenario"] for it in items}),
        "rounds": rounds,
        "per_round": per_round,
        "stages": dict(Counter(it["score"]["stage"] for it in items)),
        "verdicts": dict(
            Counter(it["parse"]["verdict"] if it["parse"] else "call_failed" for it in items)
        ),
        "citation": dict(cite) if cite else None,
        "cost": sum(it["usage"]["cost"] or 0 for it in items),
        "prompt_tokens": sum(it["usage"]["prompt_tokens"] or 0 for it in items),
        "completion_tokens": sum(it["usage"]["completion_tokens"] or 0 for it in items),
    }


def scenario_key(name: str) -> int:
    return int(name.split("-")[1])


# 清單頁用的一列
def row_of(run_id: str, filename: str, item: dict) -> dict:
    parsed = item["parse"]["parsed"] if item["parse"] else None
    return {
        "run_id": run_id,
        "file": filename,
        "scenario": item["scenario"],
        "round": item["round"],
        "score": item["score"]["score"],
        "stage": item["score"]["stage"],
        "verdict": item["parse"]["verdict"] if item["parse"] else "call_failed",
        "entity": parsed.get("root_cause_entity") if isinstance(parsed, dict) else None,
        "expected": item["ground_truth"]["root"]["id"],
        "citation": item["citation"]["status"] if item["citation"] else None,
        "cost": item["usage"]["cost"],
        "prompt_tokens": item["usage"]["prompt_tokens"],
    }


# §5 入口
def main() -> int:
    if not TRACES_ROOT.is_dir():
        print(f"找不到 {TRACES_ROOT}", file=sys.stderr)
        return 1
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    (OUT_ROOT / "traces").mkdir(parents=True)

    gt_cache: dict = {}
    runs = []
    rows = []
    for run_dir in sorted(p for p in TRACES_ROOT.iterdir() if p.is_dir()):
        items = []
        out_dir = OUT_ROOT / "traces" / run_dir.name
        out_dir.mkdir()
        for path in sorted(run_dir.glob("*.json")):
            item = export_one(json.loads(path.read_text(encoding="utf-8")), gt_cache)
            (out_dir / path.name).write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
            items.append(item)
            rows.append(row_of(run_dir.name, path.name, item))
        if items:
            runs.append(summarize(run_dir.name, items))

    # 第二種來源:一個檔裡的一種設定算一批
    mismatches = 0
    for path in sorted(RECORDS_DIR.glob("records*.json")) if RECORDS_DIR.is_dir() else []:
        created_at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        recs = json.loads(path.read_text(encoding="utf-8"))["records"]
        labels = list(dict.fromkeys(r["config"] for r in recs))
        for i, label in enumerate(labels):
            run_id = f"{path.stem}-{i}"
            out_dir = OUT_ROOT / "traces" / run_id
            out_dir.mkdir()
            items = []
            for rec in recs:
                if rec["config"] != label:
                    continue
                item = export_record(rec, run_id, created_at, label, gt_cache)
                mismatches += item["stored_score"] != item["score"]["score"]
                name = f"{item['scenario']}_{item['round']}.json"
                (out_dir / name).write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
                items.append(item)
                rows.append(row_of(run_id, name, item))
            runs.append(summarize(run_id, items))

    runs.sort(key=lambda r: r["created_at"])
    rows.sort(key=lambda r: (r["run_id"], scenario_key(r["scenario"]), r["round"]))
    issues = {s: issue_view(s) for s in sorted({r["scenario"] for r in rows}, key=scenario_key)}
    index = {"runs": runs, "rows": rows, "issues": issues}
    (OUT_ROOT / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    for run in runs:
        per = ", ".join(
            f"round {p['round']}: {p['correct']}/{p['total']}" for p in run["per_round"]
        )
        print(f"{run['run_id']}  {run['n_traces']} traces  {per}")
    print(f"舊實驗紀錄:重算的分數跟當時存的分數不一致 {mismatches} 筆")
    print(f"寫到 {OUT_ROOT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
