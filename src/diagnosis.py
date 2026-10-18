# 「診斷結果」的資料形狀。
#
# 欄位歷程:作業 0-4 的練習版是最小可用的三個;W1 作業 1-3 依工作簿 §7.3.1 ①
# (Calvin 2026-09-20 定案)加上 root_cause_entity,而且是必填——評分只看這一欄,
# 沒填等於 schema 驗證失敗,落進解析失敗那條線。其餘三欄照常收、照常驗型別,
# 但不計入分數。三個欄位本身要不要再改,移到 W3(工作簿 §7.2.2 的「不在本項範圍」)。
#
# ⚠️ 這個 schema 是 2026-09-20 才變成四個欄位的。作業 1-2 的成敗表
# (§7.2.1,格式服從率 90%–95%)是三欄時期量的,拿變更後的腳本重跑不會得到
# 同一批數字。

from pydantic import BaseModel, Field


# 一次事故診斷的輸出。
class Diagnosis(BaseModel):
    root_cause: str
    # 評分唯一看的欄位(§7.3.1 ①):只填資源名,不寫解釋。
    root_cause_entity: str
    confidence: float
    evidence: list[str]
    # W02 §7 第 4 項(Calvin 2026-10-18 選 c,子分支 TA 決定):兩個選填欄位。
    # 有預設值,不允許說不知道的那一組沒輸出這兩欄也不會變成解析失敗。
    # abstain = true 就是說不知道,此時 root_cause_entity 填 ""。
    abstain: bool = False
    # 每條「候選 — 排除理由」。判定規則留到 W3 作業 3-2。
    ruled_out: list[str] = Field(default_factory=list)
