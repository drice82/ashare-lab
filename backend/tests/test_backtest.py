"""Hand accounts: 100 shares at 10, sell at 12 => 200 profit before fees."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from ashare_lab.backtest import dividend_tax_rate, simulate
from ashare_lab.schemas import BacktestRequest


def request(**changes):
    return BacktestRequest.model_validate(
        {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-04",
            "end_date": "2024-01-12",
            "initial_cash": "1000",
            "strategy": "buy_hold",
            "short_window": 2,
            "long_window": 3,
            "commission_rate": "0",
            "minimum_commission": "0",
            "transfer_rate": "0",
            "stamp_rate": "0",
            "data_versions": {"daily": "pinned"},
            **changes,
        }
    )


def market(prices=None):
    days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(12)]
    prices = prices or [10] * 12
    return {
        "days": days,
        "bars": {
            d: {"open": p, "close": p, "volume": 1000} for d, p in zip(days, prices, strict=True)
        },
        "factors": dict.fromkeys(days, 1),
        "limits": {d: {"up_limit": 100, "down_limit": 1} for d in days},
        "suspended": set(),
        "events": [],
        "issues": {},
        "dividend_complete": True,
    }


def test_next_open_hold_and_cash_conservation():
    data = market()
    data["bars"]["2024-01-04"]["close"] = 20
    result = simulate(request(), data)
    buys = [r for r in result["ledger"] if r["event"] == "buy"]
    assert [(r["date"], r["signal_date"], r["price"]) for r in buys] == [
        ("2024-01-05", "2024-01-04", 10)
    ]
    assert result["summary"]["shares"] == 100
    assert result["summary"]["total_assets"] == 1000


def test_crossings_not_initial_bull_and_reversal_cancels():
    data = market([9, 10, 11, 12, 13, 8, 7, 6, 12, 14, 15, 16])
    result = simulate(request(strategy="ma_cross", initial_cash="2000"), data)
    buys = [r for r in result["ledger"] if r["event"] == "buy"]
    assert buys[0]["date"] == "2024-01-10"
    assert buys[0]["signal_date"] == "2024-01-09"
    assert len(buys) == 1
    data["suspended"] = {"2024-01-10", "2024-01-11", "2024-01-12"}
    result = simulate(request(strategy="ma_cross", initial_cash="2000"), data)
    assert result["summary"]["pending_orders"] == 1


def test_blocked_open_fees_insufficient_and_star_lot():
    data = market()
    data["limits"]["2024-01-05"]["up_limit"] = 10
    result = simulate(
        request(initial_cash="2000", minimum_commission="5", transfer_rate="0.00001"), data
    )
    buy = next(r for r in result["ledger"] if r["event"] == "buy")
    assert buy["date"] == "2024-01-06"
    assert buy["fee"] == 5.01 and buy["shares_delta"] == 100
    assert result["summary"]["cash"] == 994.99
    assert simulate(request(), market())["summary"]["shares"] == 100
    assert (
        simulate(request(stock_code="688001.SH", initial_cash="2010"), market())["summary"][
            "shares"
        ]
        == 201
    )
    assert simulate(request(minimum_commission="5"), market())["summary"]["shares"] == 0


@pytest.mark.parametrize(
    "bought,sold,rate",
    [
        ("2024-01-31", "2024-02-29", "0.2"),
        ("2024-01-31", "2024-03-01", "0.1"),
        ("2024-02-29", "2025-02-28", "0.1"),
        ("2024-02-29", "2025-03-01", "0"),
    ],
)
def test_tax_natural_month_year_boundaries(bought, sold, rate):
    assert dividend_tax_rate(bought, sold) == Decimal(rate)


def test_missing_data_and_future_prices_do_not_change_past_signals():
    data = market()
    del data["bars"]["2024-01-06"]
    result = simulate(request(), data)
    assert result["summary"]["profit"] is None
    assert result["summary"]["confirmed_through"] == "2024-01-05"
    a = simulate(request(end_date="2024-01-06"), market())
    data = market()
    data["bars"]["2024-01-12"]["close"] = 999
    assert simulate(request(end_date="2024-01-06"), data) == a


def test_dividend_receivable_no_double_count():
    data = market()
    data["events"] = [
        {
            "end_date": "2023-12-31",
            "ann_date": "2024-01-01",
            "imp_ann_date": "2024-01-02",
            "record_date": "2024-01-05",
            "ex_date": "2024-01-06",
            "pay_date": "2024-01-09",
            "div_proc": "实施",
            "stk_div": 0,
            "cash_div_tax": 1,
        }
    ]
    for day in data["days"][5:]:
        data["bars"][day]["open"] = data["bars"][day]["close"] = 9
        data["factors"][day] = 10 / 9
    result = simulate(request(), data)
    assert result["summary"]["total_assets"] == 1000
    assert result["summary"]["dividends"] == 100
    assert result["summary"]["dividend_tax_paid"] == 0
    assert result["summary"]["complete"]


@pytest.mark.parametrize("pay_date", ["2024-01-06", "2024-01-09"])
def test_sell_tax_before_or_after_payment(pay_date):
    data = market([12, 11, 10, 13, 13, 8, 8, 8, 8, 8, 8, 8])
    data["bars"]["2024-01-05"]["open"] = 10
    data["bars"]["2024-01-07"]["open"] = 12
    data["events"] = [
        {
            "end_date": "2023-12-31",
            "ann_date": "2024-01-01",
            "imp_ann_date": "2024-01-02",
            "record_date": "2024-01-05",
            "ex_date": "2024-01-06",
            "pay_date": pay_date,
            "div_proc": "实施",
            "stk_div": 0,
            "cash_div_tax": 1,
        }
    ]
    for day in data["days"][5:]:
        data["factors"][day] = 13 / 12
    result = simulate(request(strategy="ma_cross"), data)
    assert result["summary"]["complete"]
    assert result["summary"]["buy_count"] == 1
    assert result["summary"]["sell_count"] == 1
    assert result["summary"]["shares"] == 0
    assert result["summary"]["cash"] == 1280
    assert result["summary"]["dividend_tax_paid"] == 20
    if pay_date == "2024-01-09":
        partial = simulate(request(strategy="ma_cross", end_date="2024-01-07"), data)
        assert partial["summary"]["receivable"] == 100
        assert partial["summary"]["tax_liability"] == 20
        assert partial["summary"]["total_assets"] == 1280


def test_sell_fee_down_limit_and_pending_reversal():
    data = market([12, 11, 10, 13, 13, 8, 8, 8, 20, 20, 20, 20])
    data["bars"]["2024-01-05"]["open"] = 10
    data["bars"]["2024-01-07"]["open"] = 12
    result = simulate(request(strategy="ma_cross", stamp_rate="0.0005"), data)
    sale = next(r for r in result["ledger"] if r["event"] == "sell")
    assert sale["fee"] == 0.6
    data["limits"]["2024-01-05"]["up_limit"] = 10
    data["limits"]["2024-01-06"]["up_limit"] = 8
    data["limits"]["2024-01-07"]["up_limit"] = 12
    result = simulate(request(strategy="ma_cross"), data)
    assert any(r["event"] == "order_cancelled" for r in result["ledger"])


def test_one_day_signal_unknown_coverage_and_company_action():
    result = simulate(request(end_date="2024-01-04"), market())
    assert result["summary"]["pending_orders"] == 1
    assert result["summary"]["buy_count"] == 0
    data = market()
    data["dividend_complete"] = False
    assert simulate(request(), data)["summary"]["profit"] is None
    data = market()
    data["factors"]["2024-01-06"] = 2
    assert simulate(request(), data)["summary"]["profit"] is None


def test_unsupported_historical_tax_stops_before_speculative_accounting():
    data = market()
    data["days"] = [d.replace("2024", "2014") for d in data["days"]]
    for key in ("bars", "limits", "factors"):
        data[key] = {d.replace("2024", "2014"): value for d, value in data[key].items()}
    data["events"] = [
        {
            "end_date": "2013-12-31",
            "ann_date": "2014-01-01",
            "imp_ann_date": "2014-01-02",
            "record_date": "2014-01-05",
            "ex_date": "2014-01-06",
            "pay_date": "2014-01-09",
            "div_proc": "实施",
            "stk_div": 0,
            "cash_div_tax": 1,
        }
    ]
    result = simulate(request(start_date="2014-01-04", end_date="2014-01-12"), data)
    assert result["summary"]["profit"] is None
    assert result["summary"]["confirmed_through"] == "2014-01-04"
    assert result["summary"]["dividends"] == 0
    assert any("历史分红税制" in warning for warning in result["warnings"])
