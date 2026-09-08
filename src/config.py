"""環境常數的唯一入口(CLAUDE.md /scaffold 慣例 1)。

本體檔一律 `from config import settings`,不各自 os.getenv。
值從 .env 讀,已存在的環境變數優先——CI 或臨時覆寫不會被 .env 蓋掉。

作業 0-4 建立,接手 experiments/W00/check_api.py 的「環境變數齊不齊」那一半。
另一半(真的打一次 API)留在那支腳本,因為它要花錢,不適合放進 pytest。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # 帶日期的版本字串,理由見 W00 工作簿 §8.2:版本被換掉的話,分數抖動就分不出來源。
    openrouter_model: str


settings = Settings()  # type: ignore[call-arg]
