"""評分層:把一次診斷的 root_cause_entity 判成 0 或 1。

規格正本是 W01 工作簿 `§7.3.1` ①–⑧(Calvin 2026-09-20 定案)。這一層只做
W1 的暫時比法——本週不變量是刻意留下之後會被打臉的設計,所以這裡的每一條
規則都預期會壞,壞在哪裡寫在規格的「會在哪裡壞」那幾段,不在這裡重複。

七條規格對應到這個檔的哪一段:

  ① 評分只看 root_cause_entity，其餘三欄照收不計分      → §3 score_one
  ② 標準答案 = 根因 group + aliases 裡同服務的成員       → §2 same_service_members
  ③ 三階段比對，早命中早結束                             → §3 score_one
  ④ 0/1，沒有部分分                                      → §3 score_one 的回傳
  ⑤ 解析失敗算 0 分、留在分母                            → 分母由驅動腳本掌握，
                                                          這一層只回報單題
  ⑥ 跑全部 35 題                                         → 驅動腳本
  ⑦ 判官是程式，不是模型                                 → 整個檔沒有模型呼叫
  ⑧ 六個實作分支                                         → 各段就地註明 ⑧n

INV-13:這個檔會讀 ground_truth.yaml，因為它是評分層。答案只能往這個方向流,
不准回到 prompt 或索引裡。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 資源名的字元集(⑧5)。K8s 的 DNS-1123 子網域寫法:小寫英數起頭與結尾,
# 中間允許連字號與點。整串不符合就算「沒回答」,不是答錯。
ENTITY_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")

# 正則的元字元。抽字面前綴時掃到第一個就停(②的 filter 訊號)。
META_CHARS = set(".*+?[]{}()|^$\\")


def normalize(text: str) -> str:
    """去頭尾空白 + 轉小寫(⑧3:三個階段都先做這一步)。"""
    return text.strip().lower()


def is_entity_like(text: str) -> bool:
    """這個字串長得像一個資源名嗎(⑧5)。

    False 的三種來源:空字串、帶括號說明(`load-generator-pod-1 (Pod)`)、
    一次答兩個實體(逗號或空白隔開)。三種都歸到「沒回答」。
    """
    return bool(ENTITY_RE.match(normalize(text)))


# §1 讀答案檔
@dataclass
class Group:
    """ground_truth.yaml 的一個 group。"""

    id: str
    kind: str = ""
    filters: list[str] = field(default_factory=list)
    root_cause: bool = False


@dataclass
class GroundTruth:
    scenario: str
    root: Group
    groups: list[Group]
    aliases: list[list[str]]


def load_ground_truth(scenario_dir: Path) -> GroundTruth:
    """讀一個場景的答案檔。

    35 份裡有 4 份(Scenario-20、23、31、105)外面包了一層 apiVersion/kind/
    metadata/spec,真正的內容縮在 spec 底下(資料集導覽 §7 的 7-01)。
    兩種寫法在這裡收斂成同一個形狀。
    """
    raw = yaml.safe_load((scenario_dir / "ground_truth.yaml").read_text(encoding="utf-8"))
    body = raw.get("spec", raw)
    groups = []
    for g in body.get("groups") or []:
        groups.append(
            Group(
                id=str(g.get("id", "")),
                kind=str(g.get("kind") or ""),
                filters=[str(f) for f in (g.get("filter") or [])],
                root_cause=bool(g.get("root_cause")),
            )
        )
    roots = [g for g in groups if g.root_cause]
    if len(roots) != 1:
        raise ValueError(f"{scenario_dir.name}:root_cause group 有 {len(roots)} 個,預期 1 個")
    aliases = [[str(x) for x in grp] for grp in (body.get("aliases") or [])]
    return GroundTruth(scenario_dir.name, roots[0], groups, aliases)


# §2 同服務成員(②)
def literal_prefix(pattern: str) -> str:
    """從一條 filter 抽出字面共同前綴——②的第一個訊號。

    砍掉開頭的 `.*`、拿掉 `\\b`,再取到第一個正則元字元為止。尾端的分隔符
    一併砍掉,因為同一個服務的兩條 filter 常常一條帶尾巴一條不帶:
    Scenario-1 的 `load-generator-.*`(Pod)與 `load-generator\\b`(Service)
    指的是同一個服務,不砍尾端分隔符的話這兩條會被判成不同服務,而規格②
    明講那兩個要算同服務。
    """
    p = pattern.strip()
    while p.startswith(".*"):
        p = p[2:]
    p = p.replace(r"\b", "")
    out = []
    for ch in p:
        if ch in META_CHARS:
            break
        out.append(ch)
    return "".join(out).rstrip("-_.").lower()


def id_prefix(group: Group) -> str:
    """從 id 砍掉 kind 段之後的前綴——②的第二個訊號。

    kind 段的樣子是 `-pod-1` / `-service-1`,也就是 `-<kind 小寫>` 後面接
    選擇性的 `-<編號>`。id 不帶 kind 段時(`flagd-config`)整串原樣回傳,
    這時兩個訊號抽出來的東西對不上,AND 不成立——規格②已經寫下這個後果。
    """
    gid = group.id.strip().lower()
    kind = group.kind.strip().lower()
    if not kind:
        return gid
    m = re.search(rf"-{re.escape(kind)}(-\d+)?$", gid)
    return gid[: m.start()] if m else gid


def same_service_members(gt: GroundTruth) -> list[Group]:
    """aliases 裡跟根因同服務的成員(②:兩個訊號都同意才算)。

    跨服務的成員不納入——Scenario-1 的 aliases 組裡有 frontend-proxy-*,
    同一份檔案的 propagations 說它是下游受害者,答它不算對。
    """
    by_id = {g.id: g for g in gt.groups}
    root_fp = {literal_prefix(f) for f in gt.root.filters}
    root_ip = id_prefix(gt.root)

    members = []
    for grp in gt.aliases:
        if gt.root.id not in grp:
            continue
        for member_id in grp:
            if member_id == gt.root.id:
                continue
            member = by_id.get(member_id)
            if member is None:
                continue
            # 訊號一:filter 的字面前綴要有交集。任一邊沒有 filter 就抽不出
            # 訊號,AND 不成立。
            member_fp = {literal_prefix(f) for f in member.filters}
            signal_filter = bool(root_fp & member_fp) and "" not in (root_fp & member_fp)
            # 訊號二:砍掉 kind 段之後的前綴要一樣。
            signal_id = bool(root_ip) and id_prefix(member) == root_ip
            if signal_filter and signal_id:
                members.append(member)
    return members


# §3 比一題(①③④⑧)
@dataclass
class ScoreResult:
    score: int
    stage: str
    detail: str


def _match_any(filters: list[str], entity: str) -> tuple[bool, list[str]]:
    """拿一組 filter 去比一個資源名。

    ⑧1:部分命中(re.search),對應 JS `.match()` 的語意,不錨定。
    ⑧2:無效正則跳過,不讓整批評分炸掉(Scenario-38 的 filter 是 `*.*`,
         re.compile 直接丟 re.error)。
    """
    skipped = []
    for pat in filters:
        try:
            rx = re.compile(pat.lower())
        except re.error:
            skipped.append(pat)
            continue
        if rx.search(entity):
            return True, skipped
    return False, skipped


def score_one(entity: str | None, gt: GroundTruth) -> ScoreResult:
    """一題的 0/1。

    entity 是 None 代表上游解析失敗(schema 沒過、模型沒吐這個欄位)。
    """
    if entity is None:
        return ScoreResult(0, "no_answer", "parse failed, no root_cause_entity")
    if not is_entity_like(entity):
        return ScoreResult(0, "no_answer", f"not a valid resource name: {entity[:60]!r}")

    e = normalize(entity)

    # 第 1 階段:根因 group 的 filter
    hit, skipped = _match_any(gt.root.filters, e)
    if hit:
        return ScoreResult(1, "root_filter", f"{gt.root.filters} ~ {e}")

    # 第 2 階段:aliases 裡同服務成員的 filter
    for member in same_service_members(gt):
        hit, _ = _match_any(member.filters, e)
        if hit:
            return ScoreResult(1, "same_service_filter", f"{member.id}{member.filters} ~ {e}")

    # 第 3 階段:跟 id 比(⑧4:無條件 fallback,不限定沒有 filter 的場景。
    # 實測有 3 題的 filter 連自己的 id 都比不到)。
    # 比的範圍是根因 + 同服務成員(Calvin 2026-09-20 決定的 (c) 案):成員的
    # filter 比不到成員自己的 id 時(Scenario-20 的 productcatalog-pod-1、
    # Scenario-34 的 valkey-service-1),規格②說要收的成員在前兩階段收不進來。
    if normalize(gt.root.id) == e:
        return ScoreResult(1, "root_id", f"{gt.root.id} == {e}")
    for member in same_service_members(gt):
        if normalize(member.id) == e:
            return ScoreResult(1, "same_service_id", f"{member.id} == {e}")

    note = f" (skipped invalid filters {skipped})" if skipped else ""
    return ScoreResult(0, "miss", f"{e} vs {gt.root.id}{gt.root.filters}{note}")
