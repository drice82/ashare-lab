import operator
from typing import Any, Literal

from pydantic import Field, model_validator

from ashare_lab.factors import BOOLEAN_FACTORS, NUMERIC_FACTORS
from ashare_lab.schemas import StrictModel

RULE_VERSION = "1.0.0"
OPERATORS = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
}


class Common(StrictModel):
    exclude_st: bool = False
    exclude_suspended: bool = True


class A(Common):
    exclude_st: bool = True
    dividend_min: float = Field(default=0.04, ge=0, le=1)
    daily_return_max: float = Field(default=-0.03, ge=-1, le=1)


class B(Common):
    dividend_min: float = Field(default=0.04, ge=0, le=1)
    drawdown_min: float = Field(default=0.10, ge=0, le=1)
    amount_min: float = Field(default=30_000_000, ge=0, le=1e13)


class C(Common):
    dividend_min: float = Field(default=0.04, ge=0, le=1)
    return_20d_min: float = Field(default=0.05, ge=-1, le=10)
    return_60d_min: float = Field(default=0.10, ge=-1, le=10)
    price_ma_min: float = Field(default=1, gt=0, le=10)


class D(Common):
    volume_ratio_max: float = Field(default=0.7, ge=0, le=100)
    distance_max: float = Field(default=0.02, ge=0, le=1)
    ma_growth_min: float = Field(default=0, ge=-1, le=10)


class E(Common):
    volume_ratio_min: float = Field(default=1.5, ge=0, le=100)
    breakout_ratio_min: float = Field(default=1, ge=1, le=10)
    price_ma_min: float = Field(default=1, gt=0, le=10)


class Condition(StrictModel):
    factor: str
    operator: Literal["gt", "gte", "lt", "lte", "eq"] = "gte"
    value: float | bool
    enabled: bool = True

    @model_validator(mode="after")
    def validate_factor(self) -> "Condition":
        if self.factor not in NUMERIC_FACTORS | BOOLEAN_FACTORS:
            raise ValueError("未知因子")
        if self.factor in BOOLEAN_FACTORS and (
            self.operator != "eq" or not isinstance(self.value, bool)
        ):
            raise ValueError("布尔因子仅支持 eq 与布尔值")
        if self.factor in NUMERIC_FACTORS and isinstance(self.value, bool):
            raise ValueError("数值因子不能比较布尔值")
        return self


class Group(StrictModel):
    logic: Literal["AND", "OR"] = "AND"
    children: list["Condition | Group"] = Field(min_length=1, max_length=64)
    enabled: bool = True


class F(Common):
    group: Group

    @model_validator(mode="after")
    def tree_limits(self) -> "F":
        count = 0

        def visit(node: Condition | Group, depth: int, active: bool) -> int:
            nonlocal count
            count += 1
            if depth > 4 or count > 64:
                raise ValueError("条件树超过深度4或节点64限制")
            if isinstance(node, Condition):
                return int(active and node.enabled)
            return sum(visit(child, depth + 1, active and node.enabled) for child in node.children)

        if visit(self.group, 1, True) == 0:
            raise ValueError("至少启用一个条件")
        return self


MODELS: dict[str, type[Common]] = {"A": A, "B": B, "C": C, "D": D, "E": E, "F": F}
TITLES = {
    "A": "高股息 + 单日下跌",
    "B": "高股息 + 阶段回调",
    "C": "高股息 + 中短期上涨",
    "D": "缩量接近20日均线",
    "E": "放量突破",
    "F": "通用组合条件",
}
DEFAULT_F = {
    "group": {
        "logic": "AND",
        "children": [
            {"factor": "daily_return", "operator": "gte", "value": 0},
            {"factor": "volume_ratio_5d", "operator": "gte", "value": 1},
        ],
    }
}


def condition(factor: str, op: str, value: Any) -> Condition:
    return Condition.model_validate({"factor": factor, "operator": op, "value": value})


class RuleRegistry:
    def validate(self, rule_id: str, params: dict[str, Any]) -> Common:
        return MODELS[rule_id].model_validate(params)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "id": rule_id,
                "name": TITLES[rule_id],
                "version": RULE_VERSION,
                "defaults": model.model_validate(DEFAULT_F if rule_id == "F" else {}).model_dump(),
                "schema": model.model_json_schema(),
            }
            for rule_id, model in MODELS.items()
        ]

    def group(self, params: Common) -> Group:
        children: list[Condition | Group]
        if isinstance(params, A):
            children = [
                condition("dividend_yield_ttm", "gte", params.dividend_min),
                condition("daily_return", "lte", params.daily_return_max),
            ]
        elif isinstance(params, B):
            children = [
                condition("dividend_yield_ttm", "gte", params.dividend_min),
                condition("drawdown_60d", "gte", params.drawdown_min),
                condition("amount_mean_20d", "gte", params.amount_min),
            ]
        elif isinstance(params, C):
            children = [
                condition("dividend_yield_ttm", "gte", params.dividend_min),
                condition("return_20d", "gte", params.return_20d_min),
                condition("return_60d", "gte", params.return_60d_min),
                condition("price_ma20_ratio", "gt", params.price_ma_min),
            ]
        elif isinstance(params, D):
            children = [
                condition("volume_ratio_5d", "lte", params.volume_ratio_max),
                condition("abs_price_distance_ma20", "lte", params.distance_max),
                condition("ma20_change_5d", "gt", params.ma_growth_min),
                condition("valid_days_20d", "eq", 20),
            ]
        elif isinstance(params, E):
            children = [
                condition("breakout_price_ratio", "gt", params.breakout_ratio_min),
                condition("volume_ratio_20d", "gte", params.volume_ratio_min),
                condition("price_ma20_ratio", "gt", params.price_ma_min),
            ]
        elif isinstance(params, F):
            children = [params.group]
        else:
            raise ValueError("未知规则")
        if params.exclude_st:
            children.append(condition("is_st", "eq", False))
        if params.exclude_suspended:
            children.append(condition("is_suspended", "eq", False))
        return Group(children=children)


def evaluate(
    node: Condition | Group, values: dict[str, Any]
) -> tuple[bool | None, list[dict[str, Any]]]:
    if not node.enabled:
        return None, []
    if isinstance(node, Condition):
        actual = values.get(node.factor)
        match = None if actual is None else bool(OPERATORS[node.operator](actual, node.value))
        return match, [
            {
                "factor": node.factor,
                "operator": node.operator,
                "threshold": node.value,
                "actual": actual,
                "matched": match,
            }
        ]
    answers = []
    details = []
    for child in node.children:
        answer, child_details = evaluate(child, values)
        if child_details:
            answers.append(answer)
            details.extend(child_details)
    if not answers:
        return None, []
    if node.logic == "AND":
        outcome = False if False in answers else None if None in answers else True
    else:
        outcome = True if True in answers else None if None in answers else False
    return outcome, details
