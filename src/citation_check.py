# 引用檢查(防線):被測系統用程式檢查一筆 diagnosis 有沒有憑據,不交給 LLM。
#
# 規格正本是 W02 工作簿 §7 第 1、4、5 項與「實作用」決定(Calvin 2026-10-17/18 定案)。
#
# 兩項檢查:
#   一、每條 evidence 是否逐字出現在 retrieval 任一塊,記 found 與 located_at
#   二、root_cause_entity 是否逐字出現在至少一條 evidence 裡
# 「逐字」= 大小寫一樣的子字串(W02 §3 smoke test 的定義)。
# 空字串一律算找不到:照字面定義 "" 在任何字串裡都找得到,空答案會通過(實作用決定 ④)。
#
# status 五種,判定順序固定(§7 第 4 項,呼叫失敗是實作用決定 ③):
#   call_failed   呼叫失敗 — 沒拿到模型輸出
#   parse_failed  解析失敗 — parsing.inspect 的 verdict 不是 ok
#   abstained     說不知道 — abstain = true,不跑兩項檢查來改判
#   check_failed  檢查沒過 — 兩項任一沒過;evidence 為空時第二項必然不過
#   passed        通過
#
# 這一層只標記,不改模型的輸出(§7 2026-10-18 prompt 決定)。
#
# 開關 settings.citation_check 只收 "on" / "off",沒設或打錯就報錯(實作用決定 ②)。
# off = 破的設定:解析成功、沒說不知道的題一律回 passed,found / located_at 留 None。
#
# 比對函式刻意不給別人共用:比對腳本與失敗中的測試各寫各的(§7 第 5 項決定),
# 共用的話這裡寫錯,量尺和測試會錯在同一處,測試照樣綠。

from typing import TypedDict

from config import settings
from tracing import RetrievedBlock

MODES = ("on", "off")


class EvidenceResult(TypedDict):
    evidence: str
    found: bool | None  # 檢查關閉或沒跑檢查時為 None
    located_at: str | None  # 例:Scenario-1/objects/000714 [1203:1291];找不到或沒跑為 None


class CheckResult(TypedDict):
    status: str
    evidence: list[EvidenceResult]


# §1 開關
def resolve_mode(mode: str | None = None) -> str:
    m = settings.citation_check if mode is None else mode
    if m not in MODES:
        raise ValueError(f"CITATION_CHECK 要設成 on 或 off,現在是 {m!r}(W02 §7 實作用決定 ②)")
    return m


# §2 逐字比對
# 回傳第一處的位置:照 retrieval 的順序找第一塊含它的,記那一塊原文裡的字元起訖。
def locate(text: str, retrieval: list[RetrievedBlock]) -> str | None:
    if text == "":
        return None
    for block in retrieval:
        start = block["text"].find(text)
        if start >= 0:
            return f"{block['id']} [{start}:{start + len(text)}]"
    return None


# §3 防線本體
# obs 是 parsing.inspect 的回傳;None 代表這題沒拿到模型輸出(呼叫失敗)。
def check(
    retrieval: list[RetrievedBlock], obs: dict | None, mode: str | None = None
) -> CheckResult:
    m = resolve_mode(mode)
    if obs is None:
        return {"status": "call_failed", "evidence": []}
    if obs["verdict"] != "ok":
        return {"status": "parse_failed", "evidence": []}
    diag = obs["diagnosis"]
    unchecked: list[EvidenceResult] = [
        {"evidence": e, "found": None, "located_at": None} for e in diag.evidence
    ]
    if diag.abstain:
        return {"status": "abstained", "evidence": unchecked}
    if m == "off":
        return {"status": "passed", "evidence": unchecked}

    results: list[EvidenceResult] = []
    for e in diag.evidence:
        at = locate(e, retrieval)
        results.append({"evidence": e, "found": at is not None, "located_at": at})
    all_found = all(r["found"] for r in results)
    entity = diag.root_cause_entity
    entity_in_evidence = entity != "" and any(entity in e for e in diag.evidence)
    status = "passed" if all_found and entity_in_evidence else "check_failed"
    return {"status": status, "evidence": results}
