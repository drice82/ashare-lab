from pathlib import Path

import pytest
from ashare_lab.config import Settings
from ashare_lab.db import Database
from ashare_lab.fixtures import load_fixture
from ashare_lab.warehouse import MarketRepository


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def repository(tmp_path: Path) -> MarketRepository:
    db = Database(tmp_path / "business.db")
    db.initialize()
    repo = MarketRepository(db, tmp_path)
    load_fixture(repo)
    return repo
