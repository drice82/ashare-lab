from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    tushare_token: SecretStr = SecretStr("")
    tushare_http_url: str = "https://api.tushare.pro"
    data_dir: Path = Path("data")
    database_path: Path = Path("data/business.db")
    tz: str = "Asia/Shanghai"
    api_host: str = "127.0.0.1"
    internal_network_only: bool = False
    data_management_password: SecretStr = SecretStr("")
    api_access_token: SecretStr = SecretStr("")
    requests_per_minute: int = 40
    sync_concurrency: int = 4
    worker_research_concurrency: int = 2
    worker_poll_seconds: float = 1.0

    @model_validator(mode="after")
    def validate_config(self) -> "Settings":
        if (
            self.api_host not in {"127.0.0.1", "localhost", "::1"}
            and not self.api_access_token.get_secret_value()
            and not self.internal_network_only
        ):
            raise ValueError("非回环 API_HOST 必须配置 API_ACCESS_TOKEN")
        if not 1 <= self.requests_per_minute <= 500:
            raise ValueError("REQUESTS_PER_MINUTE 必须在 1–500 之间")
        if not 1 <= self.sync_concurrency <= 16:
            raise ValueError("SYNC_CONCURRENCY 必须在 1–16 之间")
        if not 1 <= self.worker_research_concurrency <= 2:
            raise ValueError("WORKER_RESEARCH_CONCURRENCY 必须在 1–2 之间")
        if not self.tushare_http_url.startswith(("https://", "http://")):
            raise ValueError("TUSHARE_HTTP_URL 必须使用 HTTP(S)")
        return self
