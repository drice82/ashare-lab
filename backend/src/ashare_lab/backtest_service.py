"""Backtest application boundary, sharing research validation and result I/O."""

import csv
import io
import json
from typing import Any

from ashare_lab.backtest import BACKTEST_VERSION, STRATEGY_VERSION, annualized_return
from ashare_lab.comparison import COMPARISON_VERSION, LABELS, PARAMETERS, warmup
from ashare_lab.dca_service import DcaService
from ashare_lab.schemas import BacktestCompareRequest, BacktestRequest, StrategyConfig


class BacktestService(DcaService):
    kind = "backtest"
    label = "回测"
    extra_columns = ["signal_date", "tax", "fixture_mode", "engine_version", "strategy_version"]

    def options(self) -> dict[str, Any]:
        result = super().options()
        result["defaults"] = {
            "initial_cash": "100000",
            "strategy": "buy_hold",
            "short_window": 20,
            "long_window": 60,
            "commission_rate": "0.0003",
            "minimum_commission": "5",
            "transfer_rate": "0.00001",
            "stamp_rate": "0.0005",
        }
        result["strategies"] = [
            {"id": "buy_hold", "label": "买入持有"},
            {"id": "ma_cross", "label": "双均线交叉"},
        ]
        result["comparison_strategies"] = [
            {
                "id": key,
                "label": label,
                "defaults": (config := StrategyConfig.model_validate({"id": key})).model_dump(),
                "warmup_days": warmup(config),
                "parameters": [
                    {
                        "name": name,
                        "label": PARAMETERS[name][0],
                        "minimum": PARAMETERS[name][1],
                        "maximum": PARAMETERS[name][2],
                        "step": PARAMETERS[name][3],
                        "exclusive_bounds": name in {"lower", "upper"},
                    }
                    for name in config.model_dump()
                    if name != "id"
                ],
            }
            for key, label in LABELS.items()
        ]
        result["comparison_defaults"] = {
            "strategies": [item["defaults"] for item in result["comparison_strategies"]]
        }
        return result

    def submit_comparison(self, request: BacktestCompareRequest) -> dict[str, Any]:
        self.validate(request)
        return self.db.enqueue(
            "backtest",
            request.model_dump(mode="json"),
            {
                **request.data_versions,
                "_backtest_version": BACKTEST_VERSION,
                "_comparison_version": COMPARISON_VERSION,
                **{
                    f"_strategy_version_{config.id}": STRATEGY_VERSION
                    if config.id in {"buy_hold", "ma_cross"}
                    else COMPARISON_VERSION
                    for config in request.strategies
                },
                "_universe_policy": "survivors_v1",
            },
        )

    def submit_backtest(self, request: BacktestRequest) -> dict[str, Any]:
        self.validate(request)
        return self.db.enqueue(
            "backtest",
            request.model_dump(mode="json"),
            {
                **request.data_versions,
                "_backtest_version": BACKTEST_VERSION,
                "_strategy_version": STRATEGY_VERSION,
                "_universe_policy": "survivors_v1",
            },
        )

    def result(self, job_id: str) -> dict[str, Any]:
        value = super().result(job_id)
        if value.get("mode") == "comparison":
            return value
        summary = value["summary"]
        if "annualized_return" not in summary:
            summary["annualized_return"] = (
                annualized_return(
                    summary["contributed"],
                    summary["total_assets"],
                    value["parameters"]["start_date"],
                    summary.get("confirmed_through"),
                )
                if summary["complete"]
                else None
            )
        return value

    def results(self, job_id: str, page: int, page_size: int) -> dict[str, Any]:
        value = self.result(job_id)
        if value.get("mode") != "comparison":
            return super().results(job_id, page, page_size)
        return {
            **{key: item for key, item in value.items() if key != "prices"},
            "strategies": [
                {key: item for key, item in strategy.items() if key != "ledger"}
                for strategy in value["strategies"]
            ],
        }

    def strategy_detail(
        self, job_id: str, strategy_id: str, page: int, page_size: int
    ) -> dict[str, Any]:
        from ashare_lab.services import ServiceError

        value = self.result(job_id)
        selected = next(
            (item for item in value.get("strategies", []) if item["strategy_id"] == strategy_id),
            None,
        )
        if selected is None:
            raise ServiceError(404, "not_found", "策略结果不存在")
        ledger = selected["ledger"]
        return {
            **{key: item for key, item in selected.items() if key != "ledger"},
            "prices": value["prices"],
            "trades": [row for row in ledger if row["event"] in {"buy", "sell"}],
            "items": ledger[(page - 1) * page_size : page * page_size],
            "total": len(ledger),
            "page": page,
            "page_size": page_size,
        }

    def export(self, job_id: str, view: str = "ledger") -> bytes:
        value = self.result(job_id)
        if value.get("mode") != "comparison":
            return super().export(job_id)
        rows: list[dict[str, Any]] = []
        for strategy in value["strategies"]:
            metadata = {
                "strategy_id": strategy["strategy_id"],
                "parameters": json.dumps(
                    {**value["parameters"], "strategy": strategy["parameters"]}, ensure_ascii=False
                ),
                "complete": strategy["summary"]["complete"],
                "warnings": json.dumps(strategy["warnings"], ensure_ascii=False),
                "data_versions": json.dumps(value["data_versions"], ensure_ascii=False),
                "fixture_mode": value["fixture_mode"],
                "engine_version": value["engine_version"],
                "strategy_version": strategy["strategy_version"],
                "comparison_version": value["comparison_version"],
                "universe_policy": strategy["universe_policy"],
                "assumptions": json.dumps(strategy["assumptions"], ensure_ascii=False),
            }
            records = (
                [{**strategy["summary"], "excess_return": strategy["excess_return"]}]
                if view == "summary"
                else strategy["curve"]
                if view == "curves"
                else strategy["ledger"]
            )
            rows.extend({**record, **metadata} for record in records or [{}])
        stream = io.StringIO(newline="")
        fields = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return ("\ufeff" + stream.getvalue()).encode("utf-8")
