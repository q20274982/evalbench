"""W01 追加|同一批答案,兩套判準差幾分。

只讀 out/1_3_records.json(作業 1-3 的 live 輸出),不打 API、不花錢。

  A 套 = Calvin 的 §7.3.1 規格:根因 filter → 同服務成員 filter → 根因/同服務 id,
         跨服務的下游受害者不算對。
  B 套 = ITBench 官方 judge prompt 的規則(itbench-hub/ITBench-Evaluations 的
         itbench_evaluations/prompts/main.py):「If ANY entity in an alias group has
         root_cause: true, then matching ANY entity in that alias group counts as
         correctly identifying the root cause.」也就是 alias 組內任一個都算對,
         不分同服務或下游。

⚠️ B 套是**近似**,不是官方的完整實作。兩個已知差距:
  ① 官方要求 name 對得上 filter **與 kind**;這裡只比 name,沒有 kind 這個訊號
     (模型只吐一個字串,沒有吐 kind)。
  ② 官方的 matches_gt 是 LLM judge 給的,這裡是機械比對。同樣寫「差不多的名字」,
     judge 可能算對,機械比對不會。
所以 B 套的數字是「官方規則在機械比對下的下界」,不等於官方跑出來的分數。

跑法:
    docker compose run --rm app uv run python experiments/W01/rescore_1_3.py
"""

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from scoring import ScoreResult, _match_any, is_entity_like, load_ground_truth, normalize  # noqa: E402
from scoring import score_one  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = (
    REPO_ROOT / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
)
RECORDS = Path(__file__).parent / "out/1_3_records.json"


# §1 B 套:alias 組內任一個都算對
def score_official(entity: str | None, gt) -> ScoreResult:
    if entity is None or not is_entity_like(entity):
        return ScoreResult(0, "沒回答", "")
    e = normalize(entity)

    hit, _ = _match_any(gt.root.filters, e)
    if hit:
        return ScoreResult(1, "根因 filter", "")
    if normalize(gt.root.id) == e:
        return ScoreResult(1, "根因 id", "")

    # 根因所在的 alias 組,整組都算對(官方規則)
    by_id = {g.id: g for g in gt.groups}
    for grp in gt.aliases:
        if gt.root.id not in grp:
            continue
        for member_id in grp:
            if normalize(member_id) == e:
                return ScoreResult(1, "alias 組內 id", member_id)
            member = by_id.get(member_id)
            if member is not None:
                hit, _ = _match_any(member.filters, e)
                if hit:
                    return ScoreResult(1, "alias 組內 filter", member_id)
    return ScoreResult(0, "不命中", "")


# §2 並排報
def main() -> int:
    records = json.loads(RECORDS.read_text(encoding="utf-8"))
    gts = {s: load_ground_truth(DATASET_ROOT / s) for s in {r["scenario"] for r in records}}

    rounds = sorted({r["round"] for r in records})
    a_rates, b_rates = [], []
    flipped = []
    for rnd in rounds:
        rows = [r for r in records if r["round"] == rnd]
        a = sum(score_one(r["entity"], gts[r["scenario"]]).score for r in rows)
        b = 0
        for r in rows:
            sb = score_official(r["entity"], gts[r["scenario"]]).score
            b += sb
            sa = score_one(r["entity"], gts[r["scenario"]]).score
            if sa != sb:
                flipped.append((rnd, r["scenario"], r["entity"], sa, sb))
        a_rates.append(a / len(rows))
        b_rates.append(b / len(rows))
        print(f"  第 {rnd} 輪:A 套 {a}/{len(rows)} = {a / len(rows):.0%}"
              f"   B 套 {b}/{len(rows)} = {b / len(rows):.0%}")

    def band(rates: list[float]) -> str:
        line = f"{' / '.join(f'{x:.0%}' for x in rates)}|全距 {max(rates) - min(rates):.0%}"
        if len(rates) > 1:
            line += f"|標準差 {statistics.stdev(rates):.3f}"
        return line

    print(f"\nA 套(Calvin 的 §7.3.1):{band(a_rates)}")
    print(f"B 套(ITBench 官方規則的機械近似):{band(b_rates)}")

    print(f"\n判定不一樣的題:{len(flipped)} 次")
    for rnd, sc, entity, sa, sb in flipped:
        print(f"  [{rnd}/{sc}] {entity!r}  A={sa} B={sb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
