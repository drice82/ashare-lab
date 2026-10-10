"""Read-only, bounded live connectivity report; no market data is published."""

import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ashare_lab.config import Settings
from ashare_lab.providers import ProviderError, TushareProvider

settings = Settings()
if not settings.tushare_token.get_secret_value():
    print("未配置 TUSHARE_TOKEN：真实接口验证未执行。")
    sys.exit(2)
provider = TushareProvider(settings)
end = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
try:
    calendar = provider.fetch(
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": (end - timedelta(days=14)).strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        },
    )
    days = sorted(str(row["cal_date"]) for row in calendar.to_dicts() if int(row["is_open"]) == 1)
    day = days[-1]
except (ProviderError, IndexError) as exc:
    print(
        json.dumps(
            {
                "dataset": "trade_cal",
                "status": "failed",
                "error": str(exc) if isinstance(exc, ProviderError) else "没有可确认交易日",
            },
            ensure_ascii=False,
        )
    )
    sys.exit(1)
failures = 0
for dataset in [
    "stock_basic",
    "namechange",
    "daily",
    "adj_factor",
    "daily_basic",
    "dividend",
    "stk_limit",
    "suspend_d",
]:
    params = {"ts_code": "000001.SZ"}
    if dataset in {"daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d"}:
        params["trade_date"] = day
    try:
        frame = provider.fetch(dataset, params)
        print(
            json.dumps(
                {
                    "dataset": dataset,
                    "status": "response_received",
                    "rows": frame.height,
                    "fields": frame.columns,
                },
                ensure_ascii=False,
            )
        )
    except ProviderError as exc:
        failures += 1
        print(
            json.dumps(
                {"dataset": dataset, "status": "failed", "code": exc.code, "message": str(exc)},
                ensure_ascii=False,
            )
        )
sys.exit(1 if failures else 0)
