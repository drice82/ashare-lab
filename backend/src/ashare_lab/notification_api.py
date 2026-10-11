"""Explicit public routes; inputs and delegation only, no signal rules."""

from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field

from ashare_lab.notifications import NotificationService, SubscriptionRequest
from ashare_lab.schemas import StrictModel

PREFIX = "/api/v1/notifications"
COOKIE = "ashare_notification_session"


class TargetRequest(StrictModel):
    target: str = Field(min_length=1, max_length=2048)


class EmailRequest(StrictModel):
    address: str = Field(min_length=3, max_length=254)


class CodeRequest(EmailRequest):
    code: str = Field(pattern=r"^[0-9]{6}$")


class UnsubscribeRequest(StrictModel):
    token: str = Field(min_length=1, max_length=2048)


class SubscriptionTestRequest(StrictModel):
    direction: Literal["buy", "sell"]


class PatchRequest(StrictModel):
    stock_code: str | None = None
    strategy: dict[str, Any] | None = None
    directions: list[Literal["buy", "sell"]] | None = None
    channels: list[Literal["webhook", "email"]] | None = None
    status: Literal["active", "paused"] | None = None


def router_for(service: NotificationService) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    def owner(request: Request) -> str:
        return service.authenticate(request.cookies.get(COOKIE, ""))

    def session_response(response: Response, value: dict[str, str]) -> dict[str, str]:
        response.set_cookie(
            COOKIE,
            value["session"],
            max_age=service.settings.notification_session_days * 86400,
            httponly=True,
            secure=service.settings.notification_cookie_secure,
            samesite="lax",
            path=PREFIX,
        )
        response.headers["Cache-Control"] = "no-store"
        return {key: value[key] for key in ("owner_id", "email", "expires_at")}

    @router.post("/auth/email/verification")
    def login_code(payload: EmailRequest) -> dict[str, Any]:
        return service.start_login(payload.address)

    @router.post("/auth/email/verify")
    def login(payload: CodeRequest, request: Request, response: Response) -> dict[str, str]:
        value = service.login(payload.address, payload.code)
        service.logout(request.cookies.get(COOKIE, ""))
        return session_response(response, value)

    @router.get("/session")
    def session(request: Request, response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "no-store"
        return service.session_info(request.cookies.get(COOKIE, ""))

    @router.delete("/session")
    def logout(request: Request, response: Response) -> dict[str, str]:
        service.logout(request.cookies.get(COOKIE, ""))
        response.delete_cookie(
            COOKIE,
            path=PREFIX,
            httponly=True,
            secure=service.settings.notification_cookie_secure,
            samesite="lax",
        )
        return {"status": "signed_out"}

    @router.post("/account/email/verification")
    def change_code(payload: EmailRequest, request: Request) -> dict[str, Any]:
        return service.start_email_change(owner(request), payload.address)

    @router.post("/account/email/verify")
    def change_email(payload: CodeRequest, request: Request, response: Response) -> dict[str, str]:
        return session_response(
            response, service.change_email(owner(request), payload.address, payload.code)
        )

    @router.post("/channels/{kind}/enable")
    def enable_channel(kind: Literal["webhook", "email"], request: Request) -> dict[str, str]:
        return service.enable_channel(owner(request), kind)

    @router.get("/options")
    def options() -> dict[str, Any]:
        return service.options()

    @router.get("/instruments")
    def instruments(request: Request, search: str = Query("", max_length=100)) -> dict[str, Any]:
        owner(request)
        return service.instruments(search)

    @router.get("/channels")
    def channels(request: Request) -> list[dict[str, Any]]:
        return service.list_channels(owner(request))

    @router.put("/channels/webhook")
    def bind(payload: TargetRequest, request: Request) -> dict[str, Any]:
        return service.bind_channel(owner(request), "webhook", payload.target)

    @router.delete("/channels/{kind}")
    def remove(kind: Literal["webhook", "email"], request: Request) -> dict[str, str]:
        return service.delete_channel(owner(request), kind)

    @router.post("/channels/{kind}/test")
    def test(kind: Literal["webhook", "email"], request: Request) -> dict[str, str]:
        return service.test_channel(owner(request), kind)

    @router.get("/subscriptions")
    def subscriptions(request: Request) -> list[dict[str, Any]]:
        return service.list_subscriptions(owner(request))

    @router.post("/subscriptions")
    def subscribe(payload: SubscriptionRequest, request: Request) -> dict[str, Any]:
        return service.save_subscription(owner(request), payload)

    @router.patch("/subscriptions/{sid}")
    def patch(sid: str, payload: PatchRequest, request: Request) -> dict[str, Any]:
        return service.patch_subscription(
            owner(request), sid, payload.model_dump(exclude_unset=True)
        )

    @router.delete("/subscriptions/{sid}")
    def delete_subscription(sid: str, request: Request) -> dict[str, str]:
        return service.delete_subscription(owner(request), sid)

    @router.post("/subscriptions/{sid}/test")
    def test_subscription(
        sid: str, payload: SubscriptionTestRequest, request: Request
    ) -> dict[str, Any]:
        return service.test_subscription(owner(request), sid, payload.direction)

    @router.get("/deliveries")
    def deliveries(
        request: Request,
        channel: Literal["webhook", "email", "bark"] | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        return service.list_deliveries(owner(request), channel, limit, offset)

    @router.post("/email/unsubscribe")
    def unsubscribe(payload: UnsubscribeRequest) -> dict[str, str]:
        return service.unsubscribe(payload.token)

    return router


def is_notification_route(path: str, method: str) -> bool:
    """Shared-key exceptions are exact paths/methods, not a whole prefix."""
    import re

    if not path.startswith(PREFIX):
        return False
    relative = path[len(PREFIX) :]
    fixed = {
        "/session": {"GET", "DELETE"},
        "/auth/email/verification": {"POST"},
        "/auth/email/verify": {"POST"},
        "/account/email/verification": {"POST"},
        "/account/email/verify": {"POST"},
        "/channels/email/enable": {"POST"},
        "/options": {"GET"},
        "/instruments": {"GET"},
        "/channels": {"GET"},
        "/subscriptions": {"GET", "POST"},
        "/deliveries": {"GET"},
        "/email/unsubscribe": {"POST"},
    }
    if relative in fixed:
        return method in fixed[relative]
    if re.fullmatch(r"/channels/(webhook|email)", relative):
        return method == "DELETE" or (relative == "/channels/webhook" and method == "PUT")
    if re.fullmatch(r"/channels/(webhook|email)/(test|enable)", relative):
        return method == "POST"
    if re.fullmatch(r"/subscriptions/[a-f0-9-]{36}/test", relative):
        return method == "POST"
    if re.fullmatch(r"/subscriptions/[a-f0-9-]{36}", relative):
        return method in {"PATCH", "DELETE"}
    return False
