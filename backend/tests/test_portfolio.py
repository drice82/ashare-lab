"""Hand-checkable shared-account scenarios, independent of repository fixtures."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest
from ashare_lab.portfolio import rebalance_days, simulate_portfolio
from ashare_lab.schemas import PortfolioRequest


def request(**changes):
    return PortfolioRequest.model_validate(
        {
            "members": [
                {"stock_code": code, "strategy": {"id": "buy_hold"}}
                for code in ["000001.SZ", "000002.SZ"]
            ],
            "start_date": "2024-01-29",
            "end_date": "2024-02-05",
            "initial_cash": "10000",
            "rebalance": "none",
            "commission_rate": "0",
            "minimum_commission": "0",
            "transfer_rate": "0",
            "stamp_rate": "0",
            "data_versions": {"daily": "pinned"},
            **changes,
        }
    )


def market():
    days = [(date(2024, 1, 29) + timedelta(days=i)).isoformat() for i in range(8)]
    stocks = {}
    for code, price in [("000001.SZ", 10), ("000002.SZ", 20)]:
        stocks[code] = {
            "days": days,
            "bars": {
                d: {
                    "open": price,
                    "close": price,
                    "high": price,
                    "low": price,
                    "volume": 1000,
                    "pre_close": price,
                }
                for d in days
            },
            "factors": dict.fromkeys(days, 1),
            "limits": {d: {"up_limit": 100, "down_limit": 1} for d in days},
            "suspended": set(),
            "events": [],
            "issues": {},
            "dividend_complete": True,
            "warmup_issue": None,
        }
    return {"days": days, "calendar": days, "stocks": stocks, "names": {}}


def trades(value):
    return [r for r in value["ledger"] if r["event"] in {"buy", "sell"}]


def test_equal_hand_account_and_baseline():
    result = simulate_portfolio(request(), market())
    assert [
        (r["stock_code"], r["shares_delta"], r["date"], r["signal_date"]) for r in trades(result)
    ] == [
        ("000001.SZ", 500, "2024-01-30", "2024-01-29"),
        ("000002.SZ", 200, "2024-01-30", "2024-01-29"),
    ]
    assert result["summary"]["cash"] == 1000
    assert result["summary"]["total_assets"] == 10000
    assert result["summary"] == result["benchmark"]["summary"]
    assert result["excess_return"] == 0


def test_order_invariance_fees_and_custom_weights():
    body = request(
        allocation="custom",
        members=[
            {"stock_code": "000001.SZ", "weight": ".3", "strategy": {"id": "buy_hold"}},
            {"stock_code": "000002.SZ", "weight": ".7", "strategy": {"id": "buy_hold"}},
        ],
        minimum_commission="5",
    )
    first = simulate_portfolio(body, market())
    body.members.reverse()
    assert simulate_portfolio(body, market())["ledger"] == first["ledger"]
    assert [r["shares_delta"] for r in trades(first)] == [200, 300]
    assert first["summary"]["cash"] == 1990
    assert first["summary"]["total_assets"] == 9990


def test_monthly_partial_rebalance_uses_previous_close_and_sells_first():
    data = market()
    data["stocks"]["000001.SZ"]["bars"]["2024-01-31"]["close"] = 20
    for day in data["days"][3:]:
        data["stocks"]["000001.SZ"]["bars"][day]["open"] = 20
        data["stocks"]["000001.SZ"]["bars"][day]["close"] = 20
    result = simulate_portfolio(request(rebalance="monthly"), data)
    adjustment = [r for r in trades(result) if r["date"] == "2024-02-01"]
    # Jan 31 NAV=15000, targets=7500 each: A sells 200, B buys 100.
    assert [(r["event"], r["stock_code"], r["shares_delta"]) for r in adjustment] == [
        ("sell", "000001.SZ", -200),
        ("buy", "000002.SZ", 100),
    ]
    assert result["summary"]["total_assets"] == 15000
    assert result["summary"]["cash"] == 3000
    assert result["benchmark"]["summary"]["cash"] == 1000


def test_blocked_sales_proportional_cash_and_no_automatic_topup():
    data = market()
    data["stocks"]["000001.SZ"]["bars"]["2024-01-31"]["close"] = 20
    for day in data["days"][3:]:
        data["stocks"]["000001.SZ"]["bars"][day].update(open=20, close=20)
    data["stocks"]["000001.SZ"]["limits"]["2024-02-01"]["down_limit"] = 20
    result = simulate_portfolio(request(rebalance="monthly"), data)
    assert any(r["event"] == "deferred" for r in result["ledger"])
    assert len([r for r in trades(result) if r["stock_code"] == "000002.SZ"]) == 1
    assert any(r["event"] == "sell" and r["date"] == "2024-02-02" for r in trades(result))


@pytest.mark.parametrize(
    "frequency,expected",
    [
        ("none", []),
        ("weekly", ["2024-03-28"]),
        ("monthly", ["2024-03-28", "2024-12-30"]),
        ("quarterly", ["2024-03-28", "2024-12-30"]),
        ("yearly", ["2024-12-30"]),
    ],
)
def test_calendar_boundaries_not_end_of_truncated_interval(frequency, expected):
    days = ["2024-03-27", "2024-03-28", "2024-04-01", "2024-12-30", "2025-01-02"]
    assert sorted(rebalance_days(days, frequency) & {"2024-03-28", "2024-12-30"}) == expected
    assert "2024-03-27" not in rebalance_days(days[:1], frequency)


def test_unknown_stock_stops_entire_account_before_partial_day_transactions():
    data = market()
    del data["stocks"]["000002.SZ"]["bars"]["2024-01-30"]
    result = simulate_portfolio(request(), data)
    assert result["summary"]["complete"] is False
    assert result["summary"]["total_assets"] is None
    assert result["summary"]["confirmed_through"] == "2024-01-29"
    assert trades(result) == []


def test_warmup_unknown_isolated_from_buy_hold_baseline():
    body = request()
    body.members[0].strategy.id = "ma_cross"
    data = market()
    data["stocks"]["000001.SZ"]["warmup_issue"] = "预热不足"
    result = simulate_portfolio(body, data)
    assert result["summary"]["total_assets"] is None
    assert result["benchmark"]["summary"]["complete"]
    assert result["excess_return"] is None


def test_future_bars_and_announcements_do_not_change_prior_curve():
    original = market()
    changed = deepcopy(original)
    changed["stocks"]["000001.SZ"]["bars"]["2024-02-05"]["close"] = 999
    changed["stocks"]["000001.SZ"]["events"] = [{"ann_date": "2025-01-01", "stk_div": 10}]
    first = simulate_portfolio(request(), original)
    second = simulate_portfolio(request(), changed)
    assert first["curve"][:-1] == second["curve"][:-1]


def test_schema_rejects_duplicate_invalid_and_incomplete_weights():
    for changes in [
        {"members": []},
        {"allocation": "custom"},
        {"members": [{"stock_code": "000001.SZ", "weight": ".5"}]},
        {"members": [{"stock_code": "000001.SZ"}] * 2},
        {"initial_cash": "NaN"},
        {"rebalance": "daily"},
    ]:
        with pytest.raises(ValueError):
            request(**changes)


def test_partial_fifo_rights_tax_before_and_after_payment():
    from ashare_lab.portfolio import Account, Entitlement, Lot

    account = Account(request(), market(), False)
    account.cash = Decimal("5000")
    position = account.positions["000001.SZ"]
    right = Entitlement(Decimal("500"), "2024-02-05", 500, Decimal("500"))
    position.shares, position.price = 500, Decimal("10")
    position.lots = [Lot("2024-01-01", 500, [right])]
    position.rights = [right]
    assert account.sell("000001.SZ", "2024-01-25", 100, Decimal("10"))
    assert right.tax_due == Decimal("20")
    assert account.sell("000001.SZ", "2024-02-02", 100, Decimal("10"))
    assert right.tax_due == Decimal("30")
    assert account.assets() == Decimal("10470")
    account.cash += right.amount - right.tax_due
    account.tax_paid += right.tax_due
    right.paid = True
    assert account.sell("000001.SZ", "2024-02-20", 100, Decimal("10"))
    assert right.remaining_shares == 200 and right.remaining_amount == Decimal("200")
    assert account.tax_paid == 40
    assert account.assets() == 10460
    assert sum(-r["shares_delta"] for r in trades({"ledger": account.ledger})) == 300


def test_star_partial_units_and_t1():
    from ashare_lab.portfolio import sell_quantity

    assert sell_quantity("688001.SH", 100, 500, 500) == 0
    assert sell_quantity("688001.SH", 201, 500, 500) == 201
    assert sell_quantity("688001.SH", 199, 199, 199) == 199
    assert sell_quantity("000001.SZ", 199, 500, 500) == 100
    assert sell_quantity("000001.SZ", 500, 0, 500) == 0


def test_below_minimum_buy_is_recorded_and_noop_needs_no_limits():
    data = market()
    for stock in data["stocks"].values():
        for bar in stock["bars"].values():
            bar.update(open=1000, close=1000, pre_close=1000)
        stock["limits"] = {}
    result = simulate_portfolio(request(), data)
    assert result["summary"]["complete"]
    assert result["summary"]["cash"] == 10000
    assert len([r for r in result["ledger"] if r["event"] == "insufficient_cash"]) == 2


def test_signal_exit_wins_same_day_rebalance_and_never_reopens(monkeypatch):
    from ashare_lab import portfolio

    original = portfolio.stock_signal
    data = market()
    for code, stock in data["stocks"].items():
        stock["code"] = code

    def signals(config, stock, start, day):
        if config.id == "buy_hold":
            return original(config, stock, start, day)
        if day == start:
            return "buy", None
        if day == "2024-01-31" and stock["code"] == "000001.SZ":
            return "sell", None
        return None, None

    monkeypatch.setattr(portfolio, "stock_signal", signals)
    body = request(
        rebalance="monthly",
        members=[
            {
                "stock_code": code,
                "strategy": {"id": "ma_cross", "short_window": 2, "long_window": 3},
            }
            for code in data["stocks"]
        ],
    )
    result = simulate_portfolio(body, data)
    assert [(r["event"], r["date"]) for r in trades(result) if r["stock_code"] == "000001.SZ"] == [
        ("buy", "2024-01-30"),
        ("sell", "2024-02-01"),
    ]
    assert result["positions"][-1]["items"][0]["eligible"] is False
    assert result["positions"][-1]["items"][0]["shares"] == 0


def test_single_member_signals_match_legacy_engine():
    from ashare_lab.backtest import simulate
    from ashare_lab.schemas import BacktestRequest

    for strategy in ["buy_hold", "ma_cross"]:
        data = market()
        body = request(
            members=[
                {
                    "stock_code": "000001.SZ",
                    "strategy": {"id": strategy, "short_window": 2, "long_window": 3}
                    if strategy == "ma_cross"
                    else {"id": strategy},
                }
            ]
        )
        stock = data["stocks"]["000001.SZ"]
        stock["days"] = ["2024-01-26", "2024-01-27", "2024-01-28"] + stock["days"]
        for day, price in zip(stock["days"], [9, 10, 11, 8, 7, 9, 12, 15, 8, 7, 12], strict=True):
            stock["bars"][day] = {"open": price, "close": price, "volume": 1000}
            stock["factors"][day] = 1
            stock["limits"][day] = {"up_limit": 100, "down_limit": 1}
        actual = simulate_portfolio(body, data)
        old = simulate(
            BacktestRequest.model_validate(
                {
                    **body.model_dump(exclude={"members", "allocation", "rebalance"}),
                    "stock_code": "000001.SZ",
                    "strategy": strategy,
                    "short_window": 2,
                    "long_window": 3,
                }
            ),
            stock,
        )
        for key in [
            "cash",
            "total_assets",
            "fees",
            "dividends",
            "dividend_tax_paid",
            "return_rate",
            "max_drawdown",
        ]:
            assert actual["summary"][key] == old["summary"][key]
        assert [(r["event"], r["date"], r["shares_delta"]) for r in trades(actual)] == [
            (r["event"], r["date"], r["shares_delta"]) for r in trades(old)
        ]


def test_nonfinite_quote_stops_account_and_remains_publishable():
    import json

    data = market()
    data["stocks"]["000002.SZ"]["bars"]["2024-01-30"]["open"] = float("nan")
    result = simulate_portfolio(request(), data)
    assert result["summary"]["complete"] is False
    assert result["summary"]["confirmed_through"] == "2024-01-29"
    assert trades(result) == []
    assert result["prices"]["000002.SZ"][1]["open"] is None
    json.dumps(result, allow_nan=False)
