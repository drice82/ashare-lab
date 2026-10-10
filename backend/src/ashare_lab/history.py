"""Date discovery and complete stock-history replacement with pinned recovery state."""

import json
from collections.abc import Callable
from contextlib import closing
from datetime import date, timedelta
from typing import Any

import polars as pl

from ashare_lab.db import Database
from ashare_lab.providers import LIMITS, DataProvider, ProviderError, normalize
from ashare_lab.warehouse import MarketRepository


class HistoryRefresh:
    def __init__(
        self,
        db: Database,
        repository: MarketRepository,
        provider: DataProvider,
        concurrency: int = 1,
    ):
        self.db, self.repository, self.provider = db, repository, provider
        self.concurrency = concurrency

    def run(
        self,
        job: dict[str, Any],
        dataset: str,
        codes: list[str],
        end: date,
        full: bool,
        check: Callable[[], None],
        *,
        before_complete: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        job_id, snapshot = job["id"], job["snapshot"]
        state_key = f"_{dataset}_refresh"
        if state_key not in snapshot:
            baseline = {k: v for k, v in snapshot.items() if not k.startswith("_")}
            coverage = self.repository.coverage(dataset, baseline)
            dates: dict[str, str] = {}
            legacy = (
                self.repository.version(baseline[dataset], False)["created_at"][:10]
                if dataset in baseline
                else ""
            )
            for key, row in coverage.items():
                if row.get("complete") and not key.startswith("history:"):
                    covered = (
                        [key[6:]] if key.startswith("stock:") else row.get("complete_codes", [])
                    )
                    for code in covered:
                        dates[code] = max(dates.get(code, ""), row.get("as_of_date", legacy))
            observation = coverage.get("history:observation", {})
            for code in observation.get("complete_codes", []):
                if code in dates and observation.get("complete"):
                    dates[code] = max(dates[code], observation["as_of_date"])
            existing_dates = [dates[c] for c in codes if dates.get(c)]
            # Missing histories bootstrap independently; a missing market baseline needs full refresh.
            full = full or not existing_dates
            scanned = observation.get("as_of_date") or (
                min(existing_dates) if existing_dates else end.isoformat()
            )
            start = min(end, date.fromisoformat(scanned)) - timedelta(days=30)
            targets = set(codes) if full else {c for c in codes if not dates.get(c)}
            history = self.repository.read(dataset, baseline, codes).to_dicts()
            if not full and dataset == "dividend":
                recent = (end - timedelta(days=365)).isoformat()
                targets.update(
                    r["stock_code"]
                    for r in history
                    if recent <= (r.get("ann_date") or "") <= end.isoformat()
                    and (
                        r.get("div_proc") != "实施"
                        or not r.get("pay_date")
                        or r["pay_date"] >= end.isoformat()
                    )
                )
            if not full and dataset == "namechange":
                latest: dict[str, dict[str, Any]] = {}
                for row in history:
                    if row.get("start_date") and row["start_date"] <= end.isoformat():
                        if row["start_date"] >= latest.get(row["stock_code"], {}).get(
                            "start_date", ""
                        ):
                            latest[row["stock_code"]] = row
                basics = self.repository.read("stock_basic", self.db.snapshot(), codes).to_dicts()
                targets.update(
                    r["stock_code"]
                    for r in basics
                    if r.get("name") != latest.get(r["stock_code"], {}).get("name")
                )
            snapshot[state_key] = {
                "codes": codes,
                "end": end.isoformat(),
                "start": start.isoformat(),
                "baseline": baseline,
                "full": full,
                "targets": sorted(targets),
                "queries": {},
                "discovery_complete": full,
                "baseline_full_date": min(existing_dates)
                if len(existing_dates) == len(codes) and existing_dates
                else None,
            }
            snapshot[f"_{dataset}_codes"] = json.dumps(codes)
            snapshot[f"_{dataset}_as_of"] = end.isoformat()
            self.db.update(job_id, snapshot=snapshot)
        state = snapshot[state_key]
        codes, end = state["codes"], date.fromisoformat(state["end"])

        def query(params: dict[str, str]) -> list[str]:
            check()
            key = json.dumps(params, sort_keys=True)
            if key in state["queries"]:
                return state["queries"][key]
            frame = self.provider.fetch(dataset, params)
            if frame.height >= LIMITS[dataset]:
                if dataset == "namechange" and params["start_date"] != params["end_date"]:
                    first = date(
                        int(params["start_date"][:4]),
                        int(params["start_date"][4:6]),
                        int(params["start_date"][6:]),
                    )
                    last = date(
                        int(params["end_date"][:4]),
                        int(params["end_date"][4:6]),
                        int(params["end_date"][6:]),
                    )
                    middle = first + (last - first) // 2
                    affected = sorted(
                        set(
                            query(
                                {
                                    "start_date": first.strftime("%Y%m%d"),
                                    "end_date": middle.strftime("%Y%m%d"),
                                }
                            )
                        )
                        | set(
                            query(
                                {
                                    "start_date": (middle + timedelta(days=1)).strftime("%Y%m%d"),
                                    "end_date": last.strftime("%Y%m%d"),
                                }
                            )
                        )
                    )
                else:
                    # Full stock calls resolve an overflowing single day without trusting truncation.
                    affected = codes
            else:
                if frame.height and "ts_code" not in frame.columns:
                    raise ProviderError("schema_error", f"{dataset} 日期发现缺少股票代码")
                affected = (
                    sorted(set(frame["ts_code"].to_list()) & set(codes)) if frame.height else []
                )
            state["queries"][key] = affected
            state["targets"] = sorted(set(state["targets"]) | set(affected))
            check()
            self.db.update(job_id, snapshot=snapshot)
            return affected

        if not state["discovery_complete"]:
            if dataset == "namechange":
                query(
                    {
                        "start_date": state["start"].replace("-", ""),
                        "end_date": end.strftime("%Y%m%d"),
                    }
                )
            else:
                cursor = date.fromisoformat(state["start"])
                while cursor <= end:
                    for field in ["ann_date", "imp_ann_date", "record_date", "ex_date"]:
                        query({field: cursor.strftime("%Y%m%d")})
                    cursor += timedelta(days=1)
            state["discovery_complete"] = True
            check()
            self.db.update(job_id, snapshot=snapshot)
        targets = state["targets"]
        from ashare_lab.sync import prefetch

        def request_stock(code: str) -> pl.DataFrame:
            check()
            return self.provider.fetch(dataset, {"ts_code": code})

        pending = (
            code for code in targets if not self.db.completed(job_id, f"{dataset}:stock:{code}")
        )
        with closing(prefetch(request_stock, pending, self.concurrency)) as responses:
            for index, (code, response) in enumerate(responses):
                check()
                key = f"{dataset}:stock:{code}"
                frame = response.result()
                if frame.height >= LIMITS[dataset]:
                    raise ProviderError("truncated", f"{dataset} 单股完整历史达到行数上限")
                normalized = normalize(dataset, frame, self.provider.source, job_id)
                if normalized.height and normalized.filter(pl.col("stock_code") != code).height:
                    raise ProviderError("schema_error", f"{dataset} 完整历史股票不一致")
                check()
                self.repository.publish(
                    dataset,
                    normalized,
                    f"stock:{code}",
                    self.provider.source,
                    job_id,
                    checkpoint=(job_id, key),
                    replace_codes=[code],
                    coverage_info={"as_of_date": end.isoformat(), "history_baseline": True},
                )
                self.db.update(job_id, progress=0.1 + 0.85 * (index + 1) / max(len(targets), 1))
        check()
        result = {
            "warnings": [],
            "datasets": self.db.snapshot(),
            "end_date": end.isoformat(),
            "stock_count": len(codes),
            "refreshed_count": len(targets),
            "scan_start": state["start"],
            "history_refresh": "full" if state["full"] else "incremental",
        }
        if not self.db.completed(job_id, f"{dataset}:observation"):
            previous = self.repository.coverage(dataset, self.db.snapshot()).get(
                "history:observation", {}
            )
            day = max(previous.get("as_of_date", ""), end.isoformat())
            complete_codes = (
                codes
                if end.isoformat() >= previous.get("as_of_date", "")
                else previous.get("complete_codes", [])
            )
            info = {
                "as_of_date": day,
                "complete_codes": complete_codes,
                "scan_start": state["start"],
                "refresh": result["history_refresh"],
                "full_as_of_date": max(
                    previous.get("full_as_of_date") or state.get("baseline_full_date") or "",
                    end.isoformat() if state["full"] else "",
                )
                or None,
            }
            if before_complete is not None:
                before_complete()
            self.repository.publish(
                dataset,
                normalize(dataset, pl.DataFrame(), self.provider.source, job_id),
                "history:observation",
                self.provider.source,
                job_id,
                checkpoint=(job_id, f"{dataset}:observation"),
                coverage_info=info,
                complete_job=(job_id, result),
            )
        result["datasets"] = self.db.snapshot()
        return result
