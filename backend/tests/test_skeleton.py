from pathlib import Path

import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_health(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    assert TestClient(create_app(settings)).get("/health").json()["status"] == "ok"


def test_external_bind_requires_access_control() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_host="0.0.0.0")


def test_token_repr_is_redacted() -> None:
    assert "top-secret" not in repr(Settings(_env_file=None, tushare_token="top-secret"))
