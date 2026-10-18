# 解析層:把模型的原始輸出讀成評測層可以打分的結構。
#
# 規格正本是 W01 工作簿 `§7.2.2` 三段(Calvin 2026-09-15/17 定案)。三條線:
#
#   ① 這一層只量測,不裁決。四層各試一次,每次的成敗都記下來,沒有一個結果
#      覆蓋另一個;合格線畫在哪裡由評測層決定。
#   ② pydantic 在這裡是量測儀不是閘門,而且走嚴格模式——不做任何型別轉換。
#      寬鬆模式會把 "0.8" 轉成 0.8 並記成完全正常,那一筆不服從就從統計裡消失了。
#      唯一保留的邊界是整數當浮點數用(confidence: 1),pydantic 視為同一個值。
#      往上傳三樣東西:原始 dict(parsed,一個字沒改)、錯誤報告(missing_fields /
#      type_errors / schema_err)、驗得過時的 Diagnosis 物件(diagnosis)。評測層
#      拿得到物件就知道這筆型別有保證;沒有物件就走失敗流程,從錯誤報告看是哪一種。
#   ③ 剝掉 markdown 圍欄算量測不算救援:剝完不等於這一筆算過,「沒剝時讀不動」
#      這個事實留著。前提是現行的約定只在 prompt 裡拜託模型輸出 JSON;哪天請求
#      改帶 response_format,合法 JSON 變成供應商層級的保證,那時吐圍欄就是
#      供應商違約,要報錯而不是由這一層吸收。
#
# 所以這一層不補值、不猜值、不重打模型、不丟例外中止整批。判準一句話:
# 做完之後,評測層要能從紀錄重建出模型原本吐的每一個字。

import json
import re

from diagnosis import Diagnosis

# §4 解析鏈(只記錄,不處置)
FENCE_RE = re.compile(r"```[a-zA-Z]*\s*(.*?)```", re.DOTALL)


# 把一次原始輸出走過四層,每層只回報看到什麼。
#
# §4.1 raw     — 表面特徵:有沒有 markdown 圍欄、JSON 前面有沒有多餘的字
# §4.2 strict  — 原始字串直接 json.loads 過不過(下游最天真的那種寫法)
# §4.3 salvage — 剝掉圍欄、只取第一個 {...} 之後過不過(「差一點點」的判定)
# §4.4 schema  — pydantic 嚴格模式驗證,缺欄位與型別不對分開記
def inspect(raw: str) -> dict:
    obs: dict = {"raw_chars": len(raw)}

    # §4.1
    fence = FENCE_RE.search(raw)
    obs["has_fence"] = fence is not None
    first_brace = raw.find("{")
    obs["prefix_chars"] = first_brace if first_brace >= 0 else len(raw)
    obs["has_brace"] = first_brace >= 0

    # §4.2
    try:
        strict_obj = json.loads(raw)
        obs["strict_ok"] = True
        obs["strict_err"] = ""
    except Exception as e:
        strict_obj = None
        obs["strict_ok"] = False
        obs["strict_err"] = type(e).__name__ + ": " + str(e)[:80]

    # §4.3
    candidate = fence.group(1).strip() if fence else raw
    start, end = candidate.find("{"), candidate.rfind("}")
    # 括號收不收得起來。少收尾的 `}` 是實測到的主要壞法(§7.2 第一次 live,
    # 5 筆失敗有 4 筆是這個),它跟「完全不像」差一個字元,分開記才看得出來。
    obs["brace_delta"] = candidate.count("{") - candidate.count("}")
    salvage_obj = None
    if start >= 0 and end > start:
        try:
            salvage_obj = json.loads(candidate[start : end + 1])
            obs["salvage_ok"] = True
            obs["salvage_err"] = ""
        except Exception as e:
            obs["salvage_ok"] = False
            obs["salvage_err"] = type(e).__name__ + ": " + str(e)[:80]
    else:
        obs["salvage_ok"] = False
        obs["salvage_err"] = "no matching pair of braces found"

    # §4.4
    # 原始 dict 沿用 §4.3 的 target 選法(strict 過就用它,否則用剝殼後的那個),
    # 不另立一套:兩層各自的成敗已經分開記在 strict_ok / salvage_ok。
    target = strict_obj if strict_obj is not None else salvage_obj
    obs["parsed"] = target
    obs["diagnosis"] = None
    if target is None:
        obs["schema_ok"] = False
        obs["missing_fields"] = []
        obs["type_errors"] = []
        obs["schema_err"] = "no object to validate"
    else:
        try:
            # strict=True:不做型別轉換,規格見本檔檔頭②
            obs["diagnosis"] = Diagnosis.model_validate(target, strict=True)
            obs["schema_ok"] = True
            obs["missing_fields"] = []
            obs["type_errors"] = []
            obs["schema_err"] = ""
        except Exception as e:
            obs["schema_ok"] = False
            errs = e.errors() if hasattr(e, "errors") else []
            obs["missing_fields"] = [
                ".".join(map(str, d["loc"])) for d in errs if d["type"] == "missing"
            ]
            obs["type_errors"] = [
                f"{'.'.join(map(str, d['loc']))}={d['type']}"
                for d in errs
                if d["type"] != "missing"
            ]
            obs["schema_err"] = str(e)[:120].replace("\n", " ")

    # §4.5 一句話的分類,填成敗表用
    if obs["strict_ok"] and obs["schema_ok"]:
        obs["verdict"] = "ok"
    elif obs["salvage_ok"] and obs["schema_ok"]:
        obs["verdict"] = "near_miss_wrapper"
    elif obs["salvage_ok"] or obs["strict_ok"]:
        obs["verdict"] = "near_miss_fields"
    elif obs["has_brace"] and obs["brace_delta"] != 0:
        # 括號數量對不上:少收尾或多一個。內容是完整的 JSON,壞在最後一兩個字元。
        obs["verdict"] = "near_miss_brackets"
    else:
        obs["verdict"] = "unparseable"
    return obs
