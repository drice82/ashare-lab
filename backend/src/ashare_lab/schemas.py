from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

StockCode = Annotated[str, Field(pattern=r"^(?:[036]\d{5})\.(?:SH|SZ)$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Universe(StrictModel):
    type: Literal["all", "custom"] = "all"
    codes: list[StockCode] = Field(default_factory=list, max_length=6000)

    @model_validator(mode="after")
    def validate_codes(self) -> "Universe":
        if self.type == "custom" and not self.codes:
            raise ValueError("自选股票池不能为空")
        if self.type == "all" and self.codes:
            raise ValueError("全市场股票池不得指定 codes")
        self.codes = sorted(set(self.codes))
        return self


class ScreeningRequest(StrictModel):
    as_of_date: date
    universe: Universe = Field(default_factory=Universe)
    rule_id: Literal["A", "B", "C", "D", "E", "F"]
    parameters: dict[str, Any] = Field(default_factory=dict)


class SyncRequest(StrictModel):
    mode: Literal["initialize", "incremental", "namechange", "dividend"] = "incremental"
    history_refresh: Literal["incremental", "full"] | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def range_valid(self) -> "SyncRequest":
        if self.history_refresh is not None and self.mode not in {"dividend", "namechange"}:
            raise ValueError("history_refresh 仅用于分红和名称历史")
        if self.mode == "initialize" and self.start_date is None:
            raise ValueError("初始化必须提供 start_date")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        if self.end_date and self.end_date > datetime.now(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("不能同步未来日期")
        return self


class PresetRequest(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    definition: ScreeningRequest

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("方案名称不能为空")
        return value.strip()


class DcaRequest(StrictModel):
    stock_code: StockCode
    start_date: date
    end_date: date
    monthly_amount: Annotated[
        Decimal, Field(gt=0, le=100_000_000, max_digits=12, decimal_places=2)
    ] = Decimal("3000")
    monthly_day: int = Field(default=1, ge=1, le=31)
    commission_rate: Annotated[Decimal, Field(ge=0, le=Decimal("0.01"))] = Decimal("0.0003")
    minimum_commission: Annotated[Decimal, Field(ge=0, le=10000, decimal_places=2)] = Decimal("5")
    transfer_rate: Annotated[Decimal, Field(ge=0, le=Decimal("0.01"))] = Decimal("0.00001")
    data_versions: dict[str, str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_range(self) -> "DcaRequest":
        if self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        if self.end_date > datetime.now(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("不能模拟未来日期")
        if (self.end_date - self.start_date).days > 36525:
            raise ValueError("模拟区间不能超过100年")
        return self


class BacktestCommonRequest(StrictModel):
    stock_code: StockCode
    start_date: date
    end_date: date
    initial_cash: Annotated[Decimal, Field(gt=0, le=100_000_000, decimal_places=2)] = Decimal(
        "100000"
    )
    commission_rate: Annotated[Decimal, Field(ge=0, le=Decimal("0.01"))] = Decimal("0.0003")
    minimum_commission: Annotated[Decimal, Field(ge=0, le=10000, decimal_places=2)] = Decimal("5")
    transfer_rate: Annotated[Decimal, Field(ge=0, le=Decimal("0.01"))] = Decimal("0.00001")
    stamp_rate: Annotated[Decimal, Field(ge=0, le=Decimal("0.01"))] = Decimal("0.0005")
    data_versions: dict[str, str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_range(self) -> "BacktestCommonRequest":
        if self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        if self.end_date > datetime.now(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("不能回测未来日期")
        if (self.end_date - self.start_date).days > 36525:
            raise ValueError("回测区间不能超过100年")
        return self


class BacktestRequest(BacktestCommonRequest):
    strategy: Literal["buy_hold", "ma_cross"] = "buy_hold"
    short_window: int = Field(default=20, ge=2, le=249)
    long_window: int = Field(default=60, ge=3, le=250)

    @model_validator(mode="after")
    def validate_windows(self) -> "BacktestRequest":
        if self.short_window >= self.long_window:
            raise ValueError("短均线周期必须小于长均线周期")
        return self


STRATEGY_FIELDS = {
    "buy_hold": (),
    "ma_cross": ("short_window", "long_window"),
    "macd_cross": ("fast_window", "slow_window", "signal_window"),
    "rsi_reversal": ("period", "lower", "upper"),
    "donchian_breakout": ("entry_window", "exit_window"),
    "bollinger_reversion": ("period", "deviations"),
}


class StrategyConfig(StrictModel):
    id: Literal[
        "buy_hold",
        "ma_cross",
        "macd_cross",
        "rsi_reversal",
        "donchian_breakout",
        "bollinger_reversion",
    ]
    short_window: int = Field(default=20, ge=2, le=249)
    long_window: int = Field(default=60, ge=3, le=250)
    fast_window: int = Field(default=12, ge=2, le=250)
    slow_window: int = Field(default=26, ge=2, le=250)
    signal_window: int = Field(default=9, ge=2, le=250)
    period: int = Field(default=14, ge=2, le=250)
    lower: float = Field(default=30, gt=0, lt=100)
    upper: float = Field(default=70, gt=0, lt=100)
    entry_window: int = Field(default=20, ge=2, le=250)
    exit_window: int = Field(default=10, ge=2, le=250)
    deviations: float = Field(default=2, ge=0.5, le=5)

    @model_validator(mode="after")
    def validate_parameters(self) -> "StrategyConfig":
        if self.model_fields_set - {"id", *STRATEGY_FIELDS[self.id]}:
            raise ValueError("策略包含不适用的参数")
        if self.id == "bollinger_reversion" and "period" not in self.model_fields_set:
            self.period = 20
        if self.id == "ma_cross" and self.short_window >= self.long_window:
            raise ValueError("短均线周期必须小于长均线周期")
        if self.id == "macd_cross" and self.fast_window >= self.slow_window:
            raise ValueError("MACD快周期必须小于慢周期")
        if self.id == "rsi_reversal" and self.lower >= self.upper:
            raise ValueError("RSI下阈值必须小于上阈值")
        return self

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        return {"id": self.id, **{key: getattr(self, key) for key in STRATEGY_FIELDS[self.id]}}


class BacktestCompareRequest(BacktestCommonRequest):
    strategies: list[StrategyConfig] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_strategies(self) -> "BacktestCompareRequest":
        if len({item.id for item in self.strategies}) != len(self.strategies):
            raise ValueError("每种策略最多选择一组参数")
        return self
