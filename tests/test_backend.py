import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import backend.app as webapp_module
from backend.pipeline import (
    ClassificationResult,
    PipelineResult,
    ReferenceDatabase,
    TraceQualityMetrics,
    load_fasta_records,
    write_fasta,
)
from backend.app import app, format_error_for_user, read_job, write_job


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@pytest.fixture(autouse=True)
def isolate_web_results(monkeypatch, tmp_path):
    """Keep every test job outside the live project results directory."""
    test_results = (tmp_path / "results").resolve()
    test_results.mkdir()
    monkeypatch.setattr(webapp_module, "WEB_RESULTS", test_results)
    yield test_results


def wait_for_job_status(
    client: TestClient,
    job_id: str,
    expected_status: str = "completed",
    timeout_seconds: float = 30,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest.get("status") in webapp_module.TERMINAL_JOB_STATUSES:
            assert latest["status"] == expected_status, latest
            return latest
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not reach a terminal state: {latest}")


def create_test_job(
    job_id: str,
    status: str,
    *,
    mode: str = "fast",
    sample_id: str = "test-sample",
    summary: dict | None = None,
    error: str | None = None,
    source_job_ids: list[str] | None = None,
) -> tuple[Path, dict]:
    job_dir = webapp_module.WEB_RESULTS / job_id
    shutil.rmtree(job_dir, ignore_errors=True)
    (job_dir / "outputs").mkdir(parents=True)
    payload = {
        "job_id": job_id,
        "status": status,
        "mode": mode,
        "sample_id": sample_id,
        "safe_sample_id": sample_id,
        "created_at": time.time() - 5,
        "updated_at": time.time(),
        "summary": summary,
        "files": {},
        "error": error,
        "progress": {
            "stage": status,
            "message": status,
            "percent": 0 if status in {"queued", "running"} else 100,
            "elapsed_seconds": 5,
            "log_tail": [],
        },
    }
    if source_job_ids is not None:
        payload["source_job_ids"] = source_job_ids
    write_job(job_dir, payload)
    return job_dir, payload


@pytest.fixture
def reference_derived_pipeline(monkeypatch, tmp_path):
    """Replace ABIF parsing with a deterministic fixed-reference-derived result."""
    reference = load_fasta_records(webapp_module.TREE_REFERENCE_FASTA)[0]
    consensus = reference.sequence.replace("-", "")

    def fake_pipeline(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        sample_id = str(kwargs["sample_id"])
        reverse_path = kwargs.get("reverse_ab1")
        single_read_label = kwargs.get("single_read_label") or "forward"
        if reverse_path is not None:
            read_mode = "paired"
            read_orientation = "read1:forward;read2:reverse_complement"
            read_label = "read1/read2"
        else:
            read_mode = "single"
            is_reverse_only = (kwargs.get("single_read_label") or "forward").lower() == "reverse"
            read_orientation = "reverse_complement" if is_reverse_only else "forward"
            read_label = "reverse" if is_reverse_only else single_read_label
        consensus_fasta = output_dir / f"{sample_id}_consensus.fasta"
        report_json = output_dir / f"{sample_id}_report.json"
        report_html = output_dir / f"{sample_id}_report.html"
        write_fasta([type(reference)(sample_id, consensus)], consensus_fasta)
        trace = TraceQualityMetrics(
            read_label=read_label,
            passed=True,
            failure_code=None,
            message="reference-derived simulated read",
            core_length=len(consensus),
            q_pass_fraction=1.0,
            mixed_peak_fraction=0.0,
            longest_mixed_run=0,
            n_fraction=0.0,
            longest_low_quality_run=0,
        )
        classification = ClassificationResult(
            predicted_new_genotype="",
            predicted_old_genotype="",
            predicted_new_group="",
            predicted_old_group="",
            confidence="pending",
            is_target=False,
            target_status="pending_phylogeny",
            best_hit="",
            best_identity=0.0,
            second_new_genotype="",
            second_old_genotype="",
            second_identity=0.0,
            hits=[],
        )
        return PipelineResult(
            sample_id=sample_id,
            consensus=consensus,
            consensus_length=len(consensus),
            analysis_mode="fast",
            read_mode=read_mode,
            read_orientation=read_orientation,
            orientation_identity=1.0,
            quality_threshold=kwargs.get("quality_threshold", 35),
            min_overlap=kwargs.get("min_overlap", 80),
            trace_quality=[trace],
            quality_status="passed",
            quality_note="reference-derived simulated input",
            classification=classification,
            reference_database=ReferenceDatabase(
                records=[],
                path=webapp_module.TREE_REFERENCE_FASTA,
                metadata_path=webapp_module.GENOTYPE_XLSX,
                was_rebuilt=False,
                placement=None,
            ),
            output_dir=output_dir,
            consensus_fasta=consensus_fasta,
            alignment_fasta=None,
            tree_newick=None,
            tree_svg=None,
            report_json=report_json,
            report_html=report_html,
        )

    monkeypatch.setattr(webapp_module, "run_355_pipeline", fake_pipeline)
    return fake_pipeline


def test_health_endpoint_reports_ready():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "orientia-tsa56-phylogenetic-typing"
    assert body["project_version"] == "1.0.1"
    assert body["status"] == "ok"
    assert body["reference_available"] is True
    assert body["reference_name"] == "355ref-56kDa.fas"
    assert body["reference_count"] == 355
    assert body["tree_reference_name"] == "60ref_151-850.fas"
    assert body["tree_reference_count"] == 60
    assert body["default_tree_reference"] == "60"
    tree_references = {
        reference["id"]: reference for reference in body["tree_references"]
    }
    assert set(tree_references) == {"60"}
    assert tree_references["60"]["name"] == "60ref_151-850.fas"
    assert tree_references["60"]["count"] == 60
    assert body["mafft_available"] is (webapp_module.MAFFT_RUNNER is not None)
    assert body["mafft_method"] == "mafft --add samples.fasta --keeplength reference.fasta"
    assert "blast_available" not in body
    assert "blast_method" not in body


def test_api_root_redirects_to_the_maintained_article_workbench():
    client = TestClient(app, follow_redirects=False)

    response = client.get("/")

    assert response.status_code == 307
    assert response.headers["location"] == "http://localhost:3200/"


def test_corrected_60ref_is_pre_aligned_and_has_corrected_jg_a_labels():
    references = webapp_module.load_fasta_records(DATA / "60ref_151-850.fas")
    names = {reference.name for reference in references}
    genotype_map = webapp_module.load_genotype_map(DATA / "355ref-genotype-map.xlsx")

    assert len(references) == 60
    assert len(names) == 60
    assert {len(reference.sequence) for reference in references} == {898}
    assert {
        "JX235719_4_c_JG_A",
        "GQ495611_4_c_JG_A",
        "JX188389_4_c_JG_A",
    }.issubset(names)
    assert not any("_4_d_JG_A" in name for name in names)
    for accession in ("JX235719", "GQ495611", "U19903", "JX188389"):
        assert genotype_map.by_accession[accession].matched_label == f"{accession}_4_c_JG_A"


def test_local_react_site_origin_is_allowed_by_cors():
    client = TestClient(app)

    response = client.options(
        "/api/analyze",
        headers={
            "Origin": "http://127.0.0.1:3200",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3200"


def test_config_advertises_only_article_v1():
    client = TestClient(app)

    response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    mode_ids = {mode["id"] for mode in body["available_modes"]}
    assert body["default_mode"] == webapp_module.V1_STRICT_MODE
    assert mode_ids == {webapp_module.V1_STRICT_MODE}
    assert "v2" not in body
    assert body["available_modes"][0]["id"] == webapp_module.V1_STRICT_MODE
    assert body["available_modes"][0]["version"] == "1.0.0"
    assert body["available_modes"][0]["legacy_alias"] == "iqtree"
    assert body["available_modes"][0]["available"] is body["iqtree_available"]
    assert body["iqtree_model"] == "TVM+F+R5"
    assert body["iqtree_bootstrap_replicates"] == 1000
    assert body["iqtree_threads"] == "AUTO"
    assert body["detected_cpu_count"] >= 1
    assert body["project_name_en"] == "Orientia TSA56 Phylogenetic Typing"
    assert body["reference_name"] == "355ref-56kDa.fas"
    assert body["reference_count"] == 355
    assert body["tree_reference_name"] == "60ref_151-850.fas"
    assert body["tree_reference_count"] == 60
    assert body["default_tree_reference"] == "60"
    tree_references = {
        reference["id"]: reference for reference in body["tree_references"]
    }
    assert set(tree_references) == {"60"}
    assert tree_references["60"]["name"] == "60ref_151-850.fas"
    assert tree_references["60"]["count"] == 60
    assert "blast_available" not in body
    assert "blast_method" not in body
    article = body["article_profile"]
    assert article["profile_id"] == webapp_module.V1_STRICT_PROFILE_ID
    assert article["version"] == "1.0.0"
    assert article["mode"] == webapp_module.V1_STRICT_MODE
    assert article["strict"] is True
    assert article["reference_name"] == "60ref_151-850.fas"
    assert article["reference_count"] == 60
    assert article["verification"]["valid"] is True
    assert article["verification"]["genotype_count"] == 16
    assert article["verification"]["minimum_anchors_per_genotype"] >= 3
    assert body["mafft_available"] is (webapp_module.MAFFT_RUNNER is not None)
    assert body["mafft_method"] == "mafft --add samples.fasta --keeplength reference.fasta"


def test_iqtree_threads_default_to_auto_and_accept_positive_override(monkeypatch):
    monkeypatch.delenv("PHYLO_PUBLIC_IQTREE_THREADS", raising=False)
    assert webapp_module.resolve_iqtree_threads() == "AUTO"

    monkeypatch.setenv("PHYLO_PUBLIC_IQTREE_THREADS", "6")
    assert webapp_module.resolve_iqtree_threads() == "6"


@pytest.mark.parametrize("value", ["0", "-2", "many"])
def test_iqtree_threads_reject_invalid_override(monkeypatch, value):
    monkeypatch.setenv("PHYLO_PUBLIC_IQTREE_THREADS", value)
    with pytest.raises(RuntimeError, match="AUTO or a positive integer"):
        webapp_module.resolve_iqtree_threads()


def test_config_hides_all_tree_modes_when_mafft_is_unavailable(monkeypatch):
    monkeypatch.setattr(webapp_module, "MAFFT_RUNNER", None)

    body = webapp_module.config()
    mode_ids = {mode["id"] for mode in body["available_modes"]}

    assert body["tree_available"] is False
    assert body["iqtree_available"] is False
    assert mode_ids == {webapp_module.V1_STRICT_MODE}
    assert body["available_modes"][0]["available"] is False


def test_tree_reference_validation_rejects_non_aligned_sequences(tmp_path):
    reference = tmp_path / "not_aligned.fasta"
    reference.write_text(">ref_1\nACGT\n>ref_2\nACGTA\n", encoding="utf-8")

    valid, error = webapp_module.validate_tree_reference(reference, 2)

    assert valid is False
    assert "equal-length reference alignment" in error


def test_tree_svg_separates_title_subtitle_and_first_tip(tmp_path):
    from Bio import Phylo
    from io import StringIO

    tree = Phylo.read(StringIO("(sample_A:0.1,reference_A:0.2);"), "newick")
    output = tmp_path / "tree.svg"

    webapp_module.render_tree_svg(tree, {"sample_A"}, output, "A long publication tree title")

    svg = output.read_text(encoding="utf-8")
    assert 'y="26"' in svg
    assert 'y="48"' in svg
    assert 'rooting: midpoint; order: increasing' in svg
    assert 'cy="70.00"' in svg


def test_mafft_command_adds_all_samples_to_fixed_reference_without_changing_length(
    monkeypatch,
    tmp_path,
):
    samples = tmp_path / "simulated_batch.fas"
    reference = tmp_path / "60ref_151-850.fas"
    output = tmp_path / "simulated_batch__60ref_151-850.fas.fas"
    sample_1 = "ACGT" * 6
    sample_2 = "ACGA" * 6
    samples.write_text(
        f">sample_1\n{sample_1}\n>sample_2\n{sample_2}\n",
        encoding="utf-8",
    )
    reference.write_text(
        f">ref_1\n{sample_1}\n>ref_2\n{sample_2}\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        kwargs["stdout"].write(
            f">ref_1\n{sample_1.lower()}\n"
            f">ref_2\n{sample_2.lower()}\n"
            f">sample_1\n{sample_1.lower()}\n"
            f">sample_2\n{sample_2.lower()}\n"
        )
        return SimpleNamespace(returncode=0, stdout=None, stderr="MAFFT complete")

    monkeypatch.setattr(webapp_module, "MAFFT_RUNNER", ("native", "mafft"))
    monkeypatch.setattr(webapp_module.subprocess, "run", fake_run)

    result = webapp_module.run_mafft_add_alignment(samples, reference, output)

    assert result == output.resolve()
    assert captured["command"] == [
        "mafft",
        "--add",
        str(samples.resolve()),
        "--keeplength",
        str(reference.resolve()),
    ]
    assert captured["kwargs"]["cwd"] == tmp_path
    aligned = webapp_module.load_fasta_records(output)
    assert [record.name for record in aligned] == [
        "ref_1",
        "ref_2",
        "sample_1",
        "sample_2",
    ]
    assert {len(record.sequence) for record in aligned} == {24}


def test_wsl_mafft_command_uses_safe_path_conversion_and_file_redirection(
    monkeypatch,
    tmp_path,
):
    sequence = "ACGT" * 6
    samples = tmp_path / "samples.fasta"
    reference = tmp_path / "reference.fasta"
    output = tmp_path / "combined_alignment.fasta"
    samples.write_text(f">sample\n{sequence}\n", encoding="utf-8")
    reference.write_text(f">reference\n{sequence}\n", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output.write_text(
            f">reference\n{sequence.lower()}\n>sample\n{sequence.lower()}\n",
            encoding="utf-8",
        )
        (tmp_path / f"{output.name}.mafft.log").write_text(
            "MAFFT complete",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        webapp_module,
        "MAFFT_RUNNER",
        ("wsl", "/opt/biotools/mafft"),
    )
    monkeypatch.setattr(webapp_module.subprocess, "run", fake_run)

    webapp_module.run_mafft_add_alignment(samples, reference, output)

    command = captured["command"]
    assert command[:4] == ["wsl.exe", "--exec", "sh", "-lc"]
    assert command[5] == "sh"
    assert 'exec "$6" --add "$(wslpath -a "$2")"' in command[4]
    assert '--keeplength "$(wslpath -a "$3")" > "$4" 2> "$5"' in command[4]
    assert command[6:] == [
        str(tmp_path),
        str(samples.resolve()),
        str(reference.resolve()),
        output.name,
        f"{output.name}.mafft.log",
        "/opt/biotools/mafft",
    ]
    assert captured["kwargs"]["cwd"] == tmp_path


def test_iqtree_command_uses_requested_model_bootstrap_threads_and_bnni(
    monkeypatch,
    tmp_path,
):
    alignment = tmp_path / "所有序列+参考比对.fasta"
    alignment.write_text(">A\nACGT\n>B\nACGA\n", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        (tmp_path / f"{alignment.name}.treefile").write_text(
            "(A:0.1,B:0.1)100;\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="IQ-TREE complete")

    monkeypatch.setattr(webapp_module, "MAFFT_RUNNER", ("native", "mafft"))
    monkeypatch.setattr(webapp_module, "IQTREE_RUNNER", ("native", "iqtree"))
    monkeypatch.setattr(webapp_module.subprocess, "run", fake_run)

    treefile = webapp_module.run_iqtree_alignment(alignment)

    assert treefile.name == f"{alignment.name}.treefile"
    assert captured["command"] == [
        "iqtree",
        "-s",
        alignment.name,
        "-m",
        "TVM+F+R5",
        "-bb",
        "1000",
        "-T",
        "AUTO",
        "-bnni",
    ]
    assert captured["kwargs"]["cwd"] == tmp_path


@pytest.mark.parametrize("mode", [webapp_module.V1_STRICT_MODE, "iqtree", "prepare"])
def test_each_analysis_mode_is_submitted_as_a_queued_background_job(
    monkeypatch,
    mode,
):
    client = TestClient(app)
    scheduled = []

    def capture_task(self, function, *args, **kwargs):
        scheduled.append((function, args, kwargs))

    monkeypatch.setattr(webapp_module, "MAFFT_RUNNER", ("native", "mafft"))
    monkeypatch.setattr(webapp_module, "IQTREE_RUNNER", ("native", "iqtree"))
    monkeypatch.setattr(webapp_module.BackgroundTasks, "add_task", capture_task)

    response = client.post(
        "/api/analyze",
        data={
            "sample_id": f"queued-{mode}",
            "mode": mode,
            "tree_reference": "60",
        },
        files={
            "read1_file": ("simulated.ab1", b"reference-derived-simulated", "application/octet-stream"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["progress"]["stage"] == "queued"
    assert body["tree_reference_id"] == "60"
    assert len(scheduled) == 1
    function, args, kwargs = scheduled[0]
    assert function is webapp_module.run_analysis_job
    assert kwargs == {}
    expected_job_dir = webapp_module.WEB_RESULTS / body["submission_id"] / body["job_id"]
    assert args[0] == expected_job_dir
    expected_mode = (
        webapp_module.V1_STRICT_MODE if mode == "iqtree" else mode
    )
    assert args[4] == expected_mode
    assert body["mode"] == expected_mode
    assert body["strict_article_mode"] is (expected_mode == webapp_module.V1_STRICT_MODE)
    assert args[8] == "60"
    assert body["submission_sample_count"] == 1
    assert read_job(expected_job_dir)["status"] == "queued"

    shutil.rmtree(webapp_module.WEB_RESULTS / body["submission_id"], ignore_errors=True)


@pytest.mark.parametrize("legacy_mode", ["fast", "tree"])
def test_new_analysis_rejects_legacy_submission_modes(legacy_mode):
    response = TestClient(app).post(
        "/api/analyze",
        data={"sample_id": "legacy", "mode": legacy_mode},
        files={"read1_file": ("simulated.ab1", b"reference-derived-simulated", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "mode=v1_strict_article" in response.json()["detail"]


@pytest.mark.parametrize("mode", [webapp_module.V1_STRICT_MODE, "iqtree", "prepare"])
def test_article_analysis_rejects_non_60_reference(mode):
    response = TestClient(app).post(
        "/api/analyze",
        data={"sample_id": "wrong-reference", "mode": mode, "tree_reference": "61"},
        files={"read1_file": ("simulated.ab1", b"reference-derived-simulated", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "only allows tree_reference=60" in response.json()["detail"]


def test_prepare_analysis_uses_reference_derived_simulated_reads(reference_derived_pipeline):
    client = TestClient(app)
    response = client.post(
        "/api/analyze",
        data={"sample_id": "SIM_REF_PAIRED", "mode": "prepare"},
        files={
            "forward_file": ("sim-forward.ab1", b"reference-derived-forward", "application/octet-stream"),
            "reverse_file": ("sim-reverse.ab1", b"reference-derived-reverse", "application/octet-stream"),
        },
    )

    assert response.status_code == 200
    submitted = response.json()
    assert submitted["status"] == "queued"
    body = wait_for_job_status(client, submitted["job_id"])
    assert body["mode"] == "prepare"
    assert body["summary"]["predicted_type"] == ""
    assert body["summary"]["predicted_group"] == ""
    assert body["summary"]["predicted_old_type"] == ""
    assert body["summary"]["predicted_old_group"] == ""
    assert body["summary"]["is_target"] is False
    assert body["summary"]["target_status"] == "pending_phylogeny"
    assert body["summary"]["typing_source"] == "pending_phylogeny"
    assert body["summary"]["top_hits"] == []
    assert body["summary"]["tree_rooting"] is None
    assert body["summary"]["tree_ordering"] is None
    assert body["summary"]["tree_generated"] is False
    assert body["summary"]["quality_threshold"] == 35
    assert body["summary"]["min_overlap"] == 80
    assert "tree_svg" not in body["files"]
    assert body["files"]["report_html"].endswith("_report.html")
    job_dir = webapp_module.resolve_job_dir(body["job_id"])
    report = json.loads(next((job_dir / "outputs").glob("*_report.json")).read_text(encoding="utf-8"))
    assert report["similarity_predicted_new_genotype"] is None
    assert report["best_hit"] is None
    assert report["top_hits"] == []
    report_html = next((job_dir / "outputs").glob("*_report.html")).read_text(encoding="utf-8")
    assert "BLAST-like" not in report_html
    assert "Top hits" not in report_html
    assert "seed-and-extend orientation only" in report_html


def test_job_status_returns_completed_pending_result_after_reference_simulation(reference_derived_pipeline):
    client = TestClient(app)
    analyze_response = client.post(
        "/api/analyze",
        data={"sample_id": "SIM_REF_STATUS", "mode": "prepare"},
        files={
            "forward_file": ("sim-forward.ab1", b"reference-derived-forward", "application/octet-stream"),
            "reverse_file": ("sim-reverse.ab1", b"reference-derived-reverse", "application/octet-stream"),
        },
    )

    submitted = analyze_response.json()
    assert submitted["status"] == "queued"
    completed = wait_for_job_status(client, submitted["job_id"])

    assert completed["summary"]["best_hit"] is None
    assert completed["summary"]["target_status"] == "pending_phylogeny"


def test_two_reference_derived_reads_keep_paired_input_semantics(reference_derived_pipeline):
    client = TestClient(app)
    response = client.post(
        "/api/analyze",
        data={"sample_id": "SIM_REF_PAIRED_ORDER", "mode": "prepare"},
        files={
            "read1_file": ("sim-read-a.ab1", b"reference-derived-a", "application/octet-stream"),
            "read2_file": ("sim-read-b.ab1", b"reference-derived-b", "application/octet-stream"),
        },
    )

    assert response.status_code == 200
    submitted = response.json()
    assert submitted["status"] == "queued"
    body = wait_for_job_status(client, submitted["job_id"])
    assert body["summary"]["read_mode"] == "paired"
    assert body["summary"]["read_direction"] == "paired"
    assert body["summary"]["read_orientation"] == "read1:forward;read2:reverse_complement"
    assert body["summary"]["orientation_method"] == "参考库双链 seed-and-extend 仅用于定向与拼接"
    assert body["summary"]["predicted_type"] == ""
    assert body["summary"]["target_status"] == "pending_phylogeny"


def test_single_reference_derived_forward_input_is_retained_and_analyzed(reference_derived_pipeline):
    client = TestClient(app)
    response = client.post(
        "/api/analyze",
        data={"sample_id": "SIM_REF_FORWARD_ONLY", "mode": "prepare"},
        files={
            "forward_file": ("sim-forward.ab1", b"reference-derived-forward", "application/octet-stream"),
        },
    )

    assert response.status_code == 200
    submitted = response.json()
    assert submitted["status"] == "queued"
    body = wait_for_job_status(client, submitted["job_id"])
    assert body["summary"]["read_mode"] == "single"
    assert body["summary"]["read_direction"] == "forward"
    assert body["summary"]["predicted_type"] == ""
    assert body["summary"]["target_status"] == "pending_phylogeny"


def test_single_reference_derived_reverse_input_is_retained_and_analyzed(reference_derived_pipeline):
    client = TestClient(app)
    response = client.post(
        "/api/analyze",
        data={"sample_id": "SIM_REF_REVERSE_ONLY", "mode": "prepare"},
        files={
            "reverse_file": ("sim-reverse.ab1", b"reference-derived-reverse", "application/octet-stream"),
        },
    )

    assert response.status_code == 200
    submitted = response.json()
    assert submitted["status"] == "queued"
    body = wait_for_job_status(client, submitted["job_id"])
    assert body["summary"]["read_mode"] == "single"
    assert body["summary"]["read_direction"] == "reverse"
    assert body["summary"]["predicted_type"] == ""
    assert body["summary"]["target_status"] == "pending_phylogeny"


def test_analysis_rejects_request_without_any_ab1_file():
    client = TestClient(app)

    response = client.post(
        "/api/analyze",
        data={"sample_id": "missing", "mode": "prepare"},
    )

    assert response.status_code == 400
    assert "at least one" in response.json()["detail"].lower()


def test_analysis_passes_custom_quality_and_overlap_parameters(monkeypatch):
    client = TestClient(app)
    captured = {}

    def fake_run_analysis_job(
        job_dir,
        safe_sample_id,
        forward_path,
        reverse_path,
        mode,
        single_read_label,
        quality_threshold,
        min_overlap,
        tree_reference_id,
    ):
        captured.update(
            {
                "quality_threshold": quality_threshold,
                "min_overlap": min_overlap,
                "tree_reference_id": tree_reference_id,
            }
        )

    monkeypatch.setattr(webapp_module, "run_analysis_job", fake_run_analysis_job)
    response = client.post(
        "/api/analyze",
        data={
            "sample_id": "custom-parameters",
            "mode": "prepare",
            "quality_threshold": "25",
            "min_overlap": "60",
            "tree_reference": "60",
        },
        files={
            "read1_file": ("simulated.ab1", b"reference-derived-simulated", "application/octet-stream"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["quality_threshold"] == 25
    assert body["min_overlap"] == 60
    assert captured == {
        "quality_threshold": 25,
        "min_overlap": 60,
        "tree_reference_id": "60",
    }
    shutil.rmtree(webapp_module.WEB_RESULTS / body["submission_id"], ignore_errors=True)


def test_analysis_rejects_quality_and_overlap_outside_supported_ranges():
    client = TestClient(app)
    files = {
        "read1_file": ("simulated.ab1", b"reference-derived-simulated", "application/octet-stream"),
    }

    quality_response = client.post(
        "/api/analyze",
        data={
            "sample_id": "invalid-quality",
            "quality_threshold": "41",
            "min_overlap": "80",
        },
        files=files,
    )
    overlap_response = client.post(
        "/api/analyze",
        data={
            "sample_id": "invalid-overlap",
            "quality_threshold": "20",
            "min_overlap": "19",
        },
        files=files,
    )

    assert quality_response.status_code == 400
    assert "0 and 40" in quality_response.json()["detail"]
    assert overlap_response.status_code == 400
    assert "20 and 200" in overlap_response.json()["detail"]


def test_analysis_and_batch_tree_reject_unknown_tree_reference():
    client = TestClient(app)
    source_job_id = "6" * 32
    source_job_dir = webapp_module.WEB_RESULTS / source_job_id
    shutil.rmtree(source_job_dir, ignore_errors=True)
    outputs = source_job_dir / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "sample_consensus.fasta").write_text(
        ">sample\n" + ("A" * 200) + "\n",
        encoding="utf-8",
    )
    write_job(
        source_job_dir,
        {
            "job_id": source_job_id,
            "status": "completed",
            "mode": "prepare",
            "sample_id": "sample",
            "created_at": time.time(),
            "updated_at": time.time(),
            "summary": {"predicted_type": "", "target_status": "pending_phylogeny"},
            "files": {},
            "error": None,
        },
    )

    analysis_response = client.post(
        "/api/analyze",
        data={
            "sample_id": "invalid-tree-reference",
            "mode": "iqtree",
            "tree_reference": "62",
        },
        files={
            "read1_file": ("simulated.ab1", b"reference-derived-simulated", "application/octet-stream"),
        },
    )
    batch_response = client.post(
        "/api/batch-tree",
        data={
            "job_ids": json.dumps([source_job_id]),
            "tree_reference": "62",
        },
    )

    assert analysis_response.status_code == 400
    assert "tree_reference" in analysis_response.json()["detail"]
    assert batch_response.status_code == 400
    assert "tree_reference" in batch_response.json()["detail"]

    shutil.rmtree(source_job_dir, ignore_errors=True)


def test_running_iqtree_job_status_includes_live_progress_from_log():
    client = TestClient(app)
    job_id = "b" * 32
    job_dir = webapp_module.WEB_RESULTS / job_id
    shutil.rmtree(job_dir, ignore_errors=True)
    outputs = job_dir / "outputs"
    outputs.mkdir(parents=True)
    log_path = outputs / "SIM_REF_alignment.fasta.log"
    log_path.write_text(
        "\n".join(
            [
                "Optimizing NNI: done in 140.372 secs using 600.6% CPU",
                "Iteration 40 / LogL: -7138.061 / Time: 1h:32m:8s (2h:24m:6s left)",
                "Optimizing NNI: done in 171.637 secs using 601.7% CPU",
            ]
        ),
        encoding="utf-8",
    )
    write_job(
        job_dir,
        {
            "job_id": job_id,
            "status": "running",
            "mode": "iqtree",
            "sample_id": "SIM_REF",
            "safe_sample_id": "SIM_REF",
            "created_at": time.time() - 120,
            "updated_at": time.time() - 110,
            "summary": None,
            "files": {},
            "error": None,
        },
    )

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["progress"]["stage"] == "iqtree"
    assert body["progress"]["elapsed_seconds"] >= 110
    assert "Iteration 40" in body["progress"]["message"]
    assert body["progress"]["percent"] > 0
    assert body["progress"]["log_tail"][-1].startswith("Optimizing NNI")

    shutil.rmtree(job_dir, ignore_errors=True)


@pytest.mark.parametrize(
    ("initial_status", "job_id"),
    [("queued", "1" * 32), ("running", "2" * 32)],
)
def test_cancel_queued_or_running_job_is_idempotent_and_persists_marker(
    initial_status,
    job_id,
):
    client = TestClient(app)
    job_dir, _ = create_test_job(job_id, initial_status)

    first_response = client.post(f"/api/jobs/{job_id}/cancel")
    second_response = client.post(f"/api/jobs/{job_id}/cancel")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first = first_response.json()
    second = second_response.json()
    assert first["status"] == "cancelled"
    assert second["status"] == "cancelled"
    assert first["cancel_requested"] is True
    assert second["cancel_requested"] is True
    assert first["cancelled_from_status"] == initial_status
    assert second["cancelled_from_status"] == initial_status
    assert second["cancelled_at"] == first["cancelled_at"]
    assert first["progress"]["stage"] == "cancelled"
    assert first["progress"]["message"] == "分析已停止"

    marker = webapp_module.cancel_marker_path(job_dir)
    assert marker.is_file()
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_payload["job_id"] == job_id
    assert marker_payload["requested_at"] == first["cancelled_at"]

    persisted = client.get(f"/api/jobs/{job_id}")
    assert persisted.status_code == 200
    assert persisted.json()["status"] == "cancelled"
    assert persisted.json()["cancelled_at"] == first["cancelled_at"]

    shutil.rmtree(job_dir, ignore_errors=True)


def test_cancelled_analysis_worker_does_not_start_or_resurrect(monkeypatch):
    client = TestClient(app)
    job_id = "3" * 32
    job_dir, _ = create_test_job(job_id, "queued")
    upload_dir = job_dir / "uploads"
    upload_dir.mkdir()
    read_path = upload_dir / "read1.ab1"
    read_path.write_bytes(b"reference-derived-simulated")
    pipeline_called = False

    def forbidden_pipeline(**kwargs):
        nonlocal pipeline_called
        pipeline_called = True
        raise AssertionError("A cancelled job must not enter the analysis pipeline")

    monkeypatch.setattr(webapp_module, "run_355_pipeline", forbidden_pipeline)
    cancelled = client.post(f"/api/jobs/{job_id}/cancel").json()

    webapp_module.run_analysis_job(
        job_dir,
        "cancelled-worker",
        read_path,
        None,
        "fast",
    )
    webapp_module.update_job_progress(
        job_dir,
        "qc",
        "This update must not revive a cancelled job",
        25,
    )

    persisted = read_job(job_dir)
    assert pipeline_called is False
    assert persisted["status"] == "cancelled"
    assert persisted["cancelled_at"] == cancelled["cancelled_at"]
    assert persisted["progress"]["stage"] == "cancelled"
    assert persisted["summary"] is None
    assert persisted["error"] is None

    shutil.rmtree(job_dir, ignore_errors=True)


@pytest.mark.parametrize(
    ("terminal_status", "job_id", "summary", "error"),
    [
        ("completed", "4" * 32, {"predicted_type": "5e"}, None),
        ("failed", "5" * 32, None, "expected failure"),
    ],
)
def test_cancel_terminal_job_is_a_safe_noop(
    terminal_status,
    job_id,
    summary,
    error,
):
    client = TestClient(app)
    job_dir, _ = create_test_job(
        job_id,
        terminal_status,
        summary=summary,
        error=error,
    )
    before = read_job(job_dir)

    response = client.post(f"/api/jobs/{job_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == terminal_status
    assert read_job(job_dir) == before
    assert webapp_module.cancel_marker_path(job_dir).exists() is False
    assert "cancel_requested" not in response.json()
    assert "cancelled_at" not in response.json()

    shutil.rmtree(job_dir, ignore_errors=True)


def test_cancelled_batch_tree_does_not_run_mafft_or_update_source_jobs(monkeypatch):
    client = TestClient(app)
    source_job_id = "7" * 32
    batch_job_id = "8" * 32
    source_dir, _ = create_test_job(
        source_job_id,
        "completed",
        sample_id="source-sample",
        summary={"predicted_type": "5e", "predicted_group": "Group 5"},
    )
    (source_dir / "outputs" / "source-sample_consensus.fasta").write_text(
        ">source-sample\n" + ("A" * 200) + "\n",
        encoding="utf-8",
    )
    batch_dir, _ = create_test_job(
        batch_job_id,
        "queued",
        mode="batch_tree",
        source_job_ids=[source_job_id],
    )
    source_before = read_job(source_dir)
    mafft_called = False
    source_update_called = False

    def forbidden_mafft(*args, **kwargs):
        nonlocal mafft_called
        mafft_called = True
        raise AssertionError("A cancelled batch must not start MAFFT")

    def forbidden_source_update(*args, **kwargs):
        nonlocal source_update_called
        source_update_called = True
        raise AssertionError("A cancelled batch must not update source jobs")

    monkeypatch.setattr(webapp_module, "run_mafft_add_alignment", forbidden_mafft)
    monkeypatch.setattr(
        webapp_module,
        "apply_joint_tree_typing_to_source_job",
        forbidden_source_update,
    )
    cancelled = client.post(f"/api/jobs/{batch_job_id}/cancel").json()

    webapp_module.run_batch_tree_job(
        batch_dir,
        [source_job_id],
        "nj",
        "61",
    )

    persisted = read_job(batch_dir)
    assert mafft_called is False
    assert source_update_called is False
    assert persisted["status"] == "cancelled"
    assert persisted["cancelled_at"] == cancelled["cancelled_at"]
    assert persisted["progress"]["stage"] == "cancelled"
    assert read_job(source_dir) == source_before

    for job_dir in (source_dir, batch_dir):
        shutil.rmtree(job_dir, ignore_errors=True)


def test_analyze_rejects_non_ab1_files():
    client = TestClient(app)

    response = client.post(
        "/api/analyze",
        data={"sample_id": "bad_sample", "mode": "prepare"},
        files={
            "forward_file": ("forward.txt", b"ACGT", "text/plain"),
            "reverse_file": ("reverse.txt", b"ACGT", "text/plain"),
        },
    )

    assert response.status_code == 400
    assert "ab1" in response.json()["detail"].lower()


def test_format_error_for_user_trims_long_external_command_output():
    message = format_error_for_user(RuntimeError("Command failed\n" + ("x" * 5000)))

    assert len(message) < 1400
    assert message.endswith("...")


def test_read_job_retries_transient_partial_json(monkeypatch, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "job.json").write_text("{}", encoding="utf-8")
    payload = {"job_id": "c" * 32, "status": "running"}
    calls = iter(["", json.dumps(payload)])

    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None: next(calls))

    assert read_job(job_dir) == payload


def test_final_treefile_downloads_as_attachment():
    client = TestClient(app)
    job_id = "f" * 32
    job_dir = webapp_module.WEB_RESULTS / job_id
    shutil.rmtree(job_dir, ignore_errors=True)
    outputs = job_dir / "outputs"
    outputs.mkdir(parents=True)
    newick_file = outputs / "sample_tree_midpoint_increasing.nwk"
    tree_file = outputs / "sample_tree_midpoint_increasing.treefile"
    newick_file.write_text("(sample:0.1,reference:0.1);\n", encoding="utf-8")
    write_job(
        job_dir,
        {
            "job_id": job_id,
            "status": "completed",
            "mode": "iqtree",
            "sample_id": "sample",
            "summary": {"predicted_type": "5e"},
            "files": {},
            "error": None,
        },
    )

    completed = client.get(f"/api/jobs/{job_id}").json()
    response = client.get(f"/api/jobs/{job_id}/files/{tree_file.name}")

    assert completed["files"]["tree_file"].endswith(f"/{tree_file.name}")
    assert tree_file.read_text(encoding="utf-8") == newick_file.read_text(encoding="utf-8")
    assert response.status_code == 200
    assert response.text.strip().endswith(";")
    assert response.headers["content-disposition"].startswith("attachment;")
    shutil.rmtree(job_dir, ignore_errors=True)


def test_batch_tree_combines_all_source_consensus_and_adds_bootstrap(monkeypatch):
    client = TestClient(app)
    source_job_ids = ["d" * 32, "e" * 32]
    submission_id = "20260803-120000_2样本_a1b2c3"
    for index, source_job_id in enumerate(source_job_ids, start=1):
        shutil.rmtree(webapp_module.WEB_RESULTS / source_job_id, ignore_errors=True)
        source_dir = webapp_module.WEB_RESULTS / submission_id / source_job_id
        shutil.rmtree(source_dir, ignore_errors=True)
        outputs = source_dir / "outputs"
        outputs.mkdir(parents=True)
        (outputs / f"sample_{index}_consensus.fasta").write_text(
            f">sample_{index}\n" + ("A" * (180 + index)) + "\n",
            encoding="utf-8",
        )
        write_job(
            source_dir,
            {
                "job_id": source_job_id,
                "status": "completed",
                "mode": "prepare",
                "sample_id": f"sample_{index}",
                "submission_id": submission_id,
                "submission_sample_count": 2,
                "created_at": time.time(),
                "updated_at": time.time(),
                "summary": {
                    "predicted_type": "",
                    "target_status": "pending_phylogeny",
                },
                "files": {},
                "error": None,
            },
        )

    mafft_calls = []

    def fake_mafft(
        sample_fasta,
        reference_fasta,
        output_fasta,
        cancel_callback=None,
    ):
        assert callable(cancel_callback)
        sample_records = webapp_module.load_fasta_records(sample_fasta)
        reference_records = webapp_module.load_fasta_records(reference_fasta)
        target_length = len(reference_records[0].sequence)
        aligned_samples = [
            type(reference_records[0])(
                record.name,
                record.sequence[:target_length].ljust(target_length, "-"),
            )
            for record in sample_records
        ]
        webapp_module.write_fasta(
            [*reference_records, *aligned_samples],
            output_fasta,
        )
        mafft_calls.append(
            {
                "samples": sample_fasta,
                "reference": reference_fasta,
                "output": output_fasta,
                "sample_names": [record.name for record in sample_records],
            }
        )
        return output_fasta

    def fake_iqtree(alignment_fasta, cancel_callback=None):
        assert callable(cancel_callback)
        records = webapp_module.load_fasta_records(alignment_fasta)
        treefile = alignment_fasta.parent / f"{alignment_fasta.name}.treefile"
        treefile.write_text(
            "(" + ",".join(f"{record.name}:0.1" for record in records) + ");\n",
            encoding="utf-8",
        )
        return treefile

    monkeypatch.setattr(webapp_module, "MAFFT_RUNNER", ("native", "mafft"))
    monkeypatch.setattr(webapp_module, "IQTREE_RUNNER", ("native", "iqtree"))
    monkeypatch.setattr(webapp_module, "run_mafft_add_alignment", fake_mafft)
    monkeypatch.setattr(webapp_module, "run_iqtree_alignment", fake_iqtree)
    response = client.post(
        "/api/batch-tree",
        data={
            "job_ids": json.dumps(source_job_ids),
            "tree_method": webapp_module.V1_STRICT_MODE,
            "tree_reference": "60",
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    completed = client.get(f"/api/jobs/{job_id}").json()

    assert completed["status"] == "completed"
    assert completed["summary"]["batch_sample_count"] == 2
    assert completed["summary"]["tree_reference_id"] == "60"
    assert completed["summary"]["tree_reference_name"] == "60ref_151-850.fas"
    assert completed["summary"]["tree_reference_count"] == 60
    assert completed["summary"]["bootstrap_replicates"] == 1000
    assert completed["analysis_mode"] == webapp_module.V1_STRICT_MODE
    assert completed["workflow_version"] == "1.0.0"
    assert completed["strict_article_mode"] is True
    assert completed["summary"]["analysis_mode"] == webapp_module.V1_STRICT_MODE
    assert completed["summary"]["workflow_profile_id"] == webapp_module.V1_STRICT_PROFILE_ID
    assert completed["summary"]["workflow_version"] == "1.0.0"
    assert completed["summary"]["strict_article_mode"] is True
    assert completed["summary"]["typing_source"] == "phylogenetic_tree"
    assert completed["summary"]["similarity_predicted_type"] is None
    assert completed["summary"]["similarity_predicted_group"] is None
    assert completed["summary"]["best_hit"] is None
    assert completed["summary"]["top_hits"] == []
    assert completed["summary"]["alignment_method"] == "mafft --add --keeplength"
    assert len(mafft_calls) == 1
    assert mafft_calls[0]["sample_names"] == ["sample_1", "sample_2"]
    assert mafft_calls[0]["reference"].name == "60ref_151-850.fas"
    batch_job_dir = webapp_module.resolve_job_dir(job_id)
    svg_path = batch_job_dir / "outputs" / "batch_joint_tree_midpoint_increasing.svg"
    tree_file_path = batch_job_dir / "outputs" / "batch_joint_tree_midpoint_increasing.treefile"
    alignment_path = batch_job_dir / "outputs" / "batch_joint_alignment.fasta"
    aligned_records = webapp_module.load_fasta_records(alignment_path)
    svg = svg_path.read_text(encoding="utf-8")
    assert len(aligned_records) == 62
    assert {len(record.sequence) for record in aligned_records} == {898}
    assert svg.count('class="sample-marker"') == 2
    assert completed["summary"]["typing_rule_id"] == webapp_module.TYPING_RULE_ID
    assert completed["files"]["iqtree_treefile"].endswith(
        "/batch_joint_alignment.fasta.treefile"
    )
    assert completed["files"]["tree_file"].endswith(
        "/batch_joint_tree_midpoint_increasing.treefile"
    )
    assert tree_file_path.read_text(encoding="utf-8").strip().endswith(";")
    assert webapp_module.content_disposition_for(tree_file_path) == "attachment"

    shutil.rmtree(webapp_module.WEB_RESULTS / submission_id, ignore_errors=True)


def test_fasta_analysis_skips_ab1_and_runs_joint_phylogeny(monkeypatch):
    client = TestClient(app)
    mafft_calls = []

    def fake_mafft(sample_fasta, reference_fasta, output_fasta, cancel_callback=None):
        assert callable(cancel_callback)
        sample_records = webapp_module.load_fasta_records(sample_fasta)
        reference_records = webapp_module.load_fasta_records(reference_fasta)
        target_length = len(reference_records[0].sequence)
        aligned_samples = [
            type(reference_records[0])(
                record.name,
                record.sequence[:target_length].ljust(target_length, "-"),
            )
            for record in sample_records
        ]
        webapp_module.write_fasta([*reference_records, *aligned_samples], output_fasta)
        mafft_calls.append([record.name for record in sample_records])
        return output_fasta

    def fake_iqtree(alignment_fasta, cancel_callback=None):
        assert callable(cancel_callback)
        records = webapp_module.load_fasta_records(alignment_fasta)
        treefile = alignment_fasta.parent / f"{alignment_fasta.name}.treefile"
        treefile.write_text(
            "(" + ",".join(f"{record.name}:0.1" for record in records) + ");\n",
            encoding="utf-8",
        )
        return treefile

    monkeypatch.setattr(webapp_module, "MAFFT_RUNNER", ("native", "mafft"))
    monkeypatch.setattr(webapp_module, "IQTREE_RUNNER", ("native", "iqtree"))
    monkeypatch.setattr(webapp_module, "run_mafft_add_alignment", fake_mafft)
    monkeypatch.setattr(webapp_module, "run_iqtree_alignment", fake_iqtree)
    response = client.post(
        "/api/fasta-analysis",
        data={
            "analysis_mode": webapp_module.V1_STRICT_MODE,
            "tree_reference": "60",
        },
        files={
            "fasta_file": (
                "direct.fasta",
                f">direct_A\n{'A' * 700}\n>direct_B\n{'C' * 680}\n",
                "text/plain",
            ),
        },
    )

    assert response.status_code == 200
    submitted = response.json()
    completed = wait_for_job_status(client, submitted["job_id"])
    assert completed["status"] == "completed"
    assert completed["mode"] == "fasta_iqtree"
    assert completed["analysis_mode"] == webapp_module.V1_STRICT_MODE
    assert completed["workflow_profile_id"] == webapp_module.V1_STRICT_PROFILE_ID
    assert completed["workflow_version"] == "1.0.0"
    assert completed["strict_article_mode"] is True
    assert completed["submission_sample_count"] == 2
    assert completed["input_read_mode"] == "fasta"
    assert completed["summary"]["input_source"] == "fasta"
    assert completed["summary"]["analysis_mode"] == webapp_module.V1_STRICT_MODE
    assert completed["summary"]["workflow_profile_id"] == webapp_module.V1_STRICT_PROFILE_ID
    assert completed["summary"]["workflow_version"] == "1.0.0"
    assert completed["summary"]["strict_article_mode"] is True
    assert completed["summary"]["iqtree_command"].endswith(
        "-m TVM+F+R5 -bb 1000 -T AUTO -bnni"
    )
    assert completed["summary"]["batch_sample_count"] == 2
    assert len(completed["summary"]["tree_typing_results"]) == 2
    assert mafft_calls == [["direct_A", "direct_B"]]
    assert completed["files"]["alignment_fasta"].endswith(
        "/fasta_joint_alignment.fasta"
    )
    assert completed["files"]["tree_svg"].endswith(
        "/fasta_joint_tree_midpoint_increasing.svg"
    )

    shutil.rmtree(webapp_module.WEB_RESULTS / completed["submission_id"], ignore_errors=True)


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("direct.txt", ">sample\nACGT\n", "only .fa"),
        ("direct.fasta", "ACGT\n", "before the first header"),
        ("direct.fasta", ">sample\nACGTZ\n", "unsupported characters"),
        ("direct.fasta", ">sample\nACGT\n>sample\nACGT\n", "duplicate sequence name"),
        ("direct.fasta", ">sample\n----NNNN\n", "no determined"),
    ],
)
def test_fasta_analysis_rejects_invalid_input(monkeypatch, filename, content, message):
    monkeypatch.setattr(webapp_module, "MAFFT_RUNNER", ("native", "mafft"))
    monkeypatch.setattr(webapp_module, "IQTREE_RUNNER", ("native", "iqtree"))

    response = TestClient(app).post(
        "/api/fasta-analysis",
        files={"fasta_file": (filename, content, "text/plain")},
    )

    assert response.status_code == 400
    assert message in response.json()["detail"].lower()
