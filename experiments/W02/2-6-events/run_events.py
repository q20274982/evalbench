"""作業 2-6:events 換挑法,根因名稱有沒有進查詢 / prompt,以及檢索命中率@k。不打 agent。

設定出處:W02 §9.2(Calvin 2026-10-13)
  挑法(retrieval.PICKS):warning5 = 起點;warning_all = Warning 全部放,不設上限;
  dedup_reason / dedup_kind = 同 reason(或 regarding 的 kind)只留一筆,湊滿 5 筆
  搜尋設定兩組,同 2-6-agent:A = one/raw 不換 simple;B = each/raw 換 simple 一次合併
量三件事(每組跑 1 次;搜尋沒有隨機成分,新查詢的向量算一次存進 retrieval.query_vectors):
  §3 根因名稱在不在查詢 / prompt 裡。名稱 = 答案檔根因組 filter 去掉正規式符號後最長的一段,
     沒有 filter 就用 name(修掉 W02 §9.2 Scenario-14 的漏算)
  §4 排除版 hit@5,跟同一組搜尋設定的 warning5 逐題比
prompt 裡有沒有名稱 = 告警 + 挑出的 events + 搜尋前 10 塊的文字裡有沒有;§2 抽一題跟
pipeline.build_retrieval_prompt 的全文核對結論相同。

跑法(從 repo 根目錄,DB 要先起來;會打 embedding API 算新查詢的向量):
    POSTGRES_HOST=localhost uv run python experiments/W02/2-6-events/run_events.py
INV-13:答案檔只在量尺那一側(§1 名稱、relevance)讀,不進查詢與 prompt。
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import chunk_store  # noqa: E402
import relevance  # noqa: E402
import retrieval  # noqa: E402
from pipeline import RETRIEVAL_K, build_retrieval_prompt  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
TSV = "exp_w02_hybrid.tsv"  # 2-6-hybrid 先算好的 tsvector,那邊 §1 已核對跟現算相同
CONFIGS = {"A": ("one", False), "B": ("each", True)}
PICKS = retrieval.PICKS
WORKERS = 8
DEPTH = 20


# §1 量尺側:根因名稱
def root_name(scenario: str) -> str:
    g = next(g for g in relevance.alias_set(scenario) if g.root)
    if g.filters:
        return max(re.split(r"\.\*|\\b|[\\^$()|\[\]+?]", g.filters[0]), key=len)
    return g.name or g.id


# §2 一題、一組、一種挑法
def run_one(conn, s: str, cfg: str, pick: str, rel: set[str], name: str) -> dict:
    combine, split = CONFIGS[cfg]
    queries, _ = retrieval.build_queries(conn, s, "raw", combine, pick)
    alerts, _ = retrieval.alert_texts(conn, s)
    events, ev_ids = retrieval.event_texts(conn, s, "raw", pick)
    ranked = retrieval.hybrid_search(
        conn, s, "raw", combine, DEPTH, "simple", split, "flat", TSV, pick
    )
    top = ranked[:RETRIEVAL_K]
    texts = dict(
        conn.execute("SELECT id, text FROM retrieval.chunks WHERE id = ANY(%s)", (top,)).fetchall()
    )
    in_prompt = any(name in t for t in alerts + events + [texts[c] for c in top])
    return {
        "events 筆數": len(ev_ids),
        "名稱在查詢": any(name in q for q in queries),
        "名稱在 events 段": any(name in t for t in events),
        "名稱在搜尋前 10": any(name in texts[c] for c in top),
        "名稱在 prompt": in_prompt,
        "prompt 字元": sum(map(len, alerts + events + [texts[c] for c in top])),
        **relevance.per_scenario(ranked, rel),
        "前 10": top,
    }


def run_scenario(s: str) -> dict:
    conn = chunk_store.connect()
    chunks = conn.execute(
        f"SELECT id, source, text, key FROM {chunk_store.SCHEMA}.chunks WHERE scenario = %s", (s,)
    ).fetchall()
    rel = relevance.relevant_ids(s, chunks)
    name = root_name(s)
    return {
        "名稱": name,
        **{f"{c}|{p}": run_one(conn, s, c, p, rel, name) for c in CONFIGS for p in PICKS},
    }


def main() -> None:
    t0 = time.time()
    conn = chunk_store.connect()
    scenarios = sorted(
        (r[0] for r in conn.execute(f"SELECT DISTINCT scenario FROM {chunk_store.SCHEMA}.chunks")),
        key=lambda s: int(s.split("-")[1]),
    )
    # 新查詢的向量先依序算好存起來,平行跑時不會同一段文字各算一次
    for s in scenarios:
        for c in CONFIGS:
            for p in PICKS:
                qs, _ = retrieval.build_queries(conn, s, "raw", CONFIGS[c][0], p)
                retrieval.query_vectors(conn, qs)
    print(f"[§2] 查詢向量備好,{time.time() - t0:.0f} 秒", flush=True)

    with ThreadPoolExecutor(WORKERS) as ex:
        per = dict(zip(scenarios, ex.map(run_scenario, scenarios)))
    print(f"[§2] 25 題跑完,{time.time() - t0:.0f} 秒", flush=True)

    # §2 核對:抽一題,prompt 全文的結論跟這裡的算法相同
    s = "Scenario-80"
    for c, (combine, split) in CONFIGS.items():
        for p in PICKS:
            full, _ = build_retrieval_prompt(conn, s, combine, split, pick=p)
            assert (per[s]["名稱"] in full) == per[s][f"{c}|{p}"]["名稱在 prompt"], (c, p)
    print("[§2] Scenario-80 核對 prompt 全文:相同", flush=True)

    # §3 名稱在不在 / §4 hit@5 逐題比
    table = []
    for c in CONFIGS:
        base = {s: per[s][f"{c}|warning5"] for s in scenarios}
        for p in PICKS:
            rows = {s: per[s][f"{c}|{p}"] for s in scenarios}
            ks = [s for s in scenarios if s not in relevance.EXCLUDED and rows[s]["相關塊數"]]
            win = [s for s in ks if rows[s]["hit@5"] and not base[s]["hit@5"]]
            lose = [s for s in ks if base[s]["hit@5"] and not rows[s]["hit@5"]]
            num = lambda xs: [int(x.split("-")[1]) for x in xs]  # noqa: E731
            line = {
                "組|挑法": f"{c}|{p}",
                "名稱在查詢": len([s for s in scenarios if rows[s]["名稱在查詢"]]),
                "名稱在 prompt": num([s for s in scenarios if rows[s]["名稱在 prompt"]]),
                "events 筆數 最少–最多": [
                    min(r["events 筆數"] for r in rows.values()),
                    max(r["events 筆數"] for r in rows.values()),
                ],
                "prompt 字元 最多": max(r["prompt 字元"] for r in rows.values()),
                "排除版題數": len(ks),
                "hit@5 中幾題": sum(rows[s]["hit@5"] for s in ks),
                "hit@5 贏": num(win),
                "hit@5 輸": num(lose),
            }
            table.append(line)
            print(json.dumps(line, ensure_ascii=False), flush=True)
    OUT.mkdir(exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps({"每題": per, "對照": table}, ensure_ascii=False, indent=1)
    )


if __name__ == "__main__":
    main()
