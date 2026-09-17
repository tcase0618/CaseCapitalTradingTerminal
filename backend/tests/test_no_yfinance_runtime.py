from pathlib import Path


def test_runtime_has_no_yfinance_dependency():
    backend = Path(__file__).resolve().parents[1]
    runtime_files = [backend / "requirements.txt", *sorted((backend / "services").glob("*.py"))]
    matches = [str(path.relative_to(backend)) for path in runtime_files if "yfinance" in path.read_text(encoding="utf-8").lower()]
    assert matches == []
