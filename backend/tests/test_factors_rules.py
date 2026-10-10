import polars as pl
import pytest
from ashare_lab.factors import FactorEngine, UniverseManager
from ashare_lab.fixtures import EXPECTED_MATCHES, FIXTURE_DATE
from ashare_lab.rules import DEFAULT_F, Condition, F, Group, RuleRegistry, evaluate
from ashare_lab.schemas import ScreeningRequest, Universe
from ashare_lab.screening import ScreeningEngine
from ashare_lab.warehouse import MarketRepository
from pydantic import ValidationError


def rows(repository: MarketRepository, day: str = FIXTURE_DATE) -> dict[str, dict]:
    snapshot = repository.db.snapshot()
    instruments = UniverseManager(repository).resolve(day, Universe(), snapshot)
    return {
        row["stock_code"]: row
        for row in FactorEngine(repository).compute(day, instruments, snapshot)
    }


def test_hand_calculated_prices_and_means(repository: MarketRepository) -> None:
    value = rows(repository)["000004.SZ"]["factor_values"]
    # D stock closes 10 + .01*n. Last 20 observations n=80..99, mean n=89.5.
    assert value["ma20"] == pytest.approx(10.895)
    assert value["ma60"] == pytest.approx(10.695)
    assert value["return_20d"] == pytest.approx(10.99 / 10.79 - 1)
    assert value["return_60d"] == pytest.approx(10.99 / 10.39 - 1)
    assert value["ma20_change_5d"] == pytest.approx(10.895 / 10.845 - 1)


def test_volume_denominator_excludes_today(repository: MarketRepository) -> None:
    values = rows(repository)["000005.SZ"]["factor_values"]
    assert values["volume_ratio_5d"] == 2
    assert values["volume_ratio_20d"] == 2


def test_breakout_excludes_today(repository: MarketRepository) -> None:
    snapshot = repository.db.snapshot()
    data = repository.read("daily", snapshot)
    data = data.with_columns(
        pl.when((pl.col("stock_code") == "000005.SZ") & (pl.col("trade_date") == FIXTURE_DATE))
        .then(9999)
        .otherwise(pl.col("high"))
        .alias("high")
    )
    repository.publish("daily", data, "changed", "fixture", "changed")
    assert rows(repository)["000005.SZ"]["factor_values"]["breakout_20d"] is True


def test_insufficient_listing_and_suspension(repository: MarketRepository) -> None:
    values = rows(repository)
    assert values["000008.SZ"]["factor_values"]["ma20"] is None
    assert "insufficient_20d_bars" in values["000008.SZ"]["data_quality_flags"]
    assert values["000007.SZ"]["factor_values"]["is_suspended"] is True


@pytest.mark.parametrize("rule_id", list(EXPECTED_MATCHES))
def test_each_rule_expected_matches(repository: MarketRepository, rule_id: str) -> None:
    request = ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id=rule_id)
    result = ScreeningEngine(repository).run(request, repository.db.snapshot())
    assert [r["stock_code"] for r in result if r["match_status"] == "matched"] == EXPECTED_MATCHES[
        rule_id
    ]


def test_same_version_is_reproducible_and_future_is_inaccessible(
    repository: MarketRepository,
) -> None:
    engine = ScreeningEngine(repository)
    snapshot = repository.db.snapshot()
    request = ScreeningRequest(as_of_date="2024-06-27", rule_id="E")
    first = engine.run(request, snapshot)
    data = repository.read("daily", snapshot).with_columns(
        pl.when(pl.col("trade_date") == FIXTURE_DATE)
        .then(999999)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    repository.publish("daily", data, "futurechanged", "fixture", "changed")
    assert engine.run(request, snapshot) == first
    assert engine.run(request, repository.db.snapshot()) == first


def test_dividend_future_announcement_and_implementation() -> None:
    engine = FactorEngine.dividend_yield
    event = {
        "ann_date": "2024-07-01",
        "imp_ann_date": "2024-07-02",
        "pay_date": "2024-07-05",
        "ex_date": "2024-07-04",
        "div_proc": "实施",
        "stk_div": 0,
        "cash_div_tax": 50,
    }
    assert engine(FIXTURE_DATE, {"close": 10}, [event], True)[0] == 0
    event.update(
        ann_date="2024-01-01",
        imp_ann_date="2024-01-02",
        pay_date="2024-01-05",
        ex_date="2024-01-04",
        cash_div_tax=0.6,
    )
    assert engine(FIXTURE_DATE, {"close": 10}, [event], True)[0] == 0.06
    assert engine(FIXTURE_DATE, {"close": 10}, [event], False)[0] is None
    event["imp_ann_date"] = None
    assert engine(FIXTURE_DATE, {"close": 10}, [event], True)[0] is None


def test_dividend_same_day_not_yet_visible_and_share_basis() -> None:
    event = {
        "ann_date": FIXTURE_DATE,
        "imp_ann_date": FIXTURE_DATE,
        "pay_date": FIXTURE_DATE,
        "ex_date": FIXTURE_DATE,
        "div_proc": "实施",
        "stk_div": 0,
        "cash_div_tax": 0.6,
    }
    assert FactorEngine.dividend_yield(FIXTURE_DATE, {"close": 10}, [event], True)[0] == 0
    event.update(ann_date="2024-01-01", imp_ann_date="2024-01-02", stk_div=0.1)
    assert FactorEngine.dividend_yield(FIXTURE_DATE, {"close": 10}, [event], True)[0] is None


def test_historical_st_not_current_name(repository: MarketRepository) -> None:
    snapshot = repository.db.snapshot()
    basics = repository.read("stock_basic", snapshot).with_columns(
        pl.lit("ST当前名称").alias("name")
    )
    repository.publish("stock_basic", basics, "renamed", "fixture", "changed")
    assert rows(repository)["000001.SZ"]["factor_values"]["is_st"] is False


def test_combination_and_or_unknown_disabled() -> None:
    true = Condition(factor="daily_return", value=0)
    false = Condition(factor="volume_ratio_5d", value=2)
    unknown = Condition(factor="ma20", value=0)
    values = {"daily_return": 0.1, "volume_ratio_5d": 1, "ma20": None}
    assert evaluate(Group(children=[true, false]), values)[0] is False
    assert evaluate(Group(logic="OR", children=[true, unknown]), values)[0] is True
    assert evaluate(Group(children=[true, unknown]), values)[0] is None
    false.enabled = False
    assert evaluate(Group(children=[true, false]), values)[0] is True
    with pytest.raises(ValidationError):
        F(group=Group(children=[Condition(factor="daily_return", value=0, enabled=False)]))


def test_f_rule_independent_and_bad_parameter(repository: MarketRepository) -> None:
    request = ScreeningRequest(as_of_date=FIXTURE_DATE, rule_id="F", parameters=DEFAULT_F)
    assert any(
        row["match_status"] == "matched"
        for row in ScreeningEngine(repository).run(request, repository.db.snapshot())
    )
    with pytest.raises(ValidationError):
        RuleRegistry().validate("A", {"dividend_min": -1})
    with pytest.raises(ValidationError):
        Condition(factor="__import__('os')", value=1)
