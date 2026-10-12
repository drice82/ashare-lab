"""Public implementation arrangements; never counted as paid cash dividends."""

import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

DIVIDEND_VERSION = "1.0.0"


def upcoming_dividends(
    as_of: str, days: int, close: float | None, events: list[dict[str, Any]], complete: bool
) -> dict[str, Any]:
    end = (date.fromisoformat(as_of) + timedelta(days=days)).isoformat()
    candidates: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    unknown = not complete
    flags = [] if complete else ["dividend_history_incomplete"]
    for event in events:
        # Unpublished future records must not influence either results or quality flags.
        if any(event.get(key) and event[key] >= as_of for key in ("ann_date", "imp_ann_date")):
            continue
        if event.get("div_proc") != "实施":
            continue
        record = event.get("record_date")
        if record and not as_of < record <= end:
            continue
        cash = event.get("cash_div_tax")
        if cash is not None and math.isfinite(cash) and cash == 0:
            continue
        if not record or not event.get("ann_date") or not event.get("imp_ann_date"):
            unknown = True
            flags.append("dividend_visibility_unknown")
            continue
        if cash is None or not math.isfinite(cash) or cash < 0:
            unknown = True
            flags.append("dividend_amount_unknown")
            continue
        candidates[(event.get("end_date"), record)].append(event)
    selected = []
    for records in candidates.values():
        if len({(r.get("cash_div_tax"), r.get("ex_date"), r.get("pay_date")) for r in records}) > 1:
            unknown = True
            flags.append("dividend_conflicting_events")
            continue
        event = records[-1]
        cash = Decimal(str(event["cash_div_tax"]))
        estimate = (
            Decimal(10000) / Decimal(str(close)) * cash
            if close is not None and math.isfinite(close) and close > 0
            else None
        )
        selected.append(
            {
                **{
                    key: event.get(key)
                    for key in (
                        "end_date",
                        "ann_date",
                        "imp_ann_date",
                        "record_date",
                        "ex_date",
                        "pay_date",
                    )
                },
                "cash_per_share": float(cash),
                "cash_per_10": float(cash * 10),
                "estimated_cash_10000": float(
                    estimate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
                if estimate is not None
                else None,
            }
        )
    selected.sort(key=lambda row: (row["record_date"], row.get("end_date") or ""))
    return {
        "matched": True if selected else None if unknown else False,
        "events": selected,
        "days": days,
        "flags": sorted(set(flags)),
    }
