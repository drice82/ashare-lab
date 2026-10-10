import calendar as calendar_module
import math
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from ashare_lab.schemas import Universe
from ashare_lab.warehouse import MarketRepository

FACTOR_VERSION = "1.0.0"
NUMERIC_FACTORS = {
    "dividend_yield_ttm": ("现金股息率 TTM", "%"),
    "daily_return": ("当日涨跌幅", "%"),
    "return_20d": ("20日收益率", "%"),
    "return_60d": ("60日收益率", "%"),
    "ma20": ("MA20", "元"),
    "ma60": ("MA60", "元"),
    "close": ("未复权收盘价", "元"),
    "signal_close": ("信号收盘价", "元"),
    "volume": ("成交量", "股"),
    "volume_ratio_5d": ("前5日量比", "倍"),
    "volume_ratio_20d": ("前20日量比", "倍"),
    "price_distance_ma20": ("相对MA20偏离", "%"),
    "drawdown_60d": ("60日回撤", "%"),
    "ma20_change_5d": ("MA20五日变化", "%"),
    "amount_mean_20d": ("20日平均成交额", "元"),
    "price_ma20_ratio": ("收盘价/MA20", "倍"),
    "breakout_price_ratio": ("收盘价/前20日最高", "倍"),
    "abs_price_distance_ma20": ("MA20绝对偏离", "%"),
    "valid_days_20d": ("20日有效数量", "日"),
}
BOOLEAN_FACTORS = {
    "breakout_20d": ("突破前20日最高", "布尔"),
    "is_st": ("ST状态", "布尔"),
    "is_suspended": ("停牌状态", "布尔"),
}


def adjusted_prices(
    days: list[str],
    rows: dict[str, Any],
    adjustment: dict[str, Any],
    anchor: float | None,
    field: str = "close",
) -> list[float] | None:
    """Prices anchored only to the signal day, shared by screening and backtests."""
    if not days or not anchor or anchor <= 0:
        return None
    result = []
    for day in days:
        row, adj = rows.get(day), adjustment.get(day)
        if row is None or not adj or adj <= 0 or row.get(field) is None:
            return None
        result.append(float(row[field]) * adj / anchor)
    return result


def prior_year(day: date) -> date:
    return day.replace(
        year=day.year - 1, day=min(day.day, calendar_module.monthrange(day.year - 1, day.month)[1])
    )


class UniverseManager:
    def __init__(self, repository: MarketRepository):
        self.repository = repository

    def resolve(
        self,
        as_of: str,
        universe: Universe,
        snapshot: dict[str, str],
        exclude_delisted: bool = True,
    ) -> list[dict[str, Any]]:
        basics = self.repository.read("stock_basic", snapshot)
        if basics.is_empty():
            return []
        output = []
        for row in basics.to_dicts():
            code = row["stock_code"]
            if exclude_delisted and row.get("list_status") == "D":
                continue
            # Explicit security type filter, not just a suffix filter.
            if not (
                (code.endswith(".SH") and code.startswith(("600", "601", "603", "605", "688")))
                or (
                    code.endswith(".SZ")
                    and code.startswith(("000", "001", "002", "003", "300", "301"))
                )
            ):
                continue
            if not row.get("list_date") or row["list_date"] > as_of:
                continue
            if row.get("delist_date") and row["delist_date"] <= as_of:
                continue
            if universe.type == "custom" and code not in universe.codes:
                continue
            output.append(row)
        return sorted(output, key=lambda x: x["stock_code"])


class FactorEngine:
    def __init__(self, repository: MarketRepository):
        self.repository = repository

    def trading_days(self, as_of: str, snapshot: dict[str, str]) -> list[str]:
        frame = self.repository.read("trade_cal", snapshot, end_date=as_of)
        if frame.is_empty():
            return []
        return sorted(set(frame.filter(pl.col("is_open") == 1)["cal_date"].to_list()))

    def context(self, as_of: str, snapshot: dict[str, str]) -> dict[str, Any]:
        days = self.trading_days(as_of, snapshot)
        if as_of not in days:
            raise ValueError("指定日期不是已同步的交易日")
        return {
            "window_days": days[-66:],
            "dividend_complete": self.repository.history_complete_codes(
                "dividend", as_of, snapshot
            ),
            "name_complete": self.repository.history_complete_codes("namechange", as_of, snapshot),
            "suspension_complete": self.repository.covered_date("suspend_d", as_of, snapshot),
        }

    def compute(
        self,
        as_of: str,
        instruments: list[dict[str, Any]],
        snapshot: dict[str, str],
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        context = self.context(as_of, snapshot) if context is None else context
        window_days = context["window_days"]
        codes = [row["stock_code"] for row in instruments]
        latest_dates = self.repository.last_trade_dates(snapshot, codes, as_of)
        bars = self.repository.read("daily", snapshot, codes, window_days[0], as_of)
        factors = self.repository.read("adj_factor", snapshot, codes, window_days[0], as_of)
        dividends = self.repository.read("dividend", snapshot, codes)
        names = self.repository.read("namechange", snapshot, codes)
        suspensions = self.repository.read("suspend_d", snapshot, codes, as_of, as_of)
        by_code: dict[str, list[dict[str, Any]]] = {}
        for row in bars.to_dicts():
            by_code.setdefault(row["stock_code"], []).append(row)
        adjustment = {
            (row["stock_code"], row["trade_date"]): row["adj_factor"] for row in factors.to_dicts()
        }
        names_by_code: dict[str, list[dict[str, Any]]] = {}
        divs_by_code: dict[str, list[dict[str, Any]]] = {}
        for row in names.to_dicts():
            names_by_code.setdefault(row["stock_code"], []).append(row)
        for row in dividends.to_dicts():
            divs_by_code.setdefault(row["stock_code"], []).append(row)
        suspended = {
            row["stock_code"] for row in suspensions.to_dicts() if row.get("suspend_type") == "S"
        }

        dividend_complete = context["dividend_complete"]
        name_complete = context["name_complete"]
        suspension_complete = context["suspension_complete"]
        output = []
        for instrument in instruments:
            code = instrument["stock_code"]
            rows = {row["trade_date"]: row for row in by_code.get(code, [])}
            values: dict[str, Any] = dict.fromkeys(NUMERIC_FACTORS | BOOLEAN_FACTORS)
            flags = ["conservative_pit", "historical_provider_revisions_not_reconstructed"]
            if not suspension_complete:
                flags.append("suspension_dataset_unavailable")
            current = rows.get(as_of)
            latest = latest_dates.get(code)
            values["is_suspended"] = (
                True
                if code in suspended
                else False
                if current and current.get("volume") and current["volume"] > 0
                else None
            )
            if current is None:
                flags.append("missing_current_bar")
            else:
                values.update(close=current["close"], volume=current["volume"])
                if current.get("volume") is None:
                    flags.append("missing_current_volume")
                elif current["volume"] == 0:
                    flags.append("zero_current_volume")
                if current.get("pre_close") and current["pre_close"] > 0:
                    values["daily_return"] = current["close"] / current["pre_close"] - 1
            historical_names = [
                r
                for r in names_by_code.get(code, [])
                if r.get("start_date")
                and r["start_date"] <= as_of
                and (not r.get("end_date") or r["end_date"] >= as_of)
                and r.get("ann_date")
                and r["ann_date"] < as_of
            ]
            historical_names.sort(key=lambda r: r["start_date"])
            name = instrument.get("name") or code
            if historical_names and code in name_complete:
                name = historical_names[-1]["name"]
                values["is_st"] = "ST" in name.upper()
            else:
                flags.append("historical_st_unknown")
                flags.append("display_name_current_snapshot")
            anchor = adjustment.get((code, as_of))

            def prices(
                field: str,
                count: int,
                shift: int = 0,
                *,
                rows: dict[str, Any] = rows,
                anchor: float | None = anchor,
                code: str = code,
            ) -> list[float] | None:
                subset = window_days[
                    len(window_days) - shift - count : len(window_days) - shift if shift else None
                ]
                if (
                    len(window_days) < count + shift
                    or len(subset) != count
                    or not anchor
                    or anchor <= 0
                ):
                    return None
                return adjusted_prices(
                    subset, rows, {d: adjustment.get((code, d)) for d in subset}, anchor, field
                )

            def observations(
                field: str, count: int, shift: int = 0, *, rows: dict[str, Any] = rows
            ) -> list[float] | None:
                if len(window_days) < count + shift:
                    return None
                subset = window_days[-count - shift : len(window_days) - shift if shift else None]
                if any(day not in rows or rows[day].get(field) is None for day in subset):
                    return None
                return [float(rows[day][field]) for day in subset]

            signal = prices("close", 1)
            if signal:
                values["signal_close"] = signal[0]
            for count in (20, 60):
                price_values = prices("close", count)
                if price_values:
                    values[f"ma{count}"] = float(np.mean(price_values))
                returns = prices("close", count + 1)
                if returns and returns[0] > 0:
                    values[f"return_{count}d"] = returns[-1] / returns[0] - 1
            for count in (5, 20):
                volumes = observations("volume", count, 1)
                if (
                    current
                    and current.get("volume") is not None
                    and volumes
                    and np.mean(volumes) > 0
                ):
                    values[f"volume_ratio_{count}d"] = current["volume"] / float(np.mean(volumes))
            if values["ma20"] and signal:
                values["price_ma20_ratio"] = signal[0] / values["ma20"]
                values["price_distance_ma20"] = signal[0] / values["ma20"] - 1
                values["abs_price_distance_ma20"] = abs(values["price_distance_ma20"])
                previous_ma = prices("close", 20, 5)
                if previous_ma and np.mean(previous_ma) > 0:
                    values["ma20_change_5d"] = values["ma20"] / float(np.mean(previous_ma)) - 1
            highs = prices("high", 60)
            if highs and signal:
                values["drawdown_60d"] = 1 - signal[0] / max(highs)
            prior_highs = prices("high", 20, 1)
            if prior_highs and signal:
                values["breakout_price_ratio"] = signal[0] / max(prior_highs)
                values["breakout_20d"] = signal[0] > max(prior_highs)
            amounts = observations("amount", 20)
            if amounts:
                values["amount_mean_20d"] = float(np.mean(amounts))
            values["valid_days_20d"] = sum(day in rows for day in window_days[-20:])
            if len(window_days) < 20 or values["valid_days_20d"] < 20:
                flags.append("insufficient_20d_bars")
            if not anchor or prices("close", 20) is None:
                flags.append("missing_adjustment_or_window")
            yield_value, dividend_flags = self.dividend_yield(
                as_of,
                current,
                divs_by_code.get(code, []),
                code in dividend_complete,
            )
            values["dividend_yield_ttm"] = yield_value
            flags.extend(dividend_flags)
            values = {
                k: (v if not isinstance(v, float) or math.isfinite(v) else None)
                for k, v in values.items()
            }
            output.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "as_of_date": as_of,
                    "latest_trade_date": latest,
                    "factor_values": values,
                    "data_quality_flags": flags,
                }
            )
        return output

    @staticmethod
    def dividend_yield(
        as_of: str, current: dict[str, Any] | None, events: list[dict[str, Any]], complete: bool
    ) -> tuple[float | None, list[str]]:
        if not complete:
            return None, ["dividend_history_incomplete"]
        if not current or not current.get("close"):
            return None, ["dividend_price_missing"]
        boundary = prior_year(date.fromisoformat(as_of)).isoformat()
        total = 0.0
        seen: dict[tuple[Any, Any], float] = {}
        for event in events:
            # Events not yet publicly announced have no influence, even on quality flags.
            if event.get("ann_date") and event["ann_date"] >= as_of:
                continue
            if event.get("imp_ann_date") and event["imp_ann_date"] >= as_of:
                continue
            if event.get("div_proc") != "实施":
                continue
            pay = event.get("pay_date")
            ex = event.get("ex_date")
            if pay and pay > as_of or ex and ex > as_of:
                continue
            if pay and pay <= boundary and ex and ex <= boundary:
                continue
            if any(
                not event.get(field)
                for field in ["ann_date", "imp_ann_date", "pay_date", "ex_date"]
            ):
                return None, ["dividend_visibility_unknown"]
            if event.get("stk_div") is None:
                return None, ["unsupported_share_basis"]
            if event["stk_div"] != 0:
                return None, ["unsupported_share_basis"]
            cash = event.get("cash_div_tax")
            if cash is None or cash < 0:
                return None, ["dividend_amount_unknown"]
            if pay is not None and boundary < pay <= as_of:
                event_key = (event.get("end_date"), ex)
                if event_key in seen:
                    if seen[event_key] != cash:
                        return None, ["dividend_conflicting_events"]
                    continue
                seen[event_key] = cash
                total += cash
        return total / current["close"], []
