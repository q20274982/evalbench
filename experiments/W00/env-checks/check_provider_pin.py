# W00 環境前置|實測 OpenRouter 的 provider 鎖定做不做得到。
#
# Calvin 問的是:多 provider 的模型,能不能用參數指定只打某一家、而且不要 fallback。
# 文件說可以,這支腳本實際打四次確認。model 寫死在下面,因為這裡驗的是 provider
# 機制,不是 .env 裡的 model 設定。四個情境跑完就知道答案,之後可以刪掉。
#
# 每次請求 max_tokens=4,四次合計估計不到 $0.001。

import json
import os
import sys
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4-flash-0731"


def call(label, provider_cfg, expect):
    body = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}
    if provider_cfg is not None:
        body["provider"] = provider_cfg
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.load(resp)
        got = payload.get("provider")
        print(f"[{label}] HTTP 200  provider={got!r}")
        print(f"          預期:{expect}")
        return got
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print(f"[{label}] HTTP {e.code}\n          預期:{expect}\n          回應:{detail}")
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] {type(e).__name__}: {e}\n          預期:{expect}")
    return None


def main() -> int:
    # §1 環境變數
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("✗ .env 缺 OPENROUTER_API_KEY")
        return 1

    print(f"model = {MODEL}\n")

    # §2 不鎖:看 OpenRouter 自己挑哪一家
    print("--- §2 不鎖 provider ---")
    call("no-pin", None, "任意一家,每次可能不同")

    # §3 鎖 Baidu
    print("\n--- §3 鎖 Baidu,allow_fallbacks=false ---")
    a = call("pin-baidu", {"only": ["Baidu"], "allow_fallbacks": False}, "provider 要是 Baidu")

    # §4 鎖 StreamLake
    print("\n--- §4 鎖 StreamLake,allow_fallbacks=false ---")
    b = call("pin-streamlake", {"only": ["StreamLake"], "allow_fallbacks": False},
             "provider 要是 StreamLake")

    # §5 鎖一家不服務這個模型的:要報錯,不可以偷換家
    print("\n--- §5 鎖 Anthropic(沒有在服務這個模型),allow_fallbacks=false ---")
    c = call("pin-bogus", {"only": ["Anthropic"], "allow_fallbacks": False},
             "要 HTTP 4xx 報錯。若回 200 且 provider 是別家,代表鎖不住")

    # §6 判定
    print("\n--- §6 判定 ---")
    ok = (a and "baidu" in a.lower()) and (b and "stream" in b.lower()) and (c is None)
    if ok:
        print("✓ 鎖得住:指定的 provider 有生效,鎖不到時報錯而不是偷換家")
        return 0
    print("✗ 至少一項不如預期,把上面整段輸出貼回給 TA")
    return 1


if __name__ == "__main__":
    sys.exit(main())
