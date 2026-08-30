import importlib
import asyncio


def test_signed_operator_token_survives_memory_session_clear(monkeypatch):
    monkeypatch.setenv("TERMINAL_ACCESS_CODE", "123412")
    monkeypatch.delenv("TERMINAL_SESSION_SECRET", raising=False)

    import server

    importlib.reload(server)
    token = server._issue_operator_token()

    assert token.startswith("cc1.")
    assert token not in server.OPERATOR_SESSIONS
    assert server._valid_signed_operator_token(token) is True

    server.OPERATOR_SESSIONS.clear()
    assert server._valid_signed_operator_token(token) is True


def test_signed_operator_token_rejects_tampering(monkeypatch):
    monkeypatch.setenv("TERMINAL_ACCESS_CODE", "123412")

    import server

    importlib.reload(server)
    token = server._issue_operator_token()
    forged = token[:-1] + ("0" if token[-1] != "0" else "1")

    assert server._valid_signed_operator_token(token) is True
    assert server._valid_signed_operator_token(forged) is False


def test_preview_endpoint_requires_valid_code(monkeypatch):
    monkeypatch.setenv("PREVIEW_ENABLED", "true")
    import server

    importlib.reload(server)
    good = server.AuthPreviewRequest(code="6969")
    bad = server.AuthPreviewRequest(code="000000")
    assert asyncio.run(server.auth_preview(good))["mode"] == "preview"
    try:
        asyncio.run(server.auth_preview(bad))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("invalid preview code was accepted")


def test_preview_endpoint_empty_body_is_an_auth_failure(monkeypatch):
    monkeypatch.setenv("PREVIEW_ENABLED", "true")
    import server

    importlib.reload(server)
    try:
        asyncio.run(server.auth_preview(None))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("empty preview request was accepted")


def test_preview_endpoint_empty_json_model_is_an_auth_failure(monkeypatch):
    monkeypatch.setenv("PREVIEW_ENABLED", "true")
    import server

    importlib.reload(server)
    try:
        asyncio.run(server.auth_preview(server.AuthPreviewRequest()))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("empty preview payload was accepted")
