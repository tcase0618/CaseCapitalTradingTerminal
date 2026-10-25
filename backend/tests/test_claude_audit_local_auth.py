"""Exercise the real authentication middleware around harmless ASGI handlers."""
import asyncio
import importlib

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request


@pytest.fixture
def local_server(monkeypatch):
    # Importing the API must not load deployment credentials or run lifespan.
    import dotenv
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *args, **kwargs: False)
    server = importlib.import_module('server')
    monkeypatch.setenv('APP_ENV', 'local')
    for flag in ('ENABLE_OPTIONS_EXECUTION', 'ENABLE_TRADE_EXECUTION', 'ENABLE_SCHEDULER'):
        monkeypatch.setenv(flag, 'false')
    for key in ('TERMINAL_ACCESS_CODE', 'TERMINAL_ACCESS_CODE_HASH', 'TERMINAL_SESSION_SECRET', 'CORS_ORIGINS'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(server, 'OPERATOR_SESSIONS', set())
    monkeypatch.setattr(server, 'AUTH_ATTEMPTS', {})
    return server


def request_through_gate(server, *, token=None, method='POST', path='/api/options_desk/auto_execute_latest'):
    app = FastAPI()
    reached = []
    app.middleware('http')(server.cloud_operator_write_gate)

    @app.api_route(path, methods=[method])
    async def harmless_handler():
        reached.append(True)
        return {'fixture': True}

    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test.invalid') as client:
            headers = {'Origin': 'http://untrusted.invalid'}
            if token:
                headers['Authorization'] = f'Bearer {token}'
            return await client.request(method, path, headers=headers)

    return asyncio.run(send()), reached


@pytest.mark.parametrize('flag', ['ENABLE_OPTIONS_EXECUTION', 'ENABLE_TRADE_EXECUTION', 'ENABLE_SCHEDULER'])
@pytest.mark.parametrize('legacy_token', [False, True])
def test_sensitive_local_runtime_rejects_unconfigured_access(local_server, monkeypatch, flag, legacy_token):
    monkeypatch.setenv(flag, 'true')
    token = 'offline-legacy-session' if legacy_token else None
    if token:
        local_server.OPERATOR_SESSIONS.add(token)
    response, reached = request_through_gate(local_server, token=token)
    assert response.status_code == 403
    assert not reached


def test_scheduler_default_requires_operator_configuration(local_server, monkeypatch):
    monkeypatch.delenv('ENABLE_SCHEDULER')
    response, reached = request_through_gate(local_server)
    assert response.status_code == 403
    assert not reached


def test_enabled_local_runtime_does_not_mint_arbitrary_login_tokens(local_server, monkeypatch):
    monkeypatch.setenv('ENABLE_OPTIONS_EXECUTION', 'true')
    request = Request({'type': 'http', 'client': ('offline-fixture', 1234), 'headers': []})
    with pytest.raises(Exception) as error:
        asyncio.run(local_server.auth_login(request, local_server.AuthLoginRequest(code='offline-arbitrary')))
    assert getattr(error.value, 'status_code', None) == 403
    assert not local_server.OPERATOR_SESSIONS


def test_auth_configuration_reports_missing_local_login(local_server, monkeypatch):
    monkeypatch.setenv('ENABLE_SCHEDULER', 'true')
    assert asyncio.run(local_server.auth_config())['operator_login_enabled'] is False
    assert local_server._cors_origins() == []


def test_configured_local_operator_can_authenticate(local_server, monkeypatch):
    monkeypatch.setenv('ENABLE_OPTIONS_EXECUTION', 'true')
    monkeypatch.setenv('TERMINAL_ACCESS_CODE', 'offline-fixture-code')
    request = Request({'type': 'http', 'client': ('offline-fixture', 1234), 'headers': []})
    result = asyncio.run(local_server.auth_login(request, local_server.AuthLoginRequest(code='offline-fixture-code')))
    response, reached = request_through_gate(local_server, token=result['token'])
    assert response.status_code == 200
    assert reached == [True]


def test_setup_session_cannot_gain_authority_after_access_code_is_configured(local_server, monkeypatch):
    token = local_server._issue_operator_token()
    assert token in local_server.OPERATOR_SESSIONS
    monkeypatch.setenv('TERMINAL_ACCESS_CODE', 'offline-fixture-code')
    monkeypatch.setenv('ENABLE_OPTIONS_EXECUTION', 'true')
    response, reached = request_through_gate(local_server, token=token)
    assert response.status_code == 403
    assert not reached


def test_explicitly_disabled_local_tools_keep_setup_access(local_server):
    response, reached = request_through_gate(local_server)
    assert response.status_code == 200
    assert reached == [True]


@pytest.mark.parametrize('path', ['/api/auth/config', '/api/status'])
def test_public_health_and_auth_discovery_remain_available(local_server, monkeypatch, path):
    monkeypatch.setenv('ENABLE_SCHEDULER', 'true')
    response, reached = request_through_gate(local_server, method='GET', path=path)
    assert response.status_code == 200
    assert reached == [True]
