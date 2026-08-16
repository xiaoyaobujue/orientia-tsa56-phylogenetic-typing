from __future__ import annotations

import html
import hashlib
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from Bio import Phylo
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "data"
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from pipeline import (  # noqa: E402
    FastaRecord,
    TREE_DISTANCE_RULE_ID,
    TREE_AUTO_ACCEPT_MIN_SUPPORT,
    TYPING_RULE_ID,
    TYPING_RULE_VERSION,
    TraceQualityError,
    add_bootstrap_support,
    build_distance_tree,
    infer_tree_typing_results,
    load_fasta_records,
    load_genotype_map,
    midpoint_root_and_sort,
    render_tree_svg,
    run_pipeline as run_355_pipeline,
    write_fasta,
    write_report,
)
PROJECT_ID = "orientia-tsa56-phylogenetic-typing"
PROJECT_NAME = "恙虫东方体 TSA56 系统发育分型工具"
PROJECT_NAME_EN = "Orientia TSA56 Phylogenetic Typing"
PROJECT_VERSION = "1.1.0"
API_SERVICE_ID = f"{PROJECT_ID}-api"
FRONTEND_URL = os.environ.get(
    "PHYLO_PUBLIC_FRONTEND_URL",
    "http://localhost:3200/",
).strip()

REFERENCE_FASTA = DATA_ROOT / "355ref-56kDa.fas"
GENOTYPE_XLSX = DATA_ROOT / "355ref-genotype-map.xlsx"
DEFAULT_TREE_REFERENCE_ID = "60"
TREE_REFERENCE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "60": {
        "id": "60",
        "label": "60参考",
        "path": DATA_ROOT / "60ref_151-850.fas",
        "expected_count": 60,
    },
}
# Backward-compatible aliases for code and integrations that use the default
# 60-reference library directly.
TREE_REFERENCE_FASTA = TREE_REFERENCE_DEFINITIONS[DEFAULT_TREE_REFERENCE_ID]["path"]
EXPECTED_TREE_REFERENCE_COUNT = TREE_REFERENCE_DEFINITIONS[
    DEFAULT_TREE_REFERENCE_ID
]["expected_count"]
REFERENCE_CACHE = ROOT / "runtime" / "reference_cache"
WEB_RESULTS = Path(
    os.environ.get("PHYLO_PUBLIC_RESULTS_DIR", ROOT / "results")
).resolve()
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
RESULT_RETENTION_HOURS = max(
    1,
    min(168, int(os.environ.get("PHYLO_PUBLIC_RETENTION_HOURS", "24"))),
)
MAX_CONCURRENT_JOBS = max(
    1,
    min(4, int(os.environ.get("PHYLO_PUBLIC_MAX_CONCURRENT_JOBS", "1"))),
)
FASTA_EXTENSIONS = {".fa", ".fas", ".fasta", ".fna"}
V1_STRICT_MODE = "v1_strict_article"
V1_STRICT_VERSION = "1.0.1"
V1_STRICT_PROFILE_ID = "article_phylogenetic_v1_strict"
LEGACY_IQTREE_MODE = "iqtree"
ALLOWED_MODES = {V1_STRICT_MODE, LEGACY_IQTREE_MODE, "prepare"}
BOOTSTRAP_REPLICATES = max(
    10,
    min(1000, int(os.environ.get("PHYLO_PUBLIC_BOOTSTRAP_REPLICATES", "100"))),
)
IQTREE_MODEL = "TVM+F+R5"
IQTREE_BOOTSTRAP_REPLICATES = 1000
EXPECTED_GENOTYPE_RELATIONSHIPS = {
    "5e": "Karp_A",
    "5c": "Karp_B",
    "5d": "Karp_C",
    "5b": "Saitama",
    "5a": "Boryong",
    "4f": "JG_C",
    "4e": "Kawasaki",
    "4d": "JG_B",
    "4c": "JG_A",
    "4b": "Gilliam",
    "4a": "TD",
    "3b": "TA763 _B",
    "3a": "TA763 _A",
    "2b": "Kato_B",
    "2a": "Kato_A",
    "1b": "Shimokoshi",
    "1a": "TA686",
}


def resolve_iqtree_threads() -> str:
    """Return IQ-TREE's device-aware thread specification.

    AUTO is the publication default and lets IQ-TREE select cores for the
    current data and computer. Advanced local users may provide a positive
    integer through PHYLO_PUBLIC_IQTREE_THREADS.
    """

    raw_value = os.environ.get("PHYLO_PUBLIC_IQTREE_THREADS", "AUTO").strip()
    if raw_value.upper() == "AUTO":
        return "AUTO"
    try:
        requested = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "PHYLO_PUBLIC_IQTREE_THREADS must be AUTO or a positive integer"
        ) from exc
    if requested < 1:
        raise RuntimeError(
            "PHYLO_PUBLIC_IQTREE_THREADS must be AUTO or a positive integer"
        )
    return str(requested)


IQTREE_THREADS = resolve_iqtree_threads()
DETECTED_CPU_COUNT = max(1, os.cpu_count() or 1)
DEFAULT_QUALITY_THRESHOLD = 35
DEFAULT_MIN_OVERLAP = 80
MAX_BATCH_SAMPLE_COUNT = 200
IQTREE_WSL_BINARY = os.environ.get(
    "PHYLO_PUBLIC_IQTREE_WSL_BINARY",
    "",
).strip()
MAFFT_WSL_BINARY = os.environ.get(
    "PHYLO_PUBLIC_MAFFT_WSL_BINARY",
    "",
).strip()
CANCEL_MARKER_NAME = "cancel.requested"
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
SUBMISSION_DIR_PATTERN = re.compile(
    r"^(?P<stamp>\d{8}-\d{6})_(?P<count>[1-9]\d{0,2})样本_(?P<token>[a-f0-9]{6})$"
)
_JOB_LOCKS: dict[str, threading.RLock] = {}
_JOB_LOCKS_GUARD = threading.Lock()
_ANALYSIS_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
LOCAL_CORS_ORIGINS = [
    "http://localhost:3200",
    "http://127.0.0.1:3200",
]


def is_article_tree_mode(mode: str) -> bool:
    return mode in {V1_STRICT_MODE, LEGACY_IQTREE_MODE}


def canonical_article_tree_mode(mode: str) -> str:
    return V1_STRICT_MODE if is_article_tree_mode(mode) else mode


def parse_extra_cors_origins() -> list[str]:
    origins: list[str] = []
    for raw_origin in os.environ.get("PHYLO_PUBLIC_CORS_ORIGINS", "").split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "PHYLO_PUBLIC_CORS_ORIGINS must contain exact http(s) origins"
            )
        origins.append(origin)
    return origins


EXTRA_CORS_ORIGINS = parse_extra_cors_origins()


class AnalysisCancelled(RuntimeError):
    pass


def bounded_analysis_job(func):
    """Keep device-adaptive article jobs within the configured concurrency budget."""

    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        with _ANALYSIS_SLOTS:
            return func(*args, **kwargs)

    return wrapped


def cleanup_expired_results(now: float | None = None) -> int:
    """Delete expired anonymous jobs only from validated submission directories."""

    if not WEB_RESULTS.is_dir():
        return 0
    resolved_root = WEB_RESULTS.resolve()
    if resolved_root == ROOT.resolve() or resolved_root == resolved_root.parent:
        raise RuntimeError("Refusing to clean an unsafe results directory")
    cutoff = (now or time.time()) - RESULT_RETENTION_HOURS * 3600
    deleted = 0
    for submission_dir in list(resolved_root.iterdir()):
        if not submission_dir.is_dir() or not SUBMISSION_DIR_PATTERN.fullmatch(submission_dir.name):
            continue
        for job_dir in list(submission_dir.iterdir()):
            if not job_dir.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", job_dir.name):
                continue
            job_json = job_dir / "job.json"
            timestamp = job_json.stat().st_mtime if job_json.is_file() else job_dir.stat().st_mtime
            if timestamp >= cutoff:
                continue
            shutil.rmtree(job_dir)
            deleted += 1
        try:
            next(submission_dir.iterdir())
        except StopIteration:
            submission_dir.rmdir()
    return deleted


def get_job_lock(job_dir: Path) -> threading.RLock:
    key = str(Path(job_dir).resolve())
    with _JOB_LOCKS_GUARD:
        return _JOB_LOCKS.setdefault(key, threading.RLock())


def cancel_marker_path(job_dir: Path) -> Path:
    return Path(job_dir) / CANCEL_MARKER_NAME


def cancellation_requested(job_dir: Path) -> bool:
    return cancel_marker_path(job_dir).is_file()


def raise_if_cancelled(job_dir: Path) -> None:
    if cancellation_requested(job_dir):
        raise AnalysisCancelled("分析已停止")


def count_fasta_records(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.startswith(">"))


def validate_tree_reference(
    path: Path,
    expected_count: int,
) -> tuple[bool, str]:
    if not path.exists():
        return False, f"{path.name} was not found"
    try:
        records = load_fasta_records(path)
    except (OSError, UnicodeError) as exc:
        return False, f"{path.name} could not be read: {exc}"
    if len(records) != expected_count:
        return (
            False,
            f"{path.name} does not contain {expected_count} references",
        )
    names = [record.name for record in records]
    if len(names) != len(set(names)):
        return False, f"{path.name} contains duplicate reference names"
    lengths = {len(record.sequence) for record in records}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) <= 0:
        return False, f"{path.name} is not an equal-length reference alignment"
    return True, ""


REFERENCE_COUNT = count_fasta_records(REFERENCE_FASTA)
TREE_REFERENCES: dict[str, dict[str, Any]] = {}
for tree_reference_id, definition in TREE_REFERENCE_DEFINITIONS.items():
    tree_reference_path = definition["path"]
    tree_reference_count = count_fasta_records(tree_reference_path)
    tree_reference_valid, tree_reference_error = validate_tree_reference(
        tree_reference_path,
        definition["expected_count"],
    )
    TREE_REFERENCES[tree_reference_id] = {
        **definition,
        "count": tree_reference_count,
        "valid": tree_reference_valid,
        "error": tree_reference_error,
    }

TREE_REFERENCE_COUNT = TREE_REFERENCES[DEFAULT_TREE_REFERENCE_ID]["count"]
TREE_REFERENCE_VALID = TREE_REFERENCES[DEFAULT_TREE_REFERENCE_ID]["valid"]
TREE_REFERENCE_ERROR = TREE_REFERENCES[DEFAULT_TREE_REFERENCE_ID]["error"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_article_reference_profile() -> dict[str, Any]:
    """Fail-fast verification for the immutable article reference profile."""
    verification: dict[str, Any] = {
        "valid": False,
        "reference_count": 0,
        "alignment_columns": 0,
        "unique_leaf_mapping": False,
        "mapping_key": "exact_leaf_id_or_unique_accession",
        "genotype_count": 0,
        "minimum_anchors_per_genotype": 0,
        "errors": [],
    }
    errors: list[str] = verification["errors"]
    try:
        references = load_fasta_records(TREE_REFERENCE_FASTA)
        verification["reference_count"] = len(references)
        if len(references) != EXPECTED_TREE_REFERENCE_COUNT:
            errors.append(
                f"expected {EXPECTED_TREE_REFERENCE_COUNT} references, found {len(references)}"
            )
        names = [reference.name for reference in references]
        if len(names) != len(set(names)):
            errors.append("reference leaf IDs are not unique")
        lengths = {len(reference.sequence) for reference in references}
        if lengths == {898}:
            verification["alignment_columns"] = 898
        else:
            errors.append(
                "article reference alignment must contain exactly 898 columns; "
                f"found {sorted(lengths)}"
            )

        genotype_map = load_genotype_map(GENOTYPE_XLSX)
        if genotype_map.duplicate_accessions:
            errors.append(
                "genotype map contains duplicate accessions: "
                + ", ".join(genotype_map.duplicate_accessions[:5])
            )
        observed_relationships: dict[str, set[str]] = {}
        for metadata in genotype_map.records:
            observed_relationships.setdefault(metadata.genotype_new, set()).add(
                metadata.genotype_old
            )
        for new_genotype, expected_old_genotype in EXPECTED_GENOTYPE_RELATIONSHIPS.items():
            observed_old_genotypes = observed_relationships.get(new_genotype, set())
            if observed_old_genotypes != {expected_old_genotype}:
                errors.append(
                    f"genotype mapping {new_genotype} must be {expected_old_genotype}; "
                    f"found {sorted(observed_old_genotypes)}"
                )
        unexpected_genotypes = sorted(
            set(observed_relationships) - set(EXPECTED_GENOTYPE_RELATIONSHIPS)
        )
        if unexpected_genotypes:
            errors.append(
                "genotype map contains unexpected new genotypes: "
                + ", ".join(unexpected_genotypes)
            )
        metadata_by_accession: dict[str, list[Any]] = {}
        for metadata in genotype_map.records:
            accession = metadata.reference_id.split("_", 1)[0].upper()
            metadata_by_accession.setdefault(accession, []).append(metadata)

        resolved_metadata: dict[str, Any] = {}
        used_mapping_labels: set[str] = set()
        for reference in references:
            exact = genotype_map.by_exact.get(reference.name)
            if exact is not None:
                matches = [exact]
            else:
                accession = reference.name.split("_", 1)[0].upper()
                matches = metadata_by_accession.get(accession, [])
            if len(matches) != 1:
                errors.append(
                    f"reference {reference.name} resolves to {len(matches)} genotype rows"
                )
                continue
            metadata = matches[0]
            if metadata.matched_label in used_mapping_labels:
                errors.append(
                    f"genotype row {metadata.matched_label} is reused by multiple tree leaves"
                )
                continue
            if not all(
                (
                    metadata.genotype_new,
                    metadata.genotype_old,
                    metadata.group_new,
                    metadata.group_old,
                )
            ):
                errors.append(f"reference {reference.name} has incomplete genotype metadata")
                continue
            new_genotype = metadata.genotype_new.strip().lower()
            if not re.fullmatch(r"\d[a-z]", new_genotype):
                errors.append(
                    f"reference {reference.name} has invalid new genotype "
                    f"{metadata.genotype_new}"
                )
                continue
            canonical_old_genotype = re.sub(r"\s+", "", metadata.genotype_old)
            accession = reference.name.split("_", 1)[0].upper()
            expected_leaf_id = (
                f"{accession}_{new_genotype[0]}_{new_genotype[1]}_"
                f"{canonical_old_genotype}"
            )
            if reference.name != expected_leaf_id:
                errors.append(
                    f"reference label {reference.name} must be {expected_leaf_id}"
                )
            used_mapping_labels.add(metadata.matched_label)
            resolved_metadata[reference.name] = metadata

        verification["unique_leaf_mapping"] = (
            len(resolved_metadata) == EXPECTED_TREE_REFERENCE_COUNT
        )
        genotype_counts = Counter(
            metadata.genotype_new for metadata in resolved_metadata.values()
        )
        verification["genotype_count"] = len(genotype_counts)
        verification["minimum_anchors_per_genotype"] = (
            min(genotype_counts.values()) if genotype_counts else 0
        )
        verification["anchors_per_genotype"] = dict(sorted(genotype_counts.items()))
        if len(genotype_counts) != 16:
            errors.append(f"expected 16 new genotypes, found {len(genotype_counts)}")
        if genotype_counts and min(genotype_counts.values()) < 3:
            errors.append("every new genotype must have at least three reference anchors")
    except Exception as exc:
        errors.append(str(exc))
    verification["valid"] = not errors
    return verification


ARTICLE_PROFILE_VERIFICATION = validate_article_reference_profile()


def resolve_tree_reference(tree_reference_id: str) -> dict[str, Any]:
    normalized_id = str(tree_reference_id or DEFAULT_TREE_REFERENCE_ID).strip()
    if normalized_id != DEFAULT_TREE_REFERENCE_ID:
        raise ValueError(
            "The article workflow only allows tree_reference=60 "
            "(60ref_151-850.fas)"
        )
    return TREE_REFERENCES[DEFAULT_TREE_REFERENCE_ID]


def tree_reference_public_config(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": reference["id"],
        "label": reference["label"],
        "name": reference["path"].name,
        "count": reference["count"],
        "available": bool(reference["valid"] and MAFFT_RUNNER is not None),
        "error": reference["error"] or None,
    }


def detect_iqtree_runner() -> tuple[str, str] | None:
    for name in ("iqtree3", "iqtree2", "iqtree"):
        executable = shutil.which(name)
        if executable:
            return ("native", executable)
    if os.name == "nt" and IQTREE_WSL_BINARY and shutil.which("wsl.exe"):
        check = subprocess.run(
            [
                "wsl.exe",
                "--exec",
                "sh",
                "-lc",
                'test -x "$1"',
                "sh",
                IQTREE_WSL_BINARY,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if check.returncode == 0:
            return ("wsl", IQTREE_WSL_BINARY)
    return None


IQTREE_RUNNER = detect_iqtree_runner()


def detect_mafft_runner() -> tuple[str, str] | None:
    executable = shutil.which("mafft")
    if executable:
        return ("native", executable)
    if os.name == "nt" and MAFFT_WSL_BINARY and shutil.which("wsl.exe"):
        check = subprocess.run(
            [
                "wsl.exe",
                "--exec",
                "sh",
                "-lc",
                'test -x "$1"',
                "sh",
                MAFFT_WSL_BINARY,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if check.returncode == 0:
            return ("wsl", MAFFT_WSL_BINARY)
    return None


MAFFT_RUNNER = detect_mafft_runner()


def article_profile_public_config() -> dict[str, Any]:
    return {
        "profile_id": V1_STRICT_PROFILE_ID,
        "version": V1_STRICT_VERSION,
        "mode": V1_STRICT_MODE,
        "legacy_alias": LEGACY_IQTREE_MODE,
        "strict": True,
        "reference_name": TREE_REFERENCE_FASTA.name,
        "reference_count": EXPECTED_TREE_REFERENCE_COUNT,
        "reference_sha256": (
            sha256_file(TREE_REFERENCE_FASTA)
            if TREE_REFERENCE_FASTA.is_file()
            else None
        ),
        "genotype_map_name": GENOTYPE_XLSX.name,
        "genotype_map_sha256": (
            sha256_file(GENOTYPE_XLSX) if GENOTYPE_XLSX.is_file() else None
        ),
        "mafft": {
            "available": MAFFT_RUNNER is not None,
            "validated_version": "7.525",
            "arguments": [
                "--add",
                "samples.fasta",
                "--keeplength",
                TREE_REFERENCE_FASTA.name,
            ],
        },
        "iqtree": {
            "available": IQTREE_RUNNER is not None,
            "validated_version": "3.0.1",
            "model": IQTREE_MODEL,
            "bootstrap_replicates": IQTREE_BOOTSTRAP_REPLICATES,
            "threads": IQTREE_THREADS,
            "bnni": True,
            "arguments": [
                "-s",
                "alignment.fasta",
                "-m",
                IQTREE_MODEL,
                "-bb",
                str(IQTREE_BOOTSTRAP_REPLICATES),
                "-T",
                str(IQTREE_THREADS),
                "-bnni",
            ],
        },
        "typing_rule_id": TYPING_RULE_ID,
        "typing_rule_version": TYPING_RULE_VERSION,
        "auto_accept_min_ufboot": TREE_AUTO_ACCEPT_MIN_SUPPORT,
        "distance_rule": {
            "id": TREE_DISTANCE_RULE_ID,
            "metrics": ["d1", "d2", "distance_margin"],
            "thresholds": ["Tg", "Mg"],
            "calibration_state": "reference_derived_not_loro_validated",
            "loro_validation_required": True,
        },
        "verification": ARTICLE_PROFILE_VERIFICATION,
    }


def terminate_process_tree(
    process: subprocess.Popen,
    wsl_pid_path: Path | None = None,
) -> None:
    if wsl_pid_path is not None and not wsl_pid_path.is_file():
        deadline = time.monotonic() + 2.0
        while process.poll() is None and time.monotonic() < deadline:
            if wsl_pid_path.is_file():
                break
            time.sleep(0.05)
    if wsl_pid_path is not None and wsl_pid_path.is_file():
        try:
            subprocess.run(
                [
                    "wsl.exe",
                    "--exec",
                    "sh",
                    "-lc",
                    (
                        'pidfile="$(wslpath -a "$1")"; '
                        'if [ -r "$pidfile" ]; then '
                        'pid="$(cat "$pidfile")"; '
                        'kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true; '
                        "sleep 0.2; "
                        'kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true; '
                        "fi"
                    ),
                    "sh",
                    str(wsl_pid_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        else:
            process.terminate()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def run_cancellable_process(
    command: list[str],
    cwd: Path,
    cancel_callback: Callable[[], None],
    *,
    stdout=None,
    stderr=None,
    wsl_pid_path: Path | None = None,
) -> int:
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "text": True,
        "stdout": stdout,
        "stderr": stderr,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0,
        )
    else:
        popen_kwargs["start_new_session"] = True
    cancel_callback()
    process = subprocess.Popen(command, **popen_kwargs)
    try:
        while process.poll() is None:
            cancel_callback()
            time.sleep(0.2)
        cancel_callback()
        return int(process.returncode or 0)
    except BaseException:
        terminate_process_tree(process, wsl_pid_path)
        raise
    finally:
        if wsl_pid_path is not None:
            wsl_pid_path.unlink(missing_ok=True)


def run_mafft_add_alignment(
    sample_fasta: Path,
    reference_fasta: Path,
    output_fasta: Path,
    cancel_callback: Callable[[], None] | None = None,
) -> Path:
    if MAFFT_RUNNER is None:
        raise RuntimeError("MAFFT 未安装或不可执行")

    sample_fasta = Path(sample_fasta).resolve()
    reference_fasta = Path(reference_fasta).resolve()
    output_fasta = Path(output_fasta).resolve()
    output_fasta.parent.mkdir(parents=True, exist_ok=True)
    log_path = output_fasta.parent / f"{output_fasta.name}.mafft.log"

    sample_records = load_fasta_records(sample_fasta)
    reference_records = load_fasta_records(reference_fasta)
    if not sample_records:
        raise ValueError("MAFFT 样本 FASTA 中没有序列")
    if not reference_records:
        raise ValueError("MAFFT 参考 FASTA 中没有序列")

    sample_names = [record.name for record in sample_records]
    reference_names = [record.name for record in reference_records]
    if len(sample_names) != len(set(sample_names)):
        raise ValueError("MAFFT 样本 FASTA 中存在重复名称")
    if len(reference_names) != len(set(reference_names)):
        raise ValueError("MAFFT 参考 FASTA 中存在重复名称")
    overlapping_names = sorted(set(sample_names) & set(reference_names))
    if overlapping_names:
        raise ValueError(f"MAFFT 样本名与参考名重复：{', '.join(overlapping_names[:5])}")

    reference_lengths = {len(record.sequence) for record in reference_records}
    if (
        len(reference_lengths) != 1
        or next(iter(reference_lengths), 0) <= 0
    ):
        raise ValueError("MAFFT --keeplength 参考 FASTA 不是等长预对齐序列")
    reference_length = next(iter(reference_lengths))

    runner_type, executable = MAFFT_RUNNER
    if runner_type == "native":
        command = [
            executable,
            "--add",
            str(sample_fasta),
            "--keeplength",
            str(reference_fasta),
        ]
        if cancel_callback is None:
            with output_fasta.open("w", encoding="utf-8", newline="\n") as output_handle:
                completed = subprocess.run(
                    command,
                    cwd=output_fasta.parent,
                    text=True,
                    stdout=output_handle,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            log_path.write_text(completed.stderr or "", encoding="utf-8")
        else:
            with (
                output_fasta.open("w", encoding="utf-8", newline="\n") as output_handle,
                log_path.open("w", encoding="utf-8", newline="\n") as log_handle,
            ):
                returncode = run_cancellable_process(
                    command,
                    output_fasta.parent,
                    cancel_callback,
                    stdout=output_handle,
                    stderr=log_handle,
                )
            completed = subprocess.CompletedProcess(command, returncode, "", "")
    else:
        if cancel_callback is None:
            command = [
                "wsl.exe",
                "--exec",
                "sh",
                "-lc",
                (
                    'set -e; cd "$(wslpath -a "$1")"; '
                    'exec "$6" --add "$(wslpath -a "$2")" '
                    '--keeplength "$(wslpath -a "$3")" > "$4" 2> "$5"'
                ),
                "sh",
                str(output_fasta.parent),
                str(sample_fasta),
                str(reference_fasta),
                output_fasta.name,
                log_path.name,
                executable,
            ]
            completed = subprocess.run(
                command,
                cwd=output_fasta.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        else:
            wsl_pid_path = output_fasta.parent / f".{output_fasta.name}.mafft.pid"
            command = [
                "wsl.exe",
                "--exec",
                "sh",
                "-lc",
                (
                    'set -e; cd "$(wslpath -a "$1")"; '
                    'pidfile="$(wslpath -a "$7")"; '
                    "exec setsid --wait sh -c '"
                    'pidfile="$1"; shift; printf "%s" "$$" > "$pidfile"; '
                    'exec "$@"'
                    "' sh \"$pidfile\" \"$6\" --add \"$(wslpath -a \"$2\")\" "
                    '--keeplength "$(wslpath -a "$3")" > "$4" 2> "$5"'
                ),
                "sh",
                str(output_fasta.parent),
                str(sample_fasta),
                str(reference_fasta),
                output_fasta.name,
                log_path.name,
                executable,
                str(wsl_pid_path),
            ]
            returncode = run_cancellable_process(
                command,
                output_fasta.parent,
                cancel_callback,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                wsl_pid_path=wsl_pid_path,
            )
            completed = subprocess.CompletedProcess(command, returncode, "", "")

    if completed.returncode != 0:
        log_lines = []
        if log_path.exists():
            log_lines.extend(
                log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        log_lines.extend((completed.stdout or "").splitlines())
        log_lines.extend((completed.stderr or "").splitlines())
        output_tail = "\n".join(log_lines[-30:])
        raise RuntimeError(
            f"MAFFT 运行失败（退出码 {completed.returncode}）：\n{output_tail}"
        )
    if not output_fasta.exists() or output_fasta.stat().st_size == 0:
        raise RuntimeError("MAFFT 已结束，但没有生成比对 FASTA")

    aligned_records = load_fasta_records(output_fasta)
    aligned_names = [record.name for record in aligned_records]
    expected_names = [*reference_names, *sample_names]
    if len(aligned_records) != len(expected_names):
        raise RuntimeError(
            "MAFFT 比对结果记录数不正确："
            f"预期 {len(expected_names)}，实际 {len(aligned_records)}"
        )
    if len(aligned_names) != len(set(aligned_names)):
        raise RuntimeError("MAFFT 比对结果中存在重复名称")
    if set(aligned_names) != set(expected_names):
        missing = sorted(set(expected_names) - set(aligned_names))
        unexpected = sorted(set(aligned_names) - set(expected_names))
        raise RuntimeError(
            "MAFFT 比对结果名称不完整："
            f"缺少 {', '.join(missing[:5]) or '无'}；"
            f"多出 {', '.join(unexpected[:5]) or '无'}"
        )
    aligned_by_name = {record.name: record for record in aligned_records}
    changed_references = [
        name
        for name, reference in zip(reference_names, reference_records)
        if aligned_by_name[name].sequence.upper() != reference.sequence.upper()
    ]
    if changed_references:
        raise RuntimeError(
            "MAFFT --add 改变了预对齐参考序列："
            f"{', '.join(changed_references[:5])}"
        )
    aligned_lengths = {len(record.sequence) for record in aligned_records}
    if aligned_lengths != {reference_length}:
        raise RuntimeError(
            "MAFFT --keeplength 比对结果长度不一致："
            f"参考长度 {reference_length}，结果长度 {sorted(aligned_lengths)}"
        )
    unusable_samples = []
    for sample_name in sample_names:
        sample_sequence = aligned_by_name[sample_name].sequence.upper()
        max_compared = max(
            sum(
                sample_base in "ACGT" and reference_base in "ACGT"
                for sample_base, reference_base in zip(
                    sample_sequence,
                    reference.sequence.upper(),
                )
            )
            for reference in reference_records
        )
        if max_compared < 20:
            unusable_samples.append(sample_name)
    if unusable_samples:
        raise RuntimeError(
            "MAFFT 比对后样本与参考的可比较碱基不足 20 bp："
            f"{', '.join(unusable_samples[:5])}"
        )
    return output_fasta


def run_iqtree_alignment(
    alignment_fasta: Path,
    cancel_callback: Callable[[], None] | None = None,
) -> Path:
    if IQTREE_RUNNER is None:
        raise RuntimeError("IQ-TREE 未安装或不可执行")
    alignment_fasta = Path(alignment_fasta).resolve()
    output_dir = alignment_fasta.parent
    runner_type, executable = IQTREE_RUNNER
    iqtree_args = [
        "-s",
        alignment_fasta.name,
        "-m",
        IQTREE_MODEL,
        "-bb",
        str(IQTREE_BOOTSTRAP_REPLICATES),
        "-T",
        str(IQTREE_THREADS),
        "-bnni",
    ]
    wsl_pid_path: Path | None = None
    if runner_type == "native":
        command = [executable, *iqtree_args]
    elif cancel_callback is None:
        command = [
            "wsl.exe",
            "--exec",
            "sh",
            "-lc",
            'set -e; cd "$(wslpath -a "$1")"; shift; exec "$@"',
            "sh",
            str(output_dir),
            executable,
            *iqtree_args,
        ]
    else:
        wsl_pid_path = output_dir / f".{alignment_fasta.name}.iqtree.pid"
        command = [
            "wsl.exe",
            "--exec",
            "sh",
            "-lc",
            (
                'set -e; cd "$(wslpath -a "$1")"; '
                'pidfile="$(wslpath -a "$2")"; shift 2; '
                "exec setsid --wait sh -c '"
                'pidfile="$1"; shift; printf "%s" "$$" > "$pidfile"; '
                'exec "$@"'
                "' sh \"$pidfile\" \"$@\""
            ),
            "sh",
            str(output_dir),
            str(wsl_pid_path),
            executable,
            *iqtree_args,
        ]
    if cancel_callback is None:
        completed = subprocess.run(
            command,
            cwd=output_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        returncode = completed.returncode
        output_tail = "\n".join((completed.stdout or "").splitlines()[-30:])
    else:
        runner_log = output_dir / f"{alignment_fasta.name}.runner.log"
        with runner_log.open("w", encoding="utf-8", newline="\n") as log_handle:
            returncode = run_cancellable_process(
                command,
                output_dir,
                cancel_callback,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                wsl_pid_path=wsl_pid_path,
            )
        output_tail = "\n".join(read_tail_lines(runner_log, 30))
    if returncode != 0:
        raise RuntimeError(f"IQ-TREE 运行失败（退出码 {returncode}）：\n{output_tail}")
    treefile = output_dir / f"{alignment_fasta.name}.treefile"
    if not treefile.exists() or treefile.stat().st_size == 0:
        raise RuntimeError("IQ-TREE 已结束，但没有生成 treefile")
    return treefile


def build_tree_reference_tree(
    result,
    tree_method: str = "nj",
    progress_callback=None,
    tree_reference: dict[str, Any] | None = None,
    cancel_callback: Callable[[], None] | None = None,
) -> None:
    if cancel_callback is not None:
        cancel_callback()
    selected_reference = tree_reference or resolve_tree_reference(
        DEFAULT_TREE_REFERENCE_ID
    )
    tree_reference_fasta: Path = selected_reference["path"]
    expected_reference_count = int(selected_reference["expected_count"])
    references = load_fasta_records(tree_reference_fasta)
    if len(references) != expected_reference_count:
        raise ValueError(f"{tree_reference_fasta.name} could not be loaded completely")
    tree_reference_count = len(references)

    tree_sample_id = result.sample_id
    reference_names = {reference.name for reference in references}
    if tree_sample_id in reference_names:
        tree_sample_id = f"QUERY_{tree_sample_id}"

    sample_fasta = result.output_dir / f"{result.sample_id}_mafft_add.fasta"
    alignment_fasta = result.output_dir / f"{result.sample_id}_alignment.fasta"
    tree_newick = result.output_dir / f"{result.sample_id}_tree_midpoint_increasing.nwk"
    tree_file = result.output_dir / f"{result.sample_id}_tree_midpoint_increasing.treefile"
    tree_svg = result.output_dir / f"{result.sample_id}_tree_midpoint_increasing.svg"
    query_record = type(references[0])(tree_sample_id, result.consensus)
    write_fasta([query_record], sample_fasta)
    run_mafft_add_alignment(
        sample_fasta,
        tree_reference_fasta,
        alignment_fasta,
        cancel_callback=cancel_callback,
    )
    if cancel_callback is not None:
        cancel_callback()
    aligned_records = load_fasta_records(alignment_fasta)
    if is_article_tree_mode(tree_method):
        tree_source = run_iqtree_alignment(
            alignment_fasta,
            cancel_callback=cancel_callback,
        )
        tree = Phylo.read(str(tree_source), "newick")
        title = (
            f"{result.sample_id} OT {tree_reference_count}-reference IQ-TREE "
            f"({IQTREE_MODEL}; UFBoot={IQTREE_BOOTSTRAP_REPLICATES})"
        )
    else:
        tree = build_distance_tree(aligned_records)
        add_bootstrap_support(
            tree,
            aligned_records,
            BOOTSTRAP_REPLICATES,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        title = f"{result.sample_id} OT {tree_reference_count}-reference NJ tree"
    genotype_map = load_genotype_map(GENOTYPE_XLSX)
    result.tree_typing = infer_tree_typing_results(
        tree,
        [tree_sample_id],
        reference_names,
        genotype_map,
    )[tree_sample_id]
    # Type on the original unrooted topology.  Midpoint rooting is a display
    # transformation only and must not affect the genotype decision.
    midpoint_root_and_sort(tree)
    if cancel_callback is not None:
        cancel_callback()
    Phylo.write(tree, str(tree_newick), "newick")
    Phylo.write(tree, str(tree_file), "newick")
    render_tree_svg(
        tree,
        tree_sample_id,
        tree_svg,
        title,
        genotype_map=genotype_map,
    )

    result.analysis_mode = (
        V1_STRICT_MODE if is_article_tree_mode(tree_method) else "tree"
    )
    result.typing_pending = False
    result.alignment_fasta = alignment_fasta
    result.tree_newick = tree_newick
    result.tree_svg = tree_svg
    write_report(result)


app = FastAPI(title=PROJECT_NAME, version=PROJECT_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[*LOCAL_CORS_ORIGINS, *EXTRA_CORS_ORIGINS],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_public_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    return response


@app.get("/")
def index() -> RedirectResponse:
    """Send users to the separate publication-facing V1 workbench."""
    return RedirectResponse(FRONTEND_URL, status_code=307)


@app.get("/api/health")
@app.get("/health")
def health() -> dict[str, Any]:
    tree_references = [
        tree_reference_public_config(TREE_REFERENCES[DEFAULT_TREE_REFERENCE_ID])
    ]
    return {
        "project_id": PROJECT_ID,
        "project_name": PROJECT_NAME,
        "project_name_en": PROJECT_NAME_EN,
        "project_version": PROJECT_VERSION,
        "service_id": API_SERVICE_ID,
        "status": "ok",
        "reference_available": bool(
            REFERENCE_FASTA
            and REFERENCE_FASTA.exists()
            and GENOTYPE_XLSX
            and GENOTYPE_XLSX.exists()
        ),
        "reference_name": REFERENCE_FASTA.name if REFERENCE_FASTA else None,
        "reference_count": REFERENCE_COUNT,
        "tree_reference_available": TREE_REFERENCE_VALID,
        "tree_reference_error": TREE_REFERENCE_ERROR or None,
        "tree_reference_name": TREE_REFERENCE_FASTA.name,
        "tree_reference_count": TREE_REFERENCE_COUNT,
        "default_tree_reference": DEFAULT_TREE_REFERENCE_ID,
        "tree_references": tree_references,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "mafft_available": MAFFT_RUNNER is not None,
        "mafft_method": "mafft --add samples.fasta --keeplength reference.fasta",
        "iqtree_available": IQTREE_RUNNER is not None,
        "iqtree_model": IQTREE_MODEL,
        "iqtree_bootstrap_replicates": IQTREE_BOOTSTRAP_REPLICATES,
        "iqtree_threads": IQTREE_THREADS,
        "detected_cpu_count": DETECTED_CPU_COUNT,
        "modes": [V1_STRICT_MODE],
        "genotype_evidence_source": "phylogenetic_tree_only",
        "retention_hours": RESULT_RETENTION_HOURS,
        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        "article_profile": article_profile_public_config(),
    }


@app.get("/ready")
def readiness() -> JSONResponse:
    checks = {
        "reference_profile": bool(ARTICLE_PROFILE_VERIFICATION["valid"]),
        "mafft": MAFFT_RUNNER is not None,
        "iqtree": IQTREE_RUNNER is not None,
        "results_directory": WEB_RESULTS.parent.is_dir(),
    }
    ready = all(checks.values())
    return JSONResponse(
        {
            "project_id": PROJECT_ID,
            "project_version": PROJECT_VERSION,
            "service_id": API_SERVICE_ID,
            "status": "ready" if ready else "degraded",
            "checks": checks,
        },
        status_code=200 if ready else 503,
    )


@app.get("/api/config")
def config() -> dict[str, Any]:
    tree_references = [
        tree_reference_public_config(TREE_REFERENCES[DEFAULT_TREE_REFERENCE_ID])
    ]
    tree_available = bool(
        tree_references[0]["available"] and ARTICLE_PROFILE_VERIFICATION["valid"]
    )
    iqtree_available = tree_available and IQTREE_RUNNER is not None
    available_modes = [
        {
            "id": V1_STRICT_MODE,
            "name": "严格 V1.0｜文章建树分型（60ref + IQ-TREE）",
            "async": True,
            "available": iqtree_available,
            "version": V1_STRICT_VERSION,
            "legacy_alias": LEGACY_IQTREE_MODE,
        },
    ]
    return {
        "project_id": PROJECT_ID,
        "project_name": PROJECT_NAME,
        "project_name_en": PROJECT_NAME_EN,
        "project_version": PROJECT_VERSION,
        "service_id": API_SERVICE_ID,
        "default_mode": V1_STRICT_MODE,
        "available_modes": available_modes,
        "tree_available": tree_available,
        "iqtree_available": iqtree_available,
        "mafft_available": MAFFT_RUNNER is not None,
        "mafft_method": "mafft --add samples.fasta --keeplength reference.fasta",
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "reference_name": REFERENCE_FASTA.name if REFERENCE_FASTA else "",
        "reference_count": REFERENCE_COUNT,
        "tree_reference_name": TREE_REFERENCE_FASTA.name,
        "tree_reference_count": TREE_REFERENCE_COUNT,
        "default_tree_reference": DEFAULT_TREE_REFERENCE_ID,
        "tree_references": tree_references,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "iqtree_model": IQTREE_MODEL,
        "iqtree_bootstrap_replicates": IQTREE_BOOTSTRAP_REPLICATES,
        "iqtree_threads": IQTREE_THREADS,
        "detected_cpu_count": DETECTED_CPU_COUNT,
        "iqtree_bnni": True,
        "default_quality_threshold": DEFAULT_QUALITY_THRESHOLD,
        "retention_hours": RESULT_RETENTION_HOURS,
        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        "genotype_evidence_source": "phylogenetic_tree_only",
        "article_profile": article_profile_public_config(),
    }


@app.post("/api/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    sample_id: str = Form(...),
    mode: str = Form(V1_STRICT_MODE),
    tree_reference: str = Form(DEFAULT_TREE_REFERENCE_ID),
    quality_threshold: int = Form(DEFAULT_QUALITY_THRESHOLD),
    min_overlap: int = Form(DEFAULT_MIN_OVERLAP),
    submission_id: str | None = Form(None),
    submission_sample_count: int = Form(1),
    read1_file: UploadFile | None = File(None),
    read2_file: UploadFile | None = File(None),
    forward_file: UploadFile | None = File(None),
    reverse_file: UploadFile | None = File(None),
) -> JSONResponse:
    cleanup_expired_results()
    if mode not in ALLOWED_MODES:
        raise HTTPException(
            status_code=400,
            detail=(
                "The article workflow only accepts mode=v1_strict_article, "
                "mode=iqtree (legacy alias), or mode=prepare"
            ),
        )
    mode = canonical_article_tree_mode(mode)
    try:
        selected_tree_reference = resolve_tree_reference(tree_reference)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not 0 <= quality_threshold <= 40:
        raise HTTPException(status_code=400, detail="quality_threshold must be between 0 and 40")
    if not 20 <= min_overlap <= 200:
        raise HTTPException(status_code=400, detail="min_overlap must be between 20 and 200")
    if not 1 <= submission_sample_count <= MAX_BATCH_SAMPLE_COUNT:
        raise HTTPException(
            status_code=400,
            detail=(
                "submission_sample_count must be between 1 and "
                f"{MAX_BATCH_SAMPLE_COUNT}"
            ),
        )
    if (
        REFERENCE_FASTA is None
        or GENOTYPE_XLSX is None
        or not REFERENCE_FASTA.exists()
        or not GENOTYPE_XLSX.exists()
    ):
        raise HTTPException(status_code=500, detail="355ref FASTA or genotype workbook was not found")
    if (is_article_tree_mode(mode) or mode == "prepare") and not selected_tree_reference["valid"]:
        raise HTTPException(
            status_code=500,
            detail=selected_tree_reference["error"],
        )
    if (is_article_tree_mode(mode) or mode == "prepare") and not ARTICLE_PROFILE_VERIFICATION["valid"]:
        raise HTTPException(
            status_code=500,
            detail=(
                "Article reference profile verification failed: "
                + "; ".join(ARTICLE_PROFILE_VERIFICATION["errors"])
            ),
        )
    if is_article_tree_mode(mode) and MAFFT_RUNNER is None:
        raise HTTPException(status_code=503, detail="MAFFT is not available")
    if is_article_tree_mode(mode) and IQTREE_RUNNER is None:
        raise HTTPException(status_code=503, detail="IQ-TREE is not available")
    first_upload = read1_file or forward_file
    second_upload = read2_file or reverse_file
    if first_upload is None and second_upload is None:
        raise HTTPException(status_code=400, detail="At least one AB1 sequencing file is required")

    job_id = new_job_id()
    normalized_submission_id = normalize_submission_id(
        submission_id,
        submission_sample_count,
    )
    job_dir = WEB_RESULTS / normalized_submission_id / job_id
    upload_dir = job_dir / "uploads"
    output_dir = job_dir / "outputs"
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    display_sample_id = sample_id.strip()
    safe_sample_id = sanitize_sample_id(display_sample_id)
    forward_path = upload_dir / "read1.ab1" if first_upload is not None else None
    reverse_path = upload_dir / "read2.ab1" if second_upload is not None else None
    if first_upload is not None and forward_path is not None:
        await save_ab1_upload(first_upload, forward_path)
    if second_upload is not None and reverse_path is not None:
        await save_ab1_upload(second_upload, reverse_path)
    primary_path = forward_path or reverse_path
    assert primary_path is not None
    pipeline_reverse_path = reverse_path if forward_path is not None else None
    single_read_label = "reverse" if forward_path is None and reverse_path is not None else "read1"

    queued_payload = {
        "job_id": job_id,
        "status": "queued",
        "mode": mode,
        "analysis_mode": mode,
        "workflow_profile_id": V1_STRICT_PROFILE_ID if is_article_tree_mode(mode) else None,
        "workflow_version": V1_STRICT_VERSION if is_article_tree_mode(mode) else None,
        "strict_article_mode": is_article_tree_mode(mode),
        "tree_reference": selected_tree_reference["id"],
        "tree_reference_id": selected_tree_reference["id"],
        "tree_reference_name": selected_tree_reference["path"].name,
        "tree_reference_count": selected_tree_reference["count"],
        "sample_id": display_sample_id,
        "safe_sample_id": safe_sample_id,
        "submission_id": normalized_submission_id,
        "submission_sample_count": submission_sample_count,
        "input_read_mode": "paired" if forward_path is not None and reverse_path is not None else "single",
        "quality_threshold": quality_threshold,
        "min_overlap": min_overlap,
        "created_at": time.time(),
        "updated_at": time.time(),
        "summary": None,
        "files": {},
        "error": None,
        "progress": {
            "stage": "queued",
            "message": "任务已提交，等待开始分析",
            "percent": 0,
            "elapsed_seconds": 0,
            "log_tail": [],
        },
    }
    write_job(job_dir, queued_payload)

    background_tasks.add_task(
        run_analysis_job,
        job_dir,
        safe_sample_id,
        primary_path,
        pipeline_reverse_path,
        mode,
        single_read_label,
        quality_threshold,
        min_overlap,
        selected_tree_reference["id"],
    )
    return JSONResponse(read_job(job_dir))


@app.post("/api/batch-tree")
async def create_batch_tree(
    background_tasks: BackgroundTasks,
    job_ids: str = Form(...),
    tree_method: str = Form(V1_STRICT_MODE),
    tree_reference: str = Form(DEFAULT_TREE_REFERENCE_ID),
    submission_id: str | None = Form(None),
) -> JSONResponse:
    cleanup_expired_results()
    if not is_article_tree_mode(tree_method):
        raise HTTPException(
            status_code=400,
            detail=(
                "The article workflow requires tree_method=v1_strict_article "
                "or the legacy iqtree alias"
            ),
        )
    tree_method = canonical_article_tree_mode(tree_method)
    try:
        selected_tree_reference = resolve_tree_reference(tree_reference)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if selected_tree_reference["id"] != "60":
        raise HTTPException(
            status_code=400,
            detail="The article workflow is fixed to 60ref_151-850.fas",
        )
    if not selected_tree_reference["valid"]:
        raise HTTPException(
            status_code=500,
            detail=selected_tree_reference["error"],
        )
    if not ARTICLE_PROFILE_VERIFICATION["valid"]:
        raise HTTPException(
            status_code=500,
            detail=(
                "Article reference profile verification failed: "
                + "; ".join(ARTICLE_PROFILE_VERIFICATION["errors"])
            ),
        )
    if MAFFT_RUNNER is None:
        raise HTTPException(status_code=503, detail="MAFFT is not available")
    if IQTREE_RUNNER is None:
        raise HTTPException(status_code=503, detail="IQ-TREE is not available")
    try:
        parsed_job_ids = json.loads(job_ids)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="job_ids must be a JSON array") from exc
    if (
        not isinstance(parsed_job_ids, list)
        or not parsed_job_ids
        or len(parsed_job_ids) > MAX_BATCH_SAMPLE_COUNT
        or any(not isinstance(job_id, str) for job_id in parsed_job_ids)
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "job_ids must contain 1 to "
                f"{MAX_BATCH_SAMPLE_COUNT} analysis job IDs"
            ),
        )
    unique_job_ids = list(dict.fromkeys(parsed_job_ids))
    source_submission_ids: set[str] = set()
    source_submission_sample_counts: set[int] = set()
    for source_job_id in unique_job_ids:
        source_job = read_job(resolve_job_dir(source_job_id))
        if source_job.get("status") != "completed" or not source_job.get("summary"):
            raise HTTPException(status_code=400, detail=f"Source analysis is not completed: {source_job_id}")
        if (
            source_job.get("mode") != "prepare"
            or source_job["summary"].get("target_status") != "pending_phylogeny"
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Source analysis must be a completed mode=prepare task "
                    f"pending phylogeny: {source_job_id}"
                ),
            )
        source_submission_id = str(source_job.get("submission_id") or "")
        if not SUBMISSION_DIR_PATTERN.fullmatch(source_submission_id):
            raise HTTPException(
                status_code=400,
                detail=f"Source analysis has no valid article-workflow submission: {source_job_id}",
            )
        source_submission_ids.add(source_submission_id)
        source_submission_sample_counts.add(
            int(source_job.get("submission_sample_count") or 0)
        )

    if len(source_submission_ids) != 1:
        raise HTTPException(
            status_code=400,
            detail="All source analyses must belong to the same v3 submission",
        )
    if len(source_submission_sample_counts) != 1:
        raise HTTPException(
            status_code=400,
            detail="Source analyses disagree on the submission sample count",
        )
    source_submission_id = next(iter(source_submission_ids))
    if submission_id and submission_id.strip() != source_submission_id:
        raise HTTPException(
            status_code=400,
            detail="submission_id must match the source analysis submission",
        )

    job_id = new_job_id()
    normalized_submission_id = source_submission_id
    submission_sample_count = next(iter(source_submission_sample_counts))
    job_dir = WEB_RESULTS / normalized_submission_id / job_id
    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
    queued_payload = {
        "job_id": job_id,
        "status": "queued",
        "mode": "batch_iqtree",
        "analysis_mode": V1_STRICT_MODE,
        "workflow_profile_id": V1_STRICT_PROFILE_ID,
        "workflow_version": V1_STRICT_VERSION,
        "strict_article_mode": True,
        "sample_id": f"{len(unique_job_ids)} 个样本联合树",
        "submission_id": normalized_submission_id,
        "submission_sample_count": submission_sample_count,
        "created_at": time.time(),
        "updated_at": time.time(),
        "summary": None,
        "files": {},
        "error": None,
        "source_job_ids": unique_job_ids,
        "tree_method": tree_method,
        "tree_reference": selected_tree_reference["id"],
        "tree_reference_id": selected_tree_reference["id"],
        "tree_reference_name": selected_tree_reference["path"].name,
        "tree_reference_count": selected_tree_reference["count"],
        "progress": {
            "stage": "queued",
            "message": "联合树任务已提交",
            "percent": 0,
            "elapsed_seconds": 0,
            "log_tail": [],
        },
    }
    write_job(job_dir, queued_payload)
    background_tasks.add_task(
        run_batch_tree_job,
        job_dir,
        unique_job_ids,
        tree_method,
        selected_tree_reference["id"],
    )
    return JSONResponse(read_job(job_dir))


@app.post("/api/fasta-analysis")
async def create_fasta_analysis(
    background_tasks: BackgroundTasks,
    fasta_file: UploadFile = File(...),
    analysis_mode: str = Form(V1_STRICT_MODE),
    tree_reference: str = Form(DEFAULT_TREE_REFERENCE_ID),
    submission_id: str | None = Form(None),
) -> JSONResponse:
    cleanup_expired_results()
    if not is_article_tree_mode(analysis_mode):
        raise HTTPException(
            status_code=400,
            detail=(
                "The article FASTA workflow requires analysis_mode=v1_strict_article "
                "or the legacy iqtree alias"
            ),
        )
    analysis_mode = canonical_article_tree_mode(analysis_mode)
    try:
        selected_tree_reference = resolve_tree_reference(tree_reference)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if selected_tree_reference["id"] != DEFAULT_TREE_REFERENCE_ID:
        raise HTTPException(
            status_code=400,
            detail="The article workflow is fixed to 60ref_151-850.fas",
        )
    if not selected_tree_reference["valid"]:
        raise HTTPException(status_code=500, detail=selected_tree_reference["error"])
    if GENOTYPE_XLSX is None or not GENOTYPE_XLSX.exists():
        raise HTTPException(status_code=500, detail="Genotype workbook was not found")
    if not ARTICLE_PROFILE_VERIFICATION["valid"]:
        raise HTTPException(
            status_code=500,
            detail=(
                "Article reference profile verification failed: "
                + "; ".join(ARTICLE_PROFILE_VERIFICATION["errors"])
            ),
        )
    if MAFFT_RUNNER is None:
        raise HTTPException(status_code=503, detail="MAFFT is not available")
    if IQTREE_RUNNER is None:
        raise HTTPException(status_code=503, detail="IQ-TREE is not available")

    records = await read_fasta_upload(fasta_file)
    sample_count = len(records)
    normalized_submission_id = normalize_submission_id(submission_id, sample_count)
    job_id = new_job_id()
    job_dir = WEB_RESULTS / normalized_submission_id / job_id
    upload_dir = job_dir / "uploads"
    output_dir = job_dir / "outputs"
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    uploaded_fasta = upload_dir / "input_sequences.fasta"
    write_fasta(records, uploaded_fasta)

    queued_payload = {
        "job_id": job_id,
        "status": "queued",
        "mode": "fasta_iqtree",
        "analysis_mode": analysis_mode,
        "workflow_profile_id": V1_STRICT_PROFILE_ID,
        "workflow_version": V1_STRICT_VERSION,
        "strict_article_mode": True,
        "sample_id": f"{sample_count} 个 FASTA 序列联合树",
        "submission_id": normalized_submission_id,
        "submission_sample_count": sample_count,
        "input_read_mode": "fasta",
        "input_filename": fasta_file.filename or "input.fasta",
        "sequence_names": [record.name for record in records],
        "sequence_lengths": [len(record.sequence.replace("-", "")) for record in records],
        "created_at": time.time(),
        "updated_at": time.time(),
        "summary": None,
        "files": {},
        "error": None,
        "tree_method": V1_STRICT_MODE,
        "tree_reference": selected_tree_reference["id"],
        "tree_reference_id": selected_tree_reference["id"],
        "tree_reference_name": selected_tree_reference["path"].name,
        "tree_reference_count": selected_tree_reference["count"],
        "progress": {
            "stage": "queued",
            "message": f"已读取 {sample_count} 条 FASTA 序列，等待固定参考比对",
            "percent": 0,
            "elapsed_seconds": 0,
            "log_tail": [],
        },
    }
    write_job(job_dir, queued_payload)
    background_tasks.add_task(
        run_batch_tree_job,
        job_dir,
        [],
        V1_STRICT_MODE,
        selected_tree_reference["id"],
        uploaded_fasta,
    )
    return JSONResponse(read_job(job_dir))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job_dir = resolve_job_dir(job_id)
    with get_job_lock(job_dir):
        payload = read_job(job_dir)
    return enrich_job_status(job_dir, payload)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> JSONResponse:
    job_dir = resolve_job_dir(job_id)
    with get_job_lock(job_dir):
        payload = read_job(job_dir)
        if payload.get("status") in TERMINAL_JOB_STATUSES:
            return JSONResponse(enrich_job_status(job_dir, payload))

        cancelled_at = float(payload.get("cancelled_at") or time.time())
        marker = cancel_marker_path(job_dir)
        marker_temp = marker.with_name(f".{marker.name}.{uuid.uuid4().hex}.tmp")
        marker_temp.write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "requested_at": cancelled_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        marker_temp.replace(marker)

        previous_status = str(payload.get("status") or "running")
        progress = dict(payload.get("progress") or {})
        progress.update(
            {
                "stage": "cancelled",
                "message": "分析已停止",
                "percent": int(progress.get("percent") or 0),
                "elapsed_seconds": max(
                    0,
                    int(time.time() - float(payload.get("created_at") or time.time())),
                ),
            }
        )
        payload.update(
            {
                "status": "cancelled",
                "cancel_requested": True,
                "cancelled_at": cancelled_at,
                "cancelled_from_status": previous_status,
                "updated_at": time.time(),
                "error": None,
                "progress": progress,
            }
        )
        write_job(job_dir, payload)
        return JSONResponse(enrich_job_status(job_dir, payload))


@app.get("/api/jobs/{job_id}/files/{filename}")
def get_job_file(job_id: str, filename: str) -> FileResponse:
    job_dir = resolve_job_dir(job_id)
    safe_name = Path(filename).name
    output_path = job_dir / "outputs" / safe_name
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="Result file not found")
    media_type = media_type_for(output_path)
    return FileResponse(
        output_path,
        media_type=media_type,
        filename=safe_name,
        content_disposition_type=content_disposition_for(output_path),
    )


@bounded_analysis_job
def run_analysis_job(
    job_dir: Path,
    safe_sample_id: str,
    forward_path: Path,
    reverse_path: Path | None,
    mode: str,
    single_read_label: str = "forward",
    quality_threshold: int = DEFAULT_QUALITY_THRESHOLD,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
    tree_reference_id: str = DEFAULT_TREE_REFERENCE_ID,
) -> None:
    try:
        raise_if_cancelled(job_dir)
    except AnalysisCancelled:
        finalize_cancelled_job(job_dir)
        return
    with get_job_lock(job_dir):
        payload = read_job(job_dir)
        if payload.get("status") == "cancelled":
            return
        payload.update({"status": "running", "updated_at": time.time()})
        payload["progress"] = make_progress(payload, "prepare", "开始分析任务", 5)
        write_job(job_dir, payload)

    def progress_callback(stage: str, message: str, percent: int | None = None) -> None:
        update_job_progress(job_dir, stage, message, percent)

    try:
        selected_tree_reference = resolve_tree_reference(tree_reference_id)
        if (is_article_tree_mode(mode) or mode == "prepare") and not selected_tree_reference["valid"]:
            raise ValueError(selected_tree_reference["error"])
        selected_tree_reference_path: Path = selected_tree_reference["path"]
        selected_tree_reference_count = int(selected_tree_reference["count"])
        qc_message = (
            "正在进行两个 AB1 文件的独立峰图质控"
            if reverse_path is not None
            else "正在进行单个 AB1 文件峰图质控"
        )
        update_job_progress(job_dir, "qc", qc_message, 18)
        result = run_355_pipeline(
            sample_id=safe_sample_id,
            forward_ab1=forward_path,
            reverse_ab1=reverse_path,
            full_reference_fasta=REFERENCE_FASTA,
            genotype_xlsx=GENOTYPE_XLSX,
            output_dir=job_dir / "outputs",
            cache_dir=REFERENCE_CACHE,
            force_rebuild_reference=False,
            analysis_mode="fast",
            quality_threshold=quality_threshold,
            min_overlap=min_overlap,
            allow_single_read=True,
            single_read_label=single_read_label,
            cancel_callback=lambda: raise_if_cancelled(job_dir),
        )
        raise_if_cancelled(job_dir)
        result.analysis_mode = mode
        result.typing_pending = True
        write_report(result)
        if is_article_tree_mode(mode):
            update_job_progress(
                job_dir,
                "iqtree",
                (
                    f"正在运行 IQ-TREE：{IQTREE_MODEL}，"
                    f"UFBoot {IQTREE_BOOTSTRAP_REPLICATES}，{IQTREE_THREADS} 线程，BNNI"
                ),
                42,
            )
            build_tree_reference_tree(
                result,
                tree_method=V1_STRICT_MODE,
                tree_reference=selected_tree_reference,
                cancel_callback=lambda: raise_if_cancelled(job_dir),
            )
        elif mode == "prepare":
            update_job_progress(
                job_dir,
                "pending_phylogeny",
                "质控、定向与拼接已完成，等待联合 IQ-TREE 分型",
                95,
            )
        else:
            raise ValueError(
                "The article workflow only accepts v1_strict_article or prepare"
            )
        classification = result.classification
        tree_typing = result.tree_typing
        pending_phylogeny = mode == "prepare"
        final_new_genotype = (
            "" if pending_phylogeny else tree_typing.predicted_new_genotype
        )
        final_old_genotype = (
            "" if pending_phylogeny else tree_typing.predicted_old_genotype
        )
        final_new_group = (
            "" if pending_phylogeny else tree_typing.predicted_new_group
        )
        final_old_group = (
            "" if pending_phylogeny else tree_typing.predicted_old_group
        )
        read_direction = (
            "paired"
            if result.read_mode == "paired"
            else "reverse"
            if "reverse_complement" in result.read_orientation
            else "forward"
        )
        second_hit = next(
            (
                hit
                for hit in classification.hits
                if hit.new_genotype == classification.second_new_genotype
            ),
            None,
        )
        summary = {
            "predicted_type": final_new_genotype,
            "predicted_group": final_new_group,
            "predicted_old_type": final_old_genotype,
            "predicted_old_group": final_old_group,
            "confidence": "pending" if pending_phylogeny else tree_typing.confidence,
            "is_target": False if pending_phylogeny else tree_typing.auto_accepted,
            "target_status": "pending_phylogeny" if pending_phylogeny else tree_typing.status,
            "typing_source": "pending_phylogeny" if pending_phylogeny else "phylogenetic_tree",
            "typing_method": (
                None if pending_phylogeny else tree_typing.method
            ),
            "typing_rule_id": TYPING_RULE_ID,
            "typing_rule_version": TYPING_RULE_VERSION,
            "similarity_predicted_type": None,
            "similarity_predicted_group": None,
            "similarity_predicted_old_type": None,
            "similarity_predicted_old_group": None,
            "tree_typing_support": tree_typing.support if tree_typing else None,
            "tree_typing_status": tree_typing.status if tree_typing else None,
            "tree_typing_references": list(tree_typing.reference_names) if tree_typing else [],
            "tree_typing_candidate_genotypes": list(tree_typing.candidate_genotypes) if tree_typing else [],
            "tree_typing_nearest_reference": tree_typing.nearest_reference if tree_typing else None,
            "tree_typing_nearest_distance": tree_typing.nearest_distance if tree_typing else None,
            "tree_typing_nearest_genotypes": list(tree_typing.nearest_genotypes) if tree_typing else [],
            "tree_typing_second_nearest_genotype": tree_typing.second_nearest_genotype if tree_typing else None,
            "tree_typing_second_nearest_distance": tree_typing.second_nearest_distance if tree_typing else None,
            "tree_typing_distance_margin": tree_typing.distance_margin if tree_typing else None,
            "tree_typing_reference_distance_threshold": tree_typing.reference_distance_threshold if tree_typing else None,
            "tree_typing_reference_margin_threshold": tree_typing.reference_margin_threshold if tree_typing else None,
            "tree_typing_topology_distance_concordant": tree_typing.topology_distance_concordant if tree_typing else None,
            "tree_typing_distance_rule_id": tree_typing.distance_rule_id if tree_typing else TREE_DISTANCE_RULE_ID,
            "tree_typing_distance_calibration_state": tree_typing.distance_calibration_state if tree_typing else "reference_derived_not_loro_validated",
            "tree_typing_reference_monophyletic": tree_typing.reference_monophyletic if tree_typing else False,
            "tree_typing_auto_accepted": tree_typing.auto_accepted if tree_typing else False,
            "tree_typing_decision_reason": tree_typing.decision_reason if tree_typing else "pending_phylogeny",
            "best_hit": None,
            "best_identity": None,
            "second_type": None,
            "second_old_type": None,
            "second_group": None,
            "second_identity": None,
            "consensus_length": result.consensus_length,
            "analysis_mode": result.analysis_mode,
            "workflow_profile_id": (
                V1_STRICT_PROFILE_ID if is_article_tree_mode(mode) else None
            ),
            "workflow_version": (
                V1_STRICT_VERSION if is_article_tree_mode(mode) else None
            ),
            "strict_article_mode": is_article_tree_mode(mode),
            "read_mode": result.read_mode,
            "read_direction": read_direction,
            "read_orientation": result.read_orientation,
            "quality_status": result.quality_status,
            "quality_note": result.quality_note,
            "quality_threshold": result.quality_threshold,
            "min_overlap": result.min_overlap,
            "orientation_identity": (
                round(result.orientation_identity, 5)
                if result.orientation_identity is not None
                else None
            ),
            "orientation_method": "参考库双链 seed-and-extend 仅用于定向与拼接",
            "trace_quality": [asdict(metric) for metric in result.trace_quality],
            "tree_rooting": "midpoint" if result.tree_newick else None,
            "tree_ordering": "increasing" if result.tree_newick else None,
            "tree_generated": bool(result.tree_newick),
            "typing_reference_name": TREE_REFERENCE_FASTA.name,
            "typing_reference_count": EXPECTED_TREE_REFERENCE_COUNT,
            "tree_reference_id": (
                selected_tree_reference["id"]
            ),
            "tree_reference_name": (
                selected_tree_reference_path.name
            ),
            "tree_reference_count": (
                selected_tree_reference_count
            ),
            "alignment_method": (
                "mafft --add --keeplength" if result.alignment_fasta else None
            ),
            "mafft_command": (
                f"mafft --add {result.sample_id}_mafft_add.fasta --keeplength "
                f"{selected_tree_reference_path.name} > {result.alignment_fasta.name}"
                if result.alignment_fasta
                else None
            ),
            "tree_method": (
                f"IQ-TREE {IQTREE_MODEL}"
                if is_article_tree_mode(mode) and result.tree_newick
                else "NJ"
                if result.tree_newick
                else None
            ),
            "bootstrap_replicates": (
                IQTREE_BOOTSTRAP_REPLICATES
                if is_article_tree_mode(mode) and result.tree_newick
                else BOOTSTRAP_REPLICATES
                if result.tree_newick
                else 0
            ),
            "iqtree_command": (
                f"iqtree -s {result.alignment_fasta.name} -m {IQTREE_MODEL} "
                f"-bb {IQTREE_BOOTSTRAP_REPLICATES} -T {IQTREE_THREADS} -bnni"
                if is_article_tree_mode(mode) and result.alignment_fasta
                else None
            ),
            "typing_tree_source": (
                f"{result.alignment_fasta.name}.treefile"
                if is_article_tree_mode(mode) and result.alignment_fasta
                else None
            ),
            "typing_tree_rooted": False if tree_typing else None,
            "warnings": [
                *([result.quality_note] if result.quality_status != "passed" else []),
                *(
                    [f"系统发育树自动判型未通过：{tree_typing.decision_reason}"]
                    if tree_typing is not None and tree_typing.status != "matched"
                    else []
                ),
            ],
            "top_hits": [],
        }
        files = build_file_urls(payload["job_id"], result.output_dir)
        with get_job_lock(job_dir):
            raise_if_cancelled(job_dir)
            payload = read_job(job_dir)
            payload.update(
                {
                    "status": "completed",
                    "updated_at": time.time(),
                    "summary": summary,
                    "files": files,
                    "error": None,
                    "progress": make_progress(payload, "completed", "分析完成", 100),
                }
            )
            write_job(job_dir, payload)
    except AnalysisCancelled:
        finalize_cancelled_job(job_dir)
    except Exception as exc:
        if cancellation_requested(job_dir):
            finalize_cancelled_job(job_dir)
            return
        error_message = format_error_for_user(exc)
        if isinstance(exc, TraceQualityError):
            error_message = f"质控失败：{error_message}"
        with get_job_lock(job_dir):
            payload = read_job(job_dir)
            if payload.get("status") == "cancelled":
                return
            payload.update(
                {
                    "status": "failed",
                    "updated_at": time.time(),
                    "error": error_message,
                    "progress": make_progress(payload, "failed", error_message, 100),
                }
            )
            write_job(job_dir, payload)


def apply_joint_tree_typing_to_source_job(
    source_job_id: str,
    tree_typing: dict[str, Any],
    joint_tree_job_id: str,
    tree_method: str,
    tree_reference_id: str,
    tree_reference_name: str,
    tree_reference_count: int,
) -> None:
    source_job_dir = resolve_job_dir(source_job_id)
    payload = read_job(source_job_dir)
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return
    summary.update(
        {
            "similarity_predicted_type": None,
            "similarity_predicted_group": None,
            "similarity_predicted_old_type": None,
            "similarity_predicted_old_group": None,
            "best_hit": None,
            "top_hits": [],
            "predicted_type": tree_typing["predicted_new_genotype"],
            "predicted_group": tree_typing["predicted_new_group"],
            "predicted_old_type": tree_typing["predicted_old_genotype"],
            "predicted_old_group": tree_typing["predicted_old_group"],
            "confidence": tree_typing["confidence"],
            "is_target": tree_typing["auto_accepted"],
            "target_status": tree_typing["status"],
            "typing_source": "phylogenetic_tree",
            "typing_method": tree_typing["method"],
            "analysis_mode": V1_STRICT_MODE,
            "workflow_profile_id": V1_STRICT_PROFILE_ID,
            "workflow_version": V1_STRICT_VERSION,
            "strict_article_mode": True,
            "typing_rule_id": tree_typing["typing_rule_id"],
            "typing_rule_version": tree_typing["typing_rule_version"],
            "tree_typing_support": tree_typing["support"],
            "tree_typing_status": tree_typing["status"],
            "tree_typing_references": tree_typing["reference_names"],
            "tree_typing_candidate_genotypes": tree_typing["candidate_genotypes"],
            "tree_typing_nearest_reference": tree_typing["nearest_reference"],
            "tree_typing_nearest_distance": tree_typing["nearest_distance"],
            "tree_typing_nearest_genotypes": tree_typing.get("nearest_genotypes", []),
            "tree_typing_second_nearest_genotype": tree_typing.get("second_nearest_genotype"),
            "tree_typing_second_nearest_distance": tree_typing.get("second_nearest_distance"),
            "tree_typing_distance_margin": tree_typing.get("distance_margin"),
            "tree_typing_reference_distance_threshold": tree_typing.get("reference_distance_threshold"),
            "tree_typing_reference_margin_threshold": tree_typing.get("reference_margin_threshold"),
            "tree_typing_topology_distance_concordant": tree_typing.get("topology_distance_concordant"),
            "tree_typing_distance_rule_id": tree_typing.get("distance_rule_id", TREE_DISTANCE_RULE_ID),
            "tree_typing_distance_calibration_state": tree_typing.get(
                "distance_calibration_state",
                "reference_derived_not_loro_validated",
            ),
            "tree_typing_reference_monophyletic": tree_typing["reference_monophyletic"],
            "tree_typing_auto_accepted": tree_typing["auto_accepted"],
            "tree_typing_decision_reason": tree_typing["decision_reason"],
            "joint_tree_job_id": joint_tree_job_id,
            "joint_tree_method": canonical_article_tree_mode(tree_method),
            "tree_reference_id": tree_reference_id,
            "tree_reference_name": tree_reference_name,
            "tree_reference_count": tree_reference_count,
            "joint_tree_reference": tree_reference_id,
            "joint_tree_reference_id": tree_reference_id,
            "joint_tree_reference_name": tree_reference_name,
            "joint_tree_reference_count": tree_reference_count,
        }
    )
    payload["summary"] = summary
    payload["updated_at"] = time.time()
    write_job(source_job_dir, payload)

    report_files = sorted((source_job_dir / "outputs").glob("*_report.json"))
    if len(report_files) == 1:
        try:
            report = json.loads(report_files[0].read_text(encoding="utf-8"))
            report.update(
                {
                    "similarity_predicted_new_genotype": None,
                    "similarity_predicted_old_genotype": None,
                    "similarity_predicted_new_group": None,
                    "similarity_predicted_old_group": None,
                    "best_hit": None,
                    "top_hits": [],
                    "predicted_new_genotype": tree_typing["predicted_new_genotype"],
                    "predicted_old_genotype": tree_typing["predicted_old_genotype"],
                    "predicted_new_group": tree_typing["predicted_new_group"],
                    "predicted_old_group": tree_typing["predicted_old_group"],
                    "confidence": tree_typing["confidence"],
                    "is_target": tree_typing["auto_accepted"],
                    "target_status": tree_typing["status"],
                    "typing_source": "phylogenetic_tree",
                    "analysis_mode": V1_STRICT_MODE,
                    "workflow_profile_id": V1_STRICT_PROFILE_ID,
                    "workflow_version": V1_STRICT_VERSION,
                    "strict_article_mode": True,
                    "typing_rule_id": tree_typing["typing_rule_id"],
                    "typing_rule_version": tree_typing["typing_rule_version"],
                    "tree_typing_candidate_genotypes": tree_typing["candidate_genotypes"],
                    "tree_typing_nearest_reference": tree_typing["nearest_reference"],
                    "tree_typing_nearest_distance": tree_typing["nearest_distance"],
                    "tree_typing_nearest_genotypes": tree_typing.get("nearest_genotypes", []),
                    "tree_typing_second_nearest_genotype": tree_typing.get("second_nearest_genotype"),
                    "tree_typing_second_nearest_distance": tree_typing.get("second_nearest_distance"),
                    "tree_typing_distance_margin": tree_typing.get("distance_margin"),
                    "tree_typing_reference_distance_threshold": tree_typing.get("reference_distance_threshold"),
                    "tree_typing_reference_margin_threshold": tree_typing.get("reference_margin_threshold"),
                    "tree_typing_topology_distance_concordant": tree_typing.get("topology_distance_concordant"),
                    "tree_typing_distance_rule_id": tree_typing.get("distance_rule_id", TREE_DISTANCE_RULE_ID),
                    "tree_typing_distance_calibration_state": tree_typing.get(
                        "distance_calibration_state",
                        "reference_derived_not_loro_validated",
                    ),
                    "tree_typing_reference_monophyletic": tree_typing["reference_monophyletic"],
                    "tree_typing_auto_accepted": tree_typing["auto_accepted"],
                    "tree_typing_decision_reason": tree_typing["decision_reason"],
                    "tree_typing": tree_typing,
                    "joint_tree_job_id": joint_tree_job_id,
                    "joint_tree_method": canonical_article_tree_mode(tree_method),
                    "tree_reference_id": tree_reference_id,
                    "tree_reference_name": tree_reference_name,
                    "tree_reference_count": tree_reference_count,
                    "joint_tree_reference": tree_reference_id,
                    "joint_tree_reference_id": tree_reference_id,
                    "joint_tree_reference_name": tree_reference_name,
                    "joint_tree_reference_count": tree_reference_count,
                }
            )
            report_files[0].write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, json.JSONDecodeError):
            pass

    html_files = sorted((source_job_dir / "outputs").glob("*_report.html"))
    if len(html_files) == 1:
        try:
            document = html_files[0].read_text(encoding="utf-8")
            replacements = {
                "Candidate new genotype": tree_typing["predicted_new_genotype"],
                "Candidate old genotype": tree_typing["predicted_old_genotype"],
                "Candidate new group": tree_typing["predicted_new_group"],
                "Candidate old group": tree_typing["predicted_old_group"],
                "Confidence": tree_typing["confidence"],
                "Typing source": "phylogenetic_tree",
                "Tree support": tree_typing["support"] if tree_typing["support"] is not None else "—",
                "Automatic decision": tree_typing["auto_accepted"],
                "Decision reason": tree_typing["decision_reason"],
            }
            for label, value in replacements.items():
                document = re.sub(
                    rf"<div><b>{re.escape(label)}</b><br>.*?</div>",
                    f"<div><b>{label}</b><br>{html.escape(str(value))}</div>",
                    document,
                    count=1,
                    flags=re.DOTALL,
                )
            html_files[0].write_text(document, encoding="utf-8")
        except OSError:
            pass


@bounded_analysis_job
def run_batch_tree_job(
    job_dir: Path,
    source_job_ids: list[str],
    tree_method: str = "iqtree",
    tree_reference_id: str = DEFAULT_TREE_REFERENCE_ID,
    source_fasta_path: Path | None = None,
) -> None:
    try:
        raise_if_cancelled(job_dir)
    except AnalysisCancelled:
        finalize_cancelled_job(job_dir)
        return
    with get_job_lock(job_dir):
        payload = read_job(job_dir)
        if payload.get("status") == "cancelled":
            return
        payload.update({"status": "running", "updated_at": time.time()})
        payload["progress"] = make_progress(
            payload,
            "alignment",
            (
                "正在读取直接导入的 FASTA 序列"
                if source_fasta_path is not None
                else "正在汇总所有样本 Consensus"
            ),
            8,
        )
        write_job(job_dir, payload)

    try:
        raise_if_cancelled(job_dir)
        if not is_article_tree_mode(tree_method):
            raise ValueError("The article workflow requires IQ-TREE")
        tree_method = canonical_article_tree_mode(tree_method)
        selected_tree_reference = resolve_tree_reference(tree_reference_id)
        if selected_tree_reference["id"] != DEFAULT_TREE_REFERENCE_ID:
            raise ValueError("The article workflow is fixed to 60ref_151-850.fas")
        if not selected_tree_reference["valid"]:
            raise ValueError(selected_tree_reference["error"])
        selected_tree_reference_path: Path = selected_tree_reference["path"]
        expected_reference_count = int(selected_tree_reference["expected_count"])
        references = load_fasta_records(selected_tree_reference_path)
        if len(references) != expected_reference_count:
            raise ValueError(
                f"{selected_tree_reference_path.name} could not be loaded completely"
            )
        reference_names = {record.name for record in references}
        sample_records = []
        sample_ids: list[str] = []
        sample_sources: list[tuple[str, str | None, str]] = []
        used_names = set(reference_names)

        if source_fasta_path is not None:
            direct_records = load_fasta_records(source_fasta_path)
            if not direct_records:
                raise ValueError("Direct FASTA input contains no sequences")
            record_sources = [
                (record, None, record.name)
                for record in direct_records
            ]
        else:
            record_sources = []
            for source_job_id in source_job_ids:
                raise_if_cancelled(job_dir)
                source_job_dir = resolve_job_dir(source_job_id)
                source_payload = read_job(source_job_dir)
                consensus_files = sorted((source_job_dir / "outputs").glob("*_consensus.fasta"))
                if len(consensus_files) != 1:
                    raise ValueError(f"Consensus file is missing for {source_payload.get('sample_id') or source_job_id}")
                consensus_records = load_fasta_records(consensus_files[0])
                if len(consensus_records) != 1:
                    raise ValueError(f"Consensus file is invalid for {source_payload.get('sample_id') or source_job_id}")
                record_sources.append(
                    (
                        consensus_records[0],
                        source_job_id,
                        str(source_payload.get("sample_id") or source_job_id),
                    )
                )

        for source_record, source_job_id, display_sample_id in record_sources:
            raise_if_cancelled(job_dir)
            consensus = source_record.sequence
            base_name = sanitize_sample_id(display_sample_id)
            tree_name = base_name
            if tree_name in used_names:
                tree_name = f"QUERY_{tree_name}"
            suffix = 2
            while tree_name in used_names:
                tree_name = f"QUERY_{base_name}_{suffix}"
                suffix += 1
            used_names.add(tree_name)
            sample_records.append(type(references[0])(tree_name, consensus))
            sample_ids.append(tree_name)
            sample_sources.append(
                (
                    tree_name,
                    source_job_id,
                    display_sample_id,
                )
            )

        output_dir = job_dir / "outputs"
        output_prefix = "fasta_joint" if source_fasta_path is not None else "batch_joint"
        sample_fasta = output_dir / f"{output_prefix}_mafft_add.fasta"
        alignment_fasta = output_dir / f"{output_prefix}_alignment.fasta"
        tree_newick = output_dir / f"{output_prefix}_tree_midpoint_increasing.nwk"
        tree_file = output_dir / f"{output_prefix}_tree_midpoint_increasing.treefile"
        tree_svg = output_dir / f"{output_prefix}_tree_midpoint_increasing.svg"
        write_fasta(sample_records, sample_fasta)
        update_job_progress(
            job_dir,
            "alignment",
            (
                f"正在将 {len(sample_ids)} 个"
                f"{'直接导入序列' if source_fasta_path is not None else '质控拼接序列'}一次性加入 "
                f"{selected_tree_reference_path.name}（MAFFT --add --keeplength）"
            ),
            14,
        )
        run_mafft_add_alignment(
            sample_fasta,
            selected_tree_reference_path,
            alignment_fasta,
            cancel_callback=lambda: raise_if_cancelled(job_dir),
        )
        raise_if_cancelled(job_dir)
        aligned_records = load_fasta_records(alignment_fasta)
        if is_article_tree_mode(tree_method):
            update_job_progress(
                job_dir,
                "iqtree",
                (
                    f"正在构建 {len(sample_ids)} 个样本与 {len(references)} 条参考序列的联合 "
                    f"IQ-TREE（{IQTREE_MODEL}；UFBoot {IQTREE_BOOTSTRAP_REPLICATES}）"
                ),
                18,
            )
            tree_source = run_iqtree_alignment(
                alignment_fasta,
                cancel_callback=lambda: raise_if_cancelled(job_dir),
            )
            tree = Phylo.read(str(tree_source), "newick")
            tree_title = (
                f"OT 56kDa joint IQ-TREE ({len(sample_ids)} samples + "
                f"{len(references)} references; {IQTREE_MODEL}; "
                f"UFBoot={IQTREE_BOOTSTRAP_REPLICATES})"
            )
        else:
            update_job_progress(
                job_dir,
                "tree",
                f"正在构建 {len(sample_ids)} 个样本与 {len(references)} 条参考序列的联合 NJ 树",
                18,
            )
            tree = build_distance_tree(aligned_records)
            add_bootstrap_support(
                tree,
                aligned_records,
                BOOTSTRAP_REPLICATES,
                progress_callback=lambda completed, total: update_job_progress(
                    job_dir,
                    "bootstrap",
                    f"联合树 bootstrap 重采样（{completed}/{total}）",
                    20 + completed * 72 // total,
                ),
                cancel_callback=lambda: raise_if_cancelled(job_dir),
            )
            tree_title = (
                f"OT 56kDa joint NJ tree ({len(sample_ids)} samples + "
                f"{len(references)} references; bootstrap={BOOTSTRAP_REPLICATES})"
            )
        genotype_map = load_genotype_map(GENOTYPE_XLSX)
        tree_typing_by_name = infer_tree_typing_results(
            tree,
            sample_ids,
            reference_names,
            genotype_map,
        )
        # Preserve the original IQ-TREE topology for the root-independent
        # decision, then midpoint-root only the rendered/exported display copy.
        midpoint_root_and_sort(tree)
        tree_typing_results: list[dict[str, Any]] = []
        for tree_name, source_job_id, display_sample_id in sample_sources:
            raise_if_cancelled(job_dir)
            tree_typing = asdict(tree_typing_by_name[tree_name])
            tree_typing["tree_sample_id"] = tree_name
            tree_typing["sample_id"] = display_sample_id
            if source_job_id is not None:
                tree_typing["source_job_id"] = source_job_id
            tree_typing["reference_names"] = list(tree_typing["reference_names"])
            tree_typing["candidate_genotypes"] = list(tree_typing["candidate_genotypes"])
            tree_typing_results.append(tree_typing)
        Phylo.write(tree, str(tree_newick), "newick")
        Phylo.write(tree, str(tree_file), "newick")
        render_tree_svg(
            tree,
            sample_ids,
            tree_svg,
            tree_title,
            genotype_map=genotype_map,
        )
        with get_job_lock(job_dir):
            raise_if_cancelled(job_dir)
            for tree_typing in tree_typing_results:
                if tree_typing.get("source_job_id"):
                    apply_joint_tree_typing_to_source_job(
                        tree_typing["source_job_id"],
                        tree_typing,
                        str(payload["job_id"]),
                        tree_method,
                        selected_tree_reference["id"],
                        selected_tree_reference_path.name,
                        len(references),
                    )

            payload = read_job(job_dir)
            payload.update(
                {
                    "status": "completed",
                    "updated_at": time.time(),
                    "summary": {
                        "predicted_type": (
                            "FASTA 联合树" if source_fasta_path is not None else "联合树"
                        ),
                        "predicted_group": "",
                        "predicted_old_type": "",
                        "predicted_old_group": "",
                        "confidence": "bootstrap",
                        "is_target": True,
                        "target_status": "completed",
                        "typing_source": "phylogenetic_tree",
                        "typing_method": "unrooted_reference_anchor_bipartition",
                        "similarity_predicted_type": None,
                        "similarity_predicted_group": None,
                        "similarity_predicted_old_type": None,
                        "similarity_predicted_old_group": None,
                        "best_hit": None,
                        "best_identity": 0,
                        "consensus_length": 0,
                        "tree_rooting": "midpoint",
                        "tree_ordering": "increasing",
                        "tree_generated": True,
                        "analysis_mode": V1_STRICT_MODE,
                        "workflow_profile_id": V1_STRICT_PROFILE_ID,
                        "workflow_version": V1_STRICT_VERSION,
                        "strict_article_mode": True,
                        "tree_method": (
                            f"IQ-TREE {IQTREE_MODEL}"
                            if is_article_tree_mode(tree_method)
                            else "NJ"
                        ),
                        "bootstrap_replicates": (
                            IQTREE_BOOTSTRAP_REPLICATES
                            if is_article_tree_mode(tree_method)
                            else BOOTSTRAP_REPLICATES
                        ),
                        "iqtree_command": (
                            f"iqtree -s {alignment_fasta.name} -m {IQTREE_MODEL} "
                            f"-bb {IQTREE_BOOTSTRAP_REPLICATES} -T {IQTREE_THREADS} -bnni"
                            if is_article_tree_mode(tree_method)
                            else None
                        ),
                        "typing_rule_id": TYPING_RULE_ID,
                        "typing_rule_version": TYPING_RULE_VERSION,
                        "typing_tree_source": tree_source.name,
                        "typing_tree_rooted": False,
                        "batch_sample_count": len(sample_ids),
                        "input_source": (
                            "fasta" if source_fasta_path is not None else "ab1_consensus"
                        ),
                        "input_filename": payload.get("input_filename"),
                        "tree_reference_id": selected_tree_reference["id"],
                        "tree_reference_name": selected_tree_reference_path.name,
                        "tree_reference_count": len(references),
                        "alignment_method": "mafft --add --keeplength",
                        "mafft_command": (
                            f"mafft --add {sample_fasta.name} --keeplength "
                            f"{selected_tree_reference_path.name} > {alignment_fasta.name}"
                        ),
                        "tree_typing_results": tree_typing_results,
                        "warnings": [
                            f"{item['sample_id']}：{item['decision_reason']}"
                            for item in tree_typing_results
                            if not item["auto_accepted"]
                        ],
                        "top_hits": [],
                    },
                    "files": build_file_urls(payload["job_id"], output_dir),
                    "error": None,
                    "progress": make_progress(
                        payload,
                        "completed",
                        (
                            "FASTA 序列联合树已生成"
                            if source_fasta_path is not None
                            else "批次联合树已生成"
                        ),
                        100,
                    ),
                }
            )
            write_job(job_dir, payload)
    except AnalysisCancelled:
        finalize_cancelled_job(job_dir)
    except Exception as exc:
        if cancellation_requested(job_dir):
            finalize_cancelled_job(job_dir)
            return
        error_message = format_error_for_user(exc)
        with get_job_lock(job_dir):
            payload = read_job(job_dir)
            if payload.get("status") == "cancelled":
                return
            payload.update(
                {
                    "status": "failed",
                    "updated_at": time.time(),
                    "error": error_message,
                    "progress": make_progress(payload, "failed", error_message, 100),
                }
            )
            write_job(job_dir, payload)


def make_progress(
    payload: dict[str, Any],
    stage: str,
    message: str,
    percent: int | None = None,
    log_tail: list[str] | None = None,
) -> dict[str, Any]:
    previous = payload.get("progress") or {}
    created_at = float(payload.get("created_at") or time.time())
    previous_percent = int(previous.get("percent") or 0)
    next_percent = previous_percent if percent is None else max(0, min(100, int(percent)))
    return {
        "stage": stage,
        "message": message,
        "percent": next_percent,
        "elapsed_seconds": max(0, int(time.time() - created_at)),
        "log_tail": log_tail if log_tail is not None else previous.get("log_tail", []),
    }


def finalize_cancelled_job(job_dir: Path) -> dict[str, Any]:
    with get_job_lock(job_dir):
        payload = read_job(job_dir)
        if payload.get("status") in {"completed", "failed"}:
            return payload
        cancelled_at = float(payload.get("cancelled_at") or time.time())
        progress = dict(payload.get("progress") or {})
        progress.update(
            {
                "stage": "cancelled",
                "message": "分析已停止",
                "percent": int(progress.get("percent") or 0),
                "elapsed_seconds": max(
                    0,
                    int(time.time() - float(payload.get("created_at") or time.time())),
                ),
            }
        )
        payload.update(
            {
                "status": "cancelled",
                "cancel_requested": True,
                "cancelled_at": cancelled_at,
                "updated_at": time.time(),
                "error": None,
                "progress": progress,
            }
        )
        write_job(job_dir, payload)
        return payload


def update_job_progress(job_dir: Path, stage: str, message: str, percent: int | None = None) -> None:
    with get_job_lock(job_dir):
        if cancellation_requested(job_dir):
            return
        payload = read_job(job_dir)
        if payload.get("status") in TERMINAL_JOB_STATUSES:
            return
        payload["progress"] = make_progress(payload, stage, message, percent)
        payload["updated_at"] = time.time()
        write_job(job_dir, payload)


def enrich_job_status(job_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    progress = dict(payload.get("progress") or make_progress(payload, "queued", "任务已提交", 0))
    progress["elapsed_seconds"] = max(0, int(time.time() - float(payload.get("created_at") or time.time())))

    if payload.get("status") in {"queued", "running"}:
        progress.update(infer_live_progress(job_dir, payload, progress))
    elif payload.get("status") == "completed":
        output_dir = job_dir / "outputs"
        ensure_final_treefiles(output_dir)
        payload["files"] = {
            **dict(payload.get("files") or {}),
            **build_file_urls(str(payload.get("job_id") or job_dir.name), output_dir),
        }

    payload["progress"] = progress
    return payload


def ensure_final_treefiles(output_dir: Path) -> None:
    for newick_path in output_dir.glob("*_tree_midpoint_increasing.nwk"):
        tree_file = newick_path.with_suffix(".treefile")
        if tree_file.exists():
            continue
        temporary = tree_file.with_name(f".{tree_file.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(newick_path, temporary)
        temporary.replace(tree_file)


def infer_live_progress(job_dir: Path, payload: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status") == "queued":
        return {
            "stage": "queued",
            "message": "任务已提交，等待后台开始",
            "percent": max(int(progress.get("percent") or 0), 1),
        }

    output_dir = job_dir / "outputs"
    log_files = sorted(output_dir.glob("*_alignment.fasta.log"))
    treefiles = sorted(output_dir.glob("*_alignment.fasta.treefile"))
    log_tail: list[str] = []
    live = {
        "stage": progress.get("stage", "running"),
        "message": progress.get("message", "任务运行中"),
        "percent": int(progress.get("percent") or 5),
    }

    if log_files:
        log_tail = read_tail_lines(log_files[0], 12)
        live.update(parse_iqtree_progress(log_tail, live))
    elif sorted(output_dir.glob("*_alignment.fasta")):
        live.update({"stage": "iqtree", "message": "比对文件已生成，正在启动 IQ-TREE", "percent": max(live["percent"], 42)})
    elif sorted(output_dir.glob("*_consensus.fasta")):
        live.update({"stage": "mafft", "message": "Consensus 已生成，正在准备多序列比对", "percent": max(live["percent"], 28)})

    if treefiles:
        live.update(
            {
                "stage": "iqtree",
                "message": "IQ-TREE treefile 已生成，正在等待程序收尾或渲染树图",
                "percent": max(int(live.get("percent") or 0), 88),
            }
        )

    if log_tail:
        live["log_tail"] = log_tail
    return live


def parse_iqtree_progress(log_tail: list[str], current: dict[str, Any]) -> dict[str, Any]:
    live = {
        "stage": "iqtree",
        "message": current.get("message", "IQ-TREE 运行中"),
        "percent": max(int(current.get("percent") or 0), 48),
    }
    for line in reversed(log_tail):
        sample_match = re.search(r"(\d+)\s+samples done", line)
        if sample_match:
            samples = int(sample_match.group(1))
            live["message"] = f"IQ-TREE bootstrap 已完成 {samples} samples"
            live["percent"] = max(live["percent"], min(92, 55 + samples * 35 // 1000))
            return live
        iteration_match = re.search(r"Iteration\s+(\d+).*", line)
        if iteration_match:
            iteration = int(iteration_match.group(1))
            live["message"] = f"IQ-TREE {line}"
            live["percent"] = max(live["percent"], min(86, 50 + iteration))
            return live
        if "Optimizing NNI" in line:
            live["message"] = "IQ-TREE 正在优化树拓扑"
            live["percent"] = max(live["percent"], 52)
        elif "Computing log-likelihood" in line:
            live["message"] = "IQ-TREE 正在计算候选树似然"
            live["percent"] = max(live["percent"], 50)
    return live


def read_tail_lines(path: Path, max_lines: int) -> list[str]:
    try:
        return [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:] if line]
    except OSError:
        return []


def format_error_for_user(exc: Exception, max_length: int = 1200) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if len(message) <= max_length:
        return message
    return message[: max_length - 3].rstrip() + "..."


def build_file_urls(job_id: str, output_dir: Path) -> dict[str, str]:
    mapping = {}
    for key, pattern in {
        "report_json": "*_report.json",
        "report_html": "*_report.html",
        "consensus_fasta": "*_consensus.fasta",
        "alignment_fasta": "*_alignment.fasta",
        "tree_newick": "*_tree_midpoint_increasing.nwk",
        "tree_file": "*_tree_midpoint_increasing.treefile",
        "tree_svg": "*_tree_midpoint_increasing.svg",
        "iqtree_treefile": "*_alignment.fasta.treefile",
        "iqtree_report": "*_alignment.fasta.iqtree",
        "iqtree_log": "*_alignment.fasta.log",
        "iqtree_ufboot": "*_alignment.fasta.ufboot",
        "iqtree_contree": "*_alignment.fasta.contree",
    }.items():
        matches = sorted(output_dir.glob(pattern))
        if matches:
            mapping[key] = f"/api/jobs/{job_id}/files/{matches[0].name}"
    return mapping


def write_job(job_dir: Path, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / "job.json"
    temp = job_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for attempt in range(20):
            try:
                temp.replace(target)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


def read_job(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "job.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    last_error: Exception | None = None
    for _ in range(4):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            last_error = exc
            time.sleep(0.04)
    raise HTTPException(status_code=503, detail=f"Job status is being updated: {last_error}")


def resolve_job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    legacy_job_dir = WEB_RESULTS / job_id
    if legacy_job_dir.is_dir():
        return legacy_job_dir
    if WEB_RESULTS.is_dir():
        for submission_dir in WEB_RESULTS.iterdir():
            if not submission_dir.is_dir():
                continue
            job_dir = submission_dir / job_id
            if job_dir.is_dir():
                return job_dir
    raise HTTPException(status_code=404, detail="Job not found")


async def save_ab1_upload(upload: UploadFile, destination: Path) -> None:
    filename = upload.filename or ""
    if not filename.lower().endswith(".ab1"):
        raise HTTPException(status_code=400, detail="Only .ab1 files are supported")
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Uploaded file is larger than 8 MB")
    destination.write_bytes(data)


async def read_fasta_upload(upload: UploadFile) -> list[FastaRecord]:
    filename = upload.filename or ""
    if Path(filename).suffix.lower() not in FASTA_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only .fa, .fas, .fasta, or .fna files are supported",
        )
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded FASTA file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Uploaded file is larger than 8 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="FASTA file must be UTF-8 text") from exc

    records: list[FastaRecord] = []
    current_name: str | None = None
    current_parts: list[str] = []
    seen_names: set[str] = set()

    def finish_record() -> None:
        if current_name is None:
            return
        sequence = "".join(current_parts).upper()
        if not sequence:
            raise HTTPException(
                status_code=400,
                detail=f"FASTA sequence is empty: {current_name}",
            )
        invalid = sorted(set(sequence) - set("ACGTN-"))
        if invalid:
            symbols = "".join(invalid[:12])
            raise HTTPException(
                status_code=400,
                detail=f"FASTA sequence {current_name} contains unsupported characters: {symbols}",
            )
        if not any(base in "ACGT" for base in sequence):
            raise HTTPException(
                status_code=400,
                detail=f"FASTA sequence has no determined A/C/G/T bases: {current_name}",
            )
        records.append(FastaRecord(current_name, sequence))

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            finish_record()
            header = line[1:].strip()
            if not header:
                raise HTTPException(
                    status_code=400,
                    detail=f"FASTA header is empty at line {line_number}",
                )
            current_name = header.split()[0]
            if current_name in seen_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"FASTA contains duplicate sequence name: {current_name}",
                )
            seen_names.add(current_name)
            current_parts = []
            continue
        if current_name is None:
            raise HTTPException(
                status_code=400,
                detail=f"FASTA sequence data appears before the first header at line {line_number}",
            )
        current_parts.append(re.sub(r"\s+", "", line))

    finish_record()
    if not records:
        raise HTTPException(status_code=400, detail="FASTA file contains no sequences")
    if len(records) > 100:
        raise HTTPException(status_code=400, detail="FASTA file may contain at most 100 sequences")
    return records


def sanitize_sample_id(value: str) -> str:
    value = value.strip()
    if not value:
        return "sample"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    safe = safe.strip("._-")
    return safe[:60] or "sample"


def new_job_id() -> str:
    return uuid.uuid4().hex


def normalize_submission_id(value: str | None, sample_count: int) -> str:
    submitted = (value or "").strip()
    if submitted:
        match = SUBMISSION_DIR_PATTERN.fullmatch(submitted)
        if match is None or int(match.group("count")) != sample_count:
            raise HTTPException(
                status_code=400,
                detail="submission_id must match the submission time and sample count",
            )
        return submitted
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{stamp}_{sample_count}样本_{uuid.uuid4().hex[:6]}"


def media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".svg": "image/svg+xml",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json",
        ".fasta": "text/plain; charset=utf-8",
        ".nwk": "text/plain; charset=utf-8",
        ".treefile": "text/plain; charset=utf-8",
        ".iqtree": "text/plain; charset=utf-8",
        ".log": "text/plain; charset=utf-8",
        ".ufboot": "text/plain; charset=utf-8",
        ".contree": "text/plain; charset=utf-8",
    }.get(suffix, "application/octet-stream")


def content_disposition_for(path: Path) -> str:
    if path.suffix.lower() in {
        ".svg",
        ".html",
        ".json",
        ".fasta",
        ".nwk",
        ".iqtree",
        ".log",
        ".ufboot",
        ".contree",
    }:
        return "inline"
    return "attachment"


def clear_results_dir() -> None:
    if WEB_RESULTS.exists():
        shutil.rmtree(WEB_RESULTS)
