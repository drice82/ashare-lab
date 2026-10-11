"""Passwordless accounts, independent sessions and purpose-bound one-use codes."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection

from ashare_lab.db import identifier, now
from ashare_lab.notification_models import accounts, auth_challenges, channels, owners, sessions
from ashare_lab.services import ServiceError

if TYPE_CHECKING:
    from ashare_lab.notifications import NotificationService

Purpose = Literal["login", "change_email"]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_email(address: str) -> str:
    address = address.strip().casefold()
    if len(address) > 254 or not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", address
    ):
        raise ServiceError(422, "invalid_email", "邮箱地址格式无效")
    return address


def masked_email(address: str) -> str:
    return address[:1] + "***@" + address.split("@")[-1]


class NotificationAuth:
    def __init__(self, service: "NotificationService"):
        self.service = service
        self.db = service.db

    def _code_hash(self, key: str, code: str) -> str:
        return hmac.new(
            self.service.settings.notification_encryption_key.get_secret_value().encode(),
            f"notification-auth:{key}:{code}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _key(self, purpose: Purpose, owner: str | None, address: str) -> str:
        return digest(f"{purpose}:{owner or ''}:{address}")

    def start(
        self,
        address: str,
        purpose: Purpose = "login",
        owner: str | None = None,
        limited: bool = True,
    ) -> dict[str, Any]:
        address = normalize_email(address)
        self.service._cipher()
        if owner is not None:
            self.email(owner)
        if limited:
            self.service.limit_target("email", address)
        key = self._key(purpose, owner, address)
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            conn.execute(delete(auth_challenges).where(auth_challenges.c.expires_at <= now()))
            old = conn.execute(
                select(auth_challenges.c.code_hash).where(auth_challenges.c.key == key)
            ).scalar()
            code = f"{secrets.randbelow(1000000):06d}"
            while old and hmac.compare_digest(old, self._code_hash(key, code)):
                code = f"{secrets.randbelow(1000000):06d}"
            values = {
                "purpose": purpose,
                "owner_id": owner,
                "email_hash": digest(address),
                "code_hash": self._code_hash(key, code),
                "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                "attempts": 0,
            }
            conn.execute(
                insert(auth_challenges)
                .values(key=key, **values)
                .on_conflict_do_update(
                    index_elements=[auth_challenges.c.key],
                    set_=values,
                )
            )
        try:
            self.service.send_verification(address, code)
        except ServiceError:
            with self.db.engine.begin() as conn:
                conn.execute(
                    delete(auth_challenges).where(
                        auth_challenges.c.key == key,
                        auth_challenges.c.code_hash == values["code_hash"],
                    )
                )
            raise
        return {
            "status": "accepted",
            "message": "服务端已接受验证码邮件，请查看邮箱",
            "expires_in": 600,
            "resend_after": 60,
        }

    def _consume(
        self, conn: Connection, purpose: Purpose, owner: str | None, address: str, code: str
    ) -> bool:
        key = self._key(purpose, owner, address)
        row = (
            conn.execute(select(auth_challenges).where(auth_challenges.c.key == key))
            .mappings()
            .first()
        )
        if not row or row["expires_at"] <= now() or row["attempts"] >= 5:
            return False
        valid = hmac.compare_digest(row["code_hash"], self._code_hash(key, code))
        if valid:
            conn.execute(delete(auth_challenges).where(auth_challenges.c.key == key))
        else:
            conn.execute(
                auth_challenges.update()
                .where(auth_challenges.c.key == key)
                .values(
                    attempts=row["attempts"] + 1,
                )
            )
        return valid

    def _invalid_code(self) -> ServiceError:
        return ServiceError(422, "invalid_verification", "验证码无效、已过期或超过尝试次数")

    def _legacy_owner(self, conn: Connection, address: str) -> str | None:
        rows = (
            conn.execute(
                select(channels)
                .outerjoin(accounts, channels.c.owner_id == accounts.c.owner_id)
                .where(
                    channels.c.type == "email",
                    channels.c.verified.is_(True),
                    accounts.c.owner_id.is_(None),
                )
            )
            .mappings()
            .all()
        )
        candidates = []
        for row in rows:
            target = normalize_email(self.service._decrypt(row["ciphertext"]))
            if target == address:
                candidates.append(str(row["owner_id"]))
        if len(candidates) > 1:
            raise ServiceError(
                409, "legacy_email_conflict", "该邮箱关联多个旧身份，请联系管理员处理"
            )
        return candidates[0] if candidates else None

    def _session(self, conn: Connection, owner: str, address: str) -> dict[str, str]:
        token = secrets.token_urlsafe(32)
        expires = (
            datetime.now(UTC) + timedelta(days=self.service.settings.notification_session_days)
        ).isoformat()
        conn.execute(delete(sessions).where(sessions.c.expires_at <= now()))
        conn.execute(
            sessions.insert().values(session_hash=digest(token), owner_id=owner, expires_at=expires)
        )
        return {
            "owner_id": owner,
            "email": masked_email(address),
            "expires_at": expires,
            "session": token,
        }

    def login(self, address: str, code: str) -> dict[str, str]:
        address = normalize_email(address)
        result = None
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            if self._consume(conn, "login", None, address, code):
                owner = conn.execute(
                    select(accounts.c.owner_id).where(accounts.c.email_hash == digest(address))
                ).scalar()
                if owner is None:
                    owner = self._legacy_owner(conn, address)
                    if owner is None:
                        owner = identifier()
                        conn.execute(
                            owners.insert().values(
                                id=owner,
                                session_hash=f"disabled-session:{owner}",
                                recovery_hash=f"disabled-recovery:{owner}",
                                expires_at="1970-01-01T00:00:00+00:00",
                            )
                        )
                        self.service._save_channel(owner, "email", address, True, conn)
                    conn.execute(
                        accounts.insert().values(
                            owner_id=owner,
                            email_hash=digest(address),
                            ciphertext=self.service._encrypt(address),
                        )
                    )
                result = self._session(conn, str(owner), address)
        if result is None:
            raise self._invalid_code()
        return result

    def authenticate(self, token: str) -> str:
        with self.db.engine.connect() as conn:
            owner = conn.execute(
                select(sessions.c.owner_id)
                .join(accounts, sessions.c.owner_id == accounts.c.owner_id)
                .where(
                    sessions.c.session_hash == digest(token),
                    sessions.c.expires_at > now(),
                )
            ).scalar()
        if owner is None:
            raise ServiceError(
                401, "notification_unauthorized", "登录已过期或尚未登录，请使用邮箱验证码登录"
            )
        return str(owner)

    def email(self, owner: str, conn: Connection | None = None) -> str:
        if conn is None:
            with self.db.engine.connect() as connection:
                return self.email(owner, connection)
        ciphertext = conn.execute(
            select(accounts.c.ciphertext).where(accounts.c.owner_id == owner)
        ).scalar()
        if ciphertext is None:
            raise ServiceError(401, "notification_unauthorized", "请使用邮箱验证码登录")
        return self.service._decrypt(ciphertext)

    def session_info(self, token: str) -> dict[str, str]:
        with self.db.engine.connect() as conn:
            row = (
                conn.execute(
                    select(sessions.c.owner_id, sessions.c.expires_at, accounts.c.ciphertext)
                    .join(accounts, sessions.c.owner_id == accounts.c.owner_id)
                    .where(sessions.c.session_hash == digest(token), sessions.c.expires_at > now())
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ServiceError(401, "notification_unauthorized", "请使用邮箱验证码登录")
        return {
            "owner_id": str(row["owner_id"]),
            "email": masked_email(self.service._decrypt(row["ciphertext"])),
            "expires_at": str(row["expires_at"]),
        }

    def logout(self, token: str) -> None:
        with self.db.engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.session_hash == digest(token)))

    def change_email(self, owner: str, address: str, code: str) -> dict[str, str]:
        address = normalize_email(address)
        result = None
        with self.db.engine.begin() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            old_address = self.email(owner, conn)
            if self._consume(conn, "change_email", owner, address, code):
                existing = conn.execute(
                    select(accounts.c.owner_id).where(accounts.c.email_hash == digest(address))
                ).scalar()
                legacy_owner = self._legacy_owner(conn, address) if existing is None else None
                if (existing is not None and existing != owner) or legacy_owner is not None:
                    raise ServiceError(
                        409, "email_in_use", "该邮箱已有账号或旧身份，请使用其他邮箱"
                    )
                if address != old_address:
                    old_channel = (
                        conn.execute(
                            select(channels).where(
                                channels.c.owner_id == owner, channels.c.type == "email"
                            )
                        )
                        .mappings()
                        .first()
                    )
                    conn.execute(
                        accounts.update()
                        .where(accounts.c.owner_id == owner)
                        .values(
                            email_hash=digest(address),
                            ciphertext=self.service._encrypt(address),
                        )
                    )
                    self.service._save_channel(owner, "email", address, True, conn)
                    if old_channel and old_channel["status"] != "active":
                        conn.execute(
                            channels.update()
                            .where(channels.c.owner_id == owner, channels.c.type == "email")
                            .values(status=old_channel["status"])
                        )
                    conn.execute(
                        delete(auth_challenges).where(
                            (auth_challenges.c.owner_id == owner)
                            | (auth_challenges.c.email_hash == digest(old_address))
                            | (auth_challenges.c.email_hash == digest(address)),
                        )
                    )
                conn.execute(delete(sessions).where(sessions.c.owner_id == owner))
                result = self._session(conn, owner, address)
        if result is None:
            raise self._invalid_code()
        return result
