"""W00 作業 0-5|「隨機猜」基準線:agent 不看任何觀測資料,純亂猜能拿幾分。

這支在算什麼
------------
ITBench-Lite 有 35 個 SRE 場景。每個場景的 ground_truth.yaml 帶一個 `groups` 清單
(n 項),其中恰好一項標了 `root_cause: true`。任務設定是:agent 從這 n 項裡挑 k 個
id 當候選,正確根因落在候選裡就算這題命中。

「隨機猜」= 完全不看告警、指標、log、trace,直接從 n 項裡等機率抽 k 個(不重複)。
單場景命中機率有閉式解,不需要模擬:

    P(命中) = min(k, n) / n

    (n 項裡只有 1 項是對的,抽 k 項不放回,對的那項被抽中的機率就是 k/n;
     k >= n 時整包都抽走,必中,所以取 min(k, n)。)

35 場景的平均命中機率(對 35 個單場景機率取「不加權算術平均」)就是該 k 的
random baseline。因為是閉式解,不跑亂數,所以沒有 run-to-run 區間。

為什麼要算
----------
作業 0-5 要幫評測台的「根因命中」訂一條及格門檻。門檻的意義在於「贏過亂猜」——
如果一個只會亂猜的 agent 就能過門檻,這條門檻沒有鑑別力。所以要先知道亂猜在
k = 1..5 各能拿多少,門檻必須明顯高過對應的這個數字。

注意 n 偏小(最小 2、中位數約 5),所以 k 大的時候 baseline 會很高,k=5 時
亂猜就逼近全中。門檻能真正拉開差距的區間是 k=1、k=2。

ground_truth.yaml 的兩種寫法
----------------------------
  A 型(31 個):`groups` 直接在檔案頂層。
  B 型(4 個,Scenario 20 / 23 / 31 / 105):k8s CR 風格,頂層是
    apiVersion / kind / metadata / spec,`groups` 在 `spec` 底下。
兩種都讀,讀不出來的場景會在下面「異常場景」區明確列出,不靜默跳過;有異常時
腳本以非 0 結束碼收工。

跑法
----
    cd ~/evalbench && docker compose run --rm app \
        uv run python experiments/W00/0-5-random-baseline/run_0_5_random_baseline.py
"""

import glob
import statistics
import sys

import yaml

SRE_GLOB = "data/ITBench-Lite/snapshots/sre/v0.2-*/Scenario-*/ground_truth.yaml"
K_VALUES = [1, 2, 3, 4, 5]


def extract_groups(doc):
    """回傳 (groups_list, shape)。shape 是 'A' / 'B' / '?'。"""
    if isinstance(doc, dict) and isinstance(doc.get("groups"), list):
        return doc["groups"], "A"
    spec = doc.get("spec") if isinstance(doc, dict) else None
    if isinstance(spec, dict) and isinstance(spec.get("groups"), list):
        return spec["groups"], "B"
    return None, "?"


def load_scenarios():
    """讀所有場景。回傳 (ok, anomalies)。

    ok       = [(scenario_name, n, shape), ...]
    anomalies= [(scenario_name, reason, top_level_keys), ...]
    """
    ok = []
    anomalies = []
    for path in sorted(glob.glob(SRE_GLOB), key=_scenario_sort_key):
        name = path.split("/")[-2]
        try:
            doc = yaml.safe_load(open(path))
        except yaml.YAMLError as exc:
            anomalies.append((name, f"YAML 解析失敗: {exc}", []))
            continue
        keys = sorted(doc.keys()) if isinstance(doc, dict) else [f"<{type(doc).__name__}>"]
        groups, shape = extract_groups(doc)
        if groups is None:
            anomalies.append((name, "找不到 groups 清單(A/B 兩種寫法都不符)", keys))
            continue
        n = len(groups)
        rc = sum(1 for g in groups if isinstance(g, dict) and g.get("root_cause") is True)
        if rc != 1:
            anomalies.append((name, f"root_cause: true 的項數 = {rc}(預期 1)", keys))
            continue
        if n < 1:
            anomalies.append((name, f"groups 清單長度 = {n}", keys))
            continue
        ok.append((name, n, shape))
    return ok, anomalies


def _scenario_sort_key(path):
    name = path.split("/")[-2]
    try:
        return (0, int(name.split("-")[1]))
    except (IndexError, ValueError):
        return (1, name)


def hit_prob(k, n):
    return min(k, n) / n


def fmt_pct(x):
    return f"{x * 100:5.1f}%"


def print_per_scenario_table(ok):
    header = f"{'場景':<14}{'n':>4}   " + "".join(f"k={k:<7}" for k in K_VALUES)
    print(header)
    print("-" * len(header))
    for name, n, _shape in ok:
        row = f"{name:<14}{n:>4}   "
        row += "".join(f"{fmt_pct(hit_prob(k, n)):<8}" for k in K_VALUES)
        print(row)


def print_baseline_table(ok):
    print(f"{'k':>3}   {'random baseline(35 場景平均命中機率)':<20}")
    print("-" * 48)
    for k in K_VALUES:
        mean_p = statistics.fmean(hit_prob(k, n) for _name, n, _s in ok)
        print(f"{k:>3}   {fmt_pct(mean_p)}   ({mean_p:.4f})")


def print_n_distribution(ok):
    ns = sorted(n for _name, n, _s in ok)
    print(f"場景數(有效)      : {len(ns)}")
    print(f"n 最小 / 最大      : {min(ns)} / {max(ns)}")
    print(f"n 中位數          : {statistics.median(ns)}")
    print(f"n 平均            : {statistics.fmean(ns):.3f}")
    print()
    print(f"{'n':>4}   {'場景數':>6}   分佈")
    print("-" * 40)
    for value in range(min(ns), max(ns) + 1):
        count = ns.count(value)
        if count:
            print(f"{value:>4}   {count:>6}   {'#' * count}")


def print_shape_tally(ok):
    a = sum(1 for _n, _cnt, s in ok if s == "A")
    b = sum(1 for _n, _cnt, s in ok if s == "B")
    print(f"ground_truth.yaml 寫法:A 型(頂層 groups)= {a}  /  B 型(spec.groups)= {b}")


def main():
    ok, anomalies = load_scenarios()

    print("=" * 72)
    print("W00 作業 0-5 — 隨機猜基準線(閉式解 min(k,n)/n,不跑亂數,無區間)")
    print("=" * 72)
    print()
    print_shape_tally(ok)
    print()

    print("── 每場景命中機率 ─────────────────────────────────────────────────────")
    print_per_scenario_table(ok)
    print()

    print("── random baseline:k = 1..5 ─────────────────────────────────────────")
    print_baseline_table(ok)
    print()

    print("── groups 項數 n 的分佈 ─────────────────────────────────────────────")
    print_n_distribution(ok)
    print()

    print("── 異常場景(解析失敗 / 結構不符)──────────────────────────────────")
    if not anomalies:
        print("(無)35 個場景全部讀出,且各自恰好一項 root_cause: true。")
    else:
        for name, reason, keys in anomalies:
            print(f"  {name}: {reason}")
            print(f"    頂層 keys = {keys}")
    print()

    if anomalies:
        print(f"有 {len(anomalies)} 個異常場景,以非 0 結束碼收工。")
        sys.exit(1)


if __name__ == "__main__":
    main()
