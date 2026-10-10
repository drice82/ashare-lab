"""Hand-checkable DCA accounts; all cash assertions are in yuan."""

from datetime import date

import pytest
from ashare_lab.dca import simulate
from ashare_lab.schemas import DcaRequest


def request(**changes):
    return DcaRequest.model_validate(
        {
            "stock_code": "000001.SZ",
            "start_date": "2024-01-31",
            "end_date": "2024-03-01",
            "monthly_amount": "1000",
            "monthly_day": 31,
            "commission_rate": "0",
            "minimum_commission": "0",
            "transfer_rate": "0",
            "data_versions": {"daily": "test"},
            **changes,
        }
    )


def market():
    days = ["2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01"]
    return {
        "days": days,
        "bars": {d: {"close": 10, "volume": 1000} for d in days},
        "limits": {d: {"up_limit": 11} for d in days},
        "suspended": set(),
        "factors": {d: 1 for d in days},
        "events": [],
        "issues": {},
        "dividend_complete": True,
    }


def test_month_end_cash_conservation():
    result = simulate(request(), market())
    assert result["summary"]["contributed"] == 2000
    assert result["summary"]["shares"] == 200
    assert result["summary"]["cash"] == 0
    assert result["summary"]["total_assets"] == 2000
    assert result["summary"]["profit"] == 0


def test_minimum_commission_and_insufficient_lot():
    result = simulate(request(minimum_commission="5", transfer_rate="0.00001"), market())
    # Jan cannot afford 100 shares plus fees. Feb buys 100 for 1005.01.
    assert result["summary"]["shares"] == 100
    assert result["summary"]["fees"] == 5.01
    assert result["summary"]["cash"] == 994.99
    assert result["summary"]["profit"] == pytest.approx(-5.01)


def test_deferred_plans_merge_and_end_with_cash():
    data = market()
    data["suspended"] = {"2024-01-31", "2024-02-01"}
    data["limits"]["2024-02-29"]["up_limit"] = 10
    result = simulate(request(), data)
    buys = [r for r in result["ledger"] if r["event"] == "buy"]
    assert len(buys) == 1 and buys[0]["date"] == "2024-03-01"
    assert buys[0]["shares_delta"] == 200
    assert result["summary"]["contributed"] == 2000
    data["suspended"].add("2024-03-01")
    result = simulate(request(), data)
    assert result["summary"]["cash"] == 2000
    assert result["summary"]["pending_plans"] == 2


def dividend(**changes):
    return {
        "end_date": "2023-12-31",
        "ann_date": "2024-01-01",
        "imp_ann_date": "2024-01-02",
        "record_date": "2024-01-31",
        "ex_date": "2024-02-01",
        "pay_date": "2024-02-01",
        "div_proc": "实施",
        "stk_div": 0,
        "cash_div_tax": 1,
        **changes,
    }


def test_record_close_entitlement_and_reinvestment_without_double_count():
    data = market()
    data["events"] = [dividend(), dividend()]
    result = simulate(request(), data)
    assert result["summary"]["dividends"] == 100
    assert result["summary"]["shares"] == 200
    assert result["summary"]["cash"] == 100
    assert len([r for r in result["ledger"] if r["event"] == "dividend"]) == 1
    # No extra purchase when the dividend arrives without a pending plan.
    assert [r["date"] for r in result["ledger"] if r["event"] == "buy"] == [
        "2024-01-31",
        "2024-02-29",
    ]


def test_receivable_future_invisibility_and_unknown():
    data = market()
    data["events"] = [dividend(pay_date="2024-04-01")]
    result = simulate(request(), data)
    assert result["summary"]["receivable"] == 100
    assert result["summary"]["total_assets"] == 2100
    data["events"] = [dividend(ann_date="2024-04-01", stk_div=1)]
    assert simulate(request(), data)["summary"]["complete"] is True
    data["events"] = [dividend(cash_div_tax=None)]
    result = simulate(request(), data)
    assert result["summary"]["profit"] is None
    assert result["summary"]["complete"] is False


def test_star_quantity_and_no_investment_return():
    result = simulate(request(stock_code="688001.SH", monthly_amount="2500"), market())
    assert result["summary"]["shares"] == 500
    result = simulate(
        request(monthly_day=1, start_date=date(2024, 1, 31), end_date=date(2024, 1, 31)), market()
    )
    assert result["summary"]["return_rate"] is None


def test_unexplained_adjustment_missing_data_and_conflicting_dividends():
    data = market()
    data["factors"]["2024-02-01"] = 2
    result = simulate(request(), data)
    assert result["summary"]["complete"] is False
    assert result["summary"]["confirmed_through"] == "2024-01-31"
    data = market()
    del data["bars"]["2024-02-01"]
    assert simulate(request(), data)["summary"]["profit"] is None
    data = market()
    data["events"] = [dividend(), dividend(cash_div_tax=2)]
    assert simulate(request(), data)["summary"]["profit"] is None


def test_cash_dividend_adjustment_not_double_counted_and_star_increment():
    data = market()
    data["events"] = [dividend()]
    # 10 -> 9 ex-dividend; factor 1 -> 10/9; 100 shares receive 100 cash.
    for day in data["days"][1:]:
        data["bars"][day]["close"] = 9
        data["factors"][day] = 10 / 9
    result = simulate(request(), data)
    assert result["summary"]["complete"] is True
    assert result["summary"]["total_assets"] == 2000
    assert result["summary"]["profit"] == 0
    result = simulate(request(stock_code="688001.SH", monthly_amount="2010"), market())
    assert result["summary"]["shares"] == 402


def test_weekend_rollover_new_year_and_suspended_valuation():
    data = market()
    data["days"] = ["2023-12-29", "2024-01-02", "2024-01-03"]
    for key in ["bars", "limits", "factors"]:
        value = next(iter(data[key].values()))
        data[key] = {d: value for d in data["days"]}
    result = simulate(request(start_date="2023-12-29", end_date="2024-01-03", monthly_day=1), data)
    assert result["summary"]["contributed"] == 1000
    assert result["ledger"][0]["planned_dates"] == ["2024-01-01"]
    assert result["ledger"][0]["date"] == "2024-01-02"
    data["suspended"] = {"2024-01-03"}
    result = simulate(request(start_date="2023-12-29", end_date="2024-01-03", monthly_day=1), data)
    assert result["summary"]["valuation_date"] == "2024-01-02"


def test_ex_date_purchase_not_eligible_and_zero_volume_deferred():
    data = market()
    data["events"] = [dividend()]
    result = simulate(request(monthly_day=1), data)
    assert result["summary"]["dividends"] == 0
    data = market()
    data["bars"]["2024-01-31"]["volume"] = 0
    result = simulate(request(), data)
    assert result["summary"]["complete"] is True
    assert [r["date"] for r in result["ledger"] if r["event"] == "buy"][0] == "2024-02-01"


def test_missing_limit_stops_before_unconfirmed_day_cash_flows():
    data = market()
    del data["limits"]["2024-02-29"]
    result = simulate(request(), data)
    assert result["summary"]["complete"] is False
    assert result["summary"]["contributed"] == 1000
    assert result["summary"]["confirmed_through"] == "2024-02-01"
    assert result["summary"]["valuation_date"] == "2024-02-01"


def test_annualized_return_and_flow_adjusted_drawdown():
    from ashare_lab.dca import performance_metrics

    # One year: invest 1000, fall 20%, add 1000, finish at 1980.
    # Units: 1000 -> 2250; NAV: 1 -> .8 -> .88; max drawdown stays 20%.
    curve = [
        {"date": "2023-01-01", "contributed": 1000, "total_assets": 1000},
        {"date": "2023-06-30", "contributed": 1000, "total_assets": 800},
        {"date": "2023-07-01", "contributed": 2000, "total_assets": 1800},
        {"date": "2024-01-01", "contributed": 2000, "total_assets": 1980},
    ]
    annualized, drawdown = performance_metrics(curve)
    assert drawdown == pytest.approx(0.2)
    # Check the dated cash-flow equation independently.
    assert 1000 * (1 + annualized) + 1000 * (1 + annualized) ** (184 / 365) == pytest.approx(1980)
    annualized, drawdown = performance_metrics(
        [
            curve[0],
            {
                "date": "2024-01-01",
                "contributed": 1000,
                "total_assets": 1100,
            },
        ]
    )
    assert annualized == pytest.approx(0.1)
    assert drawdown == 0


def test_metrics_unknown_zero_duration_and_initial_fee():
    from ashare_lab.dca import performance_metrics

    assert performance_metrics([]) == (None, None)
    assert performance_metrics([{"date": "2024-01-01", "contributed": 0, "total_assets": 0}]) == (
        None,
        None,
    )
    annualized, drawdown = performance_metrics(
        [
            {"date": "2024-01-01", "contributed": 1000, "total_assets": 995},
        ]
    )
    assert annualized is None
    assert drawdown == pytest.approx(0.005)
    data = market()
    data["dividend_complete"] = False
    summary = simulate(request(), data)["summary"]
    assert summary["annualized_return"] is None
    assert summary["max_drawdown"] is None


def test_cosco_small_factor_change_with_unchanged_reference_price():
    data = market()
    # Real pinned 601919.SH values on 2022-06-29/30: +0.034734%,
    # with unchanged ex-reference price. Prices/holdings remain unadjusted.
    for day in data["days"]:
        data["bars"][day].update(close=13.89, pre_close=13.89)
        data["limits"][day]["up_limit"] = 20
        data["factors"][day] = 1.4395 if day == data["days"][0] else 1.44
    result = simulate(request(monthly_amount="3000"), data)
    assert result["summary"]["complete"] is True
    assert result["summary"]["shares"] == 400
    assert result["summary"]["total_assets"] == 6000
    assert result["summary"]["dividends"] == 0
    assert any("微小变化" in warning for warning in result["warnings"])
    assert len(result["warnings"]) == 1


@pytest.mark.parametrize("reference", [None, 13.88])
def test_small_factor_change_without_matching_reference_stays_unknown(reference):
    data = market()
    data["factors"]["2024-02-01"] = 1.0003
    data["bars"]["2024-02-01"]["pre_close"] = reference
    assert simulate(request(), data)["summary"]["complete"] is False


def test_small_factor_changes_do_not_hide_cumulative_drift():
    data = market()
    for day, factor in zip(data["days"], [1, 1.0004, 1.0008, 1.0012], strict=True):
        data["factors"][day] = factor
        data["bars"][day]["pre_close"] = 10
    result = simulate(request(), data)
    assert result["summary"]["complete"] is False
    assert result["summary"]["confirmed_through"] == "2024-02-29"


def test_known_dividend_still_checks_factor_even_with_small_change():
    data = market()
    data["events"] = [dividend()]
    data["factors"]["2024-02-01"] = 1.0003
    data["bars"]["2024-02-01"]["pre_close"] = 10
    result = simulate(request(), data)
    assert result["summary"]["complete"] is False
    assert "不一致" in result["warnings"][0]


@pytest.mark.parametrize(
    "factor,complete", [(1.001, True), (1.0010001, False), (0.999, True), (0.9989999, False)]
)
def test_small_factor_tolerance_boundary(factor, complete):
    data = market()
    for day in data["days"][1:]:
        data["factors"][day] = factor
        data["bars"][day]["pre_close"] = 10
    assert simulate(request(), data)["summary"]["complete"] is complete


def test_stable_factor_day_does_not_reset_cumulative_anchor():
    data = market()
    for day, factor in zip(data["days"], [1, 1.0006, 1.0006, 1.0012], strict=True):
        data["factors"][day] = factor
        data["bars"][day]["pre_close"] = 10
    result = simulate(request(), data)
    assert result["summary"]["complete"] is False
    assert result["summary"]["confirmed_through"] == "2024-02-29"
