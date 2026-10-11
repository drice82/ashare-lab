import secrets
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response

from ashare_lab.config import Settings
from ashare_lab.schemas import (
    BacktestCompareRequest,
    BacktestRequest,
    DcaRequest,
    PortfolioRequest,
    PresetRequest,
    ScreeningRequest,
    SyncRequest,
)
from ashare_lab.services import ApplicationService, ServiceError


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    service = ApplicationService(settings)
    app = FastAPI(title="ashare-lab", version="0.1.0")
    app.state.settings = settings
    app.state.service = service

    @app.middleware("http")
    async def access_control(request: Request, call_next: Any) -> Any:
        token = settings.api_access_token.get_secret_value()
        if (
            request.url.path.startswith("/api/v1")
            and token
            and not secrets.compare_digest(
                request.headers.get("authorization", ""), f"Bearer {token}"
            )
        ):
            return JSONResponse(
                status_code=401, content={"code": "unauthorized", "message": "请输入有效访问密钥"}
            )
        protected = request.url.path.startswith("/api/v1/data/") or (
            request.url.path.rstrip("/") == "/api/v1/jobs"
            and request.query_params.get("kind")
            not in {"screening", "dca", "backtest", "portfolio"}
        )
        if protected:
            password = settings.data_management_password.get_secret_value()
            supplied = request.headers.get("X-Data-Management-Password", "")
            if not password or not secrets.compare_digest(supplied.encode(), password.encode()):
                return JSONResponse(
                    status_code=403,
                    content={"code": "data_management_locked", "message": "数据管理密码缺失或错误"},
                )
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"code": exc.code, "message": str(exc), "details": exc.details},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/v1/instruments")
    def instruments(
        as_of_date: date | None = None,
        search: str = Query("", max_length=100),
        data_version: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        return service.instruments(
            as_of_date.isoformat() if as_of_date else None, search, page, page_size, data_version
        )

    @app.get("/api/v1/market/bars")
    def bars(
        stock_code: Annotated[str, Query(pattern=r"^[036]\d{5}\.(SH|SZ)$")],
        start_date: date | None = None,
        end_date: date | None = None,
        adjustment: Literal["raw", "asof"] = "asof",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        return service.bars(stock_code, start_date, end_date, adjustment, run_id)

    @app.get("/api/v1/market/status", response_model=dict[str, Any])
    def status() -> Response:
        return Response(service.market_status_json(management=False), media_type="application/json")

    @app.get("/api/v1/data/status", response_model=dict[str, Any])
    def data_status() -> Response:
        return Response(service.market_status_json(), media_type="application/json")

    @app.get("/api/v1/data/datasets")
    def datasets() -> list[dict[str, Any]]:
        return service.datasets()

    @app.post("/api/v1/data/sync", status_code=202)
    def sync(request: SyncRequest) -> dict[str, Any]:
        return service.submit_sync(request)

    @app.delete("/api/v1/data/sync")
    def delete_all_sync() -> dict[str, Any]:
        return service.delete_jobs("sync")

    @app.delete("/api/v1/data/sync/{job_id}")
    def delete_sync(job_id: str) -> dict[str, Any]:
        return service.delete_job(job_id, "sync")

    @app.get("/api/v1/data/sync/{job_id}")
    def sync_job(job_id: str) -> dict[str, Any]:
        return service.job(job_id, "sync")

    @app.post("/api/v1/data/sync/{job_id}/cancel")
    def cancel_sync(job_id: str) -> dict[str, Any]:
        return service.cancel(job_id, "sync")

    @app.post("/api/v1/data/sync/{job_id}/resume", status_code=202)
    def resume_sync(job_id: str) -> dict[str, Any]:
        return service.resume(job_id)

    @app.get("/api/v1/jobs")
    def task_list(
        kind: Literal["sync", "screening", "dca", "backtest", "portfolio"] | None = None,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        return service.task_list(kind, limit, offset)

    @app.get("/api/v1/screening/rules")
    def rules() -> dict[str, Any]:
        from ashare_lab.factors import BOOLEAN_FACTORS, NUMERIC_FACTORS

        return {
            "rules": service.registry.describe(),
            "factors": [
                {"id": key, "name": info[0], "unit": info[1]}
                for key, info in (NUMERIC_FACTORS | BOOLEAN_FACTORS).items()
            ],
        }

    @app.post("/api/v1/screening/run", status_code=202)
    def screening(request: ScreeningRequest) -> dict[str, Any]:
        return service.submit_screen(request)

    @app.delete("/api/v1/screening/runs")
    def delete_all_screening() -> dict[str, Any]:
        return service.delete_jobs("screening")

    @app.delete("/api/v1/screening/runs/{job_id}")
    def delete_screening(job_id: str) -> dict[str, Any]:
        return service.delete_job(job_id, "screening")

    @app.get("/api/v1/screening/runs/{job_id}")
    def screening_job(job_id: str) -> dict[str, Any]:
        return service.job(job_id, "screening")

    @app.post("/api/v1/screening/runs/{job_id}/cancel")
    def cancel_screening(job_id: str) -> dict[str, Any]:
        return service.cancel(job_id, "screening")

    @app.get("/api/v1/screening/runs/{job_id}/results")
    def results(
        job_id: str,
        match_status: Literal["matched", "not_matched", "insufficient_data", "all"] = "matched",
        search: str = "",
        sort_by: str = "stock_code",
        order: Literal["asc", "desc"] = "asc",
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        filter_factor: str | None = None,
        min_value: float | None = Query(None, allow_inf_nan=False),
        max_value: float | None = Query(None, allow_inf_nan=False),
    ) -> dict[str, Any]:
        return service.results(
            job_id,
            match_status,
            search,
            sort_by,
            order,
            page,
            page_size,
            filter_factor,
            min_value,
            max_value,
        )

    @app.get("/api/v1/screening/runs/{job_id}/export")
    def export(job_id: str) -> Response:
        return Response(
            service.export(job_id),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="screening-{job_id}.csv"'},
        )

    @app.get("/api/v1/screening/presets")
    def list_presets() -> list[dict[str, Any]]:
        return service.list_presets()

    @app.post("/api/v1/screening/presets", status_code=201)
    def save_preset(request: PresetRequest) -> dict[str, Any]:
        return service.save_preset(request)

    @app.get("/api/v1/dca/options")
    def dca_options() -> dict[str, Any]:
        return service.dca.options()

    @app.get("/api/v1/backtest/options")
    def backtest_options() -> dict[str, Any]:
        return service.backtest.options()

    @app.post("/api/v1/backtest/run", status_code=202)
    def backtest_run(request: BacktestRequest) -> dict[str, Any]:
        return service.backtest.submit_backtest(request)

    @app.post("/api/v1/backtest/compare", status_code=202)
    def backtest_compare(request: BacktestCompareRequest) -> dict[str, Any]:
        return service.backtest.submit_comparison(request)

    @app.get("/api/v1/backtest/runs/{job_id}/strategies/{strategy_id}")
    def backtest_strategy(
        job_id: str,
        strategy_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        return service.backtest.strategy_detail(job_id, strategy_id, page, page_size)

    @app.delete("/api/v1/backtest/runs")
    def delete_all_backtest() -> dict[str, Any]:
        return service.delete_jobs("backtest")

    @app.delete("/api/v1/backtest/runs/{job_id}")
    def delete_backtest(job_id: str) -> dict[str, Any]:
        return service.delete_job(job_id, "backtest")

    @app.get("/api/v1/backtest/runs/{job_id}")
    def backtest_job(job_id: str) -> dict[str, Any]:
        return service.job(job_id, "backtest")

    @app.post("/api/v1/backtest/runs/{job_id}/cancel")
    def backtest_cancel(job_id: str) -> dict[str, Any]:
        return service.cancel(job_id, "backtest")

    @app.get("/api/v1/backtest/runs/{job_id}/results")
    def backtest_results(
        job_id: str, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)
    ) -> dict[str, Any]:
        return service.backtest.results(job_id, page, page_size)

    @app.get("/api/v1/backtest/runs/{job_id}/export")
    def backtest_export(
        job_id: str, view: Literal["summary", "curves", "ledger"] = "ledger"
    ) -> Response:
        return Response(
            service.backtest.export(job_id, view),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="backtest-{job_id}.csv"'},
        )

    @app.get("/api/v1/portfolio/options")
    def portfolio_options() -> dict[str, Any]:
        return service.portfolio.options()

    @app.post("/api/v1/portfolio/run", status_code=202)
    def portfolio_run(request: PortfolioRequest) -> dict[str, Any]:
        return service.portfolio.submit_portfolio(request)

    @app.delete("/api/v1/portfolio/runs")
    def delete_all_portfolio() -> dict[str, Any]:
        return service.delete_jobs("portfolio")

    @app.delete("/api/v1/portfolio/runs/{job_id}")
    def delete_portfolio(job_id: str) -> dict[str, Any]:
        return service.delete_job(job_id, "portfolio")

    @app.get("/api/v1/portfolio/runs/{job_id}")
    def portfolio_job(job_id: str) -> dict[str, Any]:
        return service.job(job_id, "portfolio")

    @app.post("/api/v1/portfolio/runs/{job_id}/cancel")
    def portfolio_cancel(job_id: str) -> dict[str, Any]:
        return service.cancel(job_id, "portfolio")

    @app.get("/api/v1/portfolio/runs/{job_id}/results")
    def portfolio_results(
        job_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        account: Literal["portfolio", "benchmark"] = "portfolio",
    ) -> dict[str, Any]:
        return service.portfolio.results(job_id, page, page_size, account)

    @app.get("/api/v1/portfolio/runs/{job_id}/positions")
    def portfolio_positions(
        job_id: str,
        date: date | None = None,
        account: Literal["portfolio", "benchmark"] = "portfolio",
    ) -> dict[str, Any]:
        return service.portfolio.positions(job_id, date.isoformat() if date else None, account)

    @app.get("/api/v1/portfolio/runs/{job_id}/stocks/{stock_code}")
    def portfolio_stock(
        job_id: str,
        stock_code: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        account: Literal["portfolio", "benchmark"] = "portfolio",
    ) -> dict[str, Any]:
        return service.portfolio.stock_detail(job_id, stock_code, account, page, page_size)

    @app.get("/api/v1/portfolio/runs/{job_id}/export")
    def portfolio_export(
        job_id: str, view: Literal["summary", "curves", "positions", "ledger"] = "ledger"
    ) -> Response:
        return Response(
            service.portfolio.export(job_id, view),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="portfolio-{job_id}-{view}.csv"'
            },
        )

    @app.post("/api/v1/dca/run", status_code=202)
    def dca_run(request: DcaRequest) -> dict[str, Any]:
        return service.dca.submit(request)

    @app.delete("/api/v1/dca/runs")
    def delete_all_dca() -> dict[str, Any]:
        return service.delete_jobs("dca")

    @app.delete("/api/v1/dca/runs/{job_id}")
    def delete_dca(job_id: str) -> dict[str, Any]:
        return service.delete_job(job_id, "dca")

    @app.get("/api/v1/dca/runs/{job_id}")
    def dca_job(job_id: str) -> dict[str, Any]:
        return service.job(job_id, "dca")

    @app.post("/api/v1/dca/runs/{job_id}/cancel")
    def dca_cancel(job_id: str) -> dict[str, Any]:
        return service.cancel(job_id, "dca")

    @app.get("/api/v1/dca/runs/{job_id}/results")
    def dca_results(
        job_id: str, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)
    ) -> dict[str, Any]:
        return service.dca.results(job_id, page, page_size)

    @app.get("/api/v1/dca/runs/{job_id}/export")
    def dca_export(job_id: str) -> Response:
        return Response(
            service.dca.export(job_id),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="dca-{job_id}.csv"'},
        )

    return app
