from collections.abc import Callable
from typing import Any

from ashare_lab.factors import FactorEngine, UniverseManager
from ashare_lab.rules import RuleRegistry, evaluate
from ashare_lab.schemas import ScreeningRequest
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
            for row in factors:
                if check_cancelled:
                    check_cancelled()
                matched, details = evaluate(group, row["factor_values"])
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
