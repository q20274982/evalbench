"""作業 1-3|評分層照不照 §7.3.1 的規格判。

跑法:
    docker compose run --rm app uv run pytest tests/test_scoring.py -s

⚠️ 斷言的比對基準(/scaffold 慣例 6):每一條的右邊都來自 ground_truth.yaml
(IBM 標的,完全外部)或規格白紙黑字寫下的決定,**沒有一條的右邊是系統自己
算出來的**。評測台自己校準自己是這個專案最致命的錯。
"""

from pathlib import Path

import pytest

from scoring import (
    id_prefix,
    is_entity_like,
    literal_prefix,
    load_ground_truth,
    same_service_members,
    score_one,
)

DATASET_ROOT = (
    Path(__file__).resolve().parents[1]
    / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
)


def gt(scenario: str):
    return load_ground_truth(DATASET_ROOT / scenario)


# §1 讀答案檔:兩種排版收斂成同一個形狀
def test_flat_and_wrapped_layouts_both_load():
    """35 份裡有 4 份外面包了一層 spec(資料集導覽 §7 的 7-01)。

    右邊的 id 是從 ground_truth.yaml 逐字抄下來的。
    """
    assert gt("Scenario-1").root.id == "load-generator-pod-1"  # 平鋪版
    assert gt("Scenario-20").root.id == "productcatalog-deployment-1"  # spec 包裝版


def test_every_scenario_has_exactly_one_root_cause():
    """35 個場景各有且只有一個 root_cause: true 的 group。

    這條是評分能成立的前提:根因不只一個的話,①「比一個欄位」就不夠用。
    """
    scenarios = sorted(p.name for p in DATASET_ROOT.iterdir() if p.is_dir())
    assert len(scenarios) == 35
    for s in scenarios:
        assert gt(s).root.id, s


# §2 同服務成員(規格②)
def test_same_service_members_include_alias_exclude_downstream():
    """Scenario-1 的 aliases 組有四個成員,規格②只收同服務的那一個。

    右邊來自規格②逐字寫下的決定:「Scenario-1 的 aliases 組裡有
    frontend-proxy-service-1 / frontend-proxy-pod-1,那是下游受害者,不算對。」
    同一份 ground_truth.yaml 的 propagations 也標明 frontend-proxy 是下游。
    """
    members = {m.id for m in same_service_members(gt("Scenario-1"))}
    assert members == {"load-generator-service-1"}


def test_two_signals_of_same_service():
    """②的兩個訊號各自抽出什麼。右邊是答案檔裡的字面值。"""
    # 訊號一:filter 的字面前綴。兩條 filter 一條帶尾巴一條不帶,砍掉之後一樣。
    assert literal_prefix("load-generator-.*") == "load-generator"
    assert literal_prefix(r"load-generator\b") == "load-generator"
    assert literal_prefix(".*product-catalog") == "product-catalog"
    # 訊號二:id 砍掉 kind 段。
    assert id_prefix(gt("Scenario-1").root) == "load-generator"  # load-generator-pod-1, Pod
    # id 不帶 kind 段時整串原樣回傳,規格②已經寫下這個後果。
    assert id_prefix(gt("Scenario-2").root) == "flagd-config"  # ConfigMap


# §3 三階段比對(規格③⑧)
def test_stage1_root_filter():
    """標準答案自己要能通過第 1 階段。右邊是答案檔的 id。"""
    res = score_one("load-generator-pod-1", gt("Scenario-1"))
    assert res.score == 1
    assert res.stage == "root_filter"


def test_same_service_alias_scores_one():
    """同服務的 alias 成員要得分(規格②要收的那一半)。

    實測命中的是第 1 階段不是第 2 階段:⑧1 的部分命中語意下,根因自己的
    filter `load-generator-.*` 已經涵蓋 load-generator-service-1,早命中早結束。
    """
    res = score_one("load-generator-service-1", gt("Scenario-1"))
    assert res.score == 1
    assert res.stage == "root_filter"


def test_stage2_never_fires_on_this_dataset():
    """第 2 階段在這 35 份資料上一次都不會觸發——這是實測結果,不是設計。

    5 個場景的 aliases 組裡有同服務成員,逐一送進評分層:3 個在第 1 階段就中
    (根因的 filter 已經涵蓋它們),2 個連第 3 階段都不中(見下一支)。所以
    規格②③的第 2 階段目前是走不到的路。這條斷言把現況釘住,規則一改它就會紅。
    """
    scenarios = sorted(p.name for p in DATASET_ROOT.iterdir() if p.is_dir())
    stages = set()
    for s in scenarios:
        g = gt(s)
        for member in same_service_members(g):
            stages.add(score_one(member.id, g).stage)
    assert "same_service_filter" not in stages


def test_alias_member_reachable_via_stage3_id():
    """Scenario-20 的同服務成員 productcatalog-pod-1 靠第 3 階段收進來。

    它的 filter 是 product-catalog-.*(帶連字號),id 是 productcatalog-pod-1
    (不帶),兩者比不到,所以前兩階段收不到它——這是答案檔自己的命名不一致
    (資料集導覽 §7 的 7-12 那一類)。Calvin 2026-09-20 選的 (c) 案:第 3 階段
    比根因 + 同服務成員的 id。Scenario-34 的 valkey-service-1 同一個毛病。
    """
    r20 = score_one("productcatalog-pod-1", gt("Scenario-20"))
    assert r20.score == 1
    assert r20.stage == "same_service_id"
    assert score_one("valkey-service-1", gt("Scenario-34")).score == 1


def test_stage3_does_not_collect_downstream_victims():
    """第 3 階段放寬到成員 id,不可以順手把下游受害者也收進來。

    Scenario-1 的 aliases 組裡有 frontend-proxy-*,規格②明說不算對。
    """
    assert score_one("frontend-proxy-pod-1", gt("Scenario-1")).score == 0
    assert score_one("frontend-proxy-service-1", gt("Scenario-1")).score == 0


def test_downstream_victim_scores_zero():
    """下游受害者不算對(規格②)。這條是整個評分最容易放水的地方。"""
    assert score_one("frontend-proxy-service-1", gt("Scenario-1")).score == 0


def test_stage3_fallback_when_filter_misses_its_own_id():
    """Scenario-20 的 filter 是 product-catalog-.*,id 是 productcatalog-deployment-1
    (答案檔自己少一個連字號),前兩階段都不中,靠 ⑧4 的無條件 fallback 得分。
    """
    res = score_one("productcatalog-deployment-1", gt("Scenario-20"))
    assert res.score == 1
    assert res.stage == "root_id"


def test_no_filter_scenario_falls_back_to_id():
    """10 題沒有 filter 的場景(資料集導覽 §7 的 7-11),第 1 階段直接跳過。"""
    res = score_one("flagd-config", gt("Scenario-2"))
    assert res.score == 1
    assert res.stage == "root_id"


def test_invalid_regex_is_skipped_not_raised(capsys):
    """Scenario-38 的 filter 是 `*.*`,re.compile 會丟 re.error(⑧2)。

    不處理的話整批評分會在這一題炸掉。跳過之後這題靠第 3 階段。
    """
    assert gt("Scenario-38").root.filters == ["*.*"]  # 答案檔的字面值
    res = score_one("otel-demo-horizontal-pod-autoscalers", gt("Scenario-38"))
    assert res.score == 1
    assert res.stage == "root_id"
    # 不命中時要說明有跳過的 filter,不然這題判 0 分會看不出原因
    assert "skipped invalid filters" in score_one("something-else", gt("Scenario-38")).detail


def test_partial_match_semantics_opens_a_false_positive():
    """⑧1 選了部分命中(re.search),代價就地釘住。

    Scenario-102 的 filter 是 otel-demo(答案檔字面值),所以任何含這串的答案
    都會得分——包括答錯服務的。這條斷言不是說這樣是對的,是把已知的假陽性
    寫下來,W2 改判準時它會紅給人看。
    """
    assert gt("Scenario-102").root.filters == ["otel-demo"]
    assert score_one("otel-demo-frontend", gt("Scenario-102")).score == 1


def test_normalization_applies_to_all_three_stages():
    """⑧3:三個階段都先去頭尾空白 + 轉小寫。"""
    assert score_one("  Load-Generator-Pod-1  ", gt("Scenario-1")).score == 1  # 第 1 階段
    assert score_one("  FlagD-Config  ", gt("Scenario-2")).score == 1  # 第 3 階段


# §4 髒值算「沒回答」(規格⑤⑧5)
@pytest.mark.parametrize(
    "entity",
    [
        "",
        "   ",
        "load-generator-pod-1 (Pod in otel-demo)",
        "load-generator-pod-1, frontend-proxy-service-1",
        "根因是 load-generator-pod-1",
    ],
)
def test_dirty_entity_counts_as_no_answer(entity):
    """三種髒值都歸到「沒回答」,不是「答錯」。

    分數一樣是 0(⑤:留在分母裡),差別在事後看報表時分得出是格式壞掉還是答錯。
    """
    res = score_one(entity, gt("Scenario-1"))
    assert res.score == 0
    assert res.stage == "no_answer"


def test_parse_failure_is_scored_zero_not_dropped():
    """解析失敗(沒有 Diagnosis 物件)傳 None 進來,拿 0 分而不是丟例外。

    ⑤ 的核心:失敗的題留在分母裡,分母不因為失敗而縮。
    """
    res = score_one(None, gt("Scenario-1"))
    assert res.score == 0
    assert res.stage == "no_answer"


def test_entity_like_accepts_resource_names_rejects_prose():
    """⑧5 的字元集規則。右邊是規格寫下的正則語意。"""
    assert is_entity_like("load-generator-pod-1")
    assert is_entity_like("flagd-config")
    assert not is_entity_like("payment service")
    assert not is_entity_like("-leading-dash")
