"""Hand-checkable screening: [12,12,12,10] is a 2/3 MA sell cross."""

from pathlib import Path

import polars as pl
import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.fixtures import FIXTURE_DATE, load_fixture
from ashare_lab.schemas import ScreeningRequest
from ashare_lab.screening import ScreeningEngine
from ashare_lab.signals import calculate_signal
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient
from pydantic import ValidationError


def definition(direction="sell", **changes):
    return {
        "as_of_date": FIXTURE_DATE,
        "mode": "signal",
        "signal_filter": {
            "direction": direction,
            "strategies": [{"id": "ma_cross", "short_window": 2, "long_window": 3}],
        },
        **changes,
    }


def test_signal_sell_is_a_new_cross_not_existing_trend(repository):
    request = ScreeningRequest.model_validate(definition())
    rows = ScreeningEngine(repository).run(request, repository.db.snapshot())
    matched = [r for r in rows if r["match_status"] == "matched"]
    assert [r["stock_code"] for r in matched] == ["000001.SZ"]
    evidence = matched[0]["signal_results"][0]["evidence"]
    assert evidence["previous_short"] == evidence["previous_long"] == 12
    assert evidence["current_short"] == 11
    assert evidence["current_long"] == pytest.approx(34 / 3)
    assert matched[0]["signal_results"][0]["trade_date"] == FIXTURE_DATE
    assert next(r for r in rows if r["stock_code"] == "000007.SZ")["match_status"] == "not_matched"


def test_multi_strategy_or_and_rule_combination(repository):
    raw = definition(mode="rule_signal", rule_id="A")
    # MACD lacks 139 warmup dates, but the MA sell is enough for OR.
    raw["signal_filter"]["strategies"].append({"id": "macd_cross"})
    rows = ScreeningEngine(repository).run(
        ScreeningRequest.model_validate(raw), repository.db.snapshot()
    )
    first = next(r for r in rows if r["stock_code"] == "000001.SZ")
    assert first["match_status"] == "matched"
    assert first["signal_results"][1]["status"] == "insufficient_data"
    assert next(r for r in rows if r["stock_code"] == "000003.SZ")["match_status"] == "not_matched"
    raw["mode"], raw["rule_id"], raw["parameters"] = "signal", None, {}
    rows = ScreeningEngine(repository).run(
        ScreeningRequest.model_validate(raw), repository.db.snapshot()
    )
    assert (
        next(r for r in rows if r["stock_code"] == "000003.SZ")["match_status"]
        == "insufficient_data"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"signal_filter": None},
        {"rule_id": "A"},
        {"mode": "rule_signal"},
        {"mode": "rule", "rule_id": "A"},
        {"signal_filter": {"strategies": []}},
        {"signal_filter": {"strategies": [{"id": "buy_hold"}]}},
        {"signal_filter": {"strategies": [{"id": "ma_cross"}, {"id": "ma_cross"}]}},
        {"signal_filter": {"direction": "invalid", "strategies": [{"id": "ma_cross"}]}},
    ],
)
def test_invalid_screening_modes(changes):
    with pytest.raises(ValidationError):
        ScreeningRequest.model_validate(definition(**changes))


def test_batch_matches_single_and_reads_are_bounded(repository, monkeypatch):
    raw = definition("either")
    raw["signal_filter"]["strategies"] += [
        {"id": "macd_cross", "fast_window": 2, "slow_window": 3, "signal_window": 2},
        {"id": "rsi_reversal", "period": 2},
        {"id": "donchian_breakout", "entry_window": 2, "exit_window": 2},
        {"id": "bollinger_reversion", "period": 3},
    ]
    snapshot = repository.db.snapshot()
    request = ScreeningRequest.model_validate(raw)
    rows = ScreeningEngine(repository).run(request, snapshot)
    for row in rows:
        for config, actual in zip(
            request.signal_filter.strategies, row["signal_results"], strict=True
        ):
            expected = calculate_signal(
                repository, row["stock_code"], config, FIXTURE_DATE, snapshot, "1.0.0"
            )
            assert actual["direction"] == expected["direction"]
            assert actual["status"] == expected["status"]
            assert actual.get("evidence") == expected.get("evidence")
    calls = []
    original = repository.read

    def read(dataset, snapshot, codes=None, start_date=None, end_date=None):
        calls.append((dataset, codes, end_date))
        if dataset in {"daily", "adj_factor", "trade_cal", "suspend_d"}:
            assert end_date == FIXTURE_DATE
        return original(dataset, snapshot, codes, start_date, end_date)

    monkeypatch.setattr(repository, "read", read)
    assert ScreeningEngine(repository).run(request, snapshot) == rows
    # One signal batch plus one factor batch, never one disk read per stock/strategy.
    assert sum(dataset == "daily" for dataset, _, _ in calls) <= 2
    assert sum(dataset == "adj_factor" for dataset, _, _ in calls) <= 2
    assert sum(dataset == "trade_cal" for dataset, _, _ in calls) <= 2
    # Adding a longer MACD window must not change the MA's initialization/evidence.
    single = ScreeningEngine(repository).run(
        ScreeningRequest.model_validate(definition("either")), snapshot
    )
    for before, multi in zip(single, rows, strict=True):
        assert before["signal_results"][0] == multi["signal_results"][0]


def test_pinned_snapshot_and_future_prices_do_not_change_signals(repository):
    request = ScreeningRequest.model_validate(definition())
    snapshot = repository.db.snapshot()
    engine = ScreeningEngine(repository)
    before = engine.run(request, snapshot)
    changed = repository.read("daily", snapshot).with_columns(pl.lit(999.0).alias("close"))
    repository.publish("daily", changed, "changed", "fixture", "changed")
    assert engine.run(request, snapshot) == before
    historical = ScreeningRequest.model_validate(definition(as_of_date="2024-06-27"))
    first = engine.run(historical, snapshot)
    future = repository.read("daily", snapshot).with_columns(
        pl.when(pl.col("trade_date") == FIXTURE_DATE)
        .then(9999)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    repository.publish("daily", future, "future", "fixture", "future")
    # Version metadata differs, so compare classifications and indicator evidence.
    after = engine.run(historical, repository.db.snapshot())
    assert [
        (r["match_status"], [s.get("evidence") for s in r["signal_results"]]) for r in first
    ] == [(r["match_status"], [s.get("evidence") for s in r["signal_results"]]) for r in after]


def test_signal_api_export_presets_recovery_and_old_results(tmp_path: Path):
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    app = create_app(settings)
    service = app.state.service
    load_fixture(service.repository)
    client = TestClient(app)
    worker = Worker(settings)
    assert len(client.get("/api/v1/screening/rules").json()["strategies"]) == 5
    invalid = definition(parameters={"dividend_min": 0.04})
    assert client.post("/api/v1/screening/run", json=invalid).status_code == 422
    job = client.post("/api/v1/screening/run", json=definition()).json()
    assert job["snapshot"]["_signal_version"]
    # Recovery continues with its original snapshot, not a new current head.
    service.db.update(job["id"], status="running")
    worker.recover()
    worker.run_once()
    results = client.get(f"/api/v1/screening/runs/{job['id']}/results").json()
    assert results["items"][0]["signal_results"][0]["direction"] == "sell"
    exported = client.get(f"/api/v1/screening/runs/{job['id']}/export").text
    assert "signal_results" in exported and "ma_cross" in exported and "sell" in exported
    preset = client.post(
        "/api/v1/screening/presets", json={"name": "信号", "definition": definition()}
    )
    assert preset.status_code == 201
    assert client.get("/api/v1/screening/presets").json()[0]["definition"]["mode"] == "signal"
    legacy = client.post(
        "/api/v1/screening/run", json={"as_of_date": FIXTURE_DATE, "rule_id": "A"}
    ).json()
    worker.run_once()
    rows = client.get(f"/api/v1/screening/runs/{legacy['id']}/results").json()["items"]
    assert rows[0]["stock_code"] == "000001.SZ"
    assert not rows[0].get("signal_results")


def test_cancel_signal_screen(repository):
    called = []

    def cancel():
        called.append(True)
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        ScreeningEngine(repository).run(
            ScreeningRequest.model_validate(definition()), repository.db.snapshot(), cancel
        )
    assert called == [True]


def test_buy_either_and_unknown_coverage(repository, monkeypatch):
    # Stock 5: previous MA2=1.5 < MA3=2; current MA2=2.5 > MA3=7/3.
    bars = repository.read("daily", repository.db.snapshot())
    days = sorted(bars["trade_date"].unique().to_list())[-4:]
    prices = dict(zip(days, [3.0, 2.0, 1.0, 4.0], strict=True))
    bars = bars.with_columns(
        pl.when((pl.col("stock_code") == "000005.SZ") & pl.col("trade_date").is_in(days))
        .then(pl.col("trade_date").replace_strict(prices, default=0.0))
        .otherwise(pl.col("close"))
        .alias("close")
    )
    repository.publish("daily", bars, "buy-example", "fixture", "buy-example")
    engine = ScreeningEngine(repository)
    snapshot = repository.db.snapshot()
    for direction, expected in [("buy", ["000005.SZ"]), ("either", ["000001.SZ", "000005.SZ"])]:
        rows = engine.run(ScreeningRequest.model_validate(definition(direction)), snapshot)
        assert [r["stock_code"] for r in rows if r["match_status"] == "matched"] == expected
    original = repository.coverage
    monkeypatch.setattr(
        repository,
        "coverage",
        lambda dataset, versions: {} if dataset == "adj_factor" else original(dataset, versions),
    )
    rows = engine.run(ScreeningRequest.model_validate(definition()), snapshot)
    first = next(r for r in rows if r["stock_code"] == "000001.SZ")
    assert first["match_status"] == "insufficient_data"
    assert "未确认完整覆盖" in first["signal_results"][0]["reason"]


def test_empty_pool_and_version_incompatibility(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "business.db")
    app = create_app(settings)
    service = app.state.service
    load_fixture(service.repository)
    client = TestClient(app)
    worker = Worker(settings)
    empty = definition(universe={"type": "custom", "codes": ["600999.SH"]})
    job = client.post("/api/v1/screening/run", json=empty).json()
    worker.run_once()
    results = client.get(f"/api/v1/screening/runs/{job['id']}/results").json()
    assert results["items"] == [] and results["summary"]["total"] == 0
    job = client.post("/api/v1/screening/run", json=definition()).json()
    service.db.update(job["id"], snapshot={**job["snapshot"], "_signal_version": "incompatible"})
    worker.run_once()
    failed = client.get(f"/api/v1/screening/runs/{job['id']}").json()
    assert failed["status"] == "failed" and "信号算法版本" in failed["error"]
    assert client.get(f"/api/v1/screening/runs/{job['id']}/results").status_code == 409
