"""W01 目標 5|trace 寫得出去、讀得回來(不打 API,不花錢)。

規格正本是 W01 工作簿 `§7.6.1`。這支檢查的是「撈得回來」那一半的前置:
欄位齊不齊、寫進去的跟讀回來的是不是同一份、失敗的呼叫有沒有落檔,以及
照存的切法參數重建 prompt 會不會跟存檔全文一致(①c 的用途)。

真的再送一次 API 的重放在 src/replay.py 的 live 模式,
那一支要花錢,不放進 pytest(慣例同 W00 的 check_api.py)。

跑法:
    docker compose run --rm app uv run pytest tests/test_tracing.py -s
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

import pipeline as base  # noqa: E402
from replay import REQUIRED, _get, run_check  # noqa: E402
from tracing import build_trace, new_run_id, read_trace, redact, write_trace  # noqa: E402

SCENARIO = "Scenario-1"


def _make_trace(run_id: str, **kw) -> dict:
    """用真的切片造一筆 trace。基準是資料集本身,不是任何模型輸出。"""
    prompt, _ = base.build_prompt(SCENARIO)
    params = {k: getattr(base, k) for k in base.SLICE_PARAMS_KEYS}
    return build_trace(
        run_id=run_id, scenario=SCENARIO, round_no=1,
        prompt=prompt, slice_params=params,
        request_body=base.build_request_body(prompt),
        **kw,
    )


def test_必要欄位都在(tmp_path):
    """§1 的 REQUIRED 少任何一個,這筆 trace 就重放不了。"""
    trace = _make_trace(new_run_id(), raw='{"root_cause_entity": "x"}')
    missing = [".".join(p) for p in REQUIRED if _get(trace, p) is None]
    assert not missing, f"缺欄位:{missing}"
    assert trace["retrieval"] is None, "④ 本週固定留空位子"
    print(f"\n[trace] 欄位齊全,versions={trace['versions']}")


def test_寫進去讀回來是同一份(tmp_path):
    run_id = new_run_id()
    trace = _make_trace(run_id, raw="hello", usage={"cost": 0.001})
    path = write_trace(trace, root=tmp_path)
    assert path == tmp_path / run_id / f"{SCENARIO}_1.json", "⑤ 一題一檔的路徑不對"
    assert read_trace(path) == trace


def test_失敗的呼叫也落檔(tmp_path):
    """③a:少掉的題之後要查得出是為什麼少的。"""
    trace = _make_trace(new_run_id(), error="HTTP 429 rate limited")
    path = write_trace(trace, root=tmp_path)
    back = read_trace(path)
    assert back["response"]["ok"] is False
    assert "429" in back["response"]["error"]
    assert back["response"]["raw"] is None


def test_api_key_不會落地():
    """② 存整包 body,但 key 不進檔案。"""
    assert redact({"model": "m", "api_key": "sk-secret"})["api_key"] == "<redacted>"


def test_讀回驗證跑得動(tmp_path, capsys):
    """check 模式:只讀那個檔,判定撈不撈得回來。回 0 = §10 後半成立。"""
    run_id = new_run_id()
    path = write_trace(_make_trace(run_id, raw='{"root_cause_entity": "x"}'), root=tmp_path)
    assert run_check(path) == 0
    out = capsys.readouterr().out
    print(out)
    assert "與存檔全文一致" in out, "①c:照存的參數重建 prompt 應該還原得出當初那份"
