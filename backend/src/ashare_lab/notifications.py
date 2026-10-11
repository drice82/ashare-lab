"""Email identity, verified channels, subscriptions and atomic signal fan-out."""

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field, model_validator
from sqlalchemy import String, delete, func, literal, select, text, union_all
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection

from ashare_lab.backtest import STRATEGY_VERSION
from ashare_lab.comparison import COMPARISON_VERSION, LABELS, PARAMETERS
from ashare_lab.config import Settings
from ashare_lab.db import Database, identifier, jobs, now
from ashare_lab.instrument_search import search_rank
from ashare_lab.notification_auth import NotificationAuth
from ashare_lab.notification_models import (
    channels,
    checks,
    deliveries,
    limits,
    service_state,
    subscriptions,
    test_deliveries,
    test_requests,
)
from ashare_lab.notification_transport import (
    DeliveryError,
    send_email,
    send_webhook,
    validate_webhook_url,
)
from ashare_lab.schemas import STRATEGY_FIELDS, StrategyConfig, StrictModel
from ashare_lab.services import ServiceError
from ashare_lab.signals import calculate_signal
from ashare_lab.warehouse import MarketRepository

ChannelType = Literal["webhook", "email"]


class SubscriptionRequest(StrictModel):
    stock_code: str = Field(pattern=r"^[036]\d{5}\.(SH|SZ)$")
    strategy: StrategyConfig
    directions: list[Literal["buy", "sell"]] = Field(
        default=["buy", "sell"], min_length=1, max_length=2
    )
    channels: list[ChannelType] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_strategy(self) -> "SubscriptionRequest":
        if self.strategy.id == "buy_hold":
            raise ValueError("通知仅支持五种日线技术策略")
        if len(set(self.channels)) != len(self.channels) or len(set(self.directions)) != len(
            self.directions
        ):
            raise ValueError("渠道或方向重复")
        self.channels.sort()
        self.directions.sort()
        return self


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def strategy_version(config: StrategyConfig) -> str:
    return STRATEGY_VERSION if config.id == "ma_cross" else COMPARISON_VERSION


def local_day() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def fail(code: str, message: str, status: int = 422) -> ServiceError:
    return ServiceError(status, code, message)


class NotificationService:
    def __init__(self, settings: Settings, db: Database, repository: MarketRepository):
        self.settings, self.db, self.repository = settings, db, repository
        key = settings.notification_encryption_key.get_secret_value()
        try:
            self.cipher = Fernet(key.encode()) if key else None
        except (ValueError, TypeError):
            self.cipher = None
        self.auth = NotificationAuth(self)

    def _cipher(self) -> Fernet:
        if self.cipher is None:
            raise fail("encryption_unavailable", "通知加密密钥未配置或无效", 503)
        return self.cipher

    def _encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        try:
            return self._cipher().decrypt(value.encode()).decode()
        except InvalidToken:
            raise fail(
                "encryption_unavailable", "通知密钥无法解密已有渠道，请检查配置", 503
            ) from None

    def rate_limit(self, key: str, seconds: int, maximum: int) -> None:
        stamp = int(datetime.now(UTC).timestamp())
        window = stamp // seconds
        key = f"{seconds}:{digest(key)}"
        denied = False
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            conn.execute(
                delete(limits).where(
                    limits.c.window < window - 2, limits.c.key.like(f"{seconds}:%")
                )
            )
            count = (
                conn.execute(
                    select(limits.c.count).where(limits.c.key == key, limits.c.window == window)
                ).scalar()
                or 0
            )
            denied = count >= maximum
            if not denied:
                conn.execute(
                    insert(limits)
                    .values(key=key, window=window, count=1)
                    .on_conflict_do_update(
                        index_elements=[limits.c.key, limits.c.window],
                        set_={"count": limits.c.count + 1},
                    )
                )
        if denied:
            raise fail("rate_limited", "请求过于频繁，请稍后再试", 429)

    def limit_target(self, kind: str, target: str) -> None:
        self.rate_limit(f"target:{kind}:{target}", 60, 1)
        self.rate_limit(f"target:{kind}:{target}", 86400, 10)

    def authenticate(self, session: str) -> str:
        return self.auth.authenticate(session)

    def session_info(self, session: str) -> dict[str, str]:
        return self.auth.session_info(session)

    def logout(self, session: str) -> None:
        self.auth.logout(session)

    def start_login(self, address: str, limited: bool = True) -> dict[str, Any]:
        return self.auth.start(address, limited=limited)

    def login(self, address: str, code: str) -> dict[str, str]:
        return self.auth.login(address, code)

    def start_email_change(self, owner: str, address: str, limited: bool = True) -> dict[str, Any]:
        return self.auth.start(address, purpose="change_email", owner=owner, limited=limited)

    def change_email(self, owner: str, address: str, code: str) -> dict[str, str]:
        return self.auth.change_email(owner, address, code)

    def enable_email(self, owner: str) -> dict[str, str]:
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            address = self.auth.email(owner, conn)
            channel = self.get_channel(owner, "email", conn)
            if not channel["verified"] or channel["status"] != "active":
                self._save_channel(owner, "email", address, True, conn)
        return {"status": "enabled", "message": "邮件渠道已启用，请在订阅中选择邮件"}

    def enable_channel(self, owner: str, kind: str) -> dict[str, str]:
        if kind == "email":
            return self.enable_email(owner)
        channel = self.get_channel(owner, kind)
        if channel["verified"] and channel["status"] == "active":
            return {"status": "enabled", "message": "Webhook 已启用"}
        target = self._decrypt(channel["ciphertext"])
        self.reserve_test(owner)
        try:
            self._send(kind, target, "ashare-lab 测试通知", "这是一条测试通知", identifier())
        except DeliveryError as exc:
            self._transport_failure(owner, kind, channel["revision"], exc)
            raise fail(exc.code, "测试发送失败，请检查 Webhook 地址及接收服务", 502) from None
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            current = self.get_channel(owner, kind, conn)
            if current["revision"] != channel["revision"]:
                raise fail("channel_changed", "渠道已更改，请重试", 409)
            self._save_channel(owner, kind, target, True, conn)
        return {"status": "enabled", "message": "Webhook 已启用"}

    def _test_timestamp(self) -> float:
        return datetime.now(UTC).timestamp()

    def reserve_test(self, owner: str, conn: Connection | None = None) -> str:
        if conn is None:
            with self.db.engine.begin() as connection:
                connection.execute(text("BEGIN IMMEDIATE"))
                return self.reserve_test(owner, connection)
        stamp = self._test_timestamp()
        stamps: list[float] = list(
            conn.execute(
                select(test_requests.c.timestamp)
                .where(
                    test_requests.c.owner_id == owner,
                    test_requests.c.timestamp > stamp - 3600,
                )
                .order_by(test_requests.c.timestamp)
            ).scalars()
        )
        wait = 0.0
        if len(stamps) >= 20:
            wait = stamps[0] + 3600 - stamp
        elif len(stamps) >= 5:
            wait = stamps[-1] + 60 - stamp
        if wait > 0:
            raise fail("rate_limited", f"测试过于频繁，请 {int(wait + 0.999)} 秒后再试", 429)
        request_id = identifier()
        conn.execute(insert(test_requests).values(id=request_id, owner_id=owner, timestamp=stamp))
        return request_id

    def test_subscription(self, owner: str, sid: str, direction: str) -> dict[str, Any]:
        name = next(
            (row["stock_name"] for row in self.list_subscriptions(owner) if row["id"] == sid), None
        )
        # Reserve quota and persist unknown outcomes before any external call. A crash
        # never turns an unconfirmed sample into success or a retryable real signal.
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            sub = self._subscription(owner, sid, conn)
            if direction not in sub["directions"]:
                raise fail("invalid_direction", "请选择订阅已配置的信号方向")
            selected = []
            results = []
            for kind in sub["channels"]:
                try:
                    channel = self.get_channel(owner, kind, conn)
                except ServiceError:
                    results.append(
                        {"channel": kind, "status": "skipped", "message": "渠道尚未绑定"}
                    )
                    continue
                if not channel["verified"] or channel["status"] != "active":
                    results.append(
                        {"channel": kind, "status": "skipped", "message": "渠道未启用或未验证"}
                    )
                    continue
                target = self._decrypt(channel["ciphertext"])
                selected.append((kind, channel, target))
            if not selected:
                raise fail("verification_required", "请先启用并验证订阅的发送渠道", 409)
            request_id = self.reserve_test(owner, conn)
            outcome = {
                "stock_code": sub["stock_code"],
                "stock_name": name or "",
                "strategy": sub["strategy"],
                "reason": "示例信号，仅用于测试通知",
                "evidence": {"说明": "示例指标，无真实行情"},
                "strategy_version": sub["strategy_version"],
                "data_versions": {},
            }
            stamp = now()
            sends = []
            for kind, channel, target in selected:
                did = identifier()
                conn.execute(
                    insert(test_deliveries).values(
                        id=did,
                        request_id=request_id,
                        owner_id=owner,
                        subscription_id=sid,
                        trade_date="示例日期",
                        direction=direction,
                        channel=kind,
                        status="unknown",
                        attempts=1,
                        created_at=stamp,
                        outcome=outcome,
                    )
                )
                sends.append((did, kind, channel, target))
        title, body = self.signal_message(sub, outcome, "示例日期", direction, sample=True)
        for did, kind, channel, target in sends:
            status, error = "accepted", None
            try:
                # A concurrent stop/rebind must not send to the superseded target.
                current = self.get_channel(owner, kind)
                if current["revision"] != channel["revision"] or current["status"] != "active":
                    status, error = "cancelled", "channel_changed"
                else:
                    self._send(kind, target, title, body, did)
            except DeliveryError as exc:
                status, error = "failed", exc.code
                self._transport_failure(owner, kind, channel["revision"], exc)
            except ServiceError as exc:
                status, error = "failed", exc.code
            except Exception:
                # Never store exception text: a target URL may contain credentials.
                status, error = "unknown", "internal_delivery_error"
            with self.db.engine.begin() as conn:
                conn.execute(
                    test_deliveries.update()
                    .where(test_deliveries.c.id == did)
                    .values(
                        status=status,
                        error=error,
                        accepted_at=now() if status == "accepted" else None,
                    )
                )
            results.append({"channel": kind, "status": status})
        return {"results": results, "message": "测试完成，请查看各渠道结果"}

    def signal_message(
        self,
        sub: dict[str, Any],
        outcome: dict[str, Any],
        day: str,
        direction: str,
        sample: bool = False,
    ) -> tuple[str, str]:
        side = "买入" if direction == "buy" else "卖出"
        title = f"{sub['stock_code']} · {side}信号"
        body = f"股票：{outcome.get('stock_name', '')} {sub['stock_code']}\n策略：{LABELS[sub['strategy']['id']]}\n参数：{json.dumps(sub['strategy'], ensure_ascii=False)}\n信号日：{day}\n方向：{side}\n依据：{outcome['reason']}\n指标值：{json.dumps(outcome.get('evidence', {}), ensure_ascii=False)}\n版本：{json.dumps(outcome['data_versions'])}\n算法版本：{outcome['strategy_version']}"
        if sample:
            title = "【测试】" + title
            body = "这是一条测试通知，以下为示例信号。\n" + body
        return title, body

    def smtp_available(self) -> bool:
        if not (self.cipher and self.settings.smtp_host and self.settings.smtp_from_email):
            return False
        with self.db.engine.connect() as conn:
            error = conn.execute(
                select(service_state.c.value).where(service_state.c.key == "smtp_error")
            ).scalar()
            return error is None or not str(error).startswith(self._smtp_fingerprint() + ":")

    def options(self) -> dict[str, Any]:
        snapshot = self.db.snapshot()
        return {
            "strategies": [
                {
                    "id": key,
                    "label": LABELS[key],
                    "parameters": {field: PARAMETERS[field] for field in fields},
                    "defaults": StrategyConfig.model_validate({"id": key}).model_dump(),
                    "version": strategy_version(StrategyConfig.model_validate({"id": key})),
                }
                for key, fields in STRATEGY_FIELDS.items()
                if key != "buy_hold"
            ],
            "channels": {"webhook": self.cipher is not None, "email": self.smtp_available()},
            "fixture_mode": any(
                self.repository.version(v, resolve_coverage=False)["source"] == "fixture"
                for k, v in snapshot.items()
                if not k.startswith("_")
            ),
            "auto_send": self.settings.notification_auto_send,
            "subscription_limit": self.settings.notification_subscription_limit,
            "auth": {
                "email_available": self.smtp_available(),
                "session_days": self.settings.notification_session_days,
            },
        }

    def instruments(self, search: str) -> dict[str, Any]:
        snapshot = self.db.snapshot()
        if "stock_basic" not in snapshot:
            return {"items": [], "data_versions": snapshot}
        rows = self.repository.read("stock_basic", snapshot).to_dicts()
        ranked = []
        for row in rows:
            code = row.get("stock_code", "")
            if (
                not re.fullmatch(r"[036]\d{5}\.(SH|SZ)", code)
                or row.get("list_status") == "D"
                or row.get("delist_date")
            ):
                continue
            item = {"stock_code": code, "stock_name": row.get("name", row.get("stock_name", ""))}
            rank = search_rank(item, search)
            if rank is not None:
                ranked.append((rank, code, item))
        ranked.sort(key=lambda r: (r[0], r[1]))
        return {"items": [r[2] for r in ranked[:50]], "data_versions": snapshot}

    def get_channel(self, owner: str, kind: str, conn: Connection | None = None) -> dict[str, Any]:
        if conn is None:
            with self.db.engine.connect() as connection:
                return self.get_channel(owner, kind, connection)
        row = (
            conn.execute(
                select(channels).where(channels.c.owner_id == owner, channels.c.type == kind)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise fail("channel_missing", "尚未绑定此渠道", 409)
        return dict(row)

    def list_channels(self, owner: str) -> list[dict[str, Any]]:
        with self.db.engine.connect() as conn:
            rows = (
                conn.execute(
                    select(channels).where(
                        channels.c.owner_id == owner, channels.c.type.in_(["webhook", "email"])
                    )
                )
                .mappings()
                .all()
            )
        result = []
        for row in rows:
            try:
                target = self._decrypt(row["ciphertext"])
                masked = (
                    (target[:1] + "***@" + target.split("@")[-1])
                    if row["type"] == "email"
                    else "已加密保存"
                )
            except ServiceError:
                masked = "密钥不可用"
            result.append(
                {
                    "type": row["type"],
                    "verified": row["verified"],
                    "status": row["status"],
                    "target": masked,
                }
            )
        return result

    def _cancel(
        self,
        conn: Connection,
        owner: str,
        kind: str | None = None,
        subscription_id: str | None = None,
    ) -> None:
        statement = deliveries.update().where(
            deliveries.c.owner_id == owner, deliveries.c.status.in_(["pending", "sending"])
        )
        if kind:
            statement = statement.where(deliveries.c.channel == kind)
        if subscription_id:
            statement = statement.where(deliveries.c.subscription_id == subscription_id)
        conn.execute(statement.values(status="cancelled"))

    def _save_channel(
        self, owner: str, kind: str, target: str, verified: bool, conn: Connection | None = None
    ) -> None:
        if conn is None:
            with self.db.engine.begin() as connection:
                connection.execute(text("BEGIN IMMEDIATE"))
                self._save_channel(owner, kind, target, verified, connection)
            return
        old = (
            conn.execute(
                select(channels).where(channels.c.owner_id == owner, channels.c.type == kind)
            )
            .mappings()
            .first()
        )
        revision = old["revision"] + 1 if old else 1
        conn.execute(
            insert(channels)
            .values(
                owner_id=owner,
                type=kind,
                ciphertext=self._encrypt(target),
                target_hash=digest(target),
                verified=verified,
                revision=revision,
                status="active",
            )
            .on_conflict_do_update(
                index_elements=[channels.c.owner_id, channels.c.type],
                set_={
                    "ciphertext": self._encrypt(target),
                    "target_hash": digest(target),
                    "verified": verified,
                    "revision": revision,
                    "status": "active",
                },
            )
        )
        self._cancel(conn, owner, kind)
        conn.execute(
            subscriptions.update()
            .where(
                subscriptions.c.owner_id == owner,
                subscriptions.c.status == "active",
                subscriptions.c.channels.cast(String).contains(f'"{kind}"'),
            )
            .values(effective_after=local_day())
        )

    def bind_channel(self, owner: str, kind: str, target: str) -> dict[str, Any]:
        if kind != "webhook":
            raise fail("verification_required", "邮箱必须先通过验证码验证")
        try:
            validate_webhook_url(target)
        except DeliveryError as exc:
            raise fail(exc.code, "请填写可访问的公网 HTTPS Webhook 地址") from None
        self._cipher()
        self.limit_target(kind, target)
        # Invalidate the old target before testing the replacement. A failed replacement
        # remains unverified; old queued deliveries cannot escape to the old device.
        self._save_channel(owner, kind, target, False)
        revision = self.get_channel(owner, kind)["revision"]
        try:
            send_webhook(target, "ashare-lab 测试通知", "这是一条测试通知")
        except DeliveryError as exc:
            raise fail(exc.code, "测试发送失败，请检查 Webhook 地址及接收服务", 502) from None
        with self.db.engine.begin() as conn:
            conn.execute(
                channels.update()
                .where(
                    channels.c.owner_id == owner,
                    channels.c.type == kind,
                    channels.c.target_hash == digest(target),
                    channels.c.revision == revision,
                )
                .values(verified=True)
            )
        return {"status": "accepted", "message": "测试通知已发送"}

    def delete_channel(self, owner: str, kind: str) -> dict[str, str]:
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            self._cancel(conn, owner, kind)
            conn.execute(
                channels.update()
                .where(channels.c.owner_id == owner, channels.c.type == kind)
                .values(verified=False, status="disabled", revision=channels.c.revision + 1)
            )
        return {"status": "disabled"}

    def test_channel(self, owner: str, kind: str) -> dict[str, str]:
        channel = self.get_channel(owner, kind)
        if not channel["verified"] or channel["status"] != "active":
            raise fail("verification_required", "请先完成渠道验证", 409)
        target = self._decrypt(channel["ciphertext"])
        self.reserve_test(owner)
        try:
            self._send(
                kind,
                target,
                "ashare-lab 测试通知",
                "这是一条测试通知",
                identifier(),
            )
        except DeliveryError as exc:
            self._transport_failure(owner, kind, channel["revision"], exc)
            raise fail(exc.code, "测试发送失败，请检查渠道和服务配置", 502) from None
        return {"status": "accepted", "message": "测试通知已发送"}

    def send_verification(self, address: str, code: str) -> None:
        if not self.smtp_available():
            raise fail("email_unavailable", "邮件服务尚未启用或配置不可用", 503)
        try:
            send_email(
                self.settings,
                address,
                "ashare-lab 邮箱验证码",
                f"验证码：{code}\n有效期10分钟，最多尝试五次。",
                self.message_id(identifier()),
            )
        except DeliveryError as exc:
            if exc.service_unavailable:
                self._smtp_error(exc.code)
            raise fail(exc.code, "验证码发送失败，请稍后重试", 502) from None

    def list_subscriptions(self, owner: str) -> list[dict[str, Any]]:
        with self.db.engine.connect() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    select(subscriptions)
                    .where(subscriptions.c.owner_id == owner, subscriptions.c.status != "deleted")
                    .order_by(subscriptions.c.created_at)
                ).mappings()
            ]
            snapshot = self.db.snapshot(conn)
        names: dict[str, str | None] = {}
        if rows and "stock_basic" in snapshot:
            codes = list({row["stock_code"] for row in rows})
            for instrument in self.repository.read("stock_basic", snapshot, codes).to_dicts():
                names[instrument["stock_code"]] = (
                    instrument.get("name") or instrument.get("stock_name") or None
                )
        # Display metadata only: history continues using its immutable signal outcome.
        return [{**row, "stock_name": names.get(row["stock_code"])} for row in rows]

    def _subscription(self, owner: str, sid: str, conn: Connection) -> dict[str, Any]:
        row = (
            conn.execute(
                select(subscriptions).where(
                    subscriptions.c.owner_id == owner,
                    subscriptions.c.id == sid,
                    subscriptions.c.status != "deleted",
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise fail("subscription_missing", "订阅不存在", 404)
        return dict(row)

    def _validate_channels(self, owner: str, selected: Sequence[str], conn: Connection) -> None:
        for kind in selected:
            channel = self.get_channel(owner, kind, conn)
            if not channel["verified"] or channel["status"] != "active":
                raise fail("verification_required", "选择的渠道尚未完成验证", 409)

    def save_subscription(self, owner: str, request: SubscriptionRequest) -> dict[str, Any]:
        rows = self.instruments(request.stock_code)["items"]
        if not any(r["stock_code"] == request.stock_code for r in rows):
            raise fail("invalid_instrument", "请选择数据版本中存续的沪深A股")
        values = request.model_dump()
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            self._validate_channels(owner, request.channels, conn)
            existing = (
                conn.execute(
                    select(subscriptions).where(
                        subscriptions.c.owner_id == owner, subscriptions.c.status != "deleted"
                    )
                )
                .mappings()
                .all()
            )
            for row in existing:
                if all(row[key] == value for key, value in values.items()):
                    return dict(row)
            if len(existing) >= self.settings.notification_subscription_limit:
                raise fail("subscription_limit", "已达到订阅数量上限", 409)
            sid = identifier()
            conn.execute(
                subscriptions.insert().values(
                    id=sid,
                    owner_id=owner,
                    **values,
                    strategy_version=strategy_version(request.strategy),
                    status="active",
                    revision=1,
                    effective_after=local_day(),
                    created_at=now(),
                )
            )
            return self._subscription(owner, sid, conn)

    def patch_subscription(self, owner: str, sid: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not patch or set(patch) - {"stock_code", "strategy", "directions", "channels", "status"}:
            raise fail("invalid_patch", "订阅修改字段无效")
        if patch.get("status", "active") not in {"active", "paused"}:
            raise fail("invalid_status", "订阅状态无效")
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            old = self._subscription(owner, sid, conn)
            definition = {
                key: old[key] for key in ("stock_code", "strategy", "directions", "channels")
            }
            definition.update({key: value for key, value in patch.items() if key != "status"})
            try:
                request = SubscriptionRequest.model_validate(definition)
            except ValueError:
                raise fail("invalid_parameters", "订阅策略或渠道参数无效") from None
            if patch.get("status", old["status"]) == "active":
                self._validate_channels(owner, request.channels, conn)
            if request.stock_code != old["stock_code"] and not any(
                r["stock_code"] == request.stock_code
                for r in self.instruments(request.stock_code)["items"]
            ):
                raise fail("invalid_instrument", "请选择存续的沪深A股")
            new = request.model_dump()
            status = patch.get("status", old["status"])
            strategy_changed = any(
                new[key] != old[key] for key in ("stock_code", "strategy", "directions")
            )
            removed = set(old["channels"]) - set(new["channels"])
            added = set(new["channels"]) - set(old["channels"])
            invalidate = strategy_changed or status != old["status"] or bool(added)
            if invalidate:
                self._cancel(conn, owner, subscription_id=sid)
            else:
                for kind in removed:
                    self._cancel(conn, owner, kind, sid)
            conn.execute(
                subscriptions.update()
                .where(subscriptions.c.id == sid)
                .values(
                    **new,
                    status=status,
                    revision=old["revision"] + int(invalidate),
                    strategy_version=strategy_version(request.strategy),
                    effective_after=local_day() if invalidate else old["effective_after"],
                    waiting_reason=None,
                )
            )
            return self._subscription(owner, sid, conn)

    def delete_subscription(self, owner: str, sid: str) -> dict[str, str]:
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            self._subscription(owner, sid, conn)
            self._cancel(conn, owner, subscription_id=sid)
            conn.execute(
                subscriptions.update()
                .where(subscriptions.c.id == sid)
                .values(status="deleted", revision=subscriptions.c.revision + 1)
            )
        return {"status": "deleted"}

    def list_deliveries(
        self, owner: str, channel: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        fields = tuple(deliveries.c.keys())
        formal = (
            select(
                *(deliveries.c[field] for field in fields),
                checks.c.outcome,
                literal(False).label("is_test"),
            )
            .select_from(deliveries.join(checks, deliveries.c.check_id == checks.c.id))
            .where(
                deliveries.c.owner_id == owner,
            )
        )
        samples = select(
            *(
                test_deliveries.c[field]
                if field in test_deliveries.c
                else literal(None).label(field)
                for field in fields
            ),
            test_deliveries.c.outcome,
            literal(True).label("is_test"),
        ).where(test_deliveries.c.owner_id == owner)
        combined = union_all(formal, samples).subquery()
        statement = select(combined)
        if channel:
            statement = statement.where(combined.c.channel == channel)
        with self.db.engine.connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(statement.subquery())
            ).scalar_one()
            rows = conn.execute(
                statement.order_by(combined.c.created_at.desc(), combined.c.id)
                .limit(limit)
                .offset(offset)
            ).mappings()
            items = [dict(row) for row in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def unsubscribe_token(self, subscription: dict[str, Any], channel: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "subscription_id": subscription["id"],
                "owner_id": subscription["owner_id"],
                "channel_revision": channel["revision"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).decode()
        signature = hmac.new(
            self.settings.notification_encryption_key.get_secret_value().encode(),
            b"email-unsubscribe:" + payload,
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded}.{signature}"

    def unsubscribe(self, token: str) -> dict[str, str]:
        self._cipher()
        try:
            encoded, signature = token.split(".")
            payload = base64.urlsafe_b64decode(encoded)
            expected = hmac.new(
                self.settings.notification_encryption_key.get_secret_value().encode(),
                b"email-unsubscribe:" + payload,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
            value = json.loads(payload)
            with self.db.engine.begin() as conn:
                conn.execute(text("BEGIN IMMEDIATE"))
                sub = self._subscription(value["owner_id"], value["subscription_id"], conn)
                channel = self.get_channel(value["owner_id"], "email", conn)
                if channel["revision"] != value["channel_revision"]:
                    raise ValueError()
                selected = [kind for kind in sub["channels"] if kind != "email"]
                self._cancel(conn, sub["owner_id"], "email", sub["id"])
                conn.execute(
                    subscriptions.update()
                    .where(subscriptions.c.id == sub["id"])
                    .values(channels=selected, status=sub["status"] if selected else "paused")
                )
        except (ValueError, KeyError, TypeError, ServiceError):
            raise fail("invalid_unsubscribe", "退订凭据无效或邮箱已变更") from None
        return {"status": "unsubscribed", "message": "已取消此订阅的邮件通知"}

    def record_check(self, sub: dict[str, Any], day: str, outcome: dict[str, Any]) -> None:
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            current_row = (
                conn.execute(
                    select(subscriptions).where(
                        subscriptions.c.owner_id == sub["owner_id"], subscriptions.c.id == sub["id"]
                    )
                )
                .mappings()
                .first()
            )
            if current_row is None:
                return
            current = dict(current_row)
            if current["status"] != "active" or current["revision"] != sub["revision"]:
                return
            # Insufficient data is retryable and not an immutable completed check.
            if outcome["status"] == "insufficient_data":
                conn.execute(
                    subscriptions.update()
                    .where(subscriptions.c.id == sub["id"])
                    .values(last_check=day, waiting_reason=outcome["reason"])
                )
                return
            check_id = identifier()
            result = conn.execute(
                insert(checks)
                .values(
                    id=check_id,
                    subscription_id=sub["id"],
                    revision=sub["revision"],
                    trade_date=day,
                    outcome=outcome,
                    created_at=now(),
                )
                .on_conflict_do_nothing()
            )
            if not result.rowcount:
                return
            conn.execute(
                subscriptions.update()
                .where(subscriptions.c.id == sub["id"])
                .values(last_check=day, waiting_reason=None)
            )
            direction = outcome.get("direction")
            if direction not in current["directions"]:
                return
            for kind in current["channels"]:
                channel = self.get_channel(sub["owner_id"], kind, conn)
                if not channel["verified"] or channel["status"] != "active":
                    continue
                conn.execute(
                    insert(deliveries)
                    .values(
                        id=identifier(),
                        owner_id=sub["owner_id"],
                        subscription_id=sub["id"],
                        check_id=check_id,
                        trade_date=day,
                        direction=direction,
                        channel=kind,
                        revision=sub["revision"],
                        channel_revision=channel["revision"],
                        status="pending",
                        attempts=0,
                        next_attempt=now(),
                        created_at=now(),
                    )
                    .on_conflict_do_nothing()
                )

    def latest_closed_day(self, snapshot: dict[str, str], at: datetime | None = None) -> str | None:
        at = (at or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Shanghai"))
        end = at.date() if at.hour >= 16 else at.date() - timedelta(days=1)
        if "trade_cal" not in snapshot:
            return None
        rows = self.repository.read("trade_cal", snapshot, end_date=end.isoformat()).to_dicts()
        # Never fall back to an earlier day when the calendar itself is stale.
        if not any(r["cal_date"] == end.isoformat() and r.get("exchange") == "SSE" for r in rows):
            return None
        return max(
            (r["cal_date"] for r in rows if r.get("exchange") == "SSE" and r["is_open"] == 1),
            default=None,
        )

    def schedule(self, at: datetime | None = None) -> None:
        if not self.settings.notification_auto_send:
            return
        if (at or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Shanghai")).hour < 16:
            return
        snapshot = {
            key: value
            for key, value in self.db.snapshot().items()
            if key in {"stock_basic", "trade_cal", "daily", "adj_factor", "suspend_d"}
        }
        day = self.latest_closed_day(snapshot, at)
        if day is None:
            with self.db.engine.begin() as conn:
                conn.execute(
                    subscriptions.update()
                    .where(subscriptions.c.status == "active")
                    .values(waiting_reason="交易日历未就绪")
                )
            return
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            conn.execute(
                deliveries.update()
                .where(
                    deliveries.c.trade_date < day, deliveries.c.status.in_(["pending", "sending"])
                )
                .values(status="expired")
            )
        if not self.settings.notification_auto_send or any(
            self.repository.version(v, resolve_coverage=False)["source"] == "fixture"
            for v in snapshot.values()
        ):
            return
        cached: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            active = (
                conn.execute(
                    select(subscriptions).where(
                        subscriptions.c.status == "active", subscriptions.c.effective_after < day
                    )
                )
                .mappings()
                .all()
            )
            for row in active:
                sub = dict(row)
                try:
                    config = StrategyConfig.model_validate(sub["strategy"])
                    compatible = config.id != "buy_hold" and sub[
                        "strategy_version"
                    ] == strategy_version(config)
                except (ValueError, TypeError):
                    compatible = False
                if not compatible:
                    conn.execute(
                        subscriptions.update()
                        .where(subscriptions.c.id == sub["id"])
                        .values(waiting_reason="算法版本不兼容，请更新订阅")
                    )
                    continue
                if conn.execute(
                    select(checks.c.id).where(
                        checks.c.subscription_id == sub["id"],
                        checks.c.revision == sub["revision"],
                        checks.c.trade_date == day,
                    )
                ).first():
                    continue
                payload = {
                    "stock_code": sub["stock_code"],
                    "strategy": sub["strategy"],
                    "trade_date": day,
                    "strategy_version": sub["strategy_version"],
                }
                key = digest(json.dumps({**payload, "data_versions": snapshot}, sort_keys=True))
                pinned = {**snapshot, "_signal_key": key}
                matching = (
                    conn.execute(
                        select(jobs).where(
                            jobs.c.kind == "notification_signal",
                            jobs.c.snapshot["_signal_key"].as_string() == key,
                        )
                    )
                    .mappings()
                    .all()
                )
                same = [job for job in matching if job["snapshot"].get("_signal_key") == key]
                if any(job["status"] in {"queued", "running"} for job in same):
                    continue
                completed = next((job for job in same if job["status"] == "succeeded"), None)
                if completed:
                    # Outcome is immutable; finish missing fan-out after a crash.
                    cached.append((sub, day, completed["result"]))
                    continue
                conn.execute(
                    jobs.insert().values(
                        id=identifier(),
                        kind="notification_signal",
                        status="queued",
                        payload=payload,
                        snapshot=pinned,
                        progress=0,
                        cancel_requested=False,
                        created_at=now(),
                        updated_at=now(),
                        attempt=0,
                    )
                )

        for sub, signal_day, outcome in cached:
            self.record_check(sub, signal_day, outcome)

    def execute_signal(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job["payload"]
        config = StrategyConfig.model_validate(payload["strategy"])
        if payload["strategy_version"] != strategy_version(config):
            raise fail("incompatible_strategy", "通知策略版本不兼容")
        pinned = {k: v for k, v in job["snapshot"].items() if not k.startswith("_")}
        latest = self.latest_closed_day(self.db.snapshot())
        if latest != payload["trade_date"]:
            return {
                "status": "insufficient_data" if latest is None else "expired",
                "direction": None,
                "reason": "交易日历未就绪" if latest is None else "信号日期已过期",
                "data_versions": pinned,
                "strategy_version": payload["strategy_version"],
            }
        outcome = calculate_signal(
            self.repository,
            payload["stock_code"],
            config,
            payload["trade_date"],
            pinned,
            payload["strategy_version"],
        )
        with self.db.engine.connect() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    select(subscriptions).where(
                        subscriptions.c.stock_code == payload["stock_code"],
                        subscriptions.c.status == "active",
                        subscriptions.c.effective_after < payload["trade_date"],
                    )
                ).mappings()
            ]
        for sub in rows:
            if (
                sub["strategy"] == payload["strategy"]
                and sub["strategy_version"] == payload["strategy_version"]
            ):
                self.record_check(sub, payload["trade_date"], outcome)
        return outcome

    def recover_deliveries(self) -> None:
        # A crash after remote acceptance is ambiguous. Retrying may duplicate remotely.
        with self.db.engine.begin() as conn:
            conn.execute(
                deliveries.update()
                .where(deliveries.c.status == "sending")
                .values(status="pending", error="interrupted_delivery")
            )

    def message_id(self, delivery_id: str) -> str:
        # Independent of SMTP configuration so a corrected sender cannot change a retry ID.
        return f"<ashare-{delivery_id}@notifications.ashare-lab.invalid>"

    def _send(
        self,
        kind: str,
        target: str,
        title: str,
        body: str,
        delivery_id: str,
        url: str | None = None,
    ) -> None:
        self._cipher()
        if kind == "webhook":
            send_webhook(target, title, body)
        elif kind == "email":
            if not self.smtp_available():
                raise DeliveryError("email_unavailable", temporary=True, service_unavailable=True)
            send_email(self.settings, target, title, body, self.message_id(delivery_id), url)
        else:
            raise DeliveryError("channel_disabled", disable_channel=True)

    def _smtp_fingerprint(self) -> str:
        return digest(
            json.dumps(
                [
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    self.settings.smtp_security,
                    self.settings.smtp_username,
                    self.settings.smtp_password.get_secret_value(),
                    self.settings.smtp_from_email,
                ]
            )
        )

    def _smtp_error(self, code: str) -> None:
        code = self._smtp_fingerprint() + ":" + code
        with self.db.engine.begin() as conn:
            conn.execute(
                insert(service_state)
                .values(key="smtp_error", value=code)
                .on_conflict_do_update(index_elements=[service_state.c.key], set_={"value": code})
            )

    def _transport_failure(self, owner: str, kind: str, revision: int, exc: DeliveryError) -> None:
        if exc.service_unavailable:
            self._smtp_error(exc.code)
        if exc.disable_channel:
            with self.db.engine.begin() as conn:
                result = conn.execute(
                    channels.update()
                    .where(
                        channels.c.owner_id == owner,
                        channels.c.type == kind,
                        channels.c.revision == revision,
                    )
                    .values(status="disabled")
                )
                if result.rowcount:
                    self._cancel(conn, owner, kind)

    def send_once(self, kind: str) -> bool:
        if not self.settings.notification_auto_send or self.cipher is None:
            return False
        snapshot = self.db.snapshot()
        latest = self.latest_closed_day(snapshot)
        if latest is None or any(
            self.repository.version(v, resolve_coverage=False)["source"] == "fixture"
            for v in snapshot.values()
        ):
            return False
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            row = (
                conn.execute(
                    select(deliveries)
                    .where(
                        deliveries.c.channel == kind,
                        deliveries.c.status == "pending",
                        deliveries.c.next_attempt <= now(),
                    )
                    .order_by(deliveries.c.created_at)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            delivery = dict(row)
            sub_row = (
                conn.execute(
                    select(subscriptions).where(subscriptions.c.id == delivery["subscription_id"])
                )
                .mappings()
                .one()
            )
            sub = dict(sub_row)
            channel = self.get_channel(delivery["owner_id"], kind, conn)
            invalid = (
                sub["status"] != "active"
                or sub["revision"] != delivery["revision"]
                or kind not in sub["channels"]
                or not channel["verified"]
                or channel["status"] != "active"
                or channel["revision"] != delivery["channel_revision"]
            )
            if invalid or delivery["trade_date"] < latest or delivery["attempts"] >= 5:
                conn.execute(
                    deliveries.update()
                    .where(deliveries.c.id == delivery["id"])
                    .values(
                        status="cancelled"
                        if invalid
                        else "expired"
                        if delivery["trade_date"] < latest
                        else "failed"
                    )
                )
                return True
            outcome: dict[str, Any] = conn.execute(
                select(checks.c.outcome).where(checks.c.id == delivery["check_id"])
            ).scalar_one()
            if any(
                self.repository.version(v, resolve_coverage=False)["source"] == "fixture"
                for v in outcome["data_versions"].values()
            ):
                conn.execute(
                    deliveries.update()
                    .where(deliveries.c.id == delivery["id"])
                    .values(status="cancelled", error="fixture_disabled")
                )
                return True
            conn.execute(
                deliveries.update()
                .where(deliveries.c.id == delivery["id"])
                .values(status="sending", attempts=delivery["attempts"] + 1)
            )
            channel = dict(channel)
        # Database write transaction is closed before all network calls.
        url = (
            self.settings.notification_public_url.rstrip("/")
            + "/notifications/unsubscribe#"
            + self.unsubscribe_token(dict(sub), channel)
            if kind == "email"
            else None
        )
        title, body = self.signal_message(
            sub, outcome, delivery["trade_date"], delivery["direction"]
        )
        try:
            target = self._decrypt(channel["ciphertext"])
            self._send(kind, target, title, body, delivery["id"], url)
        except ServiceError:
            exc = DeliveryError("encryption_unavailable", service_unavailable=False)
            self._finish_failure(delivery, channel, exc)
        except DeliveryError as exc:
            self._finish_failure(delivery, channel, exc)
        except Exception:
            self._finish_failure(delivery, channel, DeliveryError("internal_delivery_error"))
        else:
            with self.db.engine.begin() as conn:
                conn.execute(
                    deliveries.update()
                    .where(deliveries.c.id == delivery["id"], deliveries.c.status == "sending")
                    .values(status="accepted", accepted_at=now(), error=None)
                )
        return True

    def _finish_failure(
        self, delivery: dict[str, Any], channel: dict[str, Any], exc: DeliveryError
    ) -> None:
        attempt = delivery["attempts"] + 1
        retry = exc.temporary and attempt < 5
        delay = max([60, 300, 900, 3600][min(attempt - 1, 3)], exc.retry_after)
        with self.db.engine.begin() as conn:
            conn.execute(
                deliveries.update()
                .where(deliveries.c.id == delivery["id"], deliveries.c.status == "sending")
                .values(
                    status="pending" if retry else "failed",
                    error=exc.code,
                    next_attempt=(datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
                )
            )
        self._transport_failure(delivery["owner_id"], delivery["channel"], channel["revision"], exc)
