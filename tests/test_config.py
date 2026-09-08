"""作業 0-4|環境常數讀得進來。

接手 experiments/W00/check_api.py 的「.env 缺不缺變數」那一半檢查。
這支不打 API、不花錢,所以可以留在 pytest 裡每次跑。

跑法:
    docker compose run --rm app uv run pytest tests/test_config.py -s
"""

from config import settings


def test_env_is_loaded():
    assert settings.openrouter_api_key, ".env 缺 OPENROUTER_API_KEY"
    assert settings.openrouter_base_url.startswith("https://")
    # 帶日期的版本字串,理由見 W00 工作簿 §8.2。
    assert settings.openrouter_model, ".env 缺 OPENROUTER_MODEL"
    print(f"\n[config] model={settings.openrouter_model} base={settings.openrouter_base_url}")
