"""Backtest application boundary, sharing research validation and result I/O."""

from typing import Any

from ashare_lab.backtest import BACKTEST_VERSION, STRATEGY_VERSION
from ashare_lab.dca_service import DcaService
from ashare_lab.schemas import BacktestRequest


class BacktestService(DcaService):
    kind = "backtest"
    label = "回测"
    extra_columns = ["signal_date", "tax", "fixture_mode", "engine_version", "strategy_version"]

    def options(self) -> dict[str, Any]:
        result = super().options()
        result["defaults"] = {
            "initial_cash": "100000",
            "strategy": "buy_hold",
            "short_window": 20,
            "long_window": 60,
            "commission_rate": "0.0003",
            "minimum_commission": "5",
            "transfer_rate": "0.00001",
            "stamp_rate": "0.0005",
        }
        result["strategies"] = [
            {"id": "buy_hold", "label": "买入持有"},
            {"id": "ma_cross", "label": "双均线交叉"},
        ]
        return result

    def submit_backtest(self, request: BacktestRequest) -> dict[str, Any]:
        self.validate(request)
        return self.db.enqueue(
            "backtest",
            request.model_dump(mode="json"),
            {
                **request.data_versions,
                "_backtest_version": BACKTEST_VERSION,
                "_strategy_version": STRATEGY_VERSION,
                "_universe_policy": "survivors_v1",
            },
        )
