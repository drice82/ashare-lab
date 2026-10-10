from collections.abc import Iterable
from datetime import date
from typing import Any, Protocol


class Strategy(Protocol):
    def signals(
        self, as_of: date, factors: Iterable[dict[str, Any]]
    ) -> Iterable[dict[str, Any]]: ...


class BacktestEngine(Protocol):
    def run(self, strategy: Strategy, start: date, end: date, data_version: str) -> Any: ...


class PortfolioService(Protocol):
    def positions(self, as_of: date) -> Iterable[dict[str, Any]]: ...


class NotificationService(Protocol):
    def send(self, subject: str, message: str, recipient: str) -> None: ...
