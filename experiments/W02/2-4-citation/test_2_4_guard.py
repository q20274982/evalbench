# 作業 2-4 失敗中的測試(W02 §7 第 5 項)。
#
# 斷言的是防線的行為,不是模型的輸出:防線只標記、不改輸出,
# 所以修完之後「找不到」的條數不會變少,變的是那些題有沒有被標成「檢查沒過」。
#
# 輸入:調整用 25 題「破」那一輪存好的 trace(不打 API),不允許說不知道那一組。
# 比對基準:測試自己的逐字比對(下方 §1),不呼叫防線的比對函式——
# 共用的話防線寫錯,測試會錯在同一處照樣綠。基準來源是 trace 的 retrieval,
# 由程式在組 prompt 時寫入、不經過模型(§7 第 2 項)。
#
# 跑法(從 repo 根目錄;開關用執行腳本同一個環境變數):
#     CITATION_CHECK=off uv run pytest experiments/W02/2-4-citation/test_2_4_guard.py -s   # 紅
#     CITATION_CHECK=on  uv run pytest experiments/W02/2-4-citation/test_2_4_guard.py -s   # 綠
# 不在 pytest tests/ 的自動批次裡(§7 實作用決定 ②)。

import citation_check
from parsing import inspect
from tracing import TRACES_ROOT, read_trace

# 破那一輪的 run_id。跑完 run_2_4.py live(CITATION_CHECK=off)後填,來源 out/runs.jsonl。
BREAK_RUN_ID = ""


# §1 測試自己的逐字比對:大小寫一樣的子字串(W02 §3),空字串算找不到。
def appears(text: str, haystacks: list[str]) -> bool:
    return text != "" and any(text in h for h in haystacks)


def test_guard_marks_unsupported_answers():
    run_dir = TRACES_ROOT / BREAK_RUN_ID
    # 存檔不存在記失敗,不記略過:略過會被誤讀成「不是紅」(§7 第 5 項決定)
    paths = sorted(run_dir.glob("*.json")) if BREAK_RUN_ID else []
    assert paths, f"找不到破那一輪的 trace({run_dir}),先跑 run_2_4.py live"

    missing_ev, missing_ent, wrongly_failed = [], [], []
    n_not_found = 0
    for p in paths:
        t = read_trace(p)
        raw = t["response"]["raw"]
        if raw is None:
            continue  # 呼叫失敗:status 照第 4 項,不在斷言範圍
        obs = inspect(raw)
        if obs["verdict"] != "ok" or obs["diagnosis"].abstain:
            continue  # 解析失敗、說不知道:不在斷言範圍
        d = obs["diagnosis"]
        blocks = [b["text"] for b in t["retrieval"]]
        ev_ok = [appears(e, blocks) for e in d.evidence]
        ent_ok = appears(d.root_cause_entity, d.evidence)
        n_not_found += ev_ok.count(False)

        status = citation_check.check(t["retrieval"], obs)["status"]
        if not all(ev_ok) and status != "check_failed":
            missing_ev.append((t["trace_id"], status))
        if not ent_ok and status != "check_failed":
            missing_ent.append((t["trace_id"], status))
        if all(ev_ok) and ent_ok and status != "passed":
            wrongly_failed.append((t["trace_id"], status))

    # 前置檢查:災難要先重現,否則前兩條沒有題可查,第三條會讓測試直接綠
    assert n_not_found > 0, "災難沒有重現,回到破的步驟:存檔裡一條找不到的 evidence 都沒有"

    print(f"\n找不到的 evidence:{n_not_found} 條(1 輪,沒有區間)")
    print(f"有 evidence 找不到、卻沒標「檢查沒過」:{missing_ev}")
    print(f"實體不在任何 evidence、卻沒標「檢查沒過」:{missing_ent}")
    print(f"兩項都過、卻沒標「通過」:{wrongly_failed}")
    assert not missing_ev, f"有 evidence 找不到、卻沒標檢查沒過:{missing_ev}"
    assert not missing_ent, f"實體不在任何 evidence、卻沒標檢查沒過:{missing_ent}"
    assert not wrongly_failed, f"兩項都過、卻沒標通過:{wrongly_failed}"
