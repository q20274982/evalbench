"""W01 §7.7.1|case 表寫得進去、指紋算得穩,而且本週刻意不去重。

跑法:
    docker compose up -d db
    docker compose run --rm app uv run pytest tests/test_cases.py -s

測試寫進 test_w1 這個 schema,不碰 public——Calvin 手動開來看的那份在 public,
兩邊共用的話深實驗會把他的資料弄髒(/scaffold 慣例 5)。
"""

import psycopg
import pytest

from cases import TEST_SCHEMA, alert_fingerprint, ensure_schema, open_case
from config import settings

A1 = {"labels": {"alertname": "RequestErrorRate", "namespace": "otel-demo"}, "state": "firing"}
A2 = {"labels": {"alertname": "CPUThrottlingHigh", "namespace": "otel-demo"}, "state": "firing"}


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(settings.postgres_dsn, connect_timeout=5)
    except psycopg.Error as e:
        pytest.skip(f"Postgres 連不上,先跑 docker compose up -d db:{e!r}")
    with c:
        ensure_schema(c, TEST_SCHEMA)
        c.execute(f"TRUNCATE {TEST_SCHEMA}.cases")
        c.commit()
        yield c


def test_fingerprint_ignores_order():
    """同一批告警換個順序送進來,指紋要一樣(alert_fingerprint 裡先排序就是為了這個)。"""
    assert alert_fingerprint([A1, A2]) == alert_fingerprint([A2, A1])


def test_fingerprint_changes_when_labels_change():
    """labels 動一個字就是另一則告警——整組 labels 進指紋的後果。"""
    changed = {"labels": {"alertname": "RequestErrorRate", "namespace": "kube-system"}}
    assert alert_fingerprint([A1]) != alert_fingerprint([changed])
    print(f"\n[fingerprint] {alert_fingerprint([A1])} vs {alert_fingerprint([changed])}")


def _insert(conn, fp: str, round_no: int = 1) -> int:
    return open_case(
        conn,
        fingerprint=fp,
        proposed_action="apply_remediation",
        run_id="test-run",
        scenario="Scenario-1",
        round_no=round_no,
        root_cause="load-generator 打太兇",
        root_cause_entity="load-generator",
        confidence=0.7,
        evidence=["alert: RequestErrorRate", "event: BackOff"],
        schema=TEST_SCHEMA,
    )


def test_open_case_roundtrip(conn):
    """五段最後一段:寫得進去,而且四個 Diagnosis 欄位讀得回原樣。"""
    cid = _insert(conn, alert_fingerprint([A1, A2]))
    row = conn.execute(
        f"SELECT root_cause_entity, confidence, evidence, occurrence_count, proposed_action "
        f"FROM {TEST_SCHEMA}.cases WHERE id = %s",
        (cid,),
    ).fetchone()
    assert row is not None
    entity, conf, evidence, count, action = row
    assert entity == "load-generator"
    assert conf == pytest.approx(0.7)
    assert evidence == ["alert: RequestErrorRate", "event: BackOff"]
    # 本週固定 1:沒有去重鍵就併不起來(§7.7.1)。
    assert count == 1
    assert action == "apply_remediation"


def test_same_fingerprint_twice_makes_two_rows(conn):
    """**這條驗的是刻意留的醜設計**(工作簿 §1、INV-3):同一則告警餵兩次,落兩筆。

    W4 加上唯一約束之後這條會翻面,到時候要改的是這個斷言,不是 cases.py。
    """
    fp = alert_fingerprint([A1, A2])
    first, second = _insert(conn, fp), _insert(conn, fp)
    assert first != second
    rows = conn.execute(
        f"SELECT count(*) FROM {TEST_SCHEMA}.cases WHERE alert_fingerprint = %s", (fp,)
    ).fetchone()
    assert rows is not None and rows[0] == 2
    print(f"\n[INV-3 未修] 同一個指紋 {fp} 落了 {rows[0]} 筆 case:id={first}, {second}")
