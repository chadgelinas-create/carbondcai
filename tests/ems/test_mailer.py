from pathlib import Path
from types import SimpleNamespace

from skid2.ems.mailer import invite_url, send_authorization_email


def test_invite_url_opens_the_login_page() -> None:
    settings = SimpleNamespace(
        public_app_url="http://127.0.0.1:5174",
        browser_origin="http://localhost:5173",
    )
    assert invite_url(settings, "invite-token") == "http://127.0.0.1:5174/login?invite=invite-token"


def test_reset_email_uses_the_same_login_link(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        public_app_url="http://127.0.0.1:5174",
        browser_origin="http://localhost:5173",
        smtp_from="Skid 2 EMS <noreply@carbondc.io>",
        mail_dir=tmp_path,
        smtp_host="",
    )
    result = send_authorization_email(
        settings,
        to_address="op@carbondc.local",
        display_name="Plant Operator",
        token="reset-token",
        purpose="password_change",
    )
    stored = Path(result.stored_path).read_text(encoding="utf-8")
    assert "Reset your Carbon DC Skid 2 password" in stored
    assert "Open Skid 2 login" in stored
    assert "http://127.0.0.1:5174/login?invite=reset-token" in stored
