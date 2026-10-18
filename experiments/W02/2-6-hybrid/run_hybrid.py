# 作業 2-6:混合搜尋(向量 + 關鍵字,RRF 合併)vs 改動前的起點(只用向量),檢索命中率@k。
#
# 設定出處:W02 §9.2「兩份名次表怎麼合併」「關鍵字搜尋的設定」(Calvin 2026-10-11),共 24 組:
#   起點的 4 種組法(combine one/each × events_mode raw/fields)
#   × split(拆字前把 / . : 換成空白與否)× ts_config(english / simple)
#   × fuse(只在 each:flat / two_level)
# 起點 = retrieval.search(排除組查詢用到的塊,code repo 8a84c01),同一次跑重算,不讀舊的 result.json。
# 比較:排除版每題 hit@5,跟同一組法的起點逐題比——新中舊沒中 = 贏,舊中新沒中 = 輸。
#
# 跑法(從 repo 根目錄,DB 要先起來):
#     POSTGRES_HOST=localhost uv run python experiments/W02/2-6-hybrid/run_hybrid.py
# 每組跑 1 次:向量與查詢向量已固定在 DB,ts_rank 與 RRF 同分都照塊 id 排,沒有隨機成分。
#
# 加速(結果不變,只省時間):
#   - 塊的 tsvector 先算好存進本實驗自己的表 exp_w02_hybrid.tsv(4 種切字設定 × 全部塊,表在就沿用),
#     不然 24 組會把同一份 tsvector 重算 6 次。§1 會抽一題比對先算好的與現算的名次表完全相同
#   - 25 題分給 WORKERS 條執行緒平行跑,一條一個 DB 連線

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
import chunk_store  # noqa: E402
import relevance  # noqa: E402
import retrieval  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
BASES = [(c, e) for c in ("one", "each") for e in ("raw", "fields")]
K = max(relevance.KS)
WORKERS = 8
TSV = "exp_w02_hybrid.tsv"
TS_SETTINGS = [(c, sp) for c in ("simple", "english") for sp in (False, True)]


def build_tsv(cfg_split: tuple[str, bool]) -> None:
    ts_config, split = cfg_split
    conn = chunk_store.connect()
    have = conn.execute(
        f"SELECT count(*) FROM {TSV} WHERE ts_config = %s AND split = %s", (ts_config, split)
    ).fetchone()[0]
    if have:
        return
    conn.execute(
        f"INSERT INTO {TSV} SELECT id, scenario, %s, %s, "
        f"to_tsvector(%s::regconfig, {retrieval.doc_expr(split)}) "
        f"FROM {chunk_store.SCHEMA}.chunks",
        (ts_config, split, ts_config),
    )


def configs():
    for combine, events_mode in BASES:
        for split in (False, True):
            for ts_config in ("simple", "english"):
                for fuse in ("flat", "two_level") if combine == "each" else ("flat",):
                    yield combine, events_mode, split, ts_config, fuse


# 排除版的題目(relevance.report_groups 同一條件)。
def kept(rows: dict) -> list[str]:
    return [s for s, r in rows.items() if s not in relevance.EXCLUDED and r["相關塊數"]]


# 一題:起點 4 種組法 + 混合 24 組。回傳 (起點每題列, 混合每題列, 混合前 20 名)。
def run_scenario(s: str, rel: set[str]) -> tuple[dict, dict, dict]:
    conn = chunk_store.connect()
    base = {
        f"{c}/{e}": relevance.per_scenario(retrieval.search(conn, s, e, c, K), rel)
        for c, e in BASES
    }
    rows, ranked = {}, {}
    for combine, events_mode, split, ts_config, fuse in configs():
        name = f"{combine}/{events_mode}|split={split}|{ts_config}|{fuse}"
        ranked[name] = retrieval.hybrid_search(
            conn, s, events_mode, combine, K, ts_config, split, fuse, TSV
        )
        rows[name] = relevance.per_scenario(ranked[name], rel)
    return base, rows, ranked


def main() -> None:
    t0 = time.time()
    conn = chunk_store.connect()
    scenarios = sorted(
        (r[0] for r in conn.execute(f"SELECT DISTINCT scenario FROM {chunk_store.SCHEMA}.chunks")),
        key=lambda s: int(s.split("-")[1]),
    )

    # §1 先算好的 tsvector:建表,並抽一題核對跟現算的名次表相同
    conn.execute("CREATE SCHEMA IF NOT EXISTS exp_w02_hybrid")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {TSV} (id text, scenario text, ts_config text, "
        "split boolean, v tsvector, PRIMARY KEY (ts_config, split, id))"
    )
    with ThreadPoolExecutor(len(TS_SETTINGS)) as ex:
        list(ex.map(build_tsv, TS_SETTINGS))
    qs, used = retrieval.build_queries(conn, "Scenario-33", "raw", "each")
    for ts_config, split in TS_SETTINGS:
        live = retrieval.keyword_rankings(conn, "Scenario-33", qs, 100, used, ts_config, split)
        cached = retrieval.keyword_rankings(
            conn, "Scenario-33", qs, 100, used, ts_config, split, TSV
        )
        assert live == cached, f"先算好的 tsvector 跟現算的不同:{ts_config} split={split}"
    print(f"[§1] tsvector 表備好、核對相同,{time.time() - t0:.0f} 秒", flush=True)

    # §2 量尺:每題哪些塊算相關
    rel = {}
    for s in scenarios:
        chunks = conn.execute(
            f"SELECT id, source, text, key FROM {chunk_store.SCHEMA}.chunks WHERE scenario = %s",
            (s,),
        ).fetchall()
        rel[s] = relevance.relevant_ids(s, chunks)

    # §3 起點與 24 組混合搜尋,25 題平行跑
    with ThreadPoolExecutor(WORKERS) as ex:
        per = dict(zip(scenarios, ex.map(lambda s: run_scenario(s, rel[s]), scenarios)))
    print(f"[§3] 25 題跑完,{time.time() - t0:.0f} 秒", flush=True)
    base = {b: {s: per[s][0][b] for s in scenarios} for b in per[scenarios[0]][0]}

    # §4 逐題跟同一組法的起點比 hit@5
    results, table = {}, []
    for combine, events_mode, split, ts_config, fuse in configs():
        name = f"{combine}/{events_mode}|split={split}|{ts_config}|{fuse}"
        rows = {s: per[s][1][name] for s in scenarios}
        b = base[f"{combine}/{events_mode}"]
        ks = kept(rows)
        win = [s for s in ks if rows[s]["hit@5"] and not b[s]["hit@5"]]
        lose = [s for s in ks if b[s]["hit@5"] and not rows[s]["hit@5"]]
        chaos = [s for s in ks if relevance.is_chaos(s)]
        line = {
            "設定": name,
            "排除版題數": len(ks),
            "起點 hit@5 中幾題": sum(b[s]["hit@5"] for s in ks),
            "混合 hit@5 中幾題": sum(rows[s]["hit@5"] for s in ks),
            "贏": win,
            "輸": lose,
            "淨贏": len(win) - len(lose),
            "chaos 混合 hit@5 中幾題": sum(rows[s]["hit@5"] for s in chaos),
        }
        table.append(line)
        results[name] = {
            "每題": rows,
            "分組": relevance.report_groups(rows),
            "前 20 名": {s: per[s][2][name] for s in scenarios},
        }
        print(json.dumps(line, ensure_ascii=False), flush=True)

    OUT.mkdir(exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps({"起點": base, "混合": results, "對照": table}, ensure_ascii=False, indent=1)
    )


if __name__ == "__main__":
    main()
