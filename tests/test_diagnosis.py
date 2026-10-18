# 作業 0-4|pydantic 的驗證發生在什麼時候。
#
# 跑法:
#     docker compose run --rm app uv run pytest tests/test_diagnosis.py -s
#
# 交付時是「一綠一紅」,紅的那支寫的是錯的期望,用途是讓 ValidationError 真的出現一次
# (輸出留在 W00 工作簿 §5.4.1)。2026-09-08 已改成正確的期望,現在全綠。

import pytest
from pydantic import ValidationError

from diagnosis import Diagnosis

# 合法的一筆診斷結果。這裡的值是手寫的假資料,不是任何模型的輸出。
# root_cause_entity 是 2026-09-20 依 §7.3.1 ① 加的必填欄位,補進這筆假資料。
VALID_PAYLOAD = {
    "root_cause": "otel-demo-ad-jvm-chaos",
    "root_cause_entity": "otel-demo-ad-jvm-chaos",
    "confidence": 0.72,
    "evidence": ["k8s_events_raw.tsv 第 1 筆 JVMChaos 事件"],
}


# §1 綠的那支|合法輸入解析得動
def test_valid_payload_parses():
    d = Diagnosis(**VALID_PAYLOAD)
    assert d.root_cause == "otel-demo-ad-jvm-chaos"
    assert d.confidence == 0.72
    assert d.evidence == ["k8s_events_raw.tsv 第 1 筆 JVMChaos 事件"]


# §2 confidence 餵轉不動的字串,pydantic 要擋下來
#
# 這支原本寫的是相反的期望(斷言 pydantic 會接受 "high"),所以交付時是紅的。
# 那個紅的來源在「期望」這一側,不在 src/diagnosis.py,所以修的是這裡。
# 驗證發生在餵資料那一刻:錯誤指的行是下面的 Diagnosis(...),不是 class 定義那行。
#
# ⚠️ pydantic v2 預設是寬鬆模式,"0.72" 這種轉得動的字串會被自動轉成 float、不會爆。
# 擋得下來的只有 "high" 這種轉不動的。W1 作業 1-2 要決定這個預設要不要改。
def test_confidence_rejects_unparsable_string():
    with pytest.raises(ValidationError):
        Diagnosis(root_cause="x", confidence="high", evidence=[])
