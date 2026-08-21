import importlib


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
