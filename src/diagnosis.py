"""「診斷結果」的資料形狀。

⚠️ 這是作業 0-4 的練習版本,欄位是最小可用的三個,不是定案。
W1 作業 1-2 會拿真實的模型壞輸出重新定它,那時才決定少欄位、多圍欄、
型別不對這幾種情況分別要在哪一層爆掉。
"""

from pydantic import BaseModel


class Diagnosis(BaseModel):
    """一次事故診斷的輸出。"""

    root_cause: str
    confidence: float
    evidence: list[str]
