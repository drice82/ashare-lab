"""DCA application boundary: input readiness, persistence and exports."""

import csv
import io
import json
from datetime import timedelta
from typing import Any

from ashare_lab.db import Database
from ashare_lab.dca import DCA_VERSION, performance_metrics
from ashare_lab.factors import UniverseManager
from ashare_lab.schemas import BacktestRequest, DcaRequest, Universe
from ashare_lab.warehouse import MarketRepository

REQUIRED = {"stock_basic", "trade_cal", "daily"}


class DcaService:
    kind = "dca"
    label = "定投"
    extra_columns: list[str] = []

    def __init__(self, db: Database, repository: MarketRepository):
        self.db = db
        self.repository = repository

    def options(self) -> dict[str, Any]:
        snapshot = self.db.snapshot()
        daily_end = self.repository.latest_date("daily", snapshot)
        calendar_rows = (
            self.repository.read("trade_cal", snapshot, end_date=daily_end).to_dicts()
            if daily_end and "trade_cal" in snapshot
            else []
        )
        calendar_rows = [row for row in calendar_rows if row.get("exchange") == "SSE"]
        return {
            "calendar_start": min((row["cal_date"] for row in calendar_rows), default=None),
            "trading_days": sorted(
                {row["cal_date"] for row in calendar_rows if row["is_open"] == 1}
            ),
            "data_versions": snapshot,
            "latest_market_date": daily_end,
            "fixture_mode": any(
                self.repository.version(v, resolve_coverage=False)["source"] == "fixture"
                for v in snapshot.values()
            ),
            "defaults": {
                "monthly_amount": "3000",
                "monthly_day": 1,
                "commission_rate": "0.0003",
                "minimum_commission": "5",
                "transfer_rate": "0.00001",
            },
        }

    def submit(self, request: DcaRequest) -> dict[str, Any]:
        self.validate(request)
        snapshot = request.data_versions
        return self.db.enqueue(
            "dca",
            request.model_dump(mode="json"),
            {**snapshot, "_dca_version": DCA_VERSION, "_universe_policy": "survivors_v1"},
        )

    def validate(self, request: DcaRequest | BacktestRequest) -> None:
        from ashare_lab.services import ServiceError

        snapshot = request.data_versions
        if not REQUIRED <= snapshot.keys():
            raise ServiceError(409, "data_not_ready", "请先同步股票基础信息、交易日历和行情")
        for name, version_id in snapshot.items():
            if name.startswith("_"):
                raise ServiceError(422, "invalid_version", "数据版本包含非法数据集")
            try:
                version = self.repository.version(version_id, resolve_coverage=False)
            except (KeyError, ValueError) as exc:
                raise ServiceError(422, "invalid_version", "指定数据版本不存在") from exc
            if version["dataset"] != name:
                raise ServiceError(422, "invalid_version", "数据集与版本不匹配")
        instruments = UniverseManager(self.repository).resolve(
            request.start_date.isoformat(),
            Universe(type="custom", codes=[request.stock_code]),
            snapshot,
        )
        if not instruments:
            raise ServiceError(
                422, "invalid_instrument", "股票不属于该版本存续沪深A股，或开始日期早于上市日"
            )
        cal = self.repository.read(
            "trade_cal",
            snapshot,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
        ).to_dicts()
        # Both mainland markets use the SSE calendar currently synced by the provider.
        calendar = {r["cal_date"]: r for r in cal if r.get("exchange") == "SSE"}
        if any(
            d.isoformat() not in calendar or calendar[d.isoformat()]["is_open"] != 1
            for d in (request.start_date, request.end_date)
        ):
            raise ServiceError(422, "not_trading_day", "起止日期必须为已同步交易日")
        cursor = request.start_date
        while cursor <= request.end_date:
            if cursor.isoformat() not in calendar:
                raise ServiceError(409, "calendar_incomplete", "区间内交易日历不完整，请补齐数据")
            cursor += timedelta(days=1)

    def result(self, job_id: str) -> dict[str, Any]:
        from ashare_lab.services import ServiceError

        try:
            job = self.db.job(job_id)
        except KeyError as exc:
            raise ServiceError(404, "not_found", f"{self.label}任务不存在") from exc
        if job["kind"] != self.kind:
            raise ServiceError(404, "not_found", f"{self.label}任务不存在")
        if job["status"] != "succeeded" or not job["result"]:
            raise ServiceError(409, "result_not_ready", f"{self.label}结果尚未发布")
        with (self.repository.root / job["result"]["path"]).open() as handle:
            value: dict[str, Any] = json.load(handle)
        if self.kind == "dca" and "annualized_return" not in value["summary"]:
            annualized, drawdown = (
                performance_metrics(value["curve"])
                if value["summary"]["complete"]
                else (None, None)
            )
            value["summary"].update(annualized_return=annualized, max_drawdown=drawdown)
            value["assumptions"]["performance"] = (
                "年化收益为实际投入日期的XIRR（365天/年）；最大回撤剔除新增投入影响，包含费用和应收分红"
            )
        return value

    def results(self, job_id: str, page: int, page_size: int) -> dict[str, Any]:
        result = self.result(job_id)
        ledger = result.pop("ledger")
        return {
            **result,
            "items": ledger[(page - 1) * page_size : page * page_size],
            "total": len(ledger),
            "page": page,
            "page_size": page_size,
        }

    def export(self, job_id: str) -> bytes:
        result = self.result(job_id)
        stream = io.StringIO(newline="")
        columns = [
            "date",
            "event",
            "planned_dates",
            "shares_delta",
            "price",
            "amount",
            "fee",
            "cash",
            "shares",
            "reason",
            "complete",
            "warnings",
            "data_versions",
            "parameters",
            "universe_policy",
            "assumptions",
        ]
        writer = csv.DictWriter(stream, fieldnames=columns + self.extra_columns)
        writer.writeheader()
        metadata = {
            "complete": result["summary"]["complete"],
            "warnings": json.dumps(result["warnings"], ensure_ascii=False),
            "data_versions": json.dumps(result["data_versions"], ensure_ascii=False),
            "parameters": json.dumps(result["parameters"], ensure_ascii=False),
            "universe_policy": result["universe_policy"],
            "assumptions": json.dumps(result["assumptions"], ensure_ascii=False),
        }
        metadata.update(
            {column: result[column] for column in self.extra_columns if column in result}
        )
        for row in result["ledger"] or [{}]:
            writer.writerow(
                {**row, "planned_dates": " / ".join(row.get("planned_dates", [])), **metadata}
            )
        return ("\ufeff" + stream.getvalue()).encode("utf-8")
