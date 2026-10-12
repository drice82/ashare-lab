from collections import defaultdict
from collections.abc import Callable
from typing import Any

from ashare_lab.dividends import upcoming_dividends
from ashare_lab.factors import FactorEngine, UniverseManager
from ashare_lab.rules import Common, Condition, Group, RuleRegistry, condition, evaluate
from ashare_lab.schemas import ScreeningRequest
from ashare_lab.signal_screening import SignalReadContext, any_signal, batch_signals
from ashare_lab.warehouse import MarketRepository, stock_bucket

SCREENING_BATCH_SIZE = 256


class ScreeningEngine:
    def __init__(self, repository: MarketRepository):
        self.repository = repository
        self.factors = FactorEngine(repository)
        self.universes = UniverseManager(repository)
        self.registry = RuleRegistry()

    def run(
        self,
        request: ScreeningRequest,
        snapshot: dict[str, str],
        check_cancelled: Callable[[], None] | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> list[dict[str, Any]]:
        if request.mode in {"signal", "dividend"}:
            params = Common.model_validate(request.parameters)
            exclusions: list[Condition | Group] = []
            if params.exclude_st:
                exclusions.append(condition("is_st", "eq", False))
            if params.exclude_suspended:
                exclusions.append(condition("is_suspended", "eq", False))
            group = Group(children=exclusions) if exclusions else None
        else:
            assert request.rule_id is not None
            params = self.registry.validate(request.rule_id, request.parameters)
            group = self.registry.group(params)
        as_of = request.as_of_date.isoformat()
        instruments = self.universes.resolve(
            as_of,
            request.universe,
            snapshot,
            exclude_delisted=snapshot.get("_universe_policy") != "historical_v0",
        )
        if check_cancelled:
            check_cancelled()
        context = self.factors.context(as_of, snapshot)
        signal_context = (
            SignalReadContext(self.repository, snapshot, as_of, request.signal_filter.strategies)
            if request.signal_filter
            else None
        )
        # Adjacent batches share a bucket, reducing repeated reads of monthly files.
        instruments.sort(key=lambda row: (stock_bucket(row["stock_code"]), row["stock_code"]))
        results = []
        batches = [
            instruments[i : i + SCREENING_BATCH_SIZE]
            for i in range(0, len(instruments), SCREENING_BATCH_SIZE)
        ]
        processed = 0
        for batch in batches:
            if check_cancelled:
                check_cancelled()
            factors = self.factors.compute(as_of, batch, snapshot, context)
            signals = (
                batch_signals(
                    self.repository,
                    batch,
                    request.signal_filter,
                    as_of,
                    snapshot,
                    signal_context,
                    check_cancelled,
                )
                if request.signal_filter and signal_context
                else {}
            )
            dividend_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
            if snapshot.get("_dividend_version") or request.dividend_filter:
                for event in self.repository.read(
                    "dividend", snapshot, [r["stock_code"] for r in batch]
                ).to_dicts():
                    dividend_events[event["stock_code"]].append(event)
            for row in factors:
                if check_cancelled:
                    check_cancelled()
                matched, details = evaluate(group, row["factor_values"]) if group else (True, [])
                if snapshot.get("_dividend_version") or request.dividend_filter:
                    recent = upcoming_dividends(
                        as_of,
                        request.dividend_filter.days if request.dividend_filter else 30,
                        row["factor_values"].get("close"),
                        dividend_events[row["stock_code"]],
                        row["stock_code"] in context["dividend_complete"],
                    )
                    row["recent_dividend"] = recent
                    if request.dividend_filter:
                        outcome = recent["matched"]
                        matched = (
                            False
                            if matched is False or outcome is False
                            else None
                            if matched is None or outcome is None
                            else True
                        )
                        row["data_quality_flags"] = sorted(
                            set(row["data_quality_flags"] + recent["flags"])
                        )
                if request.signal_filter:
                    outcomes = signals[row["stock_code"]]
                    signal_match = any_signal(outcomes)
                    matched = (
                        False
                        if matched is False or signal_match is False
                        else None
                        if matched is None or signal_match is None
                        else True
                    )
                    row["signal_results"] = outcomes
                row.update(
                    match_status="matched"
                    if matched is True
                    else "not_matched"
                    if matched is False
                    else "insufficient_data",
                    rule_id=request.rule_id,
                    matched_factors=[d["factor"] for d in details if d["matched"]],
                    condition_results=details,
                )
                results.append(row)
            processed += len(batch)
            if progress:
                progress(0.95 * processed / max(len(instruments), 1))
        return sorted(results, key=lambda row: row["stock_code"])
