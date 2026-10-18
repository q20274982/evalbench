# embedding 層:把塊的文字送 OpenRouter(鎖 DeepInfra)換成向量。
#
# 決定的出處:
#   - 走 OpenRouter 鎖 DeepInfra、同時 16 個請求:Learning Plan §4 W7 2026-10-09 範圍變更,
#     探針數字在 W02 工作簿環境前置(embedding 模型那一項)
#   - 超過 8192 token 的塊:收到退回就按比例截短再重送,記下截短後送了幾個字元
#     (Calvin 2026-10-09 選 A)。塊的原文不動,截短只影響向量
#
# DeepInfra 的「太長」錯誤不說是批裡第幾段,所以批裡有一段太長時先對半拆,
# 拆到剩一段再截短。

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from config import settings

# §1 常數
MAX_TOKENS = 8192
TRUNCATE_TO = 8000  # 截短時瞄準的 token 數,留一點餘裕給比例換算的誤差
TOO_LONG = re.compile(r"You passed (\d+) input tokens")
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 6


@dataclass
class Embedded:
    vector: list[float]
    sent_chars: int  # 實際送進 embedding 的字元數;沒截短 = 原文長度


@dataclass
class BatchResult:
    items: list[Embedded]
    tokens: int
    retries: int
    truncated: int


class TooLong(Exception):
    def __init__(self, tokens: int):
        self.tokens = tokens


# §2 送一個請求(含重試)
def _post(texts: list[str]) -> tuple[dict, int]:
    body = json.dumps(
        {
            "model": settings.openrouter_embedding_model,
            "input": texts,
            "provider": {
                "only": [settings.openrouter_embedding_provider],
                "allow_fallbacks": False,
            },
        }
    ).encode()
    retries = 0
    while True:
        req = urllib.request.Request(
            f"{settings.openrouter_base_url}/embeddings",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.openrouter_api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()
            m = TOO_LONG.search(detail)
            if e.code == 400 and m:
                raise TooLong(int(m.group(1))) from e
            if e.code not in RETRY_STATUS or retries >= MAX_RETRIES:
                raise RuntimeError(f"OpenRouter {e.code}: {detail[:500]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if retries >= MAX_RETRIES:
                raise RuntimeError(f"OpenRouter 連線失敗:{e}") from e
        else:
            # 鎖了 provider 還是檢查一次:混進別家的向量,命中率會靜默地變成兩種模型的平均
            if d.get("provider") != settings.openrouter_embedding_provider:
                raise RuntimeError(f"provider 不是 DeepInfra:{d.get('provider')}")
            return d, retries
        retries += 1
        time.sleep(2**retries)


# §3 一批塊換成向量
def embed_batch(texts: list[str]) -> BatchResult:
    try:
        d, retries = _post(texts)
    except TooLong as e:
        if len(texts) > 1:
            mid = len(texts) // 2
            a, b = embed_batch(texts[:mid]), embed_batch(texts[mid:])
            return BatchResult(
                a.items + b.items,
                a.tokens + b.tokens,
                a.retries + b.retries,
                a.truncated + b.truncated,
            )
        cut = texts[0][: int(len(texts[0]) * TRUNCATE_TO / e.tokens)]
        r = embed_batch([cut])
        # 截短後仍超長會再截一次;這裡只算「一塊被截短過」,不算截了幾次
        r.truncated = 1
        return r
    vecs = [x["embedding"] for x in sorted(d["data"], key=lambda x: x["index"])]
    return BatchResult(
        [Embedded(v, len(t)) for v, t in zip(vecs, texts, strict=True)],
        d["usage"]["prompt_tokens"],
        retries,
        0,
    )
