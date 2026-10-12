import secrets
from datetime import date
from ipaddress import ip_address, ip_network
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from ashare_lab.analytics import INGEST_PATH, AnalyticsService, PageViewRequest
from ashare_lab.config import Settings
from ashare_lab.notification_api import PREFIX, is_notification_route, router_for
from ashare_lab.notifications import NotificationService
from ashare_lab.schemas import (
    BacktestCompareRequest,
    BacktestRequest,
    DcaRequest,
    PortfolioRequest,
    PresetRequest,
    ScreeningRequest,
    StockCode,
    SyncRequest,
)
from ashare_lab.services import ApplicationService, ServiceError
from ashare_lab.watchlist_api import PREFIX as WATCHLIST_PREFIX
from ashare_lab.watchlist_api import is_watchlist_route
from ashare_lab.watchlist_api import router_for as watchlist_router
from ashare_lab.watchlists import WatchlistService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    service = ApplicationService(settings)
    app = FastAPI(title="ashare-lab", version="0.1.0")
    app.state.settings = settings
    app.state.service = service
    analytics = AnalyticsService(service.db, settings.tz)
    app.state.analytics = analytics
    notifications = NotificationService(settings, service.db, service.repository)
    app.state.notifications = notifications
    app.include_router(router_for(notifications))
    app.state.watchlists = WatchlistService(service.db)
    app.include_router(watchlist_router(app.state.watchlists, notifications))

    @app.middleware("http")
    async def access_control(request: Request, call_next: Any) -> Any:
        if request.url.path == INGEST_PATH and request.method == "POST":
            origin = request.headers.get("origin")
            allowed = settings.public_origins() | {str(request.base_url).rstrip("/")}
            if not origin or origin not in allowed:
                return JSONResponse(
                    status_code=403,
                    content={"code": "origin_rejected", "message": "浏览上报必须来自同源页面"},
                )
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        notification_route = is_notification_route(request.url.path, request.method)
        if notification_route or is_watchlist_route(request.url.path, request.method):
            try:
                if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                    origin = request.headers.get("origin")
                    # All public writes require a same-origin Origin header.
                    allowed = settings.public_origins() | {str(request.base_url).rstrip("/")}
                    if origin and origin not in allowed:
                        return JSONResponse(
                            status_code=403,
                            content={
                                "code": "origin_rejected",
                                "message": "写请求必须来自同源页面",
                            },
                        )
                    if not origin:
                        return JSONResponse(
                            status_code=403,
                            content={"code": "origin_rejected", "message": "写请求缺少同源 Origin"},
                        )
                    ip = request.client.host if request.client else "unknown"
                    trusted = settings.notification_trusted_proxies.split(",")
                    if any(
                        network.strip() and ip_address(ip) in ip_network(network.strip())
                        for network in trusted
                    ):
                        forwarded = request.headers.get("x-real-ip", "")
                        try:
                            ip = str(ip_address(forwarded))
                        except ValueError:
                            pass
                    notifications.rate_limit(f"write-ip:{ip}", 60, 60)
                    if notification_route and (
                        request.url.path.endswith(("/test", "/verification"))
                        or request.method == "PUT"
                        or request.url.path == PREFIX + "/channels/webhook/enable"
                    ):
                        notifications.rate_limit(f"notification-send-ip:{ip}", 60, 10)
                        notifications.rate_limit(f"notification-send-ip:{ip}", 86400, 100)
                    if request.url.path in {
                        PREFIX + "/auth/email/verify",
                        PREFIX + "/account/email/verify",
                    }:
                        notifications.rate_limit(f"identity-ip:{ip}", 3600, 10)
                response = await call_next(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except ServiceError as exc:
                return JSONResponse(
                    status_code=exc.status, content={"code": exc.code, "message": str(exc)}
                )
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
                    content={"code": "data_management_locked", "message": "后台管理密码缺失或错误"},
                )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.url.path == INGEST_PATH:
            return JSONResponse(
                status_code=422,
                content={"code": "invalid_parameters", "message": "浏览上报参数无效"},
            )
        if request.url.path.startswith((PREFIX, WATCHLIST_PREFIX)):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "invalid_parameters",
                    "message": "自选列表输入参数无效"
                    if request.url.path.startswith(WATCHLIST_PREFIX)
                    else "通知输入参数无效",
                },
            )
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"code": exc.code, "message": str(exc), "details": exc.details},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0"}

    @app.post(INGEST_PATH, status_code=204)
    def pageview(payload: PageViewRequest) -> Response:
        analytics.record(payload)
        return Response(status_code=204)

    @app.get("/api/v1/data/analytics")
    def browsing_stats(
        start_date: date | None = None, end_date: date | None = None
    ) -> JSONResponse:
        return JSONResponse(
            analytics.summary(start_date, end_date), headers={"Cache-Control": "no-store"}
        )

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

    @app.get("/api/v1/instruments/{code}/company")
    def company(code: StockCode) -> dict[str, Any]:
        return service.company.read(code)

    @app.post("/api/v1/instruments/{code}/company/refresh", status_code=202)
    def refresh_company(code: StockCode, force: bool = False) -> dict[str, Any]:
        return service.company.refresh(code, force)

    @app.get("/api/v1/instruments/{code}/company/jobs/{job_id}")
    def company_job(code: StockCode, job_id: str) -> dict[str, Any]:
        job = service.job(job_id, "company_profile")
        if job["payload"]["stock_code"] != code:
            raise ServiceError(404, "not_found", "企业资料任务不存在")
        return job

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
        from ashare_lab.signal_screening import describe_strategies

        return {
            "rules": service.registry.describe(),
            "strategies": describe_strategies(),
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
