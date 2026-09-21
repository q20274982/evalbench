"""W01 作業 1-1|一個場景目錄進來之後:六種來源各幾筆、時間範圍、佔多少記憶體。

spec 是 Calvin 定的(2026-09-13 對話),四個決定:
  ① 不建完整解析層。只量筆數/時間範圍/記憶體,欄位內容不拆。
     所以 tags 的 Python dict 字串、Body 的巢狀 JSON 一律當不透明字串跳過。
  ② 行數報「邏輯筆數」,不報物理行。otel_logs 有三成的 Body 欄含換行
     (資料集導覽 §4.4),wc -l 會多數三成,所以一律走 csv 解析器。
  ③ 記憶體兩種讀法各量一次:streaming(邊讀邊丟)與 in-memory(整份留著)。
     量的是行程 RSS 峰值。
  ④ 時間戳不在載入層轉,下游要比才轉。所以掃描時只留原字串,
     最小/最大用字典序比——同一來源內日期時間是固定寬度、小數秒是純十進位小數,
     兩者的字典序都等同數值序(實測 alerts 的小數秒位數不固定,7 位與 9 位並存,
     但小數的字典序不受位數影響);
     轉成 datetime 只發生在最後印表那一步。

跑法:
    docker compose run --rm app uv run python experiments/W01/1-1-inventory/run_1_1_inventory.py

為什麼要開子行程:RSS 峰值是只升不降的高水位。同一個行程裡先量 in-memory
再量 streaming,streaming 會讀到前一次留下的高點。所以每個 (來源, 讀法) 各開
一個子行程,量它自己的峰值。
"""

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# §1 常數
REPO_ROOT = Path(__file__).resolve().parents[3]
# 資料集版本字串只寫在這一個地方(資料集導覽 §3.2 的警告)。
DATASET_ROOT = REPO_ROOT / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
SCENARIO = "Scenario-1"
OUT_PATH = Path(__file__).parent / "out/1_1_inventory.json"

# 六種來源。kind 決定怎麼掃,ts_field 是 §4 決定要取的時間欄。
# §1.1 六種來源
SOURCES = [
    {"name": "alerts",       "kind": "alerts_dir", "path": "alerts",              "ts_field": "activeAt"},
    {"name": "metrics",      "kind": "tsv_dir",    "path": "metrics",             "ts_field": "timestamp"},
    {"name": "otel_logs",    "kind": "tsv",        "path": "otel_logs_raw.tsv",   "ts_field": "Timestamp"},
    {"name": "otel_traces",  "kind": "tsv",        "path": "otel_traces_raw.tsv", "ts_field": "Timestamp"},
    {"name": "k8s_events",   "kind": "tsv",        "path": "k8s_events_raw.tsv",  "ts_field": "Timestamp"},
    {"name": "k8s_objects",  "kind": "tsv",        "path": "k8s_objects_raw.tsv", "ts_field": "Timestamp"},
]

# §1.2 兩種讀法
MODES = ["stream", "memory"]

# otel_logs 的 Body 欄單格可以很大,預設 131072 會炸。不用 sys.maxsize:
# 某些平台上它塞不進 C 的 long,csv 模組會丟 OverflowError。
# §1.3 單格大小上限
csv.field_size_limit(10**9)


# §2 掃描:產出「時間戳原字串」的序列
# 每個 iter_* 回傳一個 generator,吐的是該筆記錄的時間戳原字串(不轉型)。
# spec ① 的「不建解析層」就落在這裡:其餘欄位讀進來但不碰。

# §2.1
def iter_tsv(path: Path, ts_field: str):
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter="\t", quotechar='"')
        header = next(reader, None)
        if header is None:
            return
        idx = header.index(ts_field)
        for row in reader:
            yield row[idx] if idx < len(row) else ""


# §2.2
def iter_tsv_dir(path: Path, ts_field: str):
    # metrics/ 底下 pod_*.tsv 與 service_*.tsv 欄位數不同,但兩種都有 timestamp 欄,
    # 所以按欄名取、不按位置取。
    for f in sorted(path.glob("*.tsv")):
        yield from iter_tsv(f, ts_field)


# §2.3
def iter_alerts_dir(path: Path, ts_field: str):
    # 坑 7-2:alerts_at_*.json 最外層是陣列,alerts_in_alerting_state_*.json
    # 最外層是物件、告警在 data.alerts 底下。Scenario-1 兩種都有。
    for f in sorted(path.glob("*.json")):
        raw = json.loads(f.read_text(encoding="utf-8"))
        alerts = raw if isinstance(raw, list) else raw.get("data", {}).get("alerts", [])
        for alert in alerts:
            yield alert.get(ts_field, "")


# §2.4
def iter_tsv_rows(path: Path, ts_field: str):
    """跟 iter_tsv 同一份檔,但吐整列,不只吐時間戳那一欄(spec ⑤ 用)。"""
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter="\t", quotechar='"')
        header = next(reader, None)
        if header is None:
            return
        idx = header.index(ts_field)
        for row in reader:
            yield row, (row[idx] if idx < len(row) else "")


# §2.5 派工表
ITERATORS = {"tsv": iter_tsv, "tsv_dir": iter_tsv_dir, "alerts_dir": iter_alerts_dir}

# spec ⑤:整列留著只量這一個來源。
FULL_ROW_SOURCE = "otel_logs"


# §3 子行程:量一個 (來源, 讀法) 的筆數、時間範圍、RSS 峰值

# §3.1
def peak_rss_bytes() -> int:
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux 給 KB,macOS 給 bytes。容器裡跑是 Linux,留這行是為了在 host 直接跑也對。
    return peak * 1024 if sys.platform.startswith("linux") else peak


# §3.2
def run_worker(source: dict, mode: str) -> dict:
    scenario_dir = DATASET_ROOT / SCENARIO
    target = scenario_dir / source["path"]
    it = ITERATORS[source["kind"]](target, source["ts_field"])

    # §3.2.1 邊讀邊丟
    if mode == "stream":
        # 邊讀邊丟:任何時刻手上只有一個時間戳字串加兩個極值。
        count = 0
        lo = hi = None
        for ts in it:
            count += 1
            if not ts:
                continue
            if lo is None or ts < lo:
                lo = ts
            if hi is None or ts > hi:
                hi = ts
    # §3.2.2 整列留著
    elif mode == "memory_full":
        # 整列留著:每一欄都在記憶體裡,不只時間戳。這是「整份載入」的真實佔用。
        rows = list(iter_tsv_rows(target, source["ts_field"]))
        count = len(rows)
        present = [ts for _, ts in rows if ts]
        lo = min(present) if present else None
        hi = max(present) if present else None
    # §3.2.3 只留時間戳欄
    else:
        # 整份留著:所有時間戳字串同時在記憶體裡。
        # 註:這只留時間戳一欄,不是整份資料,所以這個數字是 in-memory 讀法的
        # 下界,不是上界。真的把每一欄都留著會更高。這一點寫進工作簿時要講。
        all_ts = list(it)
        count = len(all_ts)
        present = [t for t in all_ts if t]
        lo = min(present) if present else None
        hi = max(present) if present else None

    return {
        "source": source["name"],
        "mode": mode,
        "records": count,
        "ts_min_raw": lo,
        "ts_max_raw": hi,
        "peak_rss_bytes": peak_rss_bytes(),
    }


# §4 下游:唯一轉時間戳的地方(spec ④)

# §4.1
def parse_ts(raw: str | None) -> datetime | None:
    """把原字串轉成 datetime。轉不動就回 None,不猜、不補時區。"""
    if not raw:
        return None
    s = raw.strip()
    tz_aware = s.endswith("Z")
    if tz_aware:
        s = s[:-1]
    if "T" in s:
        s = s.replace("T", " ")
    # 小數秒最多六位,資料裡是九位(奈秒)。多的截掉。
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt


# §4.2
def fmt_ts(raw: str | None) -> str:
    dt = parse_ts(raw)
    if dt is None:
        return "—"
    mark = "Z" if raw and raw.strip().endswith("Z") else "(無時區標記)"
    return f"{dt:%H:%M:%S} {mark}"


# §4.3
def fmt_bytes(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB"


# §5 入口

# §5.1
def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        source = next(s for s in SOURCES if s["name"] == sys.argv[2])
        print(json.dumps(run_worker(source, sys.argv[3])))
        return 0

    plan = [(s_, m) for s_ in SOURCES for m in MODES]
    plan += [(s_, "memory_full") for s_ in SOURCES if s_["name"] == FULL_ROW_SOURCE]

    results = []
    for source, mode in plan:
        proc = subprocess.run(
            [sys.executable, __file__, "--worker", source["name"], mode],
            capture_output=True, text=True, check=True,
        )
        results.append(json.loads(proc.stdout))
        print(f"  量完 {source['name']} / {mode}", file=sys.stderr)

    by_key = {(r["source"], r["mode"]): r for r in results}

    print(f"\n場景:{SCENARIO}(資料集 {DATASET_ROOT.name})\n")
    header = f"{'來源':<14}{'邏輯筆數':>12}  {'時間範圍':<34}{'RSS streaming':>15}{'RSS in-memory':>15}"
    print(header)
    print("-" * len(header))
    for source in SOURCES:
        s = by_key[(source["name"], "stream")]
        m = by_key[(source["name"], "memory")]
        span = f"{fmt_ts(s['ts_min_raw'])} → {fmt_ts(s['ts_max_raw'])}"
        print(
            f"{source['name']:<14}{s['records']:>12,}  {span:<34}"
            f"{fmt_bytes(s['peak_rss_bytes']):>15}{fmt_bytes(m['peak_rss_bytes']):>15}"
        )

    full = by_key.get((FULL_ROW_SOURCE, "memory_full"))
    if full:
        stream = by_key[(FULL_ROW_SOURCE, "stream")]
        src_bytes = (DATASET_ROOT / SCENARIO / next(
            s_["path"] for s_ in SOURCES if s_["name"] == FULL_ROW_SOURCE)).stat().st_size
        print(
            f"\n整列留著(spec ⑤,只量 {FULL_ROW_SOURCE}):"
            f"{fmt_bytes(full['peak_rss_bytes'])}"
            f"|原始檔 {fmt_bytes(src_bytes)}"
            f"|倍率 {full['peak_rss_bytes'] / src_bytes:.1f}x"
            f"|同來源 streaming {fmt_bytes(stream['peak_rss_bytes'])}"
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {"scenario": SCENARIO, "dataset": DATASET_ROOT.name, "measurements": results},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n寫出:{OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
