# 作業 2-2|35 個場景的根因名稱規則,在各自的抽樣語料裡比中幾塊。
#
# 問題:第二輪發現 `ad` 這種短 filter 用子字串比對幾乎什麼都中。「只算根因實體」的定義
# 要先確認:各場景根因那一組的 filter 會不會也有同樣的問題(太寬),或像 Scenario-5 一樣
# 只在少數來源出現(太窄)。
#
# 輸入(純讀檔,不打 API、不跑 embedding、不連資料庫):
#   - 35 個場景,每個場景照 2-1 的語料定義抽樣:六種來源各抽 2000 行(不足全收),
#     一行一塊,種子 20260924(與 2-1 相同)
#   - 各場景 ground_truth.yaml 的 root_cause group:有 filter 用 re.search(任一條),
#     沒有用 id 子字串(不分大小寫)——與第一輪相同
# 自我檢查:Scenario-5 必須是 10 塊(第一輪報告 §3),不等就停。
#
# 跑法:
#     uv run python experiments/W02/2-2-evidence-trial/check_root_filters.py
# 輸出:終端表格;out/root_filters.json

# ruff: noqa: E501
import json
import random
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "data/ITBench-Lite/snapshots/sre/v0.2-B96DF826-4BB2-4B62-97AB-6D84254C53D7"
PER_SOURCE = 2000
SEED = 20260924
ORDER = ["logs", "traces", "events", "k8s_objects", "metrics", "alerts"]
OUT = Path(__file__).parent / "out"


def sources(sc: Path) -> dict[str, list[Path]]:
    return {
        "logs": [sc / "otel_logs_raw.tsv"],
        "traces": [sc / "otel_traces_raw.tsv"],
        "events": [sc / "k8s_events_raw.tsv"],
        "k8s_objects": [sc / "k8s_objects_raw.tsv"],
        "metrics": sorted(sc.glob("metrics/*.tsv")),
        "alerts": sorted(sc.glob("alerts/*.json")),
    }


# 與 2-1 的 sample_source 同邏輯,改成吃絕對路徑、檔案不存在就跳過
def sample(files: list[Path]) -> list[str]:
    files = [f for f in files if f.exists()]
    counts = []
    for f in files:
        with open(f, "rb") as fh:
            counts.append(sum(1 for _ in fh))
    total = sum(counts)
    picked = set(range(total)) if total <= PER_SOURCE else set(random.Random(SEED).sample(range(total), PER_SOURCE))
    rows, offset = [], 0
    for f, n in zip(files, counts, strict=True):
        with open(f, "rb") as fh:
            for i, raw in enumerate(fh):
                if offset + i in picked:
                    rows.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        offset += n
    return rows


def matcher(rc: dict):
    if rc.get("filter"):
        # 開頭的 `.*` 在 re.search 下語意多餘(任一位置比得上即可),但遇到上萬字元的行會
        # 讓每個起點都掃到行尾,Scenario-17 的 `.*product-catalog` 因此卡住 17 分鐘以上
        try:
            pats = [re.compile(p.removeprefix(".*")) for p in rc["filter"]]
        except re.error as e:  # Scenario-38 的 filter 是 `*.*`(像萬用字元,不是正規表示式)
            return None, f"{rc['filter']} 無法編譯:{e}"
        return lambda t: any(p.search(t) for p in pats), rc["filter"]
    key = rc["id"].lower()
    return lambda t: key in t.lower(), f"id:{rc['id']}"


def main() -> None:
    results = []
    scenarios = sorted(BASE.glob("Scenario-*"), key=lambda p: int(p.name.split("-")[1]))
    # Scenario-5 先跑,當自我檢查
    scenarios.sort(key=lambda p: p.name != "Scenario-5")
    if len(sys.argv) > 1:  # 只跑指定場景(中途失敗後補跑用),例:... 38 80 81
        scenarios = [p for p in scenarios if p.name.split("-")[1] in sys.argv[1:]]
    for sc in scenarios:
        raw = yaml.safe_load(open(sc / "ground_truth.yaml"))
        content = raw.get("spec", raw)
        rc = next(g for g in content["groups"] if g.get("root_cause"))
        hit, cond = matcher(rc)
        if hit is None:
            results.append({"scenario": sc.name, "root_id": rc["id"], "kind": rc.get("kind"), "condition": cond, "invalid": True})
            print(f"{sc.name:13} {rc['id']:32} {cond}", flush=True)
            continue
        per, size = {}, 0
        for src, files in sources(sc).items():
            rows = sample(files)
            size += len(rows)
            per[src] = sum(1 for r in rows if hit(r))
        total = sum(per.values())
        results.append({"scenario": sc.name, "root_id": rc["id"], "kind": rc.get("kind"), "condition": cond,
                        "corpus": size, "per_source": per, "total": total})
        print(f"{sc.name:13} {rc['id']:32} {str(cond)[:40]:40} " + " ".join(f"{per[s]:5}" for s in ORDER) + f" | {total:5} / {size}", flush=True)
        if sc.name == "Scenario-5" and total != 10:
            sys.exit(f"自我檢查失敗:Scenario-5 應為 10 塊,實得 {total}")
    OUT.mkdir(exist_ok=True)
    name = "root_filters.json" if len(sys.argv) == 1 else f"root_filters_{'_'.join(sys.argv[1:])}.json"
    (OUT / name).write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
