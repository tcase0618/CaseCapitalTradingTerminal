import os
import re
import pytest
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Tests are offline by default. Loading the deployment .env here previously
# made an ordinary pytest invocation capable of targeting real credentials and
# a real Postgres DSN. External tests must opt in explicitly.
RUN_EXTERNAL = os.environ.get("RUN_EXTERNAL_API_TESTS", "").strip().lower() in {"1", "true", "yes"}
if not RUN_EXTERNAL:
    os.environ["POSTGRES_ENABLED"] = "false"
    os.environ.pop("POSTGRES_DSN", None)
    os.environ.pop("REACT_APP_BACKEND_URL", None)

if os.environ.get("RUN_LIVE_TRADING_TESTS", "").strip().lower() not in {"1", "true", "yes"}:
    db_name = os.environ.get("DB_NAME")
    if db_name and not db_name.endswith("_pytest"):
        os.environ["DB_NAME"] = f"{db_name}_pytest"

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# API modules are integration tests and need an explicit target. Keep the
# module-level constants importable when the target is intentionally absent;
# the fixture will provide the actionable failure message for those tests.


@pytest.fixture(scope="session")
def base_url():
    if not BASE_URL or not RUN_EXTERNAL:
        pytest.skip("external API target is disabled; set RUN_EXTERNAL_API_TESTS=1 with an explicit test URL")
    return BASE_URL


@pytest.fixture
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    code = os.environ.get("API_TEST_ACCESS_CODE") or os.environ.get("TERMINAL_ACCESS_CODE")
    if BASE_URL and code:
        try:
            r = s.post(f"{BASE_URL}/api/auth/login", json={"code": code}, timeout=10)
            if r.ok:
                token = (r.json() or {}).get("token")
                if token:
                    s.headers.update({"Authorization": f"Bearer {token}"})
        except requests.RequestException:
            pass
    return s


@pytest.fixture(autouse=True)
def reset_motor_client_between_tests():
    yield
    try:
        from services import db as db_module

        if db_module._client is not None:
            db_module._client.close()
            db_module._client = None
    except Exception:
        pass


def pytest_collection_modifyitems(config, items):
    """Keep archived service/database exercises out of the offline suite."""
    for item in items:
        name = item.path.name
        if re.fullmatch(r"test_v\d+_api\.py", name):
            item.add_marker(pytest.mark.external_integration)
        elif name == "test_v14_phase_exits.py":
            item.add_marker(pytest.mark.postgres_integration)
