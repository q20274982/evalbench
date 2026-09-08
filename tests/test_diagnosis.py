"""作業 0-4|pydantic 的驗證發生在什麼時候。

跑法:
    docker compose run --rm app uv run pytest tests/test_diagnosis.py -s

⚠️ 這個檔案交付時是「一綠一紅」,紅的那支是故意的,見 §2 的檔內說明。
"""

from diagnosis import Diagnosis

# 合法的一筆診斷結果。這裡的值是手寫的假資料,不是任何模型的輸出。
VALID_PAYLOAD = {
    "root_cause": "otel-demo-ad-jvm-chaos",
    "confidence": 0.72,
    "evidence": ["k8s_events_raw.tsv 第 1 筆 JVMChaos 事件"],
}


# §1 綠的那支|合法輸入解析得動
def test_valid_payload_parses():
    d = Diagnosis(**VALID_PAYLOAD)
    assert d.root_cause == "otel-demo-ad-jvm-chaos"
    assert d.confidence == 0.72
    assert d.evidence == ["k8s_events_raw.tsv 第 1 筆 JVMChaos 事件"]


# §2 紅的那支|confidence 餵字串
#
# 這支寫的是「錯的期望」:它假設 pydantic 會照單全收,把 "high" 存進 confidence。
# 跑下去會紅,而紅色輸出裡的 ValidationError 就是這一題要看的東西。
#
# Calvin 的三分鐘:
#   1. 跑上面那行指令,看 1 passed / 1 failed
#   2. 把 ValidationError 那幾行抄進 W00 工作簿 §5.4
#   3. 回答:驗證是在 class 定義的那一刻發生,還是在這一行餵資料時發生?
#   4. 把這支改成「期望它爆」——用 pytest.raises 攔 ValidationError,再跑一次變全綠
def test_confidence_accepts_string():
    d = Diagnosis(root_cause="x", confidence="high", evidence=[])
    assert d.confidence == "high"
