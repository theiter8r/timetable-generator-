"""End-to-end through the HTTP layer, against a throwaway config file."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from timetable import store
from timetable.api import _state, app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Never let a test touch the user's real data/config.json.
    monkeypatch.setattr(store, "CONFIG_PATH", tmp_path / "config.json")
    _state["solution"] = None
    _state["config"] = None
    with TestClient(app) as c:
        yield c


def test_config_round_trip(client):
    config = client.get("/api/config").json()
    assert config["divisions"] and config["teachers"]

    config["teachers"][0]["session_preference"] = "afternoon"
    assert client.put("/api/config", json=config).json()["ok"] is True
    assert client.get("/api/config").json()["teachers"][0]["session_preference"] == "afternoon"


def test_views_require_a_solve_first(client):
    assert client.get("/api/timetable/division/SE-B").status_code == 404


@pytest.mark.slow
def test_solve_then_read_every_view(client):
    result = client.post("/api/solve").json()
    assert result["solved"] is True

    summary = result["summary"]
    assert summary["status"] in ("OPTIMAL", "FEASIBLE")
    assert summary["sessions_placed"] == summary["sessions_requested"]
    assert summary["clashes"] == []

    config = client.get("/api/config").json()
    for kind, ident in (
        ("division", config["divisions"][0]["id"]),
        ("batch", config["batches"][0]["id"]),
        ("teacher", config["teachers"][0]["id"]),
        ("room", config["rooms"][0]["id"]),
    ):
        view = client.get(f"/api/timetable/{kind}/{ident}").json()
        assert view["kind"] == kind and view["id"] == ident
        assert view["days"] and view["periods"]

    assert client.get("/api/timetable/teacher/NOBODY").status_code == 404

    html = client.get("/api/export/all.html")
    assert html.status_code == 200 and "<table" in html.text
    csv = client.get("/api/export/all.csv")
    assert csv.status_code == 200 and csv.text.startswith("day,start_period")


def test_impossible_config_is_reported_without_solving(client):
    config = client.get("/api/config").json()
    # Ask one division for far more class than the week can hold.
    config["assignments"][0]["sessions_per_week"] = 500
    client.put("/api/config", json=config)

    result = client.post("/api/solve").json()
    assert result["solved"] is False
    assert result["summary"] is None
    assert any(i["level"] == "error" for i in result["issues"])
    assert any("student-overload" == i["code"] for i in result["issues"])
