from pathlib import Path

from app import config as app_config
from app.utils import calendar_store


def test_calendar_path_follows_configured_data_root(tmp_path, monkeypatch):
    data_root = tmp_path / "isolated-data"
    monkeypatch.setenv("FLOAT_DATA_DIR", str(data_root))
    monkeypatch.delenv("FLOAT_CALENDAR_DIR", raising=False)
    monkeypatch.delenv("FLOAT_DEV_MODE", raising=False)

    assert (
        calendar_store.calendar_events_dir()
        == (data_root / "databases" / "calendar_events").resolve()
    )
    loaded = app_config.load_config()
    assert (
        Path(loaded["reflection_store_path"]).resolve()
        == (data_root / "databases" / "reflections.sqlite3").resolve()
    )
    assert (
        Path(loaded["work_run_store_path"]).resolve()
        == (data_root / "databases" / "work_runs.sqlite3").resolve()
    )


def test_explicit_calendar_path_still_takes_precedence(tmp_path, monkeypatch):
    explicit = tmp_path / "calendar-only"
    monkeypatch.setenv("FLOAT_DATA_DIR", str(tmp_path / "other-data"))
    monkeypatch.setenv("FLOAT_CALENDAR_DIR", str(explicit))

    assert calendar_store.calendar_events_dir() == explicit.resolve()
