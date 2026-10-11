"""Pinned multi-stock accounts with shared cash, partial FIFO sales and no provider I/O."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal
from math import isfinite
from typing import Any

from ashare_lab.backtest import annualized_return, dividend_tax_rate
from ashare_lab.comparison import LABELS, signal, warmup
from ashare_lab.dca import ZERO, check_factor_change, confirmed_events, decimal, money
from ashare_lab.factors import adjusted_prices
from ashare_lab.schemas import PortfolioRequest, StrategyConfig
from ashare_lab.warehouse import MarketRepository

PORTFOLIO_VERSION = "1.0.0"


def rebalance_days(calendar: list[str], frequency: str) -> set[str]:
    def period(value: str) -> Any:
        day = date.fromisoformat(value)
        if frequency == "weekly":
            return day.isocalendar()[:2]
        if frequency == "monthly":
            return day.year, day.month
        if frequency == "quarterly":
            return day.year, (day.month - 1) // 3
        return day.year

    if frequency == "none":
        return set()
    return {a for a, b in zip(calendar, calendar[1:], strict=False) if period(a) != period(b)}


def prepare_portfolio(
    request: PortfolioRequest,
    repository: MarketRepository,
    check: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    versions = request.data_versions
    start, end = request.start_date.isoformat(), request.end_date.isoformat()
    cal = repository.read("trade_cal", versions).to_dicts()
    known = {r["cal_date"] for r in cal if r.get("exchange") == "SSE"}
    calendar = sorted(
        {r["cal_date"] for r in cal if r.get("exchange") == "SSE" and r["is_open"] == 1}
    )
    before = [d for d in calendar if d < start]
    days = [d for d in calendar if start <= d <= end]
    count = max(warmup(m.strategy) for m in request.members)
    pre = before[-count:] if count else []
    earliest = pre[0] if pre else start
    codes = sorted(m.stock_code for m in request.members)
    frames: dict[str, list[dict[str, Any]]] = {}
    covered: dict[str, set[str]] = {}
    for name in ["daily", "adj_factor", "stk_limit", "suspend_d"]:
        check()
        frames[name] = repository.read(name, versions, codes, earliest, end).to_dicts()
        covered[name] = {
            day
            for entry in repository.coverage(name, versions).values()
            if entry.get("complete")
            for day in (
                [entry["trade_date"]] if entry.get("trade_date") else entry.get("trade_dates", [])
            )
        }
    check()
    events = repository.read("dividend", versions, codes).to_dicts()
    dividend_codes = repository.history_complete_codes("dividend", end, versions)
    stocks = {}
    for member in request.members:
        check()
        code = member.stock_code
        needed = warmup(member.strategy)
        local_pre = before[-needed:] if needed else []
        warmup_issue = None
        if len(local_pre) < needed:
            warmup_issue = "策略预热交易日不足"
        elif local_pre:
            cursor = date.fromisoformat(local_pre[0])
            while cursor < request.start_date:
                if cursor.isoformat() not in known:
                    warmup_issue = "策略预热日历不完整"
                    break
                cursor += timedelta(days=1)
            for name in ["daily", "adj_factor"]:
                if any(day not in covered[name] for day in local_pre):
                    warmup_issue = f"{name} 策略预热未确认完整发布覆盖"
        local = {
            name: [r for r in rows if r["stock_code"] == code] for name, rows in frames.items()
        }
        stocks[code] = {
            "days": local_pre + days,
            "bars": {r["trade_date"]: r for r in local["daily"]},
            "factors": {r["trade_date"]: r["adj_factor"] for r in local["adj_factor"]},
            "limits": {r["trade_date"]: r for r in local["stk_limit"]},
            "suspended": {
                r["trade_date"] for r in local["suspend_d"] if r.get("suspend_type") == "S"
            },
            "events": [r for r in events if r["stock_code"] == code],
            "issues": {
                day: f"{name} 在 {day} 未确认完整覆盖"
                for day in days
                for name in frames
                if day not in covered[name]
            },
            "dividend_complete": code in dividend_codes,
            "warmup_issue": warmup_issue,
        }
    basic = repository.read("stock_basic", versions, codes).to_dicts()
    return {
        "days": days,
        "calendar": calendar,
        "stocks": stocks,
        "names": {r["stock_code"]: r.get("name", "") for r in basic},
    }


def stock_signal(
    config: StrategyConfig, market: dict[str, Any], start: str, day: str
) -> tuple[str | None, str | None]:
    if config.id == "buy_hold":
        return ("buy" if day == start else None), None
    if market.get("warmup_issue"):
        return None, market["warmup_issue"]
    days = market["days"]
    stop = days.index(day) + 1
    if stop <= warmup(config):
        return None, "策略预热交易日不足"
    if config.id == "ma_cross":
        differences = []
        for offset in [1, 0]:
            window = days[stop - offset - config.long_window : stop - offset]
            values = adjusted_prices(
                window,
                market["bars"],
                market["factors"],
                market["factors"].get(days[stop - offset - 1]),
            )
            if values is None or any(not isfinite(v) or v <= 0 for v in values):
                return None, "均线窗口行情或复权因子缺失"
            short, long = (
                sum(values[-config.short_window :]) / config.short_window,
                sum(values) / config.long_window,
            )
            difference = short - long
            differences.append(
                0 if abs(difference) <= max(abs(short), abs(long)) * 1e-12 else difference
            )
        return (
            "buy"
            if differences[0] <= 0 < differences[1]
            else "sell"
            if differences[0] >= 0 > differences[1]
            else None
        ), None
    fields = ["close", "high", "low"] if config.id == "donchian_breakout" else ["close"]
    prices = [
        adjusted_prices(
            days[:stop], market["bars"], market["factors"], market["factors"].get(day), f
        )
        for f in fields
    ]
    if any(values is None or any(not isfinite(v) or v <= 0 for v in values) for values in prices):
        return None, "策略窗口行情或复权因子缺失"
    close = prices[0]
    assert close is not None
    high, low = (prices[1], prices[2]) if len(prices) == 3 else (close, close)
    assert high is not None and low is not None
    return signal(config, close, high, low), None


@dataclass
class Lot:
    date: str
    shares: int
    entitlements: list["Entitlement"] = field(default_factory=list)


@dataclass
class Entitlement:
    amount: Decimal
    pay_date: str
    remaining_shares: int
    remaining_amount: Decimal
    paid: bool = False
    tax_due: Decimal = ZERO

    def sale_tax(self, quantity: int, rate: Decimal) -> Decimal:
        # Allocate the last slice the residual cents, preserving the registered gross amount.
        amount = (
            self.remaining_amount
            if quantity == self.remaining_shares
            else money(self.remaining_amount * quantity / self.remaining_shares)
        )
        self.remaining_shares -= quantity
        self.remaining_amount -= amount
        tax = money(amount * rate)
        if not self.paid:
            self.tax_due += tax
        return tax


@dataclass
class Position:
    shares: int = 0
    price: Decimal | None = None
    valuation_date: str | None = None
    eligible: bool = False
    lots: list[Lot] = field(default_factory=list)
    rights: list[Entitlement] = field(default_factory=list)
    pending: dict[str, Any] | None = None
    previous_factor: Decimal | None = None
    observed_factor: Decimal | None = None
    seen: dict[tuple[Any, Any], dict[str, Any]] = field(default_factory=dict)
    registered: set[tuple[Any, Any]] = field(default_factory=set)

    def balances(self) -> tuple[Decimal, Decimal, Decimal]:
        return (
            money((self.price or ZERO) * self.shares),
            sum((e.amount for e in self.rights if not e.paid), ZERO),
            sum((e.tax_due for e in self.rights if not e.paid), ZERO),
        )


def sell_quantity(code: str, desired: int, available: int, total: int) -> int:
    desired = min(desired, available)
    if code.startswith("688"):
        return desired if desired >= 200 or desired == total else 0
    if desired == total:
        return desired
    return desired // 100 * 100


class Account:
    def __init__(self, request: PortfolioRequest, market: dict[str, Any], benchmark: bool):
        self.request, self.market, self.benchmark = request, market, benchmark
        self.members = sorted(request.members, key=lambda m: m.stock_code)
        self.codes = [m.stock_code for m in self.members]
        self.configs = {
            m.stock_code: StrategyConfig(id="buy_hold") if benchmark else m.strategy
            for m in self.members
        }
        self.weights = {
            m.stock_code: m.weight
            if request.allocation == "custom"
            else Decimal(1) / len(self.members)
            for m in self.members
        }
        self.positions = {code: Position() for code in self.codes}
        self.cash = request.initial_cash
        self.fees = self.dividends = self.tax_paid = ZERO
        self.ledger: list[dict[str, Any]] = []
        self.curve: list[dict[str, Any]] = []
        self.holdings: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.complete = True
        self.confirmed: str | None = None
        self.peak = request.initial_cash
        self.drawdown = ZERO

    def record(self, day: str, event: str, code: str | None = None, **values: Any) -> None:
        order = self.positions[code].pending if code else None
        self.ledger.append(
            {
                "date": day,
                "event": event,
                "stock_code": code,
                "signal_date": order["signal_date"] if order else None,
                "shares_delta": 0,
                "price": None,
                "amount": 0.0,
                "fee": 0.0,
                "tax": 0.0,
                "cash": float(self.cash),
                "shares": self.positions[code].shares if code else 0,
                "reason": "",
                **values,
            }
        )

    def assets(self) -> Decimal:
        return self.cash + sum(
            (
                value + receivable - liability
                for value, receivable, liability in (p.balances() for p in self.positions.values())
            ),
            ZERO,
        )

    def fee(self, amount: Decimal, sell: bool = False) -> Decimal:
        r = self.request
        return (
            money(max(r.minimum_commission, amount * r.commission_rate))
            + money(amount * r.transfer_rate)
            + (money(amount * r.stamp_rate) if sell else ZERO)
        )

    def check_day(self, day: str) -> tuple[dict[str, Any], dict[str, str | None], str | None]:
        events, sides = {}, {}
        start = self.request.start_date.isoformat()
        for code in self.codes:
            p, data = self.positions[code], self.market["stocks"][code]
            bar = data["bars"].get(day)
            suspended = day in data["suspended"]
            problem = data["issues"].get(day)
            if not data["dividend_complete"]:
                problem = "分红历史覆盖未知"
            if not suspended and (
                not bar
                or any(
                    bar.get(f) is None or not isfinite(float(bar[f])) or decimal(bar[f]) <= 0
                    for f in ["open", "close"]
                )
                or bar.get("volume") is None
                or not isfinite(float(bar["volume"]))
                or decimal(bar["volume"]) < 0
            ):
                problem = "缺少有效行情且无法确认停牌"
            factor = data["factors"].get(day)
            if not suspended and (
                factor is None or not isfinite(float(factor)) or decimal(factor) <= 0
            ):
                problem = "缺少有效复权因子"
            active, event_problem = confirmed_events(data["events"], day, start, p.seen)
            problem = event_problem or problem
            if any(e["record_date"] == day and day <= "2015-09-08" for e in active.values()):
                problem = "历史分红税制未支持"
            if not problem and factor is not None:
                p.previous_factor, p.observed_factor, problem, warning = check_factor_change(
                    decimal(factor),
                    p.previous_factor,
                    p.observed_factor,
                    p.price,
                    bar.get("pre_close") if bar and not suspended else None,
                    sum(
                        (
                            decimal(e["cash_div_tax"])
                            for e in active.values()
                            if e["ex_date"] == day
                        ),
                        ZERO,
                    ),
                    day,
                )
                if warning:
                    self.warnings.append(f"{code}：{warning}")
            side, signal_problem = (
                stock_signal(self.configs[code], data, start, day)
                if not suspended or self.configs[code].id == "buy_hold"
                else (None, None)
            )
            problem = signal_problem or problem
            if not problem and p.pending and not suspended and bar and bar.get("volume"):
                price = decimal(bar["open"])
                desired = int(p.pending["target_value"] / price)
                if not code.startswith("688"):
                    desired = desired // 100 * 100
                difference = desired - p.shares
                minimum = 200 if code.startswith("688") else 100
                if difference < 0 or difference >= minimum:
                    field = "up_limit" if difference > 0 else "down_limit"
                    limit = data["limits"].get(day, {}).get(field)
                    if limit is None or not isfinite(float(limit)) or decimal(limit) <= 0:
                        problem = "涨跌停价格未知，无法确认成交"
            if problem:
                return {}, {}, f"{code}：{problem}"
            # A held suspended position cannot be valued without a prior confirmed price.
            if p.shares and p.price is None and suspended:
                return {}, {}, f"{code}：停牌持仓缺少已确认估值"
            events[code], sides[code] = active, side
        return events, sides, None

    def sell(self, code: str, day: str, quantity: int, price: Decimal) -> bool:
        p = self.positions[code]
        amount = money(price * quantity)
        fee = self.fee(amount, True)
        # Preflight paid rights before mutating any lot.
        left, paid_tax = quantity, ZERO
        for lot in p.lots:
            taken = min(left, lot.shares) if lot.date < day else 0
            if taken:
                for e in lot.entitlements:
                    gross = (
                        e.remaining_amount
                        if taken == e.remaining_shares
                        else money(e.remaining_amount * taken / e.remaining_shares)
                    )
                    if e.paid:
                        paid_tax += money(gross * dividend_tax_rate(lot.date, day))
                left -= taken
            if not left:
                break
        if self.cash + amount < fee + paid_tax:
            self.record(day, "deferred", code, reason="卖出回款与现金不足支付费用及补税")
            return False
        self.cash += amount - fee
        self.fees += fee
        left = quantity
        for lot in p.lots:
            taken = min(left, lot.shares) if lot.date < day else 0
            if taken:
                for e in lot.entitlements:
                    tax = e.sale_tax(taken, dividend_tax_rate(lot.date, day))
                    if e.paid:
                        self.cash -= tax
                        self.tax_paid += tax
                lot.shares -= taken
                left -= taken
            if not left:
                break
        p.shares -= quantity
        self.record(
            day,
            "sell",
            code,
            shares_delta=-quantity,
            price=float(price),
            amount=float(amount),
            fee=float(fee),
            tax=float(paid_tax),
        )
        return True

    def execute(self, day: str) -> None:
        buys: dict[str, tuple[Decimal, Decimal, int]] = {}
        for code in self.codes:
            p, data = self.positions[code], self.market["stocks"][code]
            if not p.pending:
                continue
            bar = data["bars"].get(day)
            if day in data["suspended"] or not bar or not bar.get("volume"):
                self.record(day, "deferred", code, reason="停牌或零成交量")
                continue
            price, target = decimal(bar["open"]), p.pending["target_value"]
            desired = max(0, int(target / price))
            if not code.startswith("688"):
                desired = desired // 100 * 100
            difference = desired - p.shares
            minimum = 200 if code.startswith("688") else 100
            if difference == 0 or 0 < difference < minimum:
                if target > 0 and (p.shares == 0 or difference > 0):
                    self.record(
                        day,
                        "insufficient_cash",
                        code,
                        reason="目标金额不足合法买入单位；本次结束，等下次调仓",
                    )
                p.pending = None
                continue
            side = "buy" if difference > 0 else "sell"
            limits = data["limits"][day]
            if (side == "buy" and price >= decimal(limits["up_limit"])) or (
                side == "sell" and price <= decimal(limits["down_limit"])
            ):
                self.record(
                    day,
                    "deferred",
                    code,
                    reason="开盘涨停禁止买入" if side == "buy" else "开盘跌停禁止卖出",
                )
                continue
            if difference < 0:
                available = sum(lot.shares for lot in p.lots if lot.date < day)
                quantity = sell_quantity(code, -difference, available, p.shares)
                if quantity and self.sell(code, day, quantity, price):
                    if available >= -difference:
                        p.pending = None
                elif not quantity:
                    if available < -difference:
                        self.record(day, "deferred", code, reason="T+1：当日买入不可卖出")
                    else:
                        self.record(
                            day, "below_lot", code, reason="减仓不足合法交易单位，等下次调仓"
                        )
                        p.pending = None
            else:
                buys[code] = (price, target - money(price * p.shares), difference)
        total_gap = sum((gap for _, gap, _ in buys.values()), ZERO)
        available_cash = max(self.cash, ZERO)
        # Fixed proportional budgets; never recycle another stock's unused budget.
        for code, (price, gap, desired) in buys.items():
            p = self.positions[code]
            budget = min(
                gap,
                (available_cash * gap / total_gap).quantize(Decimal(".01"), rounding=ROUND_DOWN),
            )
            minimum, step = (200, 1) if code.startswith("688") else (100, 100)
            low, high = 0, desired // step
            while low < high:
                mid = (low + high + 1) // 2
                amount = money(price * mid * step)
                if amount + self.fee(amount) <= budget:
                    low = mid
                else:
                    high = mid - 1
            quantity = low * step
            if quantity >= minimum:
                amount, fee = money(price * quantity), self.fee(money(price * quantity))
                self.cash -= amount + fee
                self.fees += fee
                p.shares += quantity
                p.lots.append(Lot(day, quantity))
                self.record(
                    day,
                    "buy",
                    code,
                    shares_delta=quantity,
                    price=float(price),
                    amount=-float(amount),
                    fee=float(fee),
                )
            if quantity < desired:
                self.record(
                    day,
                    "insufficient_cash",
                    code,
                    reason="可用资金或交易单位未满足完整目标；本次结束，等下次调仓",
                )
            p.pending = None

    def run(self, check: Callable[[], None], progress: Callable[[float], None]) -> dict[str, Any]:
        start = self.request.start_date.isoformat()
        rebalance = rebalance_days(
            self.market["calendar"], "none" if self.benchmark else self.request.rebalance
        )
        self.record(start, "contribution", amount=float(self.cash), reason="一次性初始投入")
        for index, day in enumerate(self.market["days"]):
            check()
            events, sides, problem = self.check_day(day)
            if problem:
                self.complete = False
                self.warnings.append(f"{day}：{problem}")
                self.record(day, "unknown", reason=problem)
                break
            for code, p in self.positions.items():
                for e in p.rights:
                    if not e.paid and e.pay_date <= day:
                        self.cash += e.amount - e.tax_due
                        self.dividends += e.amount
                        self.tax_paid += e.tax_due
                        e.paid = True
                        self.record(
                            day,
                            "dividend",
                            code,
                            amount=float(e.amount),
                            tax=float(e.tax_due),
                            reason=f"派息日 {e.pay_date}；扣除已确定补税",
                        )
            self.execute(day)
            for code, p in self.positions.items():
                for key, event in events[code].items():
                    if key not in p.registered and event["record_date"] == day:
                        p.registered.add(key)
                        for lot in p.lots:
                            if lot.shares:
                                amount = money(decimal(event["cash_div_tax"]) * lot.shares)
                                e = Entitlement(amount, event["pay_date"], lot.shares, amount)
                                lot.entitlements.append(e)
                                p.rights.append(e)
                                self.record(
                                    day,
                                    "entitlement",
                                    code,
                                    amount=float(amount),
                                    reason="登记日收盘持仓；应收现金分红",
                                )
                data = self.market["stocks"][code]
                bar = data["bars"].get(day)
                if day not in data["suspended"] and bar and bar.get("volume"):
                    p.price, p.valuation_date = decimal(bar["close"]), day
            assets = self.assets()
            changed = set()
            for code, side in sides.items():
                if side:
                    p = self.positions[code]
                    self.record(
                        day,
                        "signal",
                        code,
                        signal_date=day,
                        reason=f"{LABELS[self.configs[code].id]}：{'买入' if side == 'buy' else '卖出'}",
                    )
                    eligible = side == "buy"
                    if p.eligible != eligible:
                        p.eligible = eligible
                        changed.add(code)
            if day in rebalance:
                changed.update(self.codes)
                self.record(
                    day,
                    "rebalance",
                    signal_date=day,
                    reason=f"{self.request.rebalance} 周期末再平衡",
                )
            for code in sorted(changed):
                p = self.positions[code]
                if p.pending:
                    self.record(day, "order_cancelled", code, reason="新信号或周期再平衡替换旧目标")
                weight = self.weights[code]
                assert weight is not None
                p.pending = {
                    "signal_date": day,
                    "target_value": money(assets * weight) if p.eligible else ZERO,
                }
            self.peak = max(self.peak, assets)
            self.drawdown = max(self.drawdown, (self.peak - assets) / self.peak)
            self.confirmed = day
            self.curve.append(
                {
                    "date": day,
                    "cash": float(self.cash),
                    "total_assets": float(assets),
                    "return_rate": float(assets / self.request.initial_cash - 1),
                    "drawdown": float(assets / self.peak - 1),
                }
            )
            self.holdings.append(
                {
                    "date": day,
                    "cash": float(self.cash),
                    "cash_weight": float(self.cash / assets) if assets else None,
                    "items": [self.position_row(code, assets) for code in self.codes],
                }
            )
            progress((index + 1) / max(1, len(self.market["days"])))
        if not self.market["days"]:
            self.complete = False
            self.warnings.append("区间无交易日")
        position, receivable, liability = map(
            sum, zip(*(p.balances() for p in self.positions.values()), strict=True)
        )
        assets = self.assets()
        return {
            "summary": {
                "complete": self.complete,
                "confirmed_through": self.confirmed,
                "contributed": float(self.request.initial_cash),
                "cash": float(self.cash),
                "position_value": float(position),
                "receivable": float(receivable),
                "tax_liability": float(liability),
                "fees": float(self.fees),
                "dividends": float(self.dividends),
                "dividend_tax_paid": float(self.tax_paid),
                "total_assets": float(assets) if self.complete else None,
                "profit": float(assets - self.request.initial_cash) if self.complete else None,
                "return_rate": float(assets / self.request.initial_cash - 1)
                if self.complete
                else None,
                "annualized_return": annualized_return(
                    float(self.request.initial_cash), float(assets), start, self.confirmed
                )
                if self.complete
                else None,
                "max_drawdown": float(self.drawdown) if self.complete else None,
                "pending_orders": sum(p.pending is not None for p in self.positions.values()),
                "buy_count": sum(r["event"] == "buy" for r in self.ledger),
                "sell_count": sum(r["event"] == "sell" for r in self.ledger),
            },
            "curve": self.curve,
            "positions": self.holdings,
            "ledger": self.ledger,
            "warnings": self.warnings,
        }

    def position_row(self, code: str, assets: Decimal) -> dict[str, Any]:
        p = self.positions[code]
        value, receivable, liability = p.balances()
        weight = self.weights[code]
        assert weight is not None
        return {
            "stock_code": code,
            "name": self.market.get("names", {}).get(code, ""),
            "strategy": self.configs[code].model_dump(),
            "shares": p.shares,
            "price": float(p.price) if p.price is not None else None,
            "valuation_date": p.valuation_date,
            "position_value": float(value),
            "receivable": float(receivable),
            "tax_liability": float(liability),
            "configured_weight": float(weight),
            "target_weight": float(weight) if p.eligible else 0.0,
            "actual_weight": float(value / assets) if assets else None,
            "eligible": p.eligible,
            "pending_order": {**p.pending, "target_value": float(p.pending["target_value"])}
            if p.pending
            else None,
        }


def simulate_portfolio(
    request: PortfolioRequest,
    market: dict[str, Any],
    check: Callable[[], None] = lambda: None,
    progress: Callable[[float], None] = lambda value: None,
) -> dict[str, Any]:
    actual = Account(request, market, False).run(check, lambda v: progress(v / 2))
    check()
    baseline = Account(request, market, True).run(check, lambda v: progress(0.5 + v / 2))
    return {
        **actual,
        "mode": "portfolio",
        "benchmark": baseline,
        "excess_return": actual["summary"]["return_rate"] - baseline["summary"]["return_rate"]
        if actual["summary"]["complete"] and baseline["summary"]["complete"]
        else None,
        "engine_version": PORTFOLIO_VERSION,
        "universe_policy": "survivors_v1",
        "prices": {
            code: [
                {
                    "date": day,
                    **{
                        f: value if value is not None and isfinite(float(value)) else None
                        for f in ["open", "high", "low", "close"]
                        for value in [bar.get(f)]
                    },
                }
                for day in market["days"]
                if (bar := data["bars"].get(day))
            ]
            for code, data in market["stocks"].items()
        },
        "assumptions": {
            "execution": "收盘冻结目标市值、次日开盘模拟；先卖后买；交易受阻延期，资金不足当次结束；不保证真实成交",
            "allocation": "目标权重基于组合净资产；未启用份额留现金；比例预算含费用，取整余款不重新分配",
            "benchmark": "相同股票、资金、初始权重和费用的独立买入持有账户，不再平衡或自动再投入分红",
            "tax_policy": "先进先出、部分卖出拆分分红权益；未卖出部分未计未来补税；派息假设开盘前到账",
            "fee_policy": "全区间固定费率假设，未重建历史费率；佣金含经手及证管费",
            "universe": "固定版本存续沪深A股，存在幸存者偏差；供应商历史修订PIT未重建",
        },
    }
