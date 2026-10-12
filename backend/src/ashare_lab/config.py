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

    notification_encryption_key: SecretStr = SecretStr("")
    notification_auto_send: bool = False
    notification_cookie_secure: bool = True
    notification_public_url: str = "http://localhost:5173"
    public_url_aliases: str = ""
    notification_trusted_proxies: str = ""
    notification_session_days: int = 180
    notification_subscription_limit: int = 20
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_email: str = ""
    smtp_from_name: str = "ashare-lab"

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
        from ipaddress import ip_network
        from urllib.parse import urlsplit

        for origin in self.public_origins():
            public_url = urlsplit(origin)
            if (
                public_url.scheme not in {"http", "https"}
                or not public_url.netloc
                or public_url.path not in {"", "/"}
                or public_url.query
                or public_url.fragment
                or public_url.username
            ):
                raise ValueError(
                    "NOTIFICATION_PUBLIC_URL / PUBLIC_URL_ALIASES 必须是网站origin（协议、域名和端口）"
                )

        for network in self.notification_trusted_proxies.split(","):
            if network.strip():
                ip_network(network.strip())
        if self.smtp_security not in {"starttls", "ssl"}:
            raise ValueError("SMTP_SECURITY 必须为 starttls 或 ssl")
        if (
            not 1 <= self.notification_session_days <= 365
            or not 1 <= self.notification_subscription_limit <= 100
        ):
            raise ValueError("通知会话或订阅上限无效")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP_PORT 无效")
        return self

    def public_origins(self) -> set[str]:
        return {self.notification_public_url.rstrip("/")} | {
            origin.strip().rstrip("/")
            for origin in self.public_url_aliases.split(",")
            if origin.strip()
        }
