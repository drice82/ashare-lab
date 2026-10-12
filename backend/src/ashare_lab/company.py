"""On-demand latest company information, isolated from historical research."""

import json
import math
import os
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import select, text

from ashare_lab.config import Settings
from ashare_lab.db import Database, identifier, jobs, now
from ashare_lab.providers import DataProvider, ProviderError, iso_date
from ashare_lab.warehouse import MarketRepository

COMPANY_VERSION = "1.0.0"
COMPANY_FIELDS = {
    "stock_company": "ts_code,com_name,com_id,exchange,chairman,manager,secretary,reg_capital,setup_date,province,city,introduction,website,email,office,employees,main_business,business_scope",
    "fina_indicator": "ts_code,ann_date,end_date,eps,bps,roe,grossprofit_margin,netprofit_margin,debt_to_assets,or_yoy,netprofit_yoy,dt_netprofit_yoy,profit_dedt,update_flag",
    "fina_mainbz": "ts_code,end_date,bz_item,bz_code,bz_sales,bz_profit,bz_cost,curr_type,update_flag",
    "income": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,basic_eps,total_revenue,revenue,operate_profit,n_income_attr_p,update_flag",
    "balancesheet": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,total_assets,total_liab,total_hldr_eqy_exc_min_int,update_flag",
    "cashflow": "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act,c_cash_equ_end_period,update_flag",
}
SECTION_DATASETS = {
    "company": "stock_company",
    "indicators": "fina_indicator",
    "products": "fina_mainbz",
    "regions": "fina_mainbz",
    "income": "income",
    "balance": "balancesheet",
    "cashflow": "cashflow",
}
TEXT_FIELDS = {
    "ts_code",
    "com_name",
    "com_id",
    "exchange",
    "chairman",
    "manager",
    "secretary",
    "province",
    "city",
    "introduction",
    "website",
    "email",
    "office",
    "main_business",
    "business_scope",
    "report_type",
    "comp_type",
    "bz_item",
    "bz_code",
    "curr_type",
    "update_flag",
}
DATE_FIELDS = {"ann_date", "f_ann_date", "end_date", "setup_date"}


def normalize_section(
    dataset: str, frame: pl.DataFrame, code: str, as_of: str
) -> list[dict[str, Any]]:
    output = []
    for raw in frame.to_dicts():
        if raw.get("ts_code") != code:
            raise ProviderError("schema_error", "企业资料返回了其他股票或缺少代码")
        row: dict[str, Any] = {}
        for key, value in raw.items():
            if key in DATE_FIELDS:
                value = iso_date(value)
                if value:
                    try:
                        date.fromisoformat(value)
                    except ValueError:
                        raise ProviderError("schema_error", "企业资料日期无效") from None
            elif key not in TEXT_FIELDS and value is not None:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    raise ProviderError("schema_error", "企业资料数值无效") from None
                if not math.isfinite(value):
                    value = None
            row[key] = value
        if dataset != "stock_company":
            if not row.get("end_date"):
                raise ProviderError("schema_error", "财务资料缺少报告期")
            if row["end_date"] > as_of:
                continue
            announced = row.get("f_ann_date") or row.get("ann_date")
            if dataset != "fina_mainbz" and (not announced or announced > as_of):
                continue
            if dataset in {"income", "balancesheet", "cashflow"} and str(
                row.get("report_type")
            ) not in {"1", "4"}:
                continue
        output.append(row)
    output.sort(
        key=lambda row: (
            row.get("end_date") or "",
            row.get("f_ann_date") or row.get("ann_date") or "",
            str(row.get("update_flag") or ""),
            str(row.get("report_type") or ""),
        )
    )
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in output:
        row_key = (
            (row.get("end_date"), row.get("bz_item"), row.get("curr_type"))
            if dataset == "fina_mainbz"
            else (row.get("end_date"),)
        )
        previous = unique.get(row_key)
        if previous and previous != row:
            previous_rank = (
                previous.get("f_ann_date") or previous.get("ann_date"),
                previous.get("update_flag"),
                previous.get("report_type"),
            )
            row_rank = (
                row.get("f_ann_date") or row.get("ann_date"),
                row.get("update_flag"),
                row.get("report_type"),
            )
            if previous_rank == row_rank:
                raise ProviderError("schema_error", "企业财务资料存在无法判定的重复记录")
        unique[row_key] = row
    return sorted(
        unique.values(),
        key=lambda row: (row.get("end_date") or "", row.get("bz_item") or ""),
        reverse=True,
    )


def fresh(section: dict[str, Any]) -> bool:
    stamp = section.get("fetched_at")
    return bool(stamp and datetime.now(UTC) - datetime.fromisoformat(stamp) < timedelta(hours=24))


class CompanyService:
    def __init__(self, settings: Settings, db: Database, repository: MarketRepository):
        self.settings, self.db, self.repository = settings, db, repository

    def _latest(self, code: str, connection: Any) -> dict[str, Any] | None:
        row = (
            connection.execute(
                select(jobs)
                .where(
                    jobs.c.kind == "company_profile",
                    jobs.c.payload["stock_code"].as_string() == code,
                )
                .order_by(jobs.c.created_at.desc(), jobs.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def read(self, code: str) -> dict[str, Any]:
        with self.db.engine.connect() as conn:
            job = self._latest(code, conn)
        result = (job or {}).get("result") or {}
        sections = result.get("sections", {})
        return {
            "stock_code": code,
            "sections": sections,
            "as_of_date": result.get("as_of_date"),
            "data_version": result.get("data_version"),
            "valuation": result.get("valuation"),
            "fixture_mode": result.get("fixture_mode", False)
            or any(section.get("source") == "fixture" for section in sections.values()),
            "stale": len(sections) < len(SECTION_DATASETS)
            or any(not fresh(s) for s in sections.values()),
            "job": {"id": job["id"], "status": job["status"], "progress": job["progress"]}
            if job
            else None,
        }

    def refresh(self, code: str, force: bool = False) -> dict[str, Any]:
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            previous = self._latest(code, conn)
            if previous and previous["status"] in {"queued", "running", "cancel_requested"}:
                return previous
            sections = ((previous or {}).get("result") or {}).get("sections", {})
            if (
                previous
                and previous["status"] in {"partial", "failed"}
                and datetime.now(UTC) - datetime.fromisoformat(previous["updated_at"])
                < timedelta(minutes=5)
            ):
                return previous
            successful = {
                key
                for key, value in sections.items()
                if value.get("status") in {"ready", "empty"} and fresh(value)
            }
            if previous and not force and len(successful) == len(SECTION_DATASETS):
                return previous
            preserved = deepcopy(sections)
            pending = (
                list(SECTION_DATASETS)
                if force
                else [key for key in SECTION_DATASETS if key not in successful]
            )
            day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            job_id = identifier()
            conn.execute(
                jobs.insert().values(
                    id=job_id,
                    kind="company_profile",
                    status="queued",
                    payload={"stock_code": code, "as_of_date": day},
                    snapshot={**self.db.snapshot(conn), "_company_version": COMPANY_VERSION},
                    progress=0,
                    cancel_requested=False,
                    created_at=now(),
                    updated_at=now(),
                    attempt=0,
                    result={
                        "sections": preserved,
                        "pending_sections": pending,
                        "as_of_date": day,
                        "data_version": job_id,
                    },
                )
            )
        return self.db.job(job_id)

    def execute(
        self, job: dict[str, Any], provider: DataProvider, check: Callable[[], None]
    ) -> dict[str, Any]:
        if job["snapshot"].get("_company_version") != COMPANY_VERSION:
            raise ProviderError("incompatible_engine_version", "企业资料版本不兼容")
        code, day = job["payload"]["stock_code"], job["payload"]["as_of_date"]
        result = deepcopy(job.get("result") or {})
        result.pop("path", None)
        sections = result.setdefault("sections", {})
        pending = result.setdefault(
            "pending_sections", [name for name in SECTION_DATASETS if name not in sections]
        )
        result.update(
            as_of_date=day, data_version=job["id"], fixture_mode=provider.source == "fixture"
        )
        for index, (name, dataset) in enumerate(SECTION_DATASETS.items()):
            check()
            if name not in pending:
                continue
            try:
                params = {"ts_code": code}
                if dataset == "stock_company":
                    items = normalize_section(dataset, provider.fetch(dataset, params), code, day)
                else:
                    # Fetch bounded individual report periods, including a full comparison window.
                    end = date.fromisoformat(day)
                    periods = []
                    for year in range(end.year - 3, end.year + 1):
                        for month, last in ((3, 31), (6, 30), (9, 30), (12, 31)):
                            period = date(year, month, last)
                            if period <= end:
                                periods.append(period)
                    items = []
                    for period in sorted(periods, reverse=True)[:12]:
                        check()
                        query = {**params, "period": period.strftime("%Y%m%d")}
                        if dataset == "fina_mainbz":
                            query["type"] = "P" if name == "products" else "D"
                        frame = provider.fetch(dataset, query)
                        if frame.height >= 100:
                            raise ProviderError("truncated", "企业资料达到单次上限，无法确认完整")
                        items.extend(normalize_section(dataset, frame, code, day))
                        if dataset == "fina_mainbz" and items:
                            break
                    items = (
                        normalize_section(dataset, pl.DataFrame(items), code, day) if items else []
                    )
                sections[name] = {
                    "status": "ready" if items else "empty",
                    "items": items,
                    "source": provider.source,
                    "fetched_at": now(),
                }
            except ProviderError as exc:
                sections[name] = {
                    "status": exc.code,
                    "items": [],
                    "message": str(exc),
                    "source": provider.source,
                    "fetched_at": now(),
                }
            check()
            pending.remove(name)
            self.db.update(job["id"], result=result, progress=(index + 1) / len(SECTION_DATASETS))
        # Valuation is explicitly from the pinned local daily-basic version, not the historical run.
        frame = self.repository.read("daily_basic", job["snapshot"], [code], end_date=day)
        if frame.height:
            valuation_row = frame.sort("trade_date").to_dicts()[-1]
            result["valuation"] = {
                key: valuation_row.get(key)
                for key in ("trade_date", "pe", "pe_ttm", "pb", "total_mv", "circ_mv", "source")
            }
        result["warnings"] = [
            name
            for name, section in sections.items()
            if section["status"] not in {"ready", "empty"}
        ]
        directory = self.settings.data_dir / "results" / job["id"]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "company.json"
        temporary = target.with_suffix(".tmp")
        result["fixture_mode"] = result["fixture_mode"] or any(
            section.get("source") == "fixture" for section in sections.values()
        )
        if target.exists():
            if json.loads(target.read_text()) != result:
                raise ProviderError("checkpoint_conflict", "企业资料结果与已发布检查点不一致")
        else:
            with temporary.open("w") as handle:
                json.dump(result, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        result["path"] = str(target.relative_to(self.settings.data_dir))
        return result
