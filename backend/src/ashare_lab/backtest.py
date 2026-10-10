"""Single-stock daily event simulation, with pinned data and lot tax accounting."""

import calendar
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from math import expm1, isfinite, log
from typing import Any

from ashare_lab.dca import ZERO, check_factor_change, confirmed_events, decimal, money
from ashare_lab.dca import prepare as prepare_market
from ashare_lab.factors import adjusted_prices
from ashare_lab.schemas import BacktestRequest
from ashare_lab.warehouse import MarketRepository

BACKTEST_VERSION = "1.0.1"
STRATEGY_VERSION = "1.0.0"


def annualized_return(
    initial: float, assets: float | None, start: str, end: str | None
) -> float | None:
    """Single contribution CAGR, measured over actual calendar days (365/year)."""
    if assets is None or end is None or initial <= 0 or assets < 0:
        return None
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    if days <= 0:
        return None
    if assets == 0:
        return -1.0
    try:
        value = expm1((log(assets) - log(initial)) * 365 / days)
    except OverflowError:
        return None
    return value if isfinite(value) else None


def anniversary(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def dividend_tax_rate(bought: str, sold: str) -> Decimal:
    start, end = date.fromisoformat(bought), date.fromisoformat(sold)
    if end <= anniversary(start, 1):
        return Decimal("0.2")
    if end <= anniversary(start, 12):
        return Decimal("0.1")
    return ZERO


def prepare(request: BacktestRequest, repository: MarketRepository) -> dict[str, Any]:
    market = prepare_market(request, repository)
    if request.strategy == "ma_cross":
        calendar_rows = repository.read(
            "trade_cal", request.data_versions, end_date=request.end_date.isoformat()
        ).to_dicts()
        days = sorted(
            {
                r["cal_date"]
                for r in calendar_rows
                if r.get("exchange") == "SSE" and r["is_open"] == 1
            }
        )
        before = [d for d in days if d < request.start_date.isoformat()]
        warmup = before[-request.long_window :]
        market["days"] = warmup + market["days"]
        if warmup:
            start = request.start_date.isoformat()
            known_dates = {r["cal_date"] for r in calendar_rows if r.get("exchange") == "SSE"}
            cursor = date.fromisoformat(warmup[0])
            while cursor < request.start_date:
                if cursor.isoformat() not in known_dates:
                    market["issues"].setdefault(start, "均线预热日历不完整")
                    break
                cursor += timedelta(days=1)
            for dataset, key, column in [
                ("daily", "bars", None),
                ("adj_factor", "factors", "adj_factor"),
            ]:
                rows = repository.read(
                    dataset, request.data_versions, [request.stock_code], warmup[0], warmup[-1]
                ).to_dicts()
                market[key].update({r["trade_date"]: r[column] if column else r for r in rows})
                coverage = repository.coverage(dataset, request.data_versions)
                covered = {
                    day
                    for entry in coverage.values()
                    if entry.get("complete")
                    for day in (
                        [entry["trade_date"]]
                        if entry.get("trade_date")
                        else entry.get("trade_dates", [])
                    )
                }
                if any(day not in covered for day in warmup):
                    market["issues"].setdefault(start, f"{dataset} 均线预热未确认完整发布覆盖")
    return market


def simulate(
    request: BacktestRequest,
    market: dict[str, Any],
    check: Callable[[], None] = lambda: None,
    progress: Callable[[float], None] = lambda value: None,
    signal_provider: Callable[[str], tuple[str | None, str | None]] | None = None,
    signal_label: str = "",
) -> dict[str, Any]:
    start, end = request.start_date.isoformat(), request.end_date.isoformat()
    all_days = [d for d in market["days"] if d <= end]
    days = [d for d in all_days if start <= d]
    cash, shares = request.initial_cash, 0
    fees, dividends, tax_paid = ZERO, ZERO, ZERO
    lots: list[dict[str, Any]] = []
    entitlements: list[dict[str, Any]] = []
    seen: dict[tuple[Any, Any], dict[str, Any]] = {}
    registered: set[tuple[Any, Any]] = set()
    ledger: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    warnings: list[str] = []
    pending: dict[str, str] | None = None
    last_price: Decimal | None = None
    valuation: str | None = None
    previous_factor: Decimal | None = None
    observed_factor: Decimal | None = None
    confirmed: str | None = None
    complete = bool(market["dividend_complete"])
    peak, drawdown = request.initial_cash, ZERO
    if not complete:
        warnings.append("分红历史覆盖未知，完整收益未知")

    def record(
        day: str,
        event: str,
        signal: str | None = None,
        quantity: int = 0,
        price: Decimal | None = None,
        amount: Decimal = ZERO,
        fee: Decimal = ZERO,
        tax: Decimal = ZERO,
        reason: str = "",
    ) -> None:
        ledger.append(
            {
                "date": day,
                "event": event,
                "signal_date": signal,
                "planned_dates": [signal] if signal else [],
                "shares_delta": quantity,
                "price": float(price) if price is not None else None,
                "amount": float(amount),
                "fee": float(fee),
                "tax": float(tax),
                "cash": float(cash),
                "shares": shares,
                "reason": reason,
            }
        )

    def balances() -> tuple[Decimal, Decimal, Decimal]:
        receivable = sum((e["amount"] for e in entitlements if not e["paid"]), ZERO)
        liability = sum((e["tax_due"] for e in entitlements if not e["paid"]), ZERO)
        position = money(last_price * shares) if last_price is not None else ZERO
        return receivable, liability, position

    def signal_at(day: str) -> tuple[str | None, str | None]:
        index = all_days.index(day)
        if index < request.long_window:
            return None, "均线预热交易日不足，无法确认交叉"
        directions = []
        for offset in (1, 0):
            stop = index + 1 - offset
            window = all_days[stop - request.long_window : stop]
            values = adjusted_prices(
                window,
                market["bars"],
                market["factors"],
                market["factors"].get(all_days[index - offset]),
            )
            if values is None or any(v <= 0 for v in values):
                return None, "均线窗口行情或复权因子缺失"
            short = sum(values[-request.short_window :]) / request.short_window
            long = sum(values) / request.long_window
            difference = short - long
            directions.append(
                0.0 if abs(difference) <= max(abs(short), abs(long)) * 1e-12 else difference
            )
        if directions[0] <= 0 < directions[1]:
            return "buy", None
        if directions[0] >= 0 > directions[1]:
            return "sell", None
        return None, None

    record(start, "contribution", amount=cash, reason="一次性初始投入")
    for index, day in enumerate(days):
        check()
        bar = market["bars"].get(day)
        suspended = day in market["suspended"]
        problem = market["issues"].get(day)
        if not suspended and (
            not bar
            or any(bar.get(f) is None or decimal(bar[f]) <= 0 for f in ("open", "close"))
            or bar.get("volume") is None
            or decimal(bar["volume"]) < 0
        ):
            problem = "缺少有效行情且无法确认停牌"
        factor = market["factors"].get(day)
        if not suspended and (factor is None or decimal(factor) <= 0):
            problem = "缺少有效复权因子"
        active, event_problem = confirmed_events(market["events"], day, start, seen)
        problem = event_problem or problem
        if any(e["record_date"] == day and day <= "2015-09-08" for e in active.values()):
            problem = "历史分红税制未支持，停止计算；完整收益未知"
        if not problem and factor is not None:
            previous_factor, observed_factor, problem, warning = check_factor_change(
                decimal(factor),
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
        side: str | None = None
        if signal_provider is not None and not suspended:
            side, signal_problem = signal_provider(day)
            problem = signal_problem or problem
        elif request.strategy == "ma_cross" and not suspended:
            side, signal_problem = signal_at(day)
            problem = signal_problem or problem
        elif request.strategy == "buy_hold" and index == 0:
            side = "buy"
        limits = market["limits"].get(day, {})
        if pending and not suspended and bar and bar.get("volume"):
            field = "up_limit" if pending["side"] == "buy" else "down_limit"
            if limits.get(field) is None or decimal(limits[field]) <= 0:
                problem = "涨跌停价格未知，无法确认成交"
        if problem:
            complete = False
            warnings.append(f"{day}：{problem}")
            record(day, "unknown", reason=problem)
            break
        # Daily simulation assumes announced payment funds are available before the open.
        for entitlement in entitlements:
            if not entitlement["paid"] and entitlement["pay_date"] <= day:
                cash += entitlement["amount"]
                dividends += entitlement["amount"]
                entitlement["paid"] = True
                record(
                    day,
                    "dividend",
                    amount=entitlement["amount"],
                    reason=f"派息日 {entitlement['pay_date']}",
                )
                if entitlement["tax_due"]:
                    cash -= entitlement["tax_due"]
                    tax_paid += entitlement["tax_due"]
                    record(
                        day, "dividend_tax", tax=entitlement["tax_due"], reason="已卖出批次派息补税"
                    )
        if pending:
            order_side, signal_day = pending["side"], pending["signal_date"]
            if suspended or not bar or not bar.get("volume"):
                record(day, "deferred", signal_day, reason="停牌或零成交量")
            else:
                execution = decimal(bar["open"])
                blocked = (order_side == "buy" and execution >= decimal(limits["up_limit"])) or (
                    order_side == "sell" and execution <= decimal(limits["down_limit"])
                )
                if blocked:
                    record(
                        day,
                        "deferred",
                        signal_day,
                        reason="开盘涨停禁止买入" if order_side == "buy" else "开盘跌停禁止卖出",
                    )
                elif order_side == "buy":
                    minimum, step = (200, 1) if request.stock_code.startswith("688") else (100, 100)

                    def cost(
                        quantity: int, execution: Decimal = execution
                    ) -> tuple[Decimal, Decimal]:
                        amount = money(execution * quantity)
                        fee = money(
                            max(request.minimum_commission, amount * request.commission_rate)
                        ) + money(amount * request.transfer_rate)
                        return amount, fee

                    low, high = 0, int(cash / execution) // step
                    while low < high:
                        mid = (low + high + 1) // 2
                        amount, fee = cost(mid * step)
                        if amount + fee <= cash:
                            low = mid
                        else:
                            high = mid - 1
                    quantity = low * step
                    if quantity >= minimum:
                        amount, fee = cost(quantity)
                        cash -= amount + fee
                        fees += fee
                        shares += quantity
                        lots.append({"date": day, "shares": quantity})
                        record(day, "buy", signal_day, quantity, execution, -amount, fee)
                    else:
                        record(day, "insufficient_cash", signal_day, reason="余额不足最低买入数量")
                    pending = None
                elif any(lot["date"] >= day and lot["shares"] for lot in lots):
                    record(day, "deferred", signal_day, reason="T+1：当日买入不可卖出")
                else:
                    quantity = shares
                    amount = money(execution * quantity)
                    fee = (
                        money(max(request.minimum_commission, amount * request.commission_rate))
                        + money(amount * request.transfer_rate)
                        + money(amount * request.stamp_rate)
                    )
                    cash += amount - fee
                    fees += fee
                    shares = 0
                    sale_tax = ZERO
                    for lot in lots:
                        if not lot["shares"]:
                            continue
                        for entitlement in entitlements:
                            if entitlement["lot"] is lot:
                                tax = money(
                                    entitlement["amount"] * dividend_tax_rate(lot["date"], day)
                                )
                                entitlement["tax_due"] = tax
                                if entitlement["paid"]:
                                    cash -= tax
                                    tax_paid += tax
                                    sale_tax += tax
                        lot["shares"] = 0
                    record(day, "sell", signal_day, -quantity, execution, amount, fee, sale_tax)
                    pending = None
        for key, event in active.items():
            if key not in registered and event["record_date"] == day:
                registered.add(key)
                for lot in lots:
                    if lot["shares"]:
                        amount = money(decimal(event["cash_div_tax"]) * lot["shares"])
                        entitlements.append(
                            {
                                "lot": lot,
                                "amount": amount,
                                "pay_date": event["pay_date"],
                                "paid": False,
                                "tax_due": ZERO,
                            }
                        )
                        record(day, "entitlement", amount=amount, reason="登记日收盘持仓")
        if not suspended and bar and bar.get("volume"):
            last_price, valuation = decimal(bar["close"]), day
        if side:
            reason = (
                f"{signal_label}：{'买入' if side == 'buy' else '卖出'}"
                if signal_provider is not None
                else "一次性买入持有"
                if request.strategy == "buy_hold"
                else "金叉买入"
                if side == "buy"
                else "死叉卖出"
            )
            record(day, "signal", day, reason=reason)
            if pending and pending["side"] != side:
                record(day, "order_cancelled", pending["signal_date"], reason="反向信号撤单")
                pending = None
            if (side == "buy" and shares == 0) or (side == "sell" and shares > 0):
                if pending is None:
                    pending = {"side": side, "signal_date": day}
        receivable, liability, position = balances()
        assets = cash + position + receivable - liability
        peak = max(peak, assets)
        drawdown = max(drawdown, (peak - assets) / peak)
        confirmed = day
        curve.append(
            {
                "date": day,
                "total_assets": float(assets),
                "contributed": float(request.initial_cash),
                "cash": float(cash),
                "shares": shares,
                "valuation_date": valuation,
            }
        )
        progress((index + 1) / max(len(days), 1))
    receivable, liability, position = balances()
    assets = cash + position + receivable - liability
    if not days:
        complete = False
        warnings.append("区间无交易日")
    return {
        "summary": {
            "complete": complete,
            "confirmed_through": confirmed,
            "contributed": float(request.initial_cash),
            "cash": float(cash),
            "shares": shares,
            "position_value": float(position),
            "receivable": float(receivable),
            "tax_liability": float(liability),
            "dividends": float(dividends),
            "fees": float(fees),
            "dividend_tax_paid": float(tax_paid),
            "total_assets": float(assets) if complete else None,
            "profit": float(assets - request.initial_cash) if complete else None,
            "return_rate": float((assets - request.initial_cash) / request.initial_cash)
            if complete
            else None,
            "annualized_return": annualized_return(
                float(request.initial_cash), float(assets), start, confirmed
            )
            if complete
            else None,
            "max_drawdown": float(drawdown) if complete else None,
            "valuation_date": valuation,
            "pending_orders": int(pending is not None),
            "pending_order": pending,
            "buy_count": sum(r["event"] == "buy" for r in ledger),
            "sell_count": sum(r["event"] == "sell" for r in ledger),
        },
        "curve": curve,
        "ledger": ledger,
        "warnings": warnings,
        "engine_version": BACKTEST_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "universe_policy": "survivors_v1",
        "assumptions": {
            "fee_policy": "全区间固定费率假设，未重建历史费率；佣金含经手及证管费",
            "tax_policy": "先进先出持有期补税；已到账在卖出日扣除，卖出早于派息在到账日扣除；期末未卖出持仓未计未来补税",
            "execution": "收盘信号、次日开盘日线模拟，涨跌停或停牌延期；不保证真实成交；派息假设开盘前到账",
            "universe": "固定版本存续沪深A股，存在幸存者偏差；供应商历史修订PIT未重建",
        },
    }
