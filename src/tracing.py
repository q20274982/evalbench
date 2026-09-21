"""可重放紀錄(trace)的寫入與讀取。

規格正本是 W01 工作簿 `§7.6.1`(Calvin 2026-09-21 定案)。存在的理由是
INV-5:三個月後拿一筆診斷結果,只讀那個檔就重放得出當初那次呼叫,而且
分得出結果不同是因為模型換了、還是自己把碼改了。

五個決策照規格落地:

  ① 輸入存 prompt 全文 + 切法參數兩個都存  → build_trace 的 input 段
  ② 存整包 request body(扣掉 API key)     → build_trace 的 request 段
  ③ 存 code commit sha + 判準版本           → build_trace 的 versions 段
  ④ 檢索欄位留位子,本週固定 null           → build_trace 的 retrieval
  ⑤ 一題一檔 traces/<run_id>/<場景>_<輪>    → write_trace

補充三條(同日定案,`§7.6.1` 表格下方):判準版本取 src/scoring.py 內容的
sha256 前 12 碼(①a);traces/ 進 .gitignore,不進版控(②b);呼叫失敗的
那一筆也落一份,response.ok = false(③a)。

這個檔不打模型、不評分,只負責把一次呼叫的前因後果寫成檔案。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACES_ROOT = REPO_ROOT / "traces"
SCORING_PATH = REPO_ROOT / "src/scoring.py"


def new_run_id() -> str:
    """一次 run 一個 id,同一批紀錄放同一個資料夾(⑤)。"""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def code_version() -> dict:
    """③ 的前半:哪一版的碼跑出這筆。

    dirty = 工作區有未 commit 的改動,這時 commit sha 不足以定位當時的碼。
    """
    return {
        "commit": _git("rev-parse", "HEAD") or None,
        "dirty": bool(_git("status", "--porcelain")),
    }


def scoring_version() -> str | None:
    """③ 的後半:判準版本 = src/scoring.py 內容的 sha256 前 12 碼(①a)。

    取內容而不是 commit,是因為還沒 commit 的改動也要看得出來。看得出
    「不一樣了」,看不出「改了什麼」——後者靠 code_version 的 commit 去查。
    """
    if not SCORING_PATH.is_file():
        return None
    return hashlib.sha256(SCORING_PATH.read_bytes()).hexdigest()[:12]


def redact(body: dict) -> dict:
    """② 存整包 request body,但 API key 不落地。

    現在的 body 本身不帶 key(key 在 HTTP header),這個函式是防線:
    之後有人把 key 放進 body 時,不會靜悄悄寫進 traces/。
    """
    clean = dict(body)
    for k in ("api_key", "authorization", "Authorization"):
        if k in clean:
            clean[k] = "<redacted>"
    return clean


def build_trace(
    *,
    run_id: str,
    scenario: str,
    round_no: int,
    prompt: str,
    slice_params: dict,
    request_body: dict,
    raw: str | None = None,
    usage: dict | None = None,
    error: str | None = None,
    retrieval: dict | None = None,
) -> dict:
    """組一筆可重放紀錄。error 有值 = 這次呼叫失敗(③a),仍然落檔。"""
    return {
        "trace_id": f"{run_id}/{scenario}_{round_no}",
        "run_id": run_id,
        "scenario": scenario,
        "round": round_no,
        "created_at": datetime.now(UTC).isoformat(),
        # ① 兩個都存:全文是當初真的送出去的字,參數是重建它需要的設定。
        "input": {"prompt": prompt, "slice_params": slice_params},
        # ④ W2 接上檢索後填這一欄,本週固定 null,紀錄形狀不因此改變。
        "retrieval": retrieval,
        "request": redact(request_body),
        "response": {
            "ok": error is None,
            "raw": raw,
            "usage": usage or {},
            "error": error,
        },
        "versions": {"code": code_version(), "scoring_sha12": scoring_version()},
    }


def write_trace(trace: dict, root: Path = TRACES_ROOT) -> Path:
    """⑤ 一題一檔:traces/<run_id>/<場景>_<輪>.json。"""
    out_dir = root / trace["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{trace['scenario']}_{trace['round']}.json"
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_trace(path: Path) -> dict:
    """重放那一側的入口:只讀這個檔,不碰產生它的腳本。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))
