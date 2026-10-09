"""環境常數的唯一入口(CLAUDE.md /scaffold 慣例 1)。

本體檔一律 `from config import settings`,不各自 os.getenv。
值從 .env 讀,已存在的環境變數優先——CI 或臨時覆寫不會被 .env 蓋掉。

作業 0-4 建立,接手 experiments/W00/env-checks/check_api.py 的「環境變數齊不齊」那一半。
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

    # W2 作業 2-1 起的 embedding:M1 Pro 本機 server(OpenAI 相容 /v1/embeddings)。
    # 位址是 Tailscale IP,屬機器細節,只放 .env;沒設時為 None,用到的腳本自己報錯。
    # 安裝與現況見 vault Ops/m1pro-embedding-server.md。
    embedding_base_url: str | None = None
    embedding_model: str = "BAAI/bge-m3"

    # W2 作業 2-6 起的 embedding:OpenRouter 鎖 DeepInfra(Learning Plan §4 W7 2026-10-09 範圍變更)。
    # 語料與查詢都走這一組,不跟上面的本機向量混用。
    openrouter_embedding_model: str = "baai/bge-m3"
    openrouter_embedding_provider: str = "DeepInfra"

    # case 表用(W1 工作簿 §7.7.1)。compose 的 db service 讀的是同一組 .env 變數,
    # 所以這裡不另設預設值,缺了就在啟動時爆掉而不是連線時才發現。
    postgres_user: str
    postgres_password: str
    postgres_db: str
    # 預設 db = compose 裡的 service 名。從 host 直接跑時用 POSTGRES_HOST=localhost 覆蓋。
    postgres_host: str = "db"
    postgres_port: int = 5432

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()  # type: ignore[call-arg]
