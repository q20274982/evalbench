"""W01 目標 5|讀回驗證:只拿一個 trace 檔,重放當初那一次呼叫。

規格正本是 W01 工作簿 `§7.6.2`。`§10` 那條勾選是兩半——「trace 開始存」由
src/tracing.py 負責,「確認撈得回來」是這支腳本。跑得動,那一條才勾得掉。

判準:重放的人手上只有那個 JSON 檔。所以這裡不重新讀資料集去造 prompt,
一律用檔案裡存的 request body 原樣再送一次。唯一例外是 check 模式的第 3 項,
它刻意去重建 prompt,目的就是抓「切法被改掉了」。

兩個模式:
  check <檔案>  — 不打 API。①欄位齊不齊 ②重放需要的東西在不在 ③照存的切法
                  參數重建 prompt,跟存的全文比對。不花錢。
  live  <檔案>  — 真的再送一次同一包 body,比對這次的輸出與當初存的。
                  一題約 $0.0008(依據工作簿 §7.5.1:六次 $0.0048)。

跑法:
    docker compose run --rm app uv run python src/replay.py \
        check traces/<run_id>/Scenario-1_1.json
    docker compose run --rm app uv run python src/replay.py \
        live traces/<run_id>/Scenario-1_1.json

比對基準是「當初那一筆 trace 自己存的內容」,不是任何模型或 judge 的裁決
(/scaffold 慣例 6:斷言右邊不能是左邊算出來的)。

2026-09-21 從 experiments/W01/ 搬進 src/:這是跨週的系統工具(INV-5 要到 W6 才
收尾),不是某一份作業的一次性腳本(/scaffold 慣例 8)。重建 prompt 那一段改成
呼叫 src/pipeline.py 的 build_prompt,不再 import 作業腳本。
"""

import json
import sys
import urllib.error
from pathlib import Path

from parsing import inspect
from pipeline import build_prompt, call_model
from tracing import code_version, read_trace, scoring_version

# §1 重放跑不動就缺的欄位。少任何一個,這筆 trace 就不滿足 INV-5。
REQUIRED = [
    ("input", "prompt"),
    ("input", "slice_params"),
    ("request", "model"),
    ("request", "messages"),
    ("versions", "code"),
    ("versions", "scoring_sha12"),
]


def _get(trace: dict, path: tuple[str, ...]):
    cur = trace
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


# §2 check 模式(不打 API)
def run_check(path: Path) -> int:
    trace = read_trace(path)
    print(f"trace {trace.get('trace_id')}|存於 {trace.get('created_at')}")

    # §2.1 欄位齊不齊
    missing = [".".join(p) for p in REQUIRED if _get(trace, p) is None]
    print(f"\n§2.1 重放必要欄位:{'全齊' if not missing else '缺 ' + ', '.join(missing)}")

    # §2.2 當初的版本 vs 現在的版本
    then_code = trace["versions"]["code"]
    now_code = code_version()
    then_scoring = trace["versions"]["scoring_sha12"]
    now_scoring = scoring_version()
    print("\n§2.2 版本比對(不同不代表錯,代表結果對不上時知道去看哪裡)")
    print(f"  code commit  當初 {then_code.get('commit')} (dirty={then_code.get('dirty')})")
    print(f"               現在 {now_code.get('commit')} (dirty={now_code.get('dirty')})")
    print(f"  判準 sha12   當初 {then_scoring} → 現在 {now_scoring} "
          f"{'(相同)' if then_scoring == now_scoring else '(已改動)'}")

    # §2.3 ①c 的用途:照存的切法參數重建 prompt,跟存的全文比對
    params = trace["input"]["slice_params"]
    import pipeline as base
    saved = {k: getattr(base, k) for k in params}
    for k, v in params.items():
        setattr(base, k, v)
    try:
        rebuilt, _ = build_prompt(trace["scenario"])
    finally:
        for k, v in saved.items():
            setattr(base, k, v)
    same = rebuilt == trace["input"]["prompt"]
    print(f"\n§2.3 照 {params} 重建 prompt:{'與存檔全文一致' if same else '與存檔全文不同'}")
    if not same:
        print(f"  長度 重建 {len(rebuilt)} vs 存檔 {len(trace['input']['prompt'])}")
        print("  → 切法或資料集在這之後被改過,存檔全文才是當初真的送出去的字")

    ok = not missing
    print(f"\n判定:{'撈得回來(§10 後半成立)' if ok else '缺欄位,重放不了'}")
    return 0 if ok else 1


# §3 live 模式(真的再送一次)
def run_live(path: Path) -> int:
    trace = read_trace(path)
    body = trace["request"]
    print(f"重放 {trace['trace_id']}|model={body.get('model')}")
    try:
        raw, usage = call_model(body)
    except urllib.error.HTTPError as e:
        print(f"✗ HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
        return 1

    then_raw = trace["response"]["raw"]
    then_entity = (inspect(then_raw)["diagnosis"].root_cause_entity
                   if then_raw and inspect(then_raw)["diagnosis"] else None)
    now_diag = inspect(raw)["diagnosis"]
    now_entity = now_diag.root_cause_entity if now_diag else None

    print("\n§3.1 逐字比對")
    print(f"  原始輸出完全相同:{raw == then_raw}")
    print("\n§3.2 評分看的那一欄(root_cause_entity)")
    print(f"  當初 {then_entity!r} → 這次 {now_entity!r} "
          f"{'(一致)' if then_entity == now_entity else '(不一致)'}")
    if then_entity != now_entity:
        print("  → 兩個嫌疑犯:模型那一側變了,或 §2.2 的版本欄位顯示自己改了碼。"
              "先跑 check 模式看版本。")
    # §7.6.2 ①b(Calvin 2026-09-21):判定看評分那一欄,不看逐字。
    # 逐字一致在這個供應商的 temperature=0 下達不到——2026-09-21 同一包 body
    # 重放三次,原始輸出三次都不同,而 root_cause_entity 拿到兩種寫法。
    same = then_entity == now_entity
    print(f"\n判定:{'重放一致(評分那一欄相同)' if same else '重放不一致'}")
    print(f"本次花費 ${float(usage.get('cost') or 0):.4f}")
    print(f"當初這次呼叫的 usage:{json.dumps(trace['response']['usage'], ensure_ascii=False)}")
    return 0 if same else 1


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode in ("check", "live") and len(sys.argv) > 2:
        path = Path(sys.argv[2])
        if not path.is_file():
            print(f"找不到 {path}")
            return 2
        return run_check(path) if mode == "check" else run_live(path)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
