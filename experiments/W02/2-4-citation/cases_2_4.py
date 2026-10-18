# 作業 2-4 自己的 case 表(W02 §7 實作用決定 ①,/scaffold 慣例 8(b))。
#
# 欄位照 W1 的 public.cases(src/cases.py),差兩處:
#   - root_cause、root_cause_entity、confidence、evidence 四欄可空:
#     解析失敗與呼叫失敗也落 case,那時四欄是空值(§7 第 4 項、實作用決定 ③)
#   - 加 status 欄:passed / check_failed / abstained / parse_failed / call_failed
#     (對照見 src/citation_check.py 檔頭)
# public.cases 與 src/demo.py 不動。原文不另存,用 run_id、scenario、round_no 回 trace 查。

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = "w2_citation"
PROPOSED_ACTION = "apply_remediation"  # 同 src/demo.py(W1 §7.4.2)

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.cases (
    id                BIGSERIAL PRIMARY KEY,
    alert_fingerprint TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposed_action   TEXT        NOT NULL,
    occurrence_count  INTEGER     NOT NULL DEFAULT 1,
    run_id            TEXT        NOT NULL,
    scenario          TEXT        NOT NULL,
    round_no          INTEGER     NOT NULL,
    status            TEXT        NOT NULL,
    root_cause        TEXT,
    root_cause_entity TEXT,
    confidence        DOUBLE PRECISION,
    evidence          JSONB
);
"""

INSERT = f"""
INSERT INTO {SCHEMA}.cases
    (alert_fingerprint, proposed_action, run_id, scenario, round_no, status,
     root_cause, root_cause_entity, confidence, evidence)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;
"""


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(DDL)  # type: ignore[arg-type]
    conn.commit()


# 這一題的告警指紋(去重鍵,核心項)。
# 待 Calvin 決定:W2 的 prompt 用的是告警塊,不是 W1 的告警 dict,
# 「哪幾則算餵進去」與「labels 怎麼從塊裡讀回來」沒有定案。決定前 live 模式跑不了。
def fingerprint(conn: psycopg.Connection, scenario: str) -> str:
    raise NotImplementedError("告警指紋的算法待 Calvin 決定(W02 §7)")


# diag 是 Diagnosis 或 None(解析失敗、呼叫失敗時四欄空值)。
def open_case(
    conn: psycopg.Connection,
    *,
    fp: str,
    run_id: str,
    scenario: str,
    round_no: int,
    status: str,
    diag,
) -> int:
    vals = (
        (diag.root_cause, diag.root_cause_entity, diag.confidence, Jsonb(diag.evidence))
        if diag is not None
        else (None, None, None, None)
    )
    with conn.cursor() as cur:
        cur.execute(
            INSERT,  # type: ignore[arg-type]
            (fp, PROPOSED_ACTION, run_id, scenario, round_no, status, *vals),
        )
        row = cur.fetchone()
    conn.commit()
    assert row is not None
    return int(row[0])
