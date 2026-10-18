# 作業 2-6:改動前的起點——只用向量搜尋的檢索命中率@k,查詢 2 × 2 種組法各跑 1 次。
#
# 組法(Calvin 2026-10-11):
#   combine     one = 告警與 events 壓成一個查詢;each = 一筆一個查詢再用 RRF 合併(R01 §12)
#   events_mode raw = events 用塊的原文;fields = 只取 reason、note、regarding 的 kind 與 name
# 被測系統在 src/retrieval.py,量尺在 src/relevance.py,兩邊不共用判斷程式。
#
# 跑法(從 repo 根目錄,DB 要先起來):
#     POSTGRES_HOST=localhost uv run python experiments/W02/2-6-vector-baseline/run_vector_baseline.py
# 每個設定跑 1 次:語料向量已固定在 DB、查詢向量算一次就存、全表精確排序加塊 id 當次要鍵,重跑結果相同。

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import chunk_store  # noqa: E402
import relevance  # noqa: E402
import retrieval  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
CONFIGS = [(c, e) for c in ("one", "each") for e in ("raw", "fields")]
SHOW = ["hit@1", "hit@5", "hit@10", "hit@20", "recall@20 macro", "precision@5"]


def main() -> None:
    conn = chunk_store.connect()
    scenarios = [
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT scenario FROM {chunk_store.SCHEMA}.chunks"
        ).fetchall()
    ]
    scenarios.sort(key=lambda s: int(s.split("-")[1]))

    # §1 量尺:每題哪些塊算相關(跟查詢組法無關,算一次)
    rel = {}
    for s in scenarios:
        chunks = conn.execute(
            f"SELECT id, source, text, key FROM {chunk_store.SCHEMA}.chunks WHERE scenario = %s",
            (s,),
        ).fetchall()
        rel[s] = relevance.relevant_ids(s, chunks)
    print("[相關塊數]", {s: len(v) for s, v in rel.items()})

    # §2 四種組法各跑一次
    results = {}
    for combine, events_mode in CONFIGS:
        name = f"{combine}/{events_mode}"
        rows, ranked_all = {}, {}
        for s in scenarios:
            ranked = retrieval.search(conn, s, events_mode, combine, max(relevance.KS))
            ranked_all[s] = ranked
            rows[s] = relevance.per_scenario(ranked, rel[s])
        groups = relevance.report_groups(rows)
        results[name] = {"每題": rows, "分組": groups, "前 20 名": ranked_all}
        print(f"\n[{name}]")
        for g, agg in groups.items():
            shown = {k: round(agg[k], 4) for k in SHOW if agg.get(k) is not None}
            print(f"  {g:10s} 題數 {agg.get('題數')}  {shown}")

    OUT.mkdir(exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
