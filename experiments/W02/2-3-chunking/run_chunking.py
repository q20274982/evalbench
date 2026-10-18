# 作業 2-3|照 SPEC §8.4 切 25 題,驗「別人讀完能照著切出一樣的塊」的前兩關。
#
# 問題:規格寫得夠不夠細,細到照著做就切得出確定的結果?這支驗兩件事:
#   1. 同一份輸入在兩個獨立的 process 各切一次,輸出逐位元相同(sha256 一樣)。
#      分成兩個 process 是因為 Python 的字串 hash 每個 process 不同,同一個 process 跑兩次
#      會蓋掉「靠 set / dict 迭代順序」這類不確定性
#   2. 各來源的塊數印出來,跟 SPEC §8.4 寫的舊數字並排(比對基準是規格原文,不是本程式算的)
#
# 第三關(程式裡每個判斷分支都對得到 §8.4 的一句)不在這支,是交付時的對照表。
#
# 輸入:25 題調整用場景。留出的 10 題寫死排除(W02 工作簿 §3,Calvin 2026-10-04 決定),
# 不打開、不印數字;不讀 ground_truth.yaml。純讀檔,不打 API、不連資料庫。
#
# 跑法:
#     uv run python experiments/W02/2-3-chunking/run_chunking.py           # 切兩次並比對
#     uv run python experiments/W02/2-3-chunking/run_chunking.py --once out/x.jsonl  # 只切一次
# 輸出:out/run1.jsonl、out/run2.jsonl(不進版控)、out/counts.json

import collections
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))

from chunking import SOURCES, chunk_scenario  # noqa: E402

BASE = REPO / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
HELD_OUT = {6, 9, 12, 15, 16, 20, 23, 29, 31, 35}  # 同 2-3-fault-types/count_fault_types.py

# SPEC §8.4 寫的舊數字(25 題合計),照抄規格原文,當並排的參考
SPEC_NUMBERS = {
    "traces": "2,829 種(只取一個狀態碼的舊算法,§8.4 補充規則說不再適用)",
    "logs": "18,788 組",
    "alerts": "1,105 個合併單位(§8.4 補充規則)",
    "metrics": "104,889 條(§8.3 的定義,跟本支的身分定義不同)",
    "objects": "18,132 個版本;超長切出的段另計",
    "events": "無舊數字",
}


def tune_scenarios() -> list[Path]:
    dirs = [d for d in BASE.iterdir() if d.name.startswith("Scenario-")]
    dirs = [d for d in dirs if int(d.name.split("-")[1]) not in HELD_OUT]
    return sorted(dirs, key=lambda d: int(d.name.split("-")[1]))


def run_once(out_path: Path) -> None:
    counts: dict[str, collections.Counter] = {}
    obj_stats = collections.Counter()
    with open(out_path, "w") as f:
        for scn in tune_scenarios():
            t0 = time.time()
            chunks, stats = chunk_scenario(scn)
            obj_stats.update(stats)
            per = collections.Counter(c.source for c in chunks)
            counts[scn.name] = per
            seq = collections.Counter()
            for c in chunks:
                seq[c.source] += 1
                rec = asdict(c) | {"id": f"{scn.name}/{c.source}/{seq[c.source]:06d}",
                                   "scenario": scn.name, "key": list(c.key)}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{scn.name}: {dict(per)} ({time.time() - t0:.0f}s)", file=sys.stderr, flush=True)
    total = sum(counts.values(), collections.Counter())
    (out_path.parent / "counts.json").write_text(json.dumps(
        {"total": total, "objects_stats": obj_stats, "per_scenario": counts},
        ensure_ascii=False, indent=1))


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    if sys.argv[1:2] == ["--once"]:
        run_once(Path(sys.argv[2]))
        return
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    runs = [out / "run1.jsonl", out / "run2.jsonl"]
    for p in runs:
        subprocess.run([sys.executable, __file__, "--once", str(p)], check=True)
    hashes = [sha256(p) for p in runs]
    c = json.loads((out / "counts.json").read_text())
    print("\n§1 兩次輸出是否相同")
    for p, h in zip(runs, hashes):
        print(f"  {p.name}: sha256 {h[:16]}…  {p.stat().st_size:,} bytes")
    print(f"  相同:{hashes[0] == hashes[1]}")
    print("\n§2 25 題各來源塊數 vs SPEC §8.4 舊數字")
    for s in SOURCES:
        print(f"  {s:8s} {c['total'].get(s, 0):>9,}   規格:{SPEC_NUMBERS[s]}")
    st = c["objects_stats"]
    print(f"  objects 細項:{st['rows']:,} 列 → {st['versions']:,} 個版本,"
          f"其中 {st['long_versions']:,} 個超長被切段")
    print(f"  objects 重新序列化:只差在 < > & 寫法 {st['reserialize_escape_only']:,} 列;"
          f"其他差異 {st['reserialize_other']:,} 列(應為 0)")


if __name__ == "__main__":
    main()
