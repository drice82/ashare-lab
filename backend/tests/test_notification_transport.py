import smtplib
import ssl
from email.message import EmailMessage

import pytest
from ashare_lab.config import Settings
from ashare_lab.notification_transport import DeliveryError, send_email


@pytest.mark.parametrize(
    "error,temporary,disabled,unavailable",
    [
        (
            smtplib.SMTPRecipientsRefused({"test@example.org": (450, b"raw upstream error")}),
            True,
            False,
            False,
        ),
        (
            smtplib.SMTPRecipientsRefused({"test@example.org": (550, b"raw upstream error")}),
            False,
            True,
            False,
        ),
        (smtplib.SMTPAuthenticationError(535, b"raw upstream error"), False, False, True),
        (ssl.SSLError("raw upstream error"), False, False, True),
        (TimeoutError("raw upstream error"), True, False, False),
    ],
)
def test_smtp_errors_are_sanitized(monkeypatch, error, temporary, disabled, unavailable):
    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            assert isinstance(kwargs["context"], ssl.SSLContext)
            raise error

        def send_message(self, *args, **kwargs):
            pytest.fail("must never downgrade to plaintext")

    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    with pytest.raises(DeliveryError) as caught:
        send_email(Settings(_env_file=None), "test@example.org", "title", "body", "<stable@local>")
    assert caught.value.temporary is temporary
    assert caught.value.disable_channel is disabled
    assert caught.value.service_unavailable is unavailable
    assert "raw upstream error" not in str(caught.value)


def test_smtp_multipart_single_recipient_and_stable_id(monkeypatch):
    messages = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def send_message(self, message: EmailMessage, **kwargs):
            assert kwargs["to_addrs"] == ["test@example.org"]
            messages.append(message)
            return {}

    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    for _ in range(2):
        send_email(
            Settings(_env_file=None, smtp_from_email="sender@example.org"),
            "test@example.org",
            "信号",
            "<signal>",
            "<stable@example.org>",
            "https://example.org/notifications/unsubscribe#credential",
        )
    assert messages[0]["Message-ID"] == messages[1]["Message-ID"]
    assert messages[0].get_body(preferencelist=("plain",)) is not None
    assert "&lt;signal&gt;" in messages[0].get_body(preferencelist=("html",)).get_content()
