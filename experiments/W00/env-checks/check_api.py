# W00 環境前置|確認模型 API 打得通。
#
# 只用 Python 標準庫,不裝任何套件——作業 0-4 才會建 uv 專案與 config.py,
# 在那之前這支腳本是「API 打得通」這一格唯一的驗證工具。
# 0-4 之後它的工作由 src/config.py + tests/ 接手,這支就可以刪掉。

import json
import os
import sys
import urllib.error
import urllib.request

REQUIRED = ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL")


def main() -> int:
    # §1 環境變數齊不齊
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print(f"✗ .env 缺這幾個變數:{', '.join(missing)}")
        return 1

    url = os.environ["OPENROUTER_BASE_URL"].rstrip("/") + "/chat/completions"
    model = os.environ["OPENROUTER_MODEL"]
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "回覆兩個字:通了"}],
            "max_tokens": 16,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
            "Content-Type": "application/json",
        },
    )

    # §2 真的打一次
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        hint = {
            401: "key 不對,或 key 已被撤銷",
            403: "帳號權限或餘額不足",
            404: "OPENROUTER_BASE_URL 或 OPENROUTER_MODEL 對不上",
        }.get(e.code, "看下面的原始回應")
        print(f"✗ HTTP {e.code} — {hint}\n{detail}")
        return 1
    except urllib.error.URLError as e:
        print(f"✗ 連不到 {url}\n{e.reason}")
        return 1

    # §3 回應長得對不對
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        print(f"✗ 回應的形狀不對:\n{json.dumps(payload, ensure_ascii=False)[:500]}")
        return 1

    usage = payload.get("usage", {})
    print(f"✓ 打通了|model={model}")
    print(f"  模型回覆:{text!r}")
    print(f"  token 用量:{usage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
