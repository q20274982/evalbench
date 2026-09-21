"""case 表:一個場景一張工單。

規格正本是 W01 工作簿 §7.7.1(Calvin 2026-09-21 定案)。這一層只負責建表與寫入,
不做任何去重——**本週刻意沒有唯一約束**(工作簿 §1 刻意做醜清單,INV-3,W4 才修)。
同一個場景餵兩次就會落兩筆,那是預期行為,不是 bug。

粒度的理由(Calvin 的 Sentry 類比):那裡的 event 對應這裡的一則告警,issue 對應
一張 case。issue 能顯示「發生了幾次」,靠的是對每個 event 算指紋、指紋相同就併進
同一個 issue 並把計數加一。這裡的 occurrence_count 就是那個計數的位置——本週固定
寫 1,因為沒有去重鍵就併不起來。

TA 在 §7.7.1 的欄位表之外補了 scenario 與 round_no 兩欄(2026-09-21),理由寫在
下面的 DDL 註解裡,待 Calvin 過目。
"""

import hashlib
import json
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# §1 常數與 DDL
# 測試不碰 Calvin 手動開的那份資料(/scaffold 慣例 5:測試用的 DB 與他手動開的不共用)。
DEFAULT_SCHEMA = "public"
TEST_SCHEMA = "test_w1"

DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.cases (
    id                BIGSERIAL PRIMARY KEY,
    -- §7.7.1:這一題實際餵進去那批告警算出來的指紋。本週沒有 UNIQUE,W4 才加。
    alert_fingerprint TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- §7.4.2 定案的「刻意保留的會動手的動作」。本週每一筆都是同一個值。
    proposed_action   TEXT        NOT NULL,
    -- 本週固定 1:沒有去重鍵就併不起來,欄位先留著等 W4。
    occurrence_count  INTEGER     NOT NULL DEFAULT 1,
    -- 接回 traces/<run_id>/<scenario>_<round>.json。
    run_id            TEXT        NOT NULL,
    -- TA 補:§7.7.1 的欄位表沒列這兩欄,但少了它們就接不回 trace 檔,
    -- 也答不出「這筆 case 是哪個場景的」。待 Calvin 過目。
    scenario          TEXT        NOT NULL,
    round_no          INTEGER     NOT NULL,
    -- Diagnosis 四欄拆開存(§7.7.1:拆開才 select 得見)。evidence 是字串陣列,存 jsonb。
    root_cause        TEXT        NOT NULL,
    root_cause_entity TEXT        NOT NULL,
    confidence        DOUBLE PRECISION NOT NULL,
    evidence          JSONB       NOT NULL
);
"""

INSERT = """
INSERT INTO {schema}.cases
    (alert_fingerprint, proposed_action, run_id, scenario, round_no,
     root_cause, root_cause_entity, confidence, evidence)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;
"""


# §2 指紋
def alert_fingerprint(alerts: list[dict[str, Any]]) -> str:
    """§7.7.1:每則 labels 各自雜湊,排序後再雜湊。取 sha256 前 16 碼。

    拿整組 labels 而不是挑欄位,理由在工作簿 §7.7.1:挑 alertname+namespace+pod
    的話,Scenario-1 同一分鐘的快照裡就有 109 則被合併成別人(pod 只出現 35%)。

    排序是為了讓順序不影響結果——同一批告警換個順序送進來,指紋要一樣。
    """
    per_alert = sorted(
        hashlib.sha256(
            json.dumps(a.get("labels", {}), sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        for a in alerts
    )
    return hashlib.sha256("".join(per_alert).encode()).hexdigest()[:16]


# §3 建表
def ensure_schema(conn: psycopg.Connection, schema: str = DEFAULT_SCHEMA) -> None:
    """建 schema 與 cases 表。已存在就什麼都不做。"""
    conn.execute(DDL.format(schema=schema))  # type: ignore[arg-type]
    conn.commit()


# §4 寫一筆 case(單純 INSERT,不查有沒有一樣的)
def open_case(
    conn: psycopg.Connection,
    *,
    fingerprint: str,
    proposed_action: str,
    run_id: str,
    scenario: str,
    round_no: int,
    root_cause: str,
    root_cause_entity: str,
    confidence: float,
    evidence: list[str],
    schema: str = DEFAULT_SCHEMA,
) -> int:
    """寫一筆 case,回傳 id。

    單純 INSERT,不查有沒有一樣的(INV-3 要打破的就是這件事)。
    """
    with conn.cursor() as cur:
        cur.execute(
            INSERT.format(schema=schema),  # type: ignore[arg-type]
            (
                fingerprint,
                proposed_action,
                run_id,
                scenario,
                round_no,
                root_cause,
                root_cause_entity,
                confidence,
                Jsonb(evidence),
            ),
        )
        row = cur.fetchone()
    conn.commit()
    assert row is not None
    return int(row[0])
