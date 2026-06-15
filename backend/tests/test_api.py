from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_list_domains(client: TestClient) -> None:
    resp = client.get("/domains")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 10
    # ordered by default_priority
    assert [d["default_priority"] for d in data] == list(range(1, 11))
    assert data[0]["slug"] == "social-calibration"


def test_domain_detail_includes_facts_and_drills(client: TestClient) -> None:
    resp = client.get("/domains/social-calibration")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "social-calibration"
    assert len(data["facts"]) == 3
    assert len(data["drills"]) == 3
    assert {d["kind"] for d in data["drills"]} <= {
        "script",
        "reflection",
        "rehearsal",
        "checklist",
        "audit",
        "record_review",
    }


def test_domain_detail_404(client: TestClient) -> None:
    resp = client.get("/domains/does-not-exist")
    assert resp.status_code == 404
    assert "domain not found" in resp.json()["detail"]


def test_list_drills_filtered_by_domain(client: TestClient) -> None:
    resp = client.get("/drills", params={"domain": "first-aid"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(d["domain_id"] == data[0]["domain_id"] for d in data)


def test_post_log_done_requires_difficulty(client: TestClient) -> None:
    resp = client.post("/logs", json={"drill_id": 1, "outcome": "done"})
    assert resp.status_code == 422  # pydantic validation


def test_post_log_done_succeeds(client: TestClient) -> None:
    resp = client.post(
        "/logs", json={"drill_id": 1, "outcome": "done", "difficulty": 2, "note": "ok"}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["drill_id"] == 1
    assert data["outcome"] == "done"
    assert data["difficulty"] == 2
    assert data["logged_at"]


def test_post_log_skipped_rejects_difficulty(client: TestClient) -> None:
    resp = client.post("/logs", json={"drill_id": 1, "outcome": "skipped", "difficulty": 2})
    assert resp.status_code == 422


def test_post_log_unknown_drill_404(client: TestClient) -> None:
    resp = client.post("/logs", json={"drill_id": 99999, "outcome": "snoozed"})
    assert resp.status_code == 404


def test_today_returns_queue(client: TestClient) -> None:
    resp = client.get("/today")
    assert resp.status_code == 200
    data = resp.json()
    assert data["budget_minutes"] == 15
    assert len(data["items"]) >= 1  # never empty
    # cold start at first run -> items carry the cold_start factor
    assert all("cold_start" in it["factors"] for it in data["items"])
    item = data["items"][0]
    assert "drill" in item and "domain_title" in item


def test_today_reflects_logged_completion(client: TestClient) -> None:
    # Log enough completions to leave cold start, then today's queue should be scored.
    for drill_id in range(1, 7):
        client.post("/logs", json={"drill_id": drill_id, "outcome": "done", "difficulty": 2})
    data = client.get("/today").json()
    assert all("cold_start" not in it["factors"] for it in data["items"])
