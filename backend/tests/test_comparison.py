"""Independent indicator calculations and shared-engine comparison invariants."""

import pytest
from ashare_lab.comparison import ema, rsi, signal, simulate_comparison
from ashare_lab.schemas import BacktestCompareRequest, StrategyConfig
from test_backtest import market, request
from test_backtest_api import payload, system  # noqa: F401


def test_indicator_hand_calculation():
    assert ema([1.0, 2.0, 3.0, 4.0], 3) == [None, None, 2.0, 3.0]
    assert rsi([10.0, 11.0, 12.0, 11.0], 2)[2:] == [100.0, 50.0]
    assert rsi([10.0] * 5, 2)[2:] == [50.0] * 3
    config = StrategyConfig(id="donchian_breakout", entry_window=2, exit_window=2)
    assert signal(config, [10.0, 10.0, 12.0], [11.0, 11.0, 13.0], [9.0, 9.0, 11.0]) == "buy"
    assert signal(config, [10.0, 10.0, 8.0], [11.0, 11.0, 9.0], [9.0, 9.0, 7.0]) == "sell"


def test_comparison_independent_accounts_and_unknown_isolation():
    old = request()
    body = BacktestCompareRequest.model_validate(
        {
            **old.model_dump(exclude={"strategy", "short_window", "long_window"}),
            "strategies": [{"id": "buy_hold"}, {"id": "ma_cross"}],
        }
    )
    output = simulate_comparison(body, market())
    assert output["strategies"][0]["summary"]["total_assets"] == 1000
    assert not output["strategies"][1]["summary"]["complete"]
    assert output["summary"]["complete_count"] == 1


def test_compare_validation():
    base = request().model_dump(exclude={"strategy", "short_window", "long_window"})
    for configs in [
        [],
        [{"id": "buy_hold"}] * 2,
        [{"id": "rsi_reversal", "lower": 80, "upper": 30}],
        [{"id": "buy_hold", "period": 20}],
        [{"id": "macd_cross", "fast_window": 30, "slow_window": 20}],
    ]:
        with pytest.raises(ValueError):
            BacktestCompareRequest.model_validate({**base, "strategies": configs})


def test_compare_worker_recovery_export_and_legacy(system):  # noqa: F811
    client, worker, service = system
    base = payload(client)
    configs = [
        {"id": "buy_hold"},
        {"id": "ma_cross", "short_window": 2, "long_window": 3},
        {"id": "macd_cross", "fast_window": 2, "slow_window": 3, "signal_window": 2},
        {"id": "rsi_reversal", "period": 2},
        {"id": "donchian_breakout", "entry_window": 2, "exit_window": 2},
        {"id": "bollinger_reversion", "period": 2},
    ]
    response = client.post("/api/v1/backtest/compare", json={**base, "strategies": configs})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    route = f"/api/v1/backtest/runs/{job_id}"
    service.db.update(job_id, status="running")
    worker.recover()
    assert worker.run_once()
    result = client.get(route + "/results").json()
    assert result["mode"] == "comparison"
    assert len(result["strategies"]) == 6
    legacy_id = client.post("/api/v1/backtest/run", json=base).json()["id"]
    assert worker.run_once()
    legacy = client.get(f"/api/v1/backtest/runs/{legacy_id}/results").json()
    assert result["strategies"][0]["summary"] == legacy["summary"]
    detail = client.get(route + "/strategies/buy_hold?page_size=1").json()
    assert len(detail["items"]) == 1
    assert len(detail["trades"]) == legacy["summary"]["buy_count"]
    for view in ["summary", "curves", "ledger"]:
        export = client.get(route + f"/export?view={view}")
        assert export.status_code == 200
        assert "strategy_id" in export.text and "fixture_mode" in export.text
    saved = service.backtest.result(job_id)
    import polars as pl

    frame = service.repository.read("daily", base["data_versions"]).with_columns(
        pl.lit(999).alias("open")
    )
    service.repository.publish("daily", frame, "new-comparison", "fixture", "new-comparison")
    assert service.backtest.result(job_id) == saved
    service.db.update(job_id, status="running", result=None)
    worker.recover()
    assert worker.run_once()
    assert service.backtest.result(job_id) == saved


def test_reversal_rules_and_constant_prices():
    rsi_config = StrategyConfig(id="rsi_reversal", period=2)
    assert signal(rsi_config, [10.0, 9.0, 8.0, 9.0], [], []) == "buy"
    assert signal(rsi_config, [10.0, 11.0, 12.0, 11.0], [], []) == "sell"
    bollinger = StrategyConfig(id="bollinger_reversion", period=3, deviations=1)
    assert signal(bollinger, [10.0, 10.0, 1.0, 5.0], [], []) == "buy"
    assert signal(bollinger, [10.0, 10.0, 1.0, 5.0, 9.0], [], []) == "sell"
    assert signal(bollinger, [10.0] * 10, [], []) is None
    assert (
        signal(
            StrategyConfig(id="macd_cross", fast_window=2, slow_window=3, signal_window=2),
            [10.0] * 20,
            [],
            [],
        )
        is None
    )


def test_fixed_seed_and_no_future_leakage():
    from copy import deepcopy

    from ashare_lab.backtest import simulate

    original = request(start_date="2024-01-12", end_date="2024-01-12", initial_cash="2000")
    common = original.model_dump(exclude={"strategy", "short_window", "long_window"})
    config = {"id": "rsi_reversal", "period": 2}
    one = BacktestCompareRequest.model_validate({**common, "strategies": [config]})
    many = BacktestCompareRequest.model_validate(
        {
            **common,
            "strategies": [
                {"id": "buy_hold"},
                config,
                {"id": "ma_cross", "short_window": 2, "long_window": 60},
            ],
        }
    )
    data = market([10, 11, 10, 9, 8, 9, 10, 11, 10, 9, 10, 11])
    # A complete recursive strategy keeps its own 11-day seed even alongside a longer strategy.
    alone = simulate_comparison(one, data)["strategies"][0]
    grouped = simulate_comparison(many, data)["strategies"][1]
    assert alone["summary"]["complete"]
    for field in ("summary", "curve", "ledger", "parameters"):
        assert alone[field] == grouped[field]
    assert (
        simulate_comparison(many, data)["strategies"][0]["summary"]
        == simulate(original, data)["summary"]
    )
    earlier = BacktestCompareRequest.model_validate(
        {
            **common,
            "start_date": "2024-01-10",
            "end_date": "2024-01-11",
            "strategies": [{"id": "donchian_breakout", "entry_window": 2, "exit_window": 2}],
        }
    )
    baseline = simulate_comparison(earlier, data)
    changed = deepcopy(data)
    changed["bars"]["2024-01-12"]["close"] = 999
    changed["factors"]["2024-01-12"] = 999
    assert simulate_comparison(earlier, changed) == baseline


def test_compare_cancellation_atomic_failure_and_version_check(system):  # noqa: F811
    from unittest.mock import patch

    client, worker, service = system
    body = {**payload(client), "strategies": [{"id": "buy_hold"}, {"id": "ma_cross"}]}

    def submit():
        response = client.post("/api/v1/backtest/compare", json=body)
        assert response.status_code == 202
        return response.json()["id"]

    job_id = submit()
    original = worker.check
    calls = 0

    def check(current):
        nonlocal calls
        calls += 1
        if calls == 5:
            service.cancel(current, "backtest")
        original(current)

    with patch.object(worker, "check", check):
        assert worker.run_once()
    assert service.db.job(job_id)["status"] == "cancelled"
    assert service.db.job(job_id)["result"] is None
    job_id = submit()
    with patch("ashare_lab.worker.os.replace", side_effect=OSError("publication interrupted")):
        assert worker.run_once()
    assert service.db.job(job_id)["status"] == "failed"
    assert service.db.job(job_id)["result"] is None
    job_id = submit()
    job = service.db.job(job_id)
    service.db.update(job_id, snapshot={**job["snapshot"], "_strategy_version_buy_hold": "future"})
    assert worker.run_once()
    assert service.db.job(job_id)["status"] == "failed"


def test_comparison_options_and_invalid_api_input(system):  # noqa: F811
    client, _, _ = system
    options = client.get("/api/v1/backtest/options").json()
    assert len(options["comparison_defaults"]["strategies"]) == 6
    bollinger = next(
        item for item in options["comparison_strategies"] if item["id"] == "bollinger_reversion"
    )
    assert bollinger["defaults"]["period"] == 20
    assert bollinger["parameters"][0]["name"] == "period"
    base = {**payload(client), "strategies": [{"id": "buy_hold"}]}
    for changes in [
        {"strategies": []},
        {"strategies": [{"id": "buy_hold"}] * 2},
        {"strategy": "buy_hold"},
        {"strategies": [{"id": "rsi_reversal", "lower": float("inf")}]},
        {"strategies": [{"id": "macd_cross", "fast_window": 26, "slow_window": 12}]},
    ]:
        body = {**base, **changes}
        # Infinity is valid JSON only as a string; the input boundary must reject it.
        if changes.get("strategies") and changes["strategies"][0].get("lower"):
            body["strategies"][0]["lower"] = "Infinity"
        assert client.post("/api/v1/backtest/compare", json=body).status_code == 422
