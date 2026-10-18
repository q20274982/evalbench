# 可重放紀錄(trace)的寫入與讀取。
#
# 規格正本是 W01 工作簿 `§7.6.1`(Calvin 2026-09-21 定案)。存在的理由是
# INV-5:三個月後拿一筆診斷結果,只讀那個檔就重放得出當初那次呼叫,而且
# 分得出結果不同是因為模型換了、還是自己把碼改了。
#
# 五個決策照規格落地:
#
#   ① 輸入存 prompt 全文 + 切法參數兩個都存  → build_trace 的 input 段
#   ② 存整包 request body(扣掉 API key)     → build_trace 的 request 段
#   ③ 存 code commit sha + 判準版本           → build_trace 的 versions 段
#   ④ 檢索欄位留位子,W1 固定 null           → build_trace 的 retrieval
#      (W2 起存比對基準,見 W02 工作簿 §7 第 2 項)
#   ⑤ 一題一檔 traces/<run_id>/<場景>_<輪>    → write_trace
#
# 補充四條(同日定案,`§7.6.1` 表格下方):判準版本取 src/scoring.py 內容的
# sha256 前 12 碼(①a);traces/ 進 .gitignore,不進版控(②b);呼叫失敗的
# 那一筆也落一份,response.ok = false(③a);commit sha 純讀 .git 拿,不裝 git
# 指令,所以 dirty 欄固定 None(不知道)。
#
# 完整欄位見下方 Trace(TypedDict)。這個檔不打模型、不評分,只負責把一次呼叫的前因後果寫成檔案。

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACES_ROOT = REPO_ROOT / "traces"
SCORING_PATH = REPO_ROOT / "src/scoring.py"


# trace 的形狀。只是寫在碼裡的欄位說明(Calvin 2026-10-18 選 TypedDict):
# 執行時不檢查,repo 也沒有型別檢查器,欄位寫錯不會報錯。
# 落檔後就是 JSON,一題一檔(⑤)。


# ① 輸入:兩個都存。
class TraceInput(TypedDict):
    prompt: str  # 當初真的送出去的 prompt 全文
    slice_params: dict  # 重建 prompt 需要的設定(W1 切法參數;W2 起含搜尋設定)


# ④ 檢索欄位的一塊:prompt 第三段「Retrieved context」裡的一塊(W02 §7 第 2 項)。
class RetrievedBlock(TypedDict):
    id: str  # 例:Scenario-1/objects/000714
    text: str  # 那一塊的原文,與送進 prompt 的一字不差


# 模型那一側收到什麼。呼叫失敗也落檔(③a)。
class TraceResponse(TypedDict):
    ok: bool  # error 為 None 時為 True
    raw: str | None  # 模型的原始輸出(含 evidence),失敗時為 None
    usage: dict  # 供應商回的 token 數與花費
    error: str | None  # 失敗原因;成功時為 None


class CodeVersion(TypedDict):
    commit: str | None  # 純讀 .git 拿到的 sha;拿不到為 None
    dirty: bool | None  # 固定 None = 不知道(見 code_version)


# ③ 版本鉤子。
class TraceVersions(TypedDict):
    code: CodeVersion
    scoring_sha12: str | None  # src/scoring.py 內容的 sha256 前 12 碼(①a)


# 一筆可重放紀錄。欄位定案見 W01 工作簿 `§7.6.1`。
class Trace(TypedDict):
    trace_id: str  # "<run_id>/<場景>_<輪>"
    run_id: str  # 一次 run 一個,同一批放同一個資料夾
    scenario: str  # 例:Scenario-1
    round: int  # 第幾輪,從 1 起
    created_at: str  # UTC ISO 8601
    input: TraceInput
    retrieval: list[RetrievedBlock] | None  # ④ W1 為 None;W2 起存比對基準
    request: dict  # ② 整包 request body,API key 已遮(redact)
    response: TraceResponse
    versions: TraceVersions


# 一次 run 一個 id,同一批紀錄放同一個資料夾(⑤)。
def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


# 讀 .git 拿當前 commit,不呼叫 git 指令。
#
# Calvin 2026-09-21 選 (b):執行容器(python:3.13-slim)裡沒有 git,而裝
# git 要動 Dockerfile。純讀檔拿得到 sha,拿不到 dirty——代價寫在
# code_version 的 dirty 欄。
#
# HEAD 兩種形態:指向分支(`ref: refs/heads/master`)或直接是 sha(detached)。
# 分支的 sha 可能在 refs/ 底下,也可能被打包進 packed-refs。
def _head_commit() -> str | None:
    git_dir = REPO_ROOT / ".git"
    head = git_dir / "HEAD"
    if not head.is_file():
        return None
    text = head.read_text(encoding="utf-8").strip()
    if not text.startswith("ref:"):
        return text or None
    ref = text.split(":", 1)[1].strip()
    ref_path = git_dir / ref
    if ref_path.is_file():
        return ref_path.read_text(encoding="utf-8").strip() or None
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.startswith(("#", "^")):
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return None


# ③ 的前半:哪一版的碼跑出這筆。
#
# dirty 固定 None = 不知道,不是「乾淨」。判斷工作區有沒有未 commit 的改動
# 要比對整棵樹,純讀 .git 做不到(見 _head_commit 的 (b) 決定)。寫成 None
# 而不是 False,是因為假的「乾淨」比缺資料危險:重放對不上時會被誤導成
# 「碼沒動過」。src/scoring.py 那一個檔不受影響——它的內容 hash 另外存
# (scoring_version),未 commit 的改動也抓得到。
def code_version() -> CodeVersion:
    return {"commit": _head_commit(), "dirty": None}


# ③ 的後半:判準版本 = src/scoring.py 內容的 sha256 前 12 碼(①a)。
#
# 取內容而不是 commit,是因為還沒 commit 的改動也要看得出來。看得出
# 「不一樣了」,看不出「改了什麼」——後者靠 code_version 的 commit 去查。
def scoring_version() -> str | None:
    if not SCORING_PATH.is_file():
        return None
    return hashlib.sha256(SCORING_PATH.read_bytes()).hexdigest()[:12]


# ② 存整包 request body,但 API key 不落地。
#
# 現在的 body 本身不帶 key(key 在 HTTP header),這個函式是防線:
# 之後有人把 key 放進 body 時,不會靜悄悄寫進 traces/。
def redact(body: dict) -> dict:
    clean = dict(body)
    for k in ("api_key", "authorization", "Authorization"):
        if k in clean:
            clean[k] = "<redacted>"
    return clean


# 組一筆可重放紀錄。error 有值 = 這次呼叫失敗(③a),仍然落檔。
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
    retrieval: list[RetrievedBlock] | None = None,
) -> Trace:
    return {
        "trace_id": f"{run_id}/{scenario}_{round_no}",
        "run_id": run_id,
        "scenario": scenario,
        "round": round_no,
        "created_at": datetime.now(UTC).isoformat(),
        # ① 兩個都存:全文是當初真的送出去的字,參數是重建它需要的設定。
        "input": {"prompt": prompt, "slice_params": slice_params},
        # ④ W1 固定 null;W2 起填比對基準 [{id, text}],紀錄形狀不因此改變。
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


# ⑤ 一題一檔:traces/<run_id>/<場景>_<輪>.json。
def write_trace(trace: Trace, root: Path = TRACES_ROOT) -> Path:
    out_dir = root / trace["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{trace['scenario']}_{trace['round']}.json"
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# 重放那一側的入口:只讀這個檔,不碰產生它的腳本。
def read_trace(path: Path) -> Trace:
    return json.loads(Path(path).read_text(encoding="utf-8"))
