"""Offline MED-020 notification and systemd wiring regressions."""
import configparser
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from services import backup_failure, db, telegram_service


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_EVENTS_ENABLED", "true")
    monkeypatch.setattr(telegram_service, "_standalone_delivery_allowed", lambda kind: kind == "ops_alert")
    monkeypatch.setattr(telegram_service, "_has_token", lambda: True)
    monkeypatch.setattr(telegram_service, "_default_chat_id", lambda: "offline-chat")
    monkeypatch.setattr(telegram_service, "_api_url", lambda method: "https://offline.invalid/" + method)
    monkeypatch.setattr(telegram_service, "_should_skip_outbound", AsyncMock(side_effect=AssertionError("DB guard used")))
    monkeypatch.setattr(db, "get_db", lambda: pytest.fail("Notifier must not read/write a database"))


@pytest.mark.asyncio
@pytest.mark.parametrize("response,sent", [
    (httpx.Response(200, json={"ok": True}), True),
    (httpx.Response(500, json={"ok": False}), False),
    (httpx.Response(200, json={"ok": False}), False),
    (httpx.Response(200, text="invalid-json"), False),
    (httpx.ReadTimeout("offline-secret-bearing-url"), False),
])
async def test_fixed_backup_notice_attempts_once_without_database(configured, monkeypatch, capsys, response, sent):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 5.0}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(backup_failure.httpx, "AsyncClient", Client)
    result = await backup_failure.notify()
    assert result == {"attempted": True, "sent": sent,
                      "reason": "sent" if sent else "delivery_unverified"}
    assert len(calls) == 1
    assert calls[0][0] == "https://offline.invalid/sendMessage"
    payload = calls[0][1]["json"]
    assert payload["text"].startswith("CASE CAPITAL | POSTGRESQL BACKUP FAILED")
    assert "NOT VERIFIED" in payload["text"]
    assert "offline-secret" not in str(result) + capsys.readouterr().out
    telegram_service._should_skip_outbound.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked,reason", [
    ("events", "disabled"), ("policy", "disabled"),
    ("token", "not_configured"), ("chat", "not_configured"),
])
async def test_disabled_or_unconfigured_notice_never_opens_http(configured, monkeypatch, blocked, reason):
    if blocked == "events":
        monkeypatch.setenv("TELEGRAM_EVENTS_ENABLED", "false")
    elif blocked == "policy":
        monkeypatch.setattr(telegram_service, "_standalone_delivery_allowed", lambda kind: False)
    elif blocked == "token":
        monkeypatch.setattr(telegram_service, "_has_token", lambda: False)
    else:
        monkeypatch.setattr(telegram_service, "_default_chat_id", lambda: "")
    monkeypatch.setattr(backup_failure.httpx, "AsyncClient", lambda **kwargs: pytest.fail("HTTP opened"))
    assert await backup_failure.notify() == {"attempted": False, "sent": False, "reason": reason}


@pytest.mark.parametrize("reason,attempted,sent,exit_code", [
    ("sent", True, True, 0), ("disabled", False, False, 0),
    ("not_configured", False, False, 1), ("delivery_unverified", True, False, 1),
])
def test_cli_reports_only_fixed_outcome(configured, monkeypatch, capsys, reason, attempted, sent, exit_code):
    result = {"attempted": attempted, "sent": sent, "reason": reason}
    sender = AsyncMock(return_value=result)
    monkeypatch.setattr(backup_failure, "notify", sender)
    assert backup_failure.main() == exit_code
    assert json.loads(capsys.readouterr().out) == result
    sender.assert_awaited_once()


def test_failed_backup_unit_invokes_installed_non_restarting_notice():
    root = Path(__file__).resolve().parents[2]
    deploy = root / "deploy" / "vps"
    backup = configparser.ConfigParser()
    backup.read(deploy / "case-capital-postgres-backup.service")
    handler = backup["Unit"]["OnFailure"]
    notice = configparser.ConfigParser()
    notice.read(deploy / handler)
    assert notice["Service"]["Type"] == "oneshot"
    assert notice["Service"]["Restart"] == "no"
    assert notice["Service"]["ExecStart"].endswith(" -m services.backup_failure")
    assert notice["Service"]["WorkingDirectory"].endswith("/backend")
    assert notice["Service"]["EnvironmentFile"].startswith("-")
    assert handler in (deploy / "install-operational-controls.sh").read_text()
