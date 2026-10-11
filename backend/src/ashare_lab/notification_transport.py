"""Public HTTPS webhooks and TLS SMTP. Never expose targets or upstream errors."""

import html
import ipaddress
import json
import smtplib
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr, parsedate_to_datetime
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlsplit

from ashare_lab.config import Settings


@dataclass
class DeliveryError(Exception):
    code: str
    temporary: bool = False
    disable_channel: bool = False
    service_unavailable: bool = False
    retry_after: int = 0


def _public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.is_site_local:
            return False
        embedded = address.ipv4_mapped or address.sixtofour
        if embedded is not None and not _public_address(embedded):
            return False
    return True


def validate_webhook_url(target: str) -> tuple[str, int, str, str]:
    """Resolve all addresses, reject non-public destinations, return a pinned address.

    DNS is checked again at send time. The HTTPS connection never resolves the hostname
    itself, while SNI/certificate verification and Host retain the original hostname.
    """
    try:
        if not target or len(target) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in target):
            raise ValueError()
        url = urlsplit(target)
        host = url.hostname
        if (
            url.scheme != "https"
            or not host
            or url.username is not None
            or url.password is not None
            or "#" in target
            or "\\" in target
        ):
            raise ValueError()
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
        if not host:
            raise ValueError()
        port = 443 if url.port is None else url.port
        if not 1 <= port <= 65535:
            raise ValueError()
        # Check literal addresses before DNS: a stubbed resolver must not conceal them.
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not _public_address(literal):
            raise ValueError()
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise OSError()
        for entry in addresses:
            address = ipaddress.ip_address(entry[4][0])
            if not _public_address(address):
                raise ValueError()
        path = url.path or "/"
        if url.query:
            path += "?" + url.query
        # Encode non-ASCII paths before the HTTP layer without changing existing escapes.
        from urllib.parse import quote

        path = quote(path, safe="/%?=&:+,;@!$'()*~-._")
        return host, port, path, str(addresses[0][4][0])
    except (ValueError, UnicodeError):
        raise DeliveryError("invalid_webhook_url", disable_channel=True) from None
    except OSError:
        raise DeliveryError("network_error", temporary=True) from None


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, hostname: str, port: int, address: str):
        self.tls_context = ssl.create_default_context()
        super().__init__(hostname, port, timeout=15, context=self.tls_context)
        self.address = address

    def connect(self) -> None:
        connection = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self.tls_context.wrap_socket(connection, server_hostname=self.host)
        except Exception:
            connection.close()
            raise


def _retry_after(raw: str) -> int:
    try:
        return max(0, int(raw))
    except ValueError:
        try:
            return max(0, int((parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            return 0


def send_webhook(target: str, title: str, body: str) -> None:
    host, port, path, address = validate_webhook_url(target)
    connection = _PinnedHTTPSConnection(host, port, address)
    try:
        connection.request(
            "POST",
            path,
            body=json.dumps(
                {"title": title, "body": body, "group": "ashare-lab"}, ensure_ascii=False
            ).encode(),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        response = connection.getresponse()
        if response.status == 429:
            raise DeliveryError(
                "rate_limited",
                temporary=True,
                retry_after=_retry_after(response.getheader("Retry-After", "")),
            )
        if response.status >= 500:
            raise DeliveryError("webhook_unavailable", temporary=True)
        if not 200 <= response.status < 300:
            raise DeliveryError(
                "webhook_rejected", disable_channel=response.status in {400, 401, 403, 404, 410}
            )
        # Generic webhooks define acceptance through HTTP. Bark's official endpoint
        # additionally reports credential errors inside an HTTP-200 response.
        if host == "api.day.app":
            try:
                code = json.loads(response.read(65537)).get("code")
            except (ValueError, AttributeError):
                raise DeliveryError("webhook_invalid_response", temporary=True) from None
            if not isinstance(code, int):
                raise DeliveryError("webhook_invalid_response", temporary=True)
            if code != 200:
                raise DeliveryError(
                    "webhook_rejected",
                    temporary=code in {429, 500, 503},
                    disable_channel=code in {400, 401, 403, 404},
                )
    except (OSError, HTTPException, UnicodeError):
        raise DeliveryError("network_error", temporary=True) from None
    finally:
        connection.close()


def send_email(
    settings: Settings,
    recipient: str,
    title: str,
    body: str,
    message_id: str,
    unsubscribe_url: str | None = None,
) -> None:
    message = EmailMessage()
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    message["To"] = recipient
    message["Subject"] = title
    message["Message-ID"] = message_id
    text = body
    rich = f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>"
    if unsubscribe_url:
        text += f"\n\n退订此订阅的邮件通知（需确认）：{unsubscribe_url}"
        rich += f'<p><a href="{html.escape(unsubscribe_url, quote=True)}">退订此订阅的邮件通知</a>（需确认）</p>'
    message.set_content(text)
    message.add_alternative(rich, subtype="html")
    try:
        context = ssl.create_default_context()
        client = (
            smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=context)
            if settings.smtp_security == "ssl"
            else smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
        )
        with client:
            if settings.smtp_security == "starttls":
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            refused = client.send_message(
                message, from_addr=settings.smtp_from_email, to_addrs=[recipient]
            )
            if refused:
                code = refused.get(recipient, (550, b""))[0]
                raise DeliveryError(
                    "smtp_recipient_rejected",
                    temporary=400 <= code < 500,
                    disable_channel=code >= 500,
                )
    except smtplib.SMTPRecipientsRefused as exc:
        codes = [item[0] for item in exc.recipients.values()]
        temporary = bool(codes) and all(400 <= code < 500 for code in codes)
        raise DeliveryError(
            "smtp_recipient_rejected", temporary=temporary, disable_channel=not temporary
        ) from None
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused):
        raise DeliveryError("smtp_configuration_error", service_unavailable=True) from None
    except (ssl.SSLError, smtplib.SMTPNotSupportedError):
        raise DeliveryError("smtp_tls_error", service_unavailable=True) from None
    except smtplib.SMTPResponseException as exc:
        raise DeliveryError("smtp_rejected", temporary=400 <= exc.smtp_code < 500) from None
    except (OSError, smtplib.SMTPServerDisconnected):
        raise DeliveryError("network_error", temporary=True) from None
    except smtplib.SMTPException:
        raise DeliveryError("smtp_protocol_error", temporary=True) from None
