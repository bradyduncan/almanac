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
    assert len(data["facts"]) == 6  # lessons across beginner/intermediate/advanced
    assert len(data["drills"]) == 9  # activities across all three levels
    assert {d["kind"] for d in data["drills"]} <= {
        "script",
        "reflection",
        "rehearsal",
        "checklist",
        "audit",
        "record_review",
        "confirm",
        "quiz",
    }


def test_domain_detail_404(client: TestClient) -> None:
    resp = client.get("/domains/does-not-exist")
    assert resp.status_code == 404
    assert "domain not found" in resp.json()["detail"]


def test_list_drills_filtered_by_domain(client: TestClient) -> None:
    resp = client.get("/drills", params={"domain": "first-aid"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 4  # 2 confirm + 2 quiz
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


# --------------------------------------------------------------------------- #
# Fact reviews
# --------------------------------------------------------------------------- #


def test_post_fact_review(client: TestClient) -> None:
    resp = client.post("/fact-reviews", json={"fact_id": 1})
    assert resp.status_code == 201
    assert resp.json()["fact_id"] == 1


def test_post_fact_review_unknown_404(client: TestClient) -> None:
    assert client.post("/fact-reviews", json={"fact_id": 99999}).status_code == 404


# --------------------------------------------------------------------------- #
# UI (HTML pages + HTMX fragments)
# --------------------------------------------------------------------------- #


def test_home_renders_paths(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "what do you want" in resp.text.lower()
    assert "Social calibration" in resp.text
    assert "XP" in resp.text  # gamified top bar


def test_section_page_shows_levels(client: TestClient) -> None:
    resp = client.get("/d/social-calibration")
    assert resp.status_code == 200
    assert "Beginner" in resp.text
    assert "Intermediate" in resp.text
    assert "Advanced" in resp.text


def test_beginner_level_opens(client: TestClient) -> None:
    resp = client.get("/d/social-calibration/beginner")
    assert resp.status_code == 200
    assert "Learn" in resp.text and "Practice" in resp.text
    assert "Mark learned" in resp.text


def test_locked_level_redirects_to_section(client: TestClient) -> None:
    # intermediate is locked until beginner is complete
    resp = client.get("/d/social-calibration/intermediate", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/d/social-calibration")


def test_coming_soon_level_redirects(client: TestClient) -> None:
    # stub sections have no intermediate content
    resp = client.get("/d/health/intermediate", follow_redirects=False)
    assert resp.status_code == 303


def test_ui_drill_log_returns_logged_fragment(client: TestClient) -> None:
    resp = client.post("/ui/drill-log", data={"drill_id": 1, "outcome": "done", "difficulty": 3})
    assert resp.status_code == 200
    assert "✓ Done" in resp.text
    assert 'id="item-drill-1"' in resp.text


def test_ui_fact_review_marks_reviewed(client: TestClient) -> None:
    resp = client.post("/ui/fact-review", data={"fact_id": 1})
    assert resp.status_code == 200
    assert "✓ reviewed" in resp.text


# --------------------------------------------------------------------------- #
# Quizzes
# --------------------------------------------------------------------------- #


def _a_quiz(client: TestClient) -> dict:
    drills = client.get("/domains/social-calibration").json()["drills"]
    return next(d for d in drills if d["kind"] == "quiz")


def test_quiz_out_hides_answer(client: TestClient) -> None:
    quiz = _a_quiz(client)
    assert quiz["prompt"]
    assert isinstance(quiz["choices"], list) and len(quiz["choices"]) >= 2
    assert "answer_index" not in quiz  # never leak the answer


def test_quiz_grading(client: TestClient) -> None:
    quiz = _a_quiz(client)
    # first guess reveals the answer index in the result
    r1 = client.post("/quiz-answers", json={"drill_id": quiz["id"], "choice_index": 0}).json()
    ans = r1["answer_index"]
    correct = client.post(
        "/quiz-answers", json={"drill_id": quiz["id"], "choice_index": ans}
    ).json()
    assert correct["correct"] is True
    wrong_idx = 0 if ans != 0 else 1
    wrong = client.post(
        "/quiz-answers", json={"drill_id": quiz["id"], "choice_index": wrong_idx}
    ).json()
    assert wrong["correct"] is (wrong_idx == ans)


def test_quiz_answer_on_non_quiz_400(client: TestClient) -> None:
    drills = client.get("/domains/social-calibration").json()["drills"]
    confirm = next(d for d in drills if d["kind"] != "quiz")
    resp = client.post("/quiz-answers", json={"drill_id": confirm["id"], "choice_index": 0})
    assert resp.status_code == 400


def test_ui_quiz_answer_fragment(client: TestClient) -> None:
    quiz = _a_quiz(client)
    resp = client.post("/ui/quiz-answer", data={"drill_id": quiz["id"], "choice_index": 0})
    assert resp.status_code == 200
    assert ("Correct" in resp.text) or ("Not quite" in resp.text)
    assert "Answer:" in resp.text
    assert f'id="item-drill-{quiz["id"]}"' in resp.text
