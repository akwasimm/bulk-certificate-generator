"""End-to-end API tests."""
import io
import time
import uuid
import zipfile

import pytest

API = "/api/v1"
TERMINAL_STATUSES = {"completed", "completed_with_failures", "failed"}


def job_payload(**overrides) -> dict:
    payload = {
        "course_name": "Advanced Python Programming",
        "issued_by": "Arena Academy",
        "issued_on": "2026-10-11",
        "recipients": [
            {"full_name": "Ada Lovelace", "email": "ada@example.com"},
            {"full_name": "Grace Hopper", "email": "grace@example.com"},
            {"full_name": "Alan Turing"},
        ],
    }
    payload.update(overrides)
    return payload


def create_job(client, **overrides) -> dict:
    response = client.post(f"{API}/certificate-jobs", json=job_payload(**overrides))
    assert response.status_code == 202, response.text
    return response.json()


def wait_for_terminal(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"{API}/certificate-jobs/{job_id}").json()
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not reach a terminal state within {timeout}s")


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_job_returns_202_with_location_header(client):
    body = create_job(client)

    assert len(body["id"]) == 36  # UUID
    assert body["course_name"] == "Advanced Python Programming"
    assert body["total_recipients"] == 3
    assert body["certificates_url"] == f"{API}/certificate-jobs/{body['id']}/certificates"
    assert body["download_url"] == f"{API}/certificate-jobs/{body['id']}/download"


def test_create_job_generates_all_certificates(client):
    body = create_job(client)
    job = wait_for_terminal(client, body["id"])

    assert job["status"] == "completed"
    assert job["total_recipients"] == 3
    assert job["processed_recipients"] == 3
    assert job["succeeded_recipients"] == 3
    assert job["failed_recipients"] == 0
    assert job["progress_percent"] == 100.0
    assert job["completed_at"] is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"recipients": []},
        {"recipients": "not-a-list"},
        {"recipients": ["just-a-string"]},
        {"course_name": "   "},
    ],
    ids=["empty-recipients", "recipients-not-a-list", "row-not-an-object", "blank-course"],
)
def test_structurally_invalid_requests_are_rejected_with_422(client, overrides):
    response = client.post(f"{API}/certificate-jobs", json=job_payload(**overrides))
    assert response.status_code == 422


def test_missing_course_name_is_rejected_with_422(client):
    payload = job_payload()
    del payload["course_name"]

    response = client.post(f"{API}/certificate-jobs", json=payload)

    assert response.status_code == 422


def test_too_many_recipients_are_rejected_with_422(client):
    payload = job_payload(recipients=[{"full_name": f"Recipient {i}"} for i in range(1001)])

    response = client.post(f"{API}/certificate-jobs", json=payload)

    assert response.status_code == 422
    assert "at most 1000" in response.text


def test_invalid_recipient_values_are_flagged_without_blocking_valid_ones(client):
    body = create_job(
        client,
        recipients=[
            {"full_name": "Ada Lovelace", "email": "ada@example.com"},
            {"full_name": "   "},
            {"full_name": "Bad Email", "email": "not-an-email"},
        ],
    )
    job = wait_for_terminal(client, body["id"])

    assert job["status"] == "completed_with_failures"
    assert job["total_recipients"] == 3
    assert job["succeeded_recipients"] == 1
    assert job["failed_recipients"] == 2

    certificates = client.get(f"{API}/certificate-jobs/{body['id']}/certificates").json()
    failed = [c for c in certificates if c["status"] == "failed"]
    assert len(failed) == 2
    assert any("name is missing or blank" in (c["error"] or "") for c in failed)
    assert any("email address is not valid" in (c["error"] or "") for c in failed)

    ada = next(c for c in certificates if c["full_name"] == "Ada Lovelace")
    assert ada["status"] == "generated"
    assert ada["download_url"] is not None

    response = client.get(f"{API}/certificates/{failed[0]['id']}/download")
    assert response.status_code == 409


def test_single_generation_failure_does_not_block_the_rest(client, monkeypatch):
    from app.services import processor

    real_render = processor.render_certificate_pdf

    def flaky_render(**kwargs):
        if kwargs["recipient_name"] == "Grace Hopper":
            raise RuntimeError("Intentional test failure")
        return real_render(**kwargs)

    monkeypatch.setattr(processor, "render_certificate_pdf", flaky_render)

    body = create_job(client)
    job = wait_for_terminal(client, body["id"])

    assert job["status"] == "completed_with_failures"
    assert job["succeeded_recipients"] == 2
    assert job["failed_recipients"] == 1

    certificates = client.get(f"{API}/certificate-jobs/{body['id']}/certificates").json()
    grace = next(c for c in certificates if c["full_name"] == "Grace Hopper")
    assert grace["status"] == "failed"
    assert grace["error"] == "Generation failed."
    assert grace["download_url"] is None
    assert sum(1 for c in certificates if c["status"] == "generated") == 2


def test_download_certificate_streams_a_real_pdf(client):
    body = create_job(client)
    certificates = client.get(f"{API}/certificate-jobs/{body['id']}/certificates").json()
    target = next(c for c in certificates if c["full_name"] == "Ada Lovelace")

    response = client.get(target["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('.pdf"')
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 1000


def test_download_job_zip_contains_every_generated_certificate(client):
    body = create_job(client)
    job = wait_for_terminal(client, body["id"])
    assert job["status"] == "completed"

    response = client.get(f"{API}/certificate-jobs/{body['id']}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    bundle = zipfile.ZipFile(io.BytesIO(response.content))
    assert len(bundle.namelist()) == 3
    assert any("Ada-Lovelace" in name for name in bundle.namelist())


def test_zip_of_job_without_generated_certificates_returns_409(client):
    body = create_job(client, recipients=[{"full_name": "   "}])
    wait_for_terminal(client, body["id"])

    response = client.get(f"{API}/certificate-jobs/{body['id']}/download")

    assert response.status_code == 409


def test_unknown_job_returns_404(client):
    response = client.get(f"{API}/certificate-jobs/{uuid.uuid4()}")
    assert response.status_code == 404


def test_certificates_of_unknown_job_return_404(client):
    response = client.get(f"{API}/certificate-jobs/{uuid.uuid4()}/certificates")
    assert response.status_code == 404


def test_unknown_certificate_download_returns_404(client):
    response = client.get(f"{API}/certificates/{uuid.uuid4()}/download")
    assert response.status_code == 404


def test_malformed_job_id_returns_422(client):
    response = client.get(f"{API}/certificate-jobs/not-a-uuid")
    assert response.status_code == 422
