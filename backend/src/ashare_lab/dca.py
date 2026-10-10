"""Monthly cash/position accounting, independent of API and job persistence."""

import calendar
from collections.abc import Callable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import exp, expm1, isfinite, log
from typing import Any

from ashare_lab.schemas import BacktestCommonRequest, DcaRequest
from ashare_lab.warehouse import MarketRepository

DCA_VERSION = "1.0.1"
CENT = Decimal("0.01")
ZERO = Decimal(0)


def decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def prepare(
    request: DcaRequest | BacktestCommonRequest, repository: MarketRepository
) -> dict[str, Any]:
    """Read only the pinned versions; no current-head fallback."""
    snapshot = request.data_versions
    start, end = request.start_date.isoformat(), request.end_date.isoformat()
    code = request.stock_code
    frames = {
        name: repository.read(name, snapshot, [code], start, end)
        for name in ["daily", "adj_factor", "stk_limit", "suspend_d"]
    }
    cal = repository.read("trade_cal", snapshot, start_date=start, end_date=end).to_dicts()
    # The synchronizer currently requests the SSE calendar for both mainland markets.
    days = sorted({r["cal_date"] for r in cal if r.get("exchange") == "SSE" and r["is_open"] == 1})
    issues: dict[str, str] = {}
    coverages = {name: repository.coverage(name, snapshot) for name in frames}
    for day in days:
        for name, coverage in coverages.items():
            if not any(
                r.get("complete")
                and (r.get("trade_date") == day or day in r.get("trade_dates", []))
                for r in coverage.values()
            ):
                issues[day] = f"{name} 在 {day} 未确认完整覆盖"
                break
    covered = code in repository.history_complete_codes("dividend", end, snapshot)
    suspensions = frames["suspend_d"].to_dicts()
    return {
        "days": days,
        "bars": {r["trade_date"]: r for r in frames["daily"].to_dicts()},
        "factors": {r["trade_date"]: r["adj_factor"] for r in frames["adj_factor"].to_dicts()},
        "limits": {r["trade_date"]: r for r in frames["stk_limit"].to_dicts()},
        "suspended": {r["trade_date"] for r in suspensions if r.get("suspend_type") == "S"},
        "events": repository.read("dividend", snapshot, [code]).to_dicts(),
        "issues": issues,
        "dividend_complete": covered,
    }


def confirmed_events(
    events: list[dict[str, Any]],
    day: str,
    start: str,
    seen: dict[tuple[Any, Any], dict[str, Any]],
) -> tuple[dict[tuple[Any, Any], dict[str, Any]], str | None]:
    """Public, implemented cash events; future announcements do not affect quality."""
    problem: str | None = None
    active: dict[tuple[Any, Any], dict[str, Any]] = {}
    for event in events:
        # Future announcements have no effect, including on quality flags.
        if any(event.get(f) and event[f] > day for f in ("ann_date", "imp_ann_date")):
            continue
        if event.get("div_proc") != "实施":
            continue
        effective = event.get("record_date") or event.get("ex_date") or event.get("pay_date")
        if (
            effective
            and max(str(event.get(f) or "") for f in ("record_date", "ex_date", "pay_date")) < start
        ):
            continue
        if effective and effective > day:
            continue
        if any(
            not event.get(f)
            for f in ("ann_date", "imp_ann_date", "record_date", "ex_date", "pay_date")
        ):
            problem = "分红事件日期或公开可见性未知"
            break
        if not (
            event["ann_date"] <= event["record_date"]
            and event["imp_ann_date"] <= event["record_date"]
            and event["record_date"] < event["ex_date"] <= event["pay_date"]
        ):
            problem = "分红事件日期顺序异常"
            break
        if (
            event.get("stk_div") != 0
            or event.get("cash_div_tax") is None
            or decimal(event["cash_div_tax"]) < 0
        ):
            problem = "送转股或现金分红每股口径未知，无法完整计算"
            break
        key = (event.get("end_date"), event["ex_date"])
        signature = {
            f: event.get(f)
            for f in ("record_date", "ex_date", "pay_date", "cash_div_tax", "stk_div")
        }
        if key in seen and seen[key] != signature:
            problem = "同一分红事件存在冲突"
            break
        seen[key] = signature
        active[key] = event
    return active, problem


def check_factor_change(
    current_factor: Decimal,
    previous_factor: Decimal | None,
    observed_factor: Decimal | None,
    last_price: Decimal | None,
    reference: Any,
    cash_per_share: Decimal,
    day: str,
) -> tuple[Decimal, Decimal, str | None, str | None]:
    """Validate against the last verified anchor; tolerated drift never resets it."""
    problem = warning = None
    keep_factor_anchor = False
    if previous_factor is not None and current_factor != observed_factor:
        if (
            cash_per_share == 0
            and last_price is not None
            and reference is not None
            and decimal(reference) == last_price
            and abs(current_factor / previous_factor - 1) <= Decimal("0.001")
        ):
            keep_factor_anchor = True
            warning = (
                f"{day}：复权因子微小变化在0.1%容差内且前收盘参考价连续；"
                "按原始价格继续计算，不推定额外分红或送转股"
            )
        elif last_price is None or cash_per_share <= 0 or last_price <= cash_per_share:
            problem = "无法解释的复权因子变化，可能存在未支持的公司行动"
        else:
            expected = last_price / (last_price - cash_per_share)
            if abs(current_factor / previous_factor / expected - 1) > Decimal("0.001"):
                problem = "复权因子变化与已确认现金分红不一致"
    if not keep_factor_anchor and current_factor != observed_factor:
        previous_factor = current_factor
    return previous_factor or current_factor, current_factor, problem, warning


def performance_metrics(curve: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Dated contribution XIRR (365 days/year) and unitized, cash-flow-neutral drawdown."""
    flows: list[tuple[str, float]] = []
    contributed = previous_assets = 0.0
    nav = peak = 1.0
    drawdown = 0.0
    for point in curve:
        assets = float(point["total_assets"])
        flow = float(point["contributed"]) - contributed
        if flow > 0:
            flows.append((point["date"], flow))
        # New cash buys units at yesterday's NAV, before today's trade and fees.
        capital = previous_assets + flow
        if capital > 0:
            nav *= assets / capital
            peak = max(peak, nav)
            drawdown = max(drawdown, 1 - nav / peak)
        contributed = float(point["contributed"])
        previous_assets = assets
    if not flows:
        return None, None
    end = date.fromisoformat(curve[-1]["date"])
    dated = [(log(amount), (end - date.fromisoformat(day)).days / 365) for day, amount in flows]
    if not any(years > 0 for _, years in dated):
        return None, drawdown
    if previous_assets == 0:
        return -1.0, drawdown
    # Sum positive accumulated contributions in log space: monotone, unique root.
    terminal = log(previous_assets)

    def balance(rate: float) -> float:
        values = [amount + rate * years for amount, years in dated]
        largest = max(values)
        return largest + log(sum(exp(value - largest) for value in values)) - terminal

    low, high = -1.0, 1.0
    while balance(low) > 0 and low > -1048576:
        low *= 2
    while balance(high) < 0 and high < 1048576:
        high *= 2
    if balance(low) > 0 or balance(high) < 0:
        return None, drawdown
    for _ in range(100):
        midpoint = (low + high) / 2
        if balance(midpoint) > 0:
            high = midpoint
        else:
            low = midpoint
    try:
        annualized = expm1((low + high) / 2)
    except OverflowError:
        return None, drawdown
    return annualized if isfinite(annualized) else None, drawdown


def simulate(
    request: DcaRequest,
    market: dict[str, Any],
    check: Callable[[], None] = lambda: None,
    progress: Callable[[float], None] = lambda value: None,
) -> dict[str, Any]:
    start, end = request.start_date.isoformat(), request.end_date.isoformat()
    days = [d for d in market["days"] if start <= d <= end]
    plans: dict[str, list[str]] = {}
    month = request.start_date.replace(day=1)
    while month <= request.end_date:
        nominal = month.replace(
            day=min(request.monthly_day, calendar.monthrange(month.year, month.month)[1])
        )
        if request.start_date <= nominal <= request.end_date:
            execution = next((d for d in days if d >= nominal.isoformat()), None)
            if execution:
                plans.setdefault(execution, []).append(nominal.isoformat())
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    cash, contributed, fees, dividends = ZERO, ZERO, ZERO, ZERO
    shares = 0
    pending: list[str] = []
    ledger: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    receivables: dict[tuple[Any, Any], dict[str, Any]] = {}
    seen: dict[tuple[Any, Any], dict[str, Any]] = {}
    warnings: list[str] = []
    last_price: Decimal | None = None
    last_price_date: str | None = None
    previous_factor: Decimal | None = None
    observed_factor: Decimal | None = None
    complete = bool(market["dividend_complete"])
    if not complete:
        warnings.append("分红历史未完整覆盖截至模拟结束日；完整收益未知")
    confirmed: str | None = None

    def record(
        day: str,
        event: str,
        nominal: list[str] | None = None,
        quantity: int = 0,
        price: Decimal | None = None,
        amount: Decimal = ZERO,
        fee: Decimal = ZERO,
        reason: str = "",
    ) -> None:
        ledger.append(
            {
                "date": day,
                "event": event,
                "planned_dates": nominal or [],
                "shares_delta": quantity,
                "price": float(price) if price is not None else None,
                "amount": float(amount),
                "fee": float(fee),
                "cash": float(cash),
                "shares": shares,
                "reason": reason,
            }
        )

    for index, day in enumerate(days):
        check()
        problem = market["issues"].get(day)
        bar = market["bars"].get(day)
        suspended = day in market["suspended"]
        if (
            not problem
            and not suspended
            and (
                not bar
                or bar.get("close") is None
                or decimal(bar["close"]) <= 0
                or bar.get("volume") is None
                or decimal(bar["volume"]) < 0
            )
        ):
            problem = f"{day} 缺少有效行情且无法确认停牌"
        factor_value = market["factors"].get(day)
        if not problem and not suspended and (factor_value is None or decimal(factor_value) <= 0):
            problem = f"{day} 缺少有效复权因子，无法核对公司行动"
        active, event_problem = confirmed_events(market["events"], day, start, seen)
        problem = event_problem or problem
        if not problem and factor_value is not None:
            previous_factor, observed_factor, problem, warning = check_factor_change(
                decimal(factor_value),
                previous_factor,
                observed_factor,
                last_price,
                bar.get("pre_close") if bar and not suspended else None,
                sum(
                    (decimal(e["cash_div_tax"]) for e in active.values() if e["ex_date"] == day),
                    ZERO,
                ),
                day,
            )
            if warning:
                warnings.append(warning)
        up = market["limits"].get(day, {}).get("up_limit")
        if (
            not problem
            and (pending or plans.get(day))
            and not suspended
            and bar
            and bar.get("volume")
        ):
            if up is None or decimal(up) <= 0:
                problem = "涨停价格未知，无法确认可买入"
        if problem:
            complete = False
            warnings.append(f"{day}：{problem}")
            record(day, "unknown", reason=problem)
            break
        # Payments may fall on a non-trading date; process by the next trading day.
        for entitlement in receivables.values():
            if not entitlement["paid"] and entitlement["pay_date"] <= day:
                amount = entitlement["amount"]
                cash += amount
                dividends += amount
                entitlement["paid"] = True
                record(day, "dividend", amount=amount, reason=f"派息日 {entitlement['pay_date']}")
        for planned_date in plans.get(day, []):
            cash += request.monthly_amount
            contributed += request.monthly_amount
            pending.append(planned_date)
            record(day, "contribution", [planned_date], amount=request.monthly_amount)
        if not suspended and bar and bar.get("volume"):
            last_price = decimal(bar["close"])
            last_price_date = day
        if pending:
            up = market["limits"].get(day, {}).get("up_limit")
            if suspended:
                record(day, "deferred", list(pending), reason="停牌，延后买入")
            elif bar and bar.get("volume") == 0:
                record(day, "deferred", list(pending), reason="无成交量，延后买入")
            elif last_price is not None and up is not None and last_price >= decimal(up):
                record(day, "deferred", list(pending), reason="收盘封涨停，延后买入")
            elif last_price is not None:
                minimum, step = (200, 1) if request.stock_code.startswith("688") else (100, 100)

                def cost(quantity: int, execution_price: Decimal) -> tuple[Decimal, Decimal]:
                    notional = money(execution_price * quantity)
                    fee = money(
                        max(request.minimum_commission, notional * request.commission_rate)
                    ) + money(notional * request.transfer_rate)
                    return notional, fee

                # Monotone binary search, including the minimum fee in affordability.
                low, high = 0, int(cash / last_price) // step
                while low < high:
                    mid = (low + high + 1) // 2
                    notional, fee = cost(mid * step, last_price)
                    if notional + fee <= cash:
                        low = mid
                    else:
                        high = mid - 1
                quantity = low * step
                if quantity >= minimum:
                    notional, fee = cost(quantity, last_price)
                    cash -= notional + fee
                    fees += fee
                    shares += quantity
                    record(day, "buy", list(pending), quantity, last_price, -notional, fee)
                else:
                    record(
                        day,
                        "insufficient_cash",
                        list(pending),
                        reason="余额不足最低买入数量，等待下期投入",
                    )
                pending.clear()
        for key, event in active.items():
            if key not in receivables and event["record_date"] == day:
                amount = money(decimal(event["cash_div_tax"]) * shares)
                receivables[key] = {"amount": amount, "pay_date": event["pay_date"], "paid": False}
                record(day, "entitlement", amount=amount, reason="登记日收盘持仓；应收现金分红")
        receivable = sum((e["amount"] for e in receivables.values() if not e["paid"]), ZERO)
        assets = (
            cash + (money(last_price * shares) if last_price is not None else ZERO) + receivable
        )
        confirmed = day
        curve.append(
            {
                "date": day,
                "contributed": float(contributed),
                "total_assets": float(assets),
                "cash": float(cash),
                "shares": shares,
                "valuation_date": last_price_date,
            }
        )
        progress((index + 1) / max(len(days), 1))
    receivable = sum((e["amount"] for e in receivables.values() if not e["paid"]), ZERO)
    position_value = money(last_price * shares) if last_price is not None else ZERO
    assets = cash + position_value + receivable
    if not days:
        complete = False
        warnings.append("区间内没有已确认交易日")
    annualized, drawdown = performance_metrics(curve) if complete else (None, None)
    return {
        "summary": {
            "complete": complete,
            "confirmed_through": confirmed,
            "contributed": float(contributed),
            "cash": float(cash),
            "shares": shares,
            "position_value": float(position_value),
            "receivable": float(receivable),
            "dividends": float(dividends),
            "fees": float(fees),
            "dividend_tax_paid": 0,
            "total_assets": float(assets) if complete else None,
            "profit": float(assets - contributed) if complete else None,
            "return_rate": float((assets - contributed) / contributed)
            if complete and contributed
            else None,
            "annualized_return": annualized,
            "max_drawdown": drawdown,
            "valuation_date": last_price_date,
            "pending_plans": len(pending),
        },
        "curve": curve,
        "ledger": ledger,
        "warnings": warnings,
        "engine_version": DCA_VERSION,
        "universe_policy": "survivors_v1",
        "assumptions": {
            "performance": "年化收益为实际投入日期的XIRR（365天/年）；最大回撤按入金前净值申购份额、每日收盘净值计算，包含费用和应收分红",
            "fee_policy": "固定费用假设，佣金含经手费及证管费",
            "tax_policy": "未卖出，分红税暂不扣；未计未来卖出补税",
            "execution": "日线收盘价模拟，不保证真实成交；停牌或封涨停延后",
        },
    }
