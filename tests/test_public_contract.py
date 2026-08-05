import json
from pathlib import Path
import time

from fastapi.testclient import TestClient

import backend.app as webapp


ROOT = Path(__file__).resolve().parents[1]


def test_openapi_has_no_v2_or_placement_routes():
    paths = set(webapp.app.openapi()["paths"])

    assert paths == {
        "/",
        "/health",
        "/ready",
        "/api/config",
        "/api/analyze",
        "/api/batch-tree",
        "/api/fasta-analysis",
        "/api/jobs/{job_id}",
        "/api/jobs/{job_id}/cancel",
        "/api/jobs/{job_id}/files/{filename}",
    }
    assert not any("/v2" in path for path in paths)


def test_public_backend_contains_no_external_similarity_classifier():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "backend").glob("*.py")
    ).lower()

    assert "makeblastdb" not in source
    assert "blastn" not in source
    assert "classify_sequence_with_local_blast" not in source


def test_api_responses_receive_public_security_headers():
    response = TestClient(webapp.app).get("/api/config")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["cache-control"] == "no-store"


def test_cleanup_removes_only_expired_valid_job_directories(monkeypatch, tmp_path):
    results = tmp_path / "results"
    valid_submission = results / "20260805-120000_1样本_abcdef"
    expired_job = valid_submission / ("a" * 32)
    current_job = valid_submission / ("b" * 32)
    invalid_submission = results / "do-not-delete"
    invalid_job = invalid_submission / ("c" * 32)
    for directory in (expired_job, current_job, invalid_job):
        directory.mkdir(parents=True)
        (directory / "job.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    old = time.time() - 48 * 3600
    (expired_job / "job.json").touch()
    Path(expired_job / "job.json").touch()
    import os
    os.utime(expired_job / "job.json", (old, old))
    os.utime(invalid_job / "job.json", (old, old))

    monkeypatch.setattr(webapp, "WEB_RESULTS", results.resolve())
    monkeypatch.setattr(webapp, "RESULT_RETENTION_HOURS", 24)

    assert webapp.cleanup_expired_results(now=time.time()) == 1
    assert not expired_job.exists()
    assert current_job.exists()
    assert invalid_job.exists()
