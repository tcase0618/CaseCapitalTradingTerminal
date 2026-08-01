import os
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")
load_dotenv(ROOT / "frontend" / ".env")

if os.environ.get("RUN_LIVE_TRADING_TESTS", "").strip().lower() not in {"1", "true", "yes"}:
    db_name = os.environ.get("DB_NAME")
    if db_name and not db_name.endswith("_pytest"):
        os.environ["DB_NAME"] = f"{db_name}_pytest"

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(scope="session")
def base_url():
    assert BASE_URL, "REACT_APP_BACKEND_URL must be set"
    return BASE_URL


@pytest.fixture
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
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
