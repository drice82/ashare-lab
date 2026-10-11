"""Portfolio application boundary: validation, pinned jobs and immutable result views."""

import csv
import io
import json
from typing import Any

from ashare_lab.backtest import BACKTEST_VERSION, STRATEGY_VERSION
from ashare_lab.backtest_service import BacktestService
from ashare_lab.comparison import COMPARISON_VERSION
from ashare_lab.dca_service import DcaService
from ashare_lab.factors import UniverseManager
from ashare_lab.portfolio import PORTFOLIO_VERSION
from ashare_lab.schemas import BacktestRequest, PortfolioRequest, Universe


class PortfolioBacktestService(DcaService):
    kind = "portfolio"
    label = "组合回测"

    def options(self) -> dict[str, Any]:
        value = BacktestService(self.db, self.repository).options()
        value["defaults"] = {
            k: v
            for k, v in value["defaults"].items()
            if k not in {"strategy", "short_window", "long_window"}
        }
        value["defaults"].update(allocation="equal", rebalance="monthly")
        value["maximum_members"] = 20
        value["rebalance_options"] = ["none", "weekly", "monthly", "quarterly", "yearly"]
        return value

    def submit_portfolio(self, request: PortfolioRequest) -> dict[str, Any]:
        from ashare_lab.services import ServiceError

        common = request.model_dump(exclude={"members", "allocation", "rebalance"})
        self.validate(
            BacktestRequest.model_validate({**common, "stock_code": request.members[0].stock_code})
        )
        resolved = UniverseManager(self.repository).resolve(
            request.start_date.isoformat(),
            Universe(type="custom", codes=[m.stock_code for m in request.members]),
            request.data_versions,
        )
        codes = {r["stock_code"] for r in resolved}
        if any(m.stock_code not in codes for m in request.members):
            raise ServiceError(
                422, "invalid_instrument", "组合含不属于该版本存续沪深A股或开始日期早于上市日的股票"
            )
        versions = {
            m.strategy.id: STRATEGY_VERSION
            if m.strategy.id in {"buy_hold", "ma_cross"}
            else COMPARISON_VERSION
            for m in request.members
        }
        return self.db.enqueue(
            "portfolio",
            request.model_dump(mode="json"),
            {
                **request.data_versions,
                "_portfolio_version": PORTFOLIO_VERSION,
                "_backtest_version": BACKTEST_VERSION,
                **{f"_strategy_version_{key}": value for key, value in versions.items()},
                "_universe_policy": "survivors_v1",
            },
        )

    @staticmethod
    def account(value: dict[str, Any], account: str) -> dict[str, Any]:
        return value["benchmark"] if account == "benchmark" else value

    def results(
        self, job_id: str, page: int, page_size: int, account: str = "portfolio"
    ) -> dict[str, Any]:
        value = self.result(job_id)
        rows = self.account(value, account)["ledger"]
        return {
            **{
                k: v
                for k, v in value.items()
                if k not in {"positions", "ledger", "prices", "benchmark"}
            },
            "benchmark": {k: value["benchmark"][k] for k in ["summary", "curve", "warnings"]},
            "account": account,
            "items": rows[(page - 1) * page_size : page * page_size],
            "total": len(rows),
            "page": page,
            "page_size": page_size,
        }

    def positions(self, job_id: str, day: str | None, account: str) -> dict[str, Any]:
        from ashare_lab.services import ServiceError

        value = self.account(self.result(job_id), account)
        day = day or value["summary"]["confirmed_through"]
        if day is None:
            return {
                "date": None,
                "cash": value["summary"]["cash"],
                "cash_weight": None,
                "items": [],
            }
        row = next((row for row in value["positions"] if row["date"] == day), None)
        if row is None:
            raise ServiceError(404, "not_found", "该日期无可确认持仓")
        return row

    def stock_detail(
        self, job_id: str, code: str, account: str, page: int, page_size: int
    ) -> dict[str, Any]:
        from ashare_lab.services import ServiceError

        value = self.result(job_id)
        if code not in value["prices"]:
            raise ServiceError(404, "not_found", "股票不属于该组合")
        selected = self.account(value, account)
        rows = [r for r in selected["ledger"] if r["stock_code"] == code]
        return {
            "stock_code": code,
            "label": code,
            "account": account,
            "prices": value["prices"][code],
            "trades": [r for r in rows if r["event"] in {"buy", "sell"}],
            "items": rows[(page - 1) * page_size : page * page_size],
            "total": len(rows),
            "page": page,
            "page_size": page_size,
            "warnings": selected["warnings"],
        }

    def export(self, job_id: str, view: str = "ledger") -> bytes:
        value = self.result(job_id)
        rows = []
        for name in ["portfolio", "benchmark"]:
            account = self.account(value, name)
            metadata = {
                "account": name,
                "engine_version": value["engine_version"],
                "fixture_mode": value["fixture_mode"],
                "complete": account["summary"]["complete"],
                "parameters": json.dumps(value["parameters"], ensure_ascii=False),
                "data_versions": json.dumps(value["data_versions"], ensure_ascii=False),
                "strategy_versions": json.dumps(value["strategy_versions"], ensure_ascii=False),
                "warnings": " / ".join(account["warnings"]),
                "assumptions": json.dumps(value["assumptions"], ensure_ascii=False),
            }
            if view == "summary":
                source = [account["summary"]]
            elif view == "curves":
                source = account["curve"]
            elif view == "positions":
                source = [
                    {**{k: v for k, v in day.items() if k != "items"}, **row}
                    for day in account["positions"]
                    for row in day["items"]
                ]
            else:
                source = account["ledger"]
            for row in source or [{}]:
                rows.append(
                    {
                        **{
                            k: json.dumps(v, ensure_ascii=False)
                            if isinstance(v, (dict, list))
                            else v
                            for k, v in row.items()
                        },
                        **metadata,
                    }
                )
        output = io.StringIO()
        columns = list(dict.fromkeys(key for row in rows for key in row)) or ["account", "complete"]
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode("utf-8-sig")
