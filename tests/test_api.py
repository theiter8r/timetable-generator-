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
    monkeypatch.setattr(store, "ONBOARDING_PATH", tmp_path / "onboarding.json")
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


# --- setup wizard -------------------------------------------------------


def test_onboarding_starts_unfinished_on_a_fresh_install(client):
    payload = client.get("/api/onboarding").json()
    assert payload["state"]["finished"] is False
    assert payload["state"]["step"] == "welcome"
    assert [s["id"] for s in payload["steps"]][:3] == ["welcome", "grid", "classes"]


def test_starting_from_blank_empties_the_config_but_keeps_a_week(client):
    payload = client.post("/api/onboarding/start", json={
        "source": "blank", "institution": "VIT", "department": "Computer Engineering",
    }).json()

    config = payload["config"]
    assert config["divisions"] == [] and config["teachers"] == []
    assert config["days"] and config["periods"], "a blank start still lays out the week"
    assert config["department"] == "Computer Engineering"
    assert payload["state"]["step"] == "grid"
    # And it is on disk, not just in the response.
    assert client.get("/api/config").json()["department"] == "Computer Engineering"

    steps = {s["id"]: s for s in payload["steps"]}
    assert steps["grid"]["done"] is True
    assert steps["classes"]["done"] is False
    assert "division" in steps["classes"]["errors"][0]


def test_every_step_saves_config_and_position_together(client):
    client.post("/api/onboarding/start", json={"source": "blank"})
    config = client.get("/api/config").json()
    config["divisions"] = [{"id": "SE-B", "year": "SE", "strength": 70}]

    result = client.put("/api/onboarding", json={
        "config": config, "state": {"step": "rooms", "visited": ["welcome", "grid", "classes"]},
    }).json()
    assert result["ok"] is True and result["saved_at"]

    # Reopening the app lands on the step that was saved, with the data intact.
    reopened = client.get("/api/onboarding").json()
    assert reopened["state"]["step"] == "rooms"
    assert client.get("/api/config").json()["divisions"][0]["id"] == "SE-B"


def test_a_half_typed_config_is_explained_not_rejected(client):
    """Mid-edit a config can be briefly invalid; that must not lose the step."""
    config = client.get("/api/config").json()
    config["teachers"][1]["id"] = config["teachers"][0]["id"]  # renaming, momentarily clashing

    response = client.put("/api/onboarding", json={"config": config, "state": {"step": "teachers"}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert any("duplicate teacher id" in message for message in body["errors"])


def test_finishing_and_reopening_the_guide(client):
    assert client.post("/api/onboarding/finish").json()["state"]["finished"] is True
    assert client.get("/api/onboarding").json()["state"]["finished"] is True
    assert client.post("/api/onboarding/reopen").json()["state"]["finished"] is False


def test_the_sample_dataset_passes_every_step(client):
    payload = client.post("/api/onboarding/start", json={"source": "sample"}).json()
    for step in payload["steps"]:
        assert step["done"] is True, f"{step['id']}: {step['errors']}"
        assert step["errors"] == []
