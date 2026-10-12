import polars as pl
import pytest
from ashare_lab.api import create_app
from ashare_lab.config import Settings
from ashare_lab.dividends import upcoming_dividends
from ashare_lab.fixtures import FIXTURE_DATE
from ashare_lab.providers import normalize
from ashare_lab.schemas import ScreeningRequest
from ashare_lab.screening import ScreeningEngine
from ashare_lab.worker import Worker
from fastapi.testclient import TestClient
from pydantic import ValidationError


def event(**changes):
    return dict(
        stock_code="000001.SZ",
        end_date="2023-12-31",
        ann_date="2024-06-20",
        imp_ann_date="2024-06-27",
        div_proc="实施",
        record_date="2024-07-01",
        ex_date="2024-07-02",
        pay_date="2024-07-02",
        cash_div_tax=0.2,
        **changes,
    )


def test_hand_calculated_estimate_and_dates():
    result = upcoming_dividends(FIXTURE_DATE, 30, 8, [event()], True)
    assert result["matched"] is True
    assert result["events"][0]["cash_per_10"] == 2
    assert result["events"][0]["estimated_cash_10000"] == 250
    assert upcoming_dividends(FIXTURE_DATE, 2, 8, [event()], True)["matched"] is False
    same = event()
    same["record_date"] = FIXTURE_DATE
    assert upcoming_dividends(FIXTURE_DATE, 30, 8, [same], True)["matched"] is False
    edge = event()
    edge["record_date"] = "2024-07-28"
    assert upcoming_dividends(FIXTURE_DATE, 30, 8, [edge], True)["matched"] is True


def test_publication_unknown_duplicate_and_missing_price():
    future = event()
    future["imp_ann_date"] = "2024-06-28"
    assert upcoming_dividends(FIXTURE_DATE, 30, 8, [future], True)["matched"] is False
    proposal = event()
    proposal["div_proc"] = "预案"
    assert upcoming_dividends(FIXTURE_DATE, 30, 8, [proposal], True)["matched"] is False
    missing = event()
    missing["imp_ann_date"] = None
    assert upcoming_dividends(FIXTURE_DATE, 30, 8, [missing], True)["matched"] is None
    assert upcoming_dividends(FIXTURE_DATE, 30, 8, [], False)["matched"] is None
    result = upcoming_dividends(FIXTURE_DATE, 30, None, [event(), event()], True)
    assert len(result["events"]) == 1
    assert result["events"][0]["estimated_cash_10000"] is None
    conflict = event()
    conflict["cash_div_tax"] = 0.3
    assert upcoming_dividends(FIXTURE_DATE, 30, 8, [event(), conflict], True)["matched"] is None


def test_batch_filter_and_pinned_snapshot(repository):
    old = repository.db.snapshot()
    row = event()
    row["ts_code"] = row.pop("stock_code")
    repository.publish(
        "dividend",
        normalize("dividend", pl.DataFrame([row]), "fixture", "new"),
        "upcoming",
        "fixture",
        "new",
    )
    request = ScreeningRequest.model_validate(
        dict(
            as_of_date=FIXTURE_DATE,
            mode="dividend",
            dividend_filter={"days": 30},
            parameters={"exclude_suspended": False},
        )
    )
    rows = ScreeningEngine(repository).run(request, repository.db.snapshot())
    assert next(r for r in rows if r["stock_code"] == "000001.SZ")["match_status"] == "matched"
    previous = ScreeningEngine(repository).run(request, old)
    assert next(r for r in previous if r["stock_code"] == "000001.SZ")["match_status"] != "matched"
    raw = request.model_dump(mode="json")
    raw.update(mode="rule", rule_id="A")
    assert (
        next(
            r
            for r in ScreeningEngine(repository).run(
                ScreeningRequest.model_validate(raw), repository.db.snapshot()
            )
            if r["stock_code"] == "000001.SZ"
        )["match_status"]
        == "matched"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"dividend_filter": None},
        {"rule_id": "A"},
        {"dividend_filter": {"days": 0}},
        {"dividend_filter": {"days": 366}},
        {"dividend_filter": {"days": 1.5}},
        {"signal_filter": {"strategies": [{"id": "ma_cross"}]}},
    ],
)
def test_invalid_definition(changes):
    with pytest.raises(ValidationError):
        ScreeningRequest.model_validate(
            dict(as_of_date=FIXTURE_DATE, mode="dividend", dividend_filter={"days": 30}, **changes)
            if "dividend_filter" not in changes
            else dict(as_of_date=FIXTURE_DATE, mode="dividend", **changes)
        )


def test_api_worker_export_and_old_result(repository):
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.db.engine.url.database
    )
    client = TestClient(create_app(settings))
    job = client.post(
        "/api/v1/screening/run",
        json={"as_of_date": FIXTURE_DATE, "mode": "dividend", "dividend_filter": {"days": 30}},
    ).json()
    assert Worker(settings).run_once()
    results = client.get(f"/api/v1/screening/runs/{job['id']}/results?match_status=all").json()
    assert results["summary"]["as_of_date"] == FIXTURE_DATE
    assert "effective_market_dates" in results["summary"]
    assert "recent_dividend" in results["items"][0]
    exported = client.get(f"/api/v1/screening/runs/{job['id']}/export").text
    assert "recent_dividend" in exported


def test_signal_dividend_and_false_dominates_unknown(repository):
    raw = dict(
        as_of_date=FIXTURE_DATE,
        mode="signal",
        dividend_filter={"days": 30},
        signal_filter={
            "direction": "sell",
            "strategies": [{"id": "ma_cross", "short_window": 2, "long_window": 3}],
        },
    )
    rows = ScreeningEngine(repository).run(
        ScreeningRequest.model_validate(raw), repository.db.snapshot()
    )
    assert all(row["match_status"] != "matched" for row in rows)
    # A known-false rule still excludes even if dividend history is absent.
    snapshot = repository.db.snapshot()
    snapshot.pop("dividend")
    raw.update(mode="rule_signal", rule_id="A")
    rows = ScreeningEngine(repository).run(ScreeningRequest.model_validate(raw), snapshot)
    assert (
        next(row for row in rows if row["stock_code"] == "000003.SZ")["match_status"]
        == "not_matched"
    )


def test_new_version_old_result_and_sorting(repository):
    settings = Settings(
        _env_file=None, data_dir=repository.root, database_path=repository.db.engine.url.database
    )
    client = TestClient(create_app(settings))
    # Explicitly execute a legacy task without the new calculation version.
    snapshot = repository.db.snapshot()
    snapshot["_universe_policy"] = "historical_v0"
    legacy = repository.db.enqueue(
        "screening", {"as_of_date": FIXTURE_DATE, "rule_id": "A"}, snapshot
    )
    Worker(settings).run_once()
    old = client.get(f"/api/v1/screening/runs/{legacy['id']}/results?match_status=all").json()
    assert "recent_dividend" not in old["items"][0]
    assert (
        client.get(f"/api/v1/screening/runs/{legacy['id']}/results?sort_by=cash_per_10").status_code
        == 200
    )
    job = client.post(
        "/api/v1/screening/run", json={"as_of_date": FIXTURE_DATE, "rule_id": "A"}
    ).json()
    assert job["snapshot"]["_dividend_version"] == "1.0.0"
    repository.db.update(job["id"], snapshot={**job["snapshot"], "_dividend_version": "invalid"})
    Worker(settings).run_once()
    assert repository.db.job(job["id"])["status"] == "failed"
    assert "近期分红" in repository.db.job(job["id"])["error"]
