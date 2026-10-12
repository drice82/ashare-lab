"""User-session routes: input validation and service delegation only."""

import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request

from ashare_lab.notification_api import session_token
from ashare_lab.notifications import NotificationService
from ashare_lab.watchlists import WatchlistInput, WatchlistService, WatchlistUpdate

PREFIX = "/api/v1/watchlists"


def is_watchlist_route(path: str, method: str) -> bool:
    if path == PREFIX:
        return method in {"GET", "POST"}
    return bool(re.fullmatch(PREFIX + r"/[a-f0-9-]{36}", path)) and method in {
        "GET",
        "PUT",
        "DELETE",
    }


def router_for(service: WatchlistService, auth: NotificationService) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    def owner(request: Request) -> str:
        return auth.authenticate(session_token(request))

    @router.get("")
    def lists(request: Request) -> list[dict[str, Any]]:
        return service.list(owner(request))

    @router.post("", status_code=201)
    def create(payload: WatchlistInput, request: Request) -> dict[str, Any]:
        return service.create(owner(request), payload)

    @router.get("/{list_id}")
    def get(list_id: UUID, request: Request) -> dict[str, Any]:
        return service.get(owner(request), str(list_id))

    @router.put("/{list_id}")
    def change(list_id: UUID, payload: WatchlistUpdate, request: Request) -> dict[str, Any]:
        return service.change(owner(request), str(list_id), payload)

    @router.delete("/{list_id}")
    def remove(list_id: UUID, request: Request, revision: int = Query(ge=1)) -> dict[str, str]:
        return service.remove(owner(request), str(list_id), revision)

    return router
