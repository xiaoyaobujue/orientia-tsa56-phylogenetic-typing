from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import openpyxl
from Bio import Align, Phylo, SeqIO
from Bio.Phylo.TreeConstruction import DistanceMatrix, DistanceTreeConstructor
from Bio.Seq import Seq


BASES = set("ACGT")
MIN_TARGET_IDENTITY = 0.80
MIN_TARGET_QUERY_COVER = 0.50
TYPING_RULE_ID = "reference_anchor_distance_v3"
TYPING_RULE_VERSION = "3.0.0"
TREE_AUTO_ACCEPT_MIN_SUPPORT = 95.0
TREE_DISTANCE_RULE_ID = "reference_tree_envelope_v3"
TRIMMED_REFERENCE_NAME = "YC2_355_trimmed_reference.fasta"
TRIM_METADATA_NAME = "YC2_355_trim_metadata.json"
TREE_BACKBONE_COLOR = "#171717"
TREE_REFERENCE_FALLBACK_COLOR = "#6B7280"
TREE_GENOTYPE_COLORS = {
    "1b": "#6BAED6",
    "2a": "#3F88C5",
    "2b": "#3A9D9C",
    "3a": "#6DBA61",
    "3b": "#238B45",
    "4a": "#9C755F",
    "4b": "#F05B61",
    "4c": "#C83E52",
    "4d": "#D8922F",
    "4e": "#E67E22",
    "4f": "#C85A17",
    "5a": "#A98BC4",
    "5b": "#7A5AA6",
    "5c": "#9A8176",
    "5d": "#C79A00",
    "5e": "#B84D21",
}


@dataclass
class FastaRecord:
    name: str
    sequence: str


@dataclass(frozen=True)
class BatchTreeSample:
    sample_id: str
    consensus: str
    best_hit: str


@dataclass(frozen=True)
class BatchTreeResult:
    alignment_fasta: Path
    tree_newick: Path
    tree_svg: Path
    sample_ids: tuple[str, ...]


@dataclass
class ReferenceMetadata:
    reference_id: str
    matched_label: str
    group_new: str
    group_old: str
    genotype_new: str
    genotype_old: str


@dataclass
class GenotypeMap:
    by_exact: dict[str, ReferenceMetadata]
    by_accession: dict[str, ReferenceMetadata]
    by_normalized: dict[str, ReferenceMetadata]
    records: tuple[ReferenceMetadata, ...] = ()
    duplicate_accessions: tuple[str, ...] = ()


@dataclass
class ClassificationHit:
    name: str
    new_genotype: str
    old_genotype: str
    new_group: str
    old_group: str
    identity: float
    aligned_length: int
    query_cover_bp: int = 0
    query_cover_pct: float = 0.0
    evalue: float = math.inf
    bitscore: float = 0.0


@dataclass
class ClassificationResult:
    predicted_new_genotype: str
    predicted_old_genotype: str
    predicted_new_group: str
    predicted_old_group: str
    confidence: str
    is_target: bool
    target_status: str
    best_hit: str
    best_identity: float
    second_new_genotype: str
    second_old_genotype: str
    second_identity: float
    hits: list[ClassificationHit]


@dataclass(frozen=True)
class TreeTypingResult:
    sample_id: str
    predicted_new_genotype: str
    predicted_old_genotype: str
    predicted_new_group: str
    predicted_old_group: str
    confidence: str
    status: str
    support: float | None
    method: str
    reference_names: tuple[str, ...]
    candidate_genotypes: tuple[str, ...]
    nearest_reference: str | None
    nearest_distance: float | None
    reference_monophyletic: bool
    auto_accepted: bool
    decision_reason: str
    typing_rule_id: str = TYPING_RULE_ID
    typing_rule_version: str = TYPING_RULE_VERSION
    nearest_genotypes: tuple[str, ...] = ()
    second_nearest_genotype: str | None = None
    second_nearest_distance: float | None = None
    distance_margin: float | None = None
    reference_distance_threshold: float | None = None
    reference_margin_threshold: float | None = None
    topology_distance_concordant: bool | None = None
    distance_rule_id: str = TREE_DISTANCE_RULE_ID
    distance_calibration_state: str = "reference_derived_not_loro_validated"


@dataclass
class ReferencePlacement:
    best_reference: str
    best_identity: float
    aligned_query_bases: int
    reference_raw_start_1based: int
    reference_raw_end_1based: int
    alignment_start_col_1based: int
    alignment_end_col_1based: int
    trimmed_alignment_columns: int


@dataclass
class ReferenceDatabase:
    records: list[FastaRecord]
    path: Path
    metadata_path: Path
    was_rebuilt: bool
    placement: ReferencePlacement | None


@dataclass
class SingleReadOrientation:
    sequence: str
    orientation: str
    best_reference: str
    best_identity: float
    aligned_length: int
    opposite_identity: float = 0.0
    decision_margin: float = 0.0
    method: str = "reference_seed_extend"


@dataclass(frozen=True)
class TrimmedTraceRead:
    sequence: str
    qualities: tuple[int, ...]


@dataclass(frozen=True)
class TraceQualityMetrics:
    read_label: str
    passed: bool
    failure_code: str | None
    message: str
    core_length: int
    q_pass_fraction: float
    mixed_peak_fraction: float
    longest_mixed_run: int
    n_fraction: float
    longest_low_quality_run: int


class TraceQualityError(ValueError):
    def __init__(self, message: str, metrics: list[TraceQualityMetrics]):
        super().__init__(message)
        self.metrics = metrics


@dataclass
class PipelineResult:
    sample_id: str
    consensus: str
    consensus_length: int
    analysis_mode: str
    read_mode: str
    read_orientation: str
    orientation_identity: float | None
    quality_threshold: int | None
    min_overlap: int
    trace_quality: list[TraceQualityMetrics]
    quality_status: str
    quality_note: str
    classification: ClassificationResult
    reference_database: ReferenceDatabase
    output_dir: Path
    consensus_fasta: Path
    alignment_fasta: Path | None
    tree_newick: Path | None
    tree_svg: Path | None
    report_json: Path
    report_html: Path
    tree_typing: TreeTypingResult | None = None
    typing_pending: bool = False


def clean_sequence(sequence: str) -> str:
    return "".join(base for base in sequence.upper() if base in "ACGTN-")


def ungap(sequence: str) -> str:
    return "".join(base for base in clean_sequence(sequence) if base in "ACGTN")


def reverse_complement(sequence: str) -> str:
    return str(Seq(clean_sequence(sequence).replace("-", "")).reverse_complement())


MIN_TRACE_CORE_LENGTH = 200
MIN_Q_PASS_FRACTION = 0.75
MAX_N_FRACTION = 0.05
MIXED_PEAK_RATIO = 0.33
MAX_MIXED_PEAK_FRACTION = 0.20


def _longest_true_run(values: Iterable[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def _abif_value(abif_raw: dict, tag: str):
    if tag in abif_raw:
        return abif_raw[tag]
    byte_tag = tag.encode("ascii")
    if byte_tag in abif_raw:
        return abif_raw[byte_tag]
    raise KeyError(tag)


def _extract_fwo_channel_traces(abif_raw: dict) -> dict[str, list]:
    flow_order = _abif_value(abif_raw, "FWO_1")
    if isinstance(flow_order, bytes):
        flow_order = flow_order.decode("ascii")
    flow_order = str(flow_order).rstrip("\x00").upper()
    if len(flow_order) != 4 or set(flow_order) != set("ACGT"):
        raise ValueError("invalid FWO_1")
    traces = {
        base: list(_abif_value(abif_raw, f"DATA{9 + index}"))
        for index, base in enumerate(flow_order)
    }
    if any(not trace for trace in traces.values()):
        raise ValueError("empty ABI trace")
    return traces


def _unreadable_trace_metrics(read_label: str) -> TraceQualityMetrics:
    return TraceQualityMetrics(
        read_label=read_label,
        passed=False,
        failure_code="ab1_unreadable",
        message="质控失败（AB1 无法读取）",
        core_length=0,
        q_pass_fraction=0.0,
        mixed_peak_fraction=0.0,
        longest_mixed_run=0,
        n_fraction=0.0,
        longest_low_quality_run=0,
    )


def _trace_read_direction(read_label: str) -> str:
    return {
        "forward": "正向",
        "reverse": "反向",
        "read1": "文件1",
        "read2": "文件2",
    }.get(read_label, read_label)


def _trace_failure_reason(metrics: TraceQualityMetrics) -> str:
    message = metrics.message
    prefix = "质控失败（"
    if message.startswith(prefix) and message.endswith("）"):
        return message[len(prefix):-1]
    return message


def _trace_quality_failure_message(metrics: list[TraceQualityMetrics]) -> str:
    failed = [item for item in metrics if not item.passed]
    if len(failed) == 1:
        return failed[0].message
    if all(item.failure_code == "double_peak_poor_quality" for item in failed):
        return "质控失败（双峰，测序质量差）"
    reasons = "；".join(
        f"{_trace_read_direction(item.read_label)}读段{_trace_failure_reason(item)}"
        for item in failed
    )
    return f"质控失败：{reasons}"


def _directional_trace_failure_message(metrics: TraceQualityMetrics) -> str:
    return f"{_trace_read_direction(metrics.read_label)}质控失败（{_trace_failure_reason(metrics)}）"


def assess_ab1_quality(path: Path, read_label: str, quality_threshold: int) -> TraceQualityMetrics:
    try:
        record = SeqIO.read(str(path), "abi")
    except (OSError, ValueError):
        return _unreadable_trace_metrics(read_label)

    try:
        abif_raw = record.annotations["abif_raw"]
        traces = _extract_fwo_channel_traces(abif_raw)
        bases = _abif_value(abif_raw, "PBAS2")
        if isinstance(bases, bytes):
            bases = bases.decode("ascii")
        bases = str(bases).rstrip("\x00").upper()
        positions = [int(position) for position in _abif_value(abif_raw, "PLOC2")]
        qualities = [int(quality) for quality in record.letter_annotations["phred_quality"]]
        if not bases or len(bases) != len(positions) or len(bases) != len(qualities):
            raise ValueError("incomplete ABI base annotations")

        core_bases = bases[30:-30]
        core_positions = positions[30:-30]
        core_qualities = qualities[30:-30]
        samples = []
        for position in core_positions:
            if position < 0 or any(position >= len(trace) for trace in traces.values()):
                raise ValueError("ABI sample position outside trace")
            samples.append([float(traces[base][position]) for base in "ACGT"])
    except (KeyError, UnicodeError, ValueError, TypeError, OverflowError):
        return _unreadable_trace_metrics(read_label)

    threshold = int(quality_threshold)
    core_length = len(core_bases)
    low_quality_flags = [quality < threshold for quality in core_qualities]
    q_pass_fraction = sum(not flag for flag in low_quality_flags) / core_length if core_length else 0.0
    mixed_flags = []
    for sample in samples:
        highest, second_highest = sorted((float(value) for value in sample), reverse=True)[:2]
        ratio = second_highest / highest if highest > 0 else 0.0
        mixed_flags.append(ratio >= MIXED_PEAK_RATIO)

    mixed_peak_fraction = sum(mixed_flags) / core_length if core_length else 0.0
    n_flags = [base == "N" for base in core_bases]
    n_fraction = sum(n_flags) / core_length if core_length else 0.0
    longest_mixed_run = _longest_true_run(mixed_flags)
    longest_low_quality_run = _longest_true_run(low_quality_flags)

    failure_code: str | None = None
    message = "质控通过"
    if core_length < MIN_TRACE_CORE_LENGTH:
        failure_code = "trace_too_short"
        message = "质控失败（有效序列过短）"
    elif n_fraction >= MAX_N_FRACTION:
        failure_code = "too_many_n_bases"
        message = "质控失败（N 碱基过多）"
    elif mixed_peak_fraction >= MAX_MIXED_PEAK_FRACTION:
        failure_code = "double_peak_poor_quality"
        message = "质控失败（双峰，测序质量差）"
    elif q_pass_fraction < MIN_Q_PASS_FRACTION:
        failure_code = "low_quality_fraction"
        message = "质控失败（测序质量差）"

    return TraceQualityMetrics(
        read_label=read_label,
        passed=failure_code is None,
        failure_code=failure_code,
        message=message,
        core_length=core_length,
        q_pass_fraction=q_pass_fraction,
        mixed_peak_fraction=mixed_peak_fraction,
        longest_mixed_run=longest_mixed_run,
        n_fraction=n_fraction,
        longest_low_quality_run=longest_low_quality_run,
    )


def _normalize_trace_sequence(sequence: str) -> str:
    """Normalize every called trace position without changing its coordinate count."""
    return "".join(base if base in "ACGTN" else "N" for base in str(sequence).upper())


def read_ab1_trimmed_with_qualities(
    path: Path,
    quality_threshold: int | None = None,
) -> TrimmedTraceRead:
    record = SeqIO.read(str(path), "abi-trim" if quality_threshold is None else "abi")
    sequence = _normalize_trace_sequence(str(record.seq))
    raw_qualities = record.letter_annotations.get("phred_quality", [])
    if raw_qualities and len(raw_qualities) != len(sequence):
        raise ValueError(
            f"AB1 sequence/quality length mismatch: sequence={len(sequence)}, "
            f"qualities={len(raw_qualities)}"
        )
    qualities = tuple(int(value) for value in raw_qualities) if raw_qualities else (0,) * len(sequence)

    if quality_threshold is None or not raw_qualities:
        return TrimmedTraceRead(sequence=sequence, qualities=qualities)

    threshold = max(0, min(40, int(quality_threshold)))
    start = 0
    end = len(qualities)
    for index, quality in enumerate(qualities):
        if quality >= threshold:
            start = index
            break
    for index in range(len(qualities) - 1, -1, -1):
        if qualities[index] >= threshold:
            end = index + 1
            break

    return TrimmedTraceRead(
        sequence=sequence[start:end],
        qualities=qualities[start:end],
    )


def read_ab1_trimmed(path: Path, quality_threshold: int | None = None) -> str:
    return read_ab1_trimmed_with_qualities(path, quality_threshold=quality_threshold).sequence


def load_fasta_records(path: Path) -> list[FastaRecord]:
    records: list[FastaRecord] = []
    current_name: str | None = None
    current_parts: list[str] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_name is not None:
                records.append(FastaRecord(current_name, clean_sequence("".join(current_parts))))
            current_name = line[1:].strip().split()[0]
            current_parts = []
        else:
            current_parts.append(line)
    if current_name is not None:
        records.append(FastaRecord(current_name, clean_sequence("".join(current_parts))))
    return records


def write_fasta(records: Iterable[FastaRecord], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(f">{record.name}\n")
            sequence = clean_sequence(record.sequence)
            for start in range(0, len(sequence), 80):
                handle.write(sequence[start : start + 80] + "\n")


def best_overlap_consensus(
    forward: str,
    reverse_read: str,
    min_overlap: int = 80,
    *,
    forward_qualities: Sequence[int] | None = None,
    reverse_qualities: Sequence[int] | None = None,
) -> str:
    return best_oriented_overlap_consensus(
        forward,
        reverse_complement(reverse_read),
        min_overlap=min_overlap,
        read1_qualities=forward_qualities,
        read2_qualities=tuple(reversed(reverse_qualities)) if reverse_qualities is not None else None,
    )


def best_oriented_overlap_consensus(
    read1: str,
    read2: str,
    min_overlap: int = 80,
    *,
    read1_qualities: Sequence[int] | None = None,
    read2_qualities: Sequence[int] | None = None,
) -> str:
    """Gap-aware merge of two reads already oriented to the reference forward strand."""
    forward = clean_sequence(read1).replace("-", "")
    reverse = clean_sequence(read2).replace("-", "")
    return gapped_overlap_consensus(
        forward,
        reverse,
        min_overlap=min_overlap,
        forward_qualities=read1_qualities,
        reverse_qualities=read2_qualities,
    )


def _validated_quality_values(
    qualities: Sequence[int] | None,
    sequence_length: int,
    label: str,
) -> tuple[int, ...] | None:
    if qualities is None:
        return None
    values = tuple(int(value) for value in qualities)
    if len(values) != sequence_length:
        raise ValueError(
            f"{label} sequence/quality length mismatch: sequence={sequence_length}, "
            f"qualities={len(values)}"
        )
    return values


def gapped_overlap_consensus(
    forward: str,
    reverse: str,
    min_overlap: int = 80,
    *,
    forward_qualities: Sequence[int] | None = None,
    reverse_qualities: Sequence[int] | None = None,
) -> str:
    forward = clean_sequence(forward).replace("-", "")
    reverse = clean_sequence(reverse).replace("-", "")
    forward_quality_values = _validated_quality_values(
        forward_qualities,
        len(forward),
        "forward",
    )
    reverse_quality_values = _validated_quality_values(
        reverse_qualities,
        len(reverse),
        "reverse",
    )

    local_aligner = Align.PairwiseAligner()
    local_aligner.mode = "local"
    local_aligner.match_score = 2.0
    local_aligner.mismatch_score = -2.0
    local_aligner.open_gap_score = -6.0
    local_aligner.extend_gap_score = -1.0
    local_alignment = local_aligner.align(forward, reverse)[0]
    local_forward = str(local_alignment[0])
    local_reverse = str(local_alignment[1])
    identity, overlap_len = aligned_string_identity(local_forward, local_reverse)
    if overlap_len < min_overlap or identity < 0.85:
        raise ValueError(f"Poor forward/reverse overlap: length={overlap_len}, identity={identity:.3f}")

    overlap_aligner = Align.PairwiseAligner()
    overlap_aligner.mode = "global"
    overlap_aligner.match_score = 2.0
    overlap_aligner.mismatch_score = -2.0
    overlap_aligner.open_gap_score = -6.0
    overlap_aligner.extend_gap_score = -1.0
    for attribute in (
        "open_left_insertion_score",
        "extend_left_insertion_score",
        "open_right_insertion_score",
        "extend_right_insertion_score",
        "open_left_deletion_score",
        "extend_left_deletion_score",
        "open_right_deletion_score",
        "extend_right_deletion_score",
    ):
        setattr(overlap_aligner, attribute, 0.0)

    overlap_alignment = overlap_aligner.align(forward, reverse)[0]
    aligned_forward = str(overlap_alignment[0])
    aligned_reverse = str(overlap_alignment[1])
    consensus: list[str] = []
    forward_index = 0
    reverse_index = 0
    for forward_base, reverse_base in zip(aligned_forward, aligned_reverse):
        forward_quality = (
            forward_quality_values[forward_index]
            if forward_base != "-" and forward_quality_values is not None
            else None
        )
        reverse_quality = (
            reverse_quality_values[reverse_index]
            if reverse_base != "-" and reverse_quality_values is not None
            else None
        )
        if forward_base == "-":
            consensus.append(reverse_base)
        elif reverse_base == "-":
            consensus.append(forward_base)
        else:
            consensus.append(
                choose_consensus_base(
                    forward_base,
                    reverse_base,
                    forward_quality,
                    reverse_quality,
                )
            )
        if forward_base != "-":
            forward_index += 1
        if reverse_base != "-":
            reverse_index += 1
    return "".join(consensus).strip("N")


def aligned_string_identity(left: str, right: str) -> tuple[float, int]:
    matches = 0
    mismatches = 0
    overlap_len = 0
    for left_base, right_base in zip(left, right):
        if left_base == "-" or right_base == "-":
            continue
        overlap_len += 1
        if left_base == "N" or right_base == "N":
            continue
        if left_base == right_base:
            matches += 1
        else:
            mismatches += 1
    return matches / max(1, matches + mismatches), overlap_len


def choose_consensus_base(
    a: str,
    b: str,
    a_quality: int | None = None,
    b_quality: int | None = None,
) -> str:
    if a and b:
        if a == b:
            return a
        if a == "N":
            return b
        if b == "N":
            return a
        if a_quality is not None and b_quality is not None and b_quality > a_quality:
            return b
        return a
    return a or b or "N"


def _sequence_kmers(sequence: str, word_size: int = 9) -> set[str]:
    raw = ungap(sequence).upper()
    return {
        raw[index : index + word_size]
        for index in range(max(0, len(raw) - word_size + 1))
        if set(raw[index : index + word_size]) <= BASES
    }


def _rank_orientation_references(
    oriented_sequence: str,
    references: list[FastaRecord],
    candidate_count: int = 12,
) -> list[tuple[int, FastaRecord]]:
    query_kmers = _sequence_kmers(oriented_sequence)
    ranked = [
        (len(query_kmers.intersection(_sequence_kmers(reference.sequence))), reference)
        for reference in references
    ]
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[: max(1, min(candidate_count, len(ranked)))]


def auto_orient_single_read(sequence: str, references: list[FastaRecord]) -> SingleReadOrientation:
    """Seed-and-extend comparison of both strands for orientation and assembly QC."""
    query = clean_sequence(sequence).replace("-", "")
    if not ungap(query):
        raise ValueError("Single-direction read is empty after trimming")
    if not references:
        raise ValueError("No reference records were loaded")

    candidates = [
        ("forward", query),
        ("reverse_complement", reverse_complement(query)),
    ]
    orientation_best: list[tuple[float, int, int, str, str, str]] = []
    for orientation, oriented_sequence in candidates:
        best_for_orientation: tuple[float, int, int, str, str, str] | None = None
        for seed_hits, reference in _rank_orientation_references(oriented_sequence, references):
            identity, aligned_length = pairwise_identity_and_length(oriented_sequence, reference.sequence)
            candidate = (
                identity,
                aligned_length,
                seed_hits,
                orientation,
                oriented_sequence,
                reference.name,
            )
            if best_for_orientation is None or candidate[:3] > best_for_orientation[:3]:
                best_for_orientation = candidate
        assert best_for_orientation is not None
        orientation_best.append(best_for_orientation)

    orientation_best.sort(key=lambda item: item[:3], reverse=True)
    best = orientation_best[0]
    opposite = orientation_best[1]
    identity, aligned_length, _, orientation, oriented_sequence, reference_name = best
    return SingleReadOrientation(
        sequence=oriented_sequence,
        orientation=orientation,
        best_reference=reference_name,
        best_identity=identity,
        aligned_length=aligned_length,
        opposite_identity=opposite[0],
        decision_margin=max(0.0, identity - opposite[0]),
    )


def select_reference_supported_single_read(
    forward: str,
    reverse_read: str,
    references: list[FastaRecord],
    minimum_identity: float = 0.8,
) -> tuple[str, SingleReadOrientation] | None:
    forward_oriented = auto_orient_single_read(forward, references)
    reverse_oriented = auto_orient_single_read(reverse_read, references)
    if forward_oriented.best_reference != reverse_oriented.best_reference:
        return None
    if min(forward_oriented.best_identity, reverse_oriented.best_identity) < minimum_identity:
        return None
    if forward_oriented.best_identity >= reverse_oriented.best_identity:
        return "forward", forward_oriented
    return "reverse", reverse_oriented


def load_genotype_map(path: Path) -> GenotypeMap:
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise ValueError(f"Genotype workbook is empty: {path}")

    headers = [normalize_header(value) for value in rows[0]]
    required = ["label", "group_new", "group", "genotype_new", "genotype"]
    indexes: dict[str, int] = {}
    for required_header in required:
        if required_header not in headers:
            raise ValueError(f"Genotype workbook is missing column: {required_header}")
        indexes[required_header] = headers.index(required_header)

    by_exact: dict[str, ReferenceMetadata] = {}
    by_accession: dict[str, ReferenceMetadata] = {}
    by_normalized: dict[str, ReferenceMetadata] = {}
    metadata_records: list[ReferenceMetadata] = []
    accession_counts: Counter[str] = Counter()
    for row in rows[1:]:
        label = text_cell(row[indexes["label"]])
        if not label:
            continue
        metadata = ReferenceMetadata(
            reference_id=label,
            matched_label=label,
            group_new=text_cell(row[indexes["group_new"]]),
            group_old=text_cell(row[indexes["group"]]),
            genotype_new=text_cell(row[indexes["genotype_new"]]),
            genotype_old=text_cell(row[indexes["genotype"]]),
        )
        metadata_records.append(metadata)
        accession_counts[accession_prefix(label)] += 1
        by_exact.setdefault(label, metadata)
        by_accession.setdefault(accession_prefix(label), metadata)
        by_normalized.setdefault(normalize_reference_key(label), metadata)
    return GenotypeMap(
        by_exact=by_exact,
        by_accession=by_accession,
        by_normalized=by_normalized,
        records=tuple(metadata_records),
        duplicate_accessions=tuple(
            sorted(accession for accession, count in accession_counts.items() if count > 1)
        ),
    )


def normalize_header(value) -> str:
    return text_cell(value).strip().lower().replace("-", "_")


def text_cell(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def accession_prefix(reference_id: str) -> str:
    return reference_id.strip().split("_", 1)[0].upper()


def normalize_reference_key(reference_id: str) -> str:
    key = reference_id.strip().lower()
    key = key.replace("_5_e", "_5e")
    return "".join(char for char in key if char.isalnum())


def lookup_reference_metadata(reference_id: str, genotype_map: GenotypeMap | None) -> ReferenceMetadata:
    if genotype_map is not None:
        exact = genotype_map.by_exact.get(reference_id)
        if exact is not None:
            return ReferenceMetadata(reference_id, exact.matched_label, exact.group_new, exact.group_old, exact.genotype_new, exact.genotype_old)
        accession = genotype_map.by_accession.get(accession_prefix(reference_id))
        if accession is not None:
            return ReferenceMetadata(
                reference_id,
                accession.matched_label,
                accession.group_new,
                accession.group_old,
                accession.genotype_new,
                accession.genotype_old,
            )
        normalized = genotype_map.by_normalized.get(normalize_reference_key(reference_id))
        if normalized is not None:
            return ReferenceMetadata(
                reference_id,
                normalized.matched_label,
                normalized.group_new,
                normalized.group_old,
                normalized.genotype_new,
                normalized.genotype_old,
            )

    parsed = genotype_from_reference_name(reference_id)
    return ReferenceMetadata(
        reference_id=reference_id,
        matched_label="",
        group_new=parse_group(parsed),
        group_old=parse_group(parsed),
        genotype_new=parsed,
        genotype_old=parsed,
    )


def genotype_from_reference_name(name: str) -> str:
    if "_" not in name:
        return name
    accession, genotype = name.split("_", 1)
    if any(char.isdigit() for char in accession):
        return genotype
    return name


def parse_group(genotype: str) -> str:
    parts = genotype.split("_")
    if len(parts) > 1 and len(parts[-1]) == 1 and parts[-1].isalpha():
        return "_".join(parts[:-1])
    return genotype


def pairwise_identity_and_length(query: str, target: str) -> tuple[float, int]:
    query = ungap(query)
    target = ungap(target)
    if not query or not target:
        return 0.0, 0
    aligner = Align.PairwiseAligner()
    aligner.mode = "local"
    aligner.match_score = 1.0
    aligner.mismatch_score = 0.0
    aligner.open_gap_score = -2.0
    aligner.extend_gap_score = -0.5
    alignments = aligner.align(target, query)
    try:
        alignment = alignments[0]
    except IndexError:
        return 0.0, 0
    matches = 0
    aligned = 0
    target_blocks, query_blocks = alignment.aligned
    for (t_start, t_end), (q_start, q_end) in zip(target_blocks, query_blocks):
        block_len = min(t_end - t_start, q_end - q_start)
        for i in range(block_len):
            t_base = target[t_start + i]
            q_base = query[q_start + i]
            if t_base in BASES and q_base in BASES:
                aligned += 1
                if t_base == q_base:
                    matches += 1
    if aligned == 0:
        return 0.0, 0
    return matches / aligned, aligned


def _classification_hit_rank(hit: ClassificationHit) -> tuple[float, ...]:
    """Rank reliable hits by exact covered query bases first, then identity."""
    coverage_bp = hit.query_cover_bp or hit.aligned_length
    reliable = (
        hit.identity >= MIN_TARGET_IDENTITY
        and hit.query_cover_pct >= MIN_TARGET_QUERY_COVER
    )
    return (
        float(reliable),
        float(coverage_bp),
        float(hit.identity),
        float(hit.query_cover_pct),
        float(hit.bitscore),
        -float(hit.evalue),
        float(hit.aligned_length),
    )


def _classification_result_from_hits(hits: list[ClassificationHit]) -> ClassificationResult:
    if not hits:
        raise ValueError("No reference hits were produced")
    hits.sort(key=_classification_hit_rank, reverse=True)
    best = hits[0]

    best_by_type: dict[str, ClassificationHit] = {}
    for hit in hits:
        best_by_type.setdefault(hit.new_genotype, hit)
    ranked_types = sorted(best_by_type.values(), key=_classification_hit_rank, reverse=True)
    second = next((hit for hit in ranked_types if hit.new_genotype != best.new_genotype), ranked_types[0])
    identity_margin = best.identity - second.identity

    is_target = bool(_classification_hit_rank(best)[0])
    if not is_target:
        confidence = "not_target"
        target_status = "no_reliable_match"
    elif best.identity >= 0.985 and identity_margin >= 0.005:
        confidence = "clear"
        target_status = "matched"
    elif best.identity >= 0.90:
        confidence = "probable"
        target_status = "matched"
    else:
        confidence = "review"
        target_status = "matched"

    return ClassificationResult(
        predicted_new_genotype=best.new_genotype if is_target else "no_reliable_match",
        predicted_old_genotype=best.old_genotype if is_target else "unclassified",
        predicted_new_group=best.new_group if is_target else "unclassified",
        predicted_old_group=best.old_group if is_target else "unclassified",
        confidence=confidence,
        is_target=is_target,
        target_status=target_status,
        best_hit=best.name,
        best_identity=best.identity,
        second_new_genotype=second.new_genotype,
        second_old_genotype=second.old_genotype,
        second_identity=second.identity,
        hits=hits[:10],
    )


def classify_sequence(
    query: str,
    references: list[FastaRecord],
    genotype_map: GenotypeMap | None = None,
) -> ClassificationResult:
    """Reference-similarity screen used only for orientation and assembly QC."""
    if not references:
        raise ValueError("No reference records were loaded")
    query_length = len(ungap(query))
    hits: list[ClassificationHit] = []
    for reference in references:
        metadata = lookup_reference_metadata(reference.name, genotype_map)
        identity, aligned_length = pairwise_identity_and_length(query, reference.sequence)
        hits.append(
            ClassificationHit(
                name=reference.name,
                new_genotype=metadata.genotype_new,
                old_genotype=metadata.genotype_old,
                new_group=metadata.group_new,
                old_group=metadata.group_old,
                identity=identity,
                aligned_length=aligned_length,
                query_cover_bp=aligned_length,
                query_cover_pct=aligned_length / query_length if query_length else 0.0,
            )
        )
    return _classification_result_from_hits(hits)


def prepare_reference_database(
    query_sequence: str,
    full_reference_fasta: Path,
    cache_dir: Path,
    force_rebuild: bool = False,
) -> ReferenceDatabase:
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    trimmed_path = cache_dir / TRIMMED_REFERENCE_NAME
    metadata_path = cache_dir / TRIM_METADATA_NAME

    if trimmed_path.exists() and not force_rebuild:
        return ReferenceDatabase(
            records=load_fasta_records(trimmed_path),
            path=trimmed_path,
            metadata_path=metadata_path,
            was_rebuilt=False,
            placement=load_placement_metadata(metadata_path),
        )

    full_records = load_fasta_records(Path(full_reference_fasta))
    if not full_records:
        raise ValueError(f"No FASTA records found in {full_reference_fasta}")
    if not ungap(query_sequence):
        raise ValueError("A query sequence is required to build the trimmed YC2 reference database")

    best_reference, best_identity = find_best_reference(query_sequence, full_records)
    query_aligned, placement = place_query_on_reference_alignment(query_sequence, best_reference, best_identity)
    start = placement.alignment_start_col_1based - 1
    end = placement.alignment_end_col_1based
    trimmed_records = [FastaRecord(record.name, record.sequence[start:end]) for record in full_records]
    write_fasta(trimmed_records, trimmed_path)
    write_trim_metadata(metadata_path, full_reference_fasta, placement, query_aligned)
    return ReferenceDatabase(
        records=trimmed_records,
        path=trimmed_path,
        metadata_path=metadata_path,
        was_rebuilt=True,
        placement=placement,
    )


def find_best_reference(query_sequence: str, references: list[FastaRecord]) -> tuple[FastaRecord, float]:
    ranked: list[tuple[float, int, FastaRecord]] = []
    for reference in references:
        identity, aligned_length = pairwise_identity_and_length(query_sequence, reference.sequence)
        ranked.append((identity, aligned_length, reference))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_identity, _, best_reference = ranked[0]
    return best_reference, best_identity


def place_query_on_reference_alignment(
    query_sequence: str,
    reference: FastaRecord,
    best_identity: float,
) -> tuple[str, ReferencePlacement]:
    reference_aligned = reference.sequence
    reference_raw = ungap(reference_aligned)
    query_raw = ungap(query_sequence)

    raw_index_to_column: dict[int, int] = {}
    raw_index = 0
    for column, base in enumerate(reference_aligned):
        if base != "-":
            raw_index_to_column[raw_index] = column
            raw_index += 1

    aligner = Align.PairwiseAligner()
    aligner.mode = "local"
    aligner.match_score = 2.0
    aligner.mismatch_score = -1.0
    aligner.open_gap_score = -5.0
    aligner.extend_gap_score = -1.0
    alignment = aligner.align(reference_raw, query_raw)[0]
    target_blocks, query_blocks = alignment.aligned

    covered_raw: list[int] = []
    query_aligned = ["-"] * len(reference_aligned)
    aligned_query_bases = 0
    for (t_start, t_end), (q_start, q_end) in zip(target_blocks, query_blocks):
        block_len = min(t_end - t_start, q_end - q_start)
        for i in range(block_len):
            target_raw_index = t_start + i
            query_raw_index = q_start + i
            column = raw_index_to_column.get(target_raw_index)
            if column is not None:
                query_aligned[column] = query_raw[query_raw_index]
                covered_raw.append(target_raw_index)
                aligned_query_bases += 1

    if not covered_raw:
        raise ValueError(f"Could not place query on reference {reference.name}")

    raw_start = min(covered_raw)
    raw_end = max(covered_raw) + 1
    col_start = raw_index_to_column[raw_start]
    col_end = raw_index_to_column[raw_end - 1] + 1
    placement = ReferencePlacement(
        best_reference=reference.name,
        best_identity=float(best_identity),
        aligned_query_bases=int(aligned_query_bases),
        reference_raw_start_1based=int(raw_start + 1),
        reference_raw_end_1based=int(raw_end),
        alignment_start_col_1based=int(col_start + 1),
        alignment_end_col_1based=int(col_end),
        trimmed_alignment_columns=int(col_end - col_start),
    )
    return "".join(query_aligned), placement


def write_trim_metadata(
    metadata_path: Path,
    full_reference_fasta: Path,
    placement: ReferencePlacement,
    query_aligned: str,
) -> None:
    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_full_reference_fasta": str(Path(full_reference_fasta).resolve()),
        "trimmed_reference_fasta": TRIMMED_REFERENCE_NAME,
        "placement": asdict(placement),
        "query_aligned_non_gap_bases": len(query_aligned.replace("-", "")),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def load_placement_metadata(metadata_path: Path) -> ReferencePlacement | None:
    if not metadata_path.exists():
        return None
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    placement = payload.get("placement")
    if not placement:
        return None
    return ReferencePlacement(**placement)


def align_query_to_reference_alignment(query: str, references: list[FastaRecord], best_hit: str) -> FastaRecord:
    reference = next(record for record in references if record.name == best_hit)
    query_aligned, _ = place_query_on_reference_alignment(query, reference, best_identity=0.0)
    return FastaRecord("QUERY_PLACEHOLDER", query_aligned)


def build_python_alignment(
    sample_id: str,
    consensus: str,
    references: list[FastaRecord],
    classification: ClassificationResult,
) -> list[FastaRecord]:
    query_record = align_query_to_reference_alignment(consensus, references, classification.best_hit)
    query_record.name = sample_id
    return [*references, query_record]


def pairwise_alignment_distance(left: str, right: str) -> float:
    compared = 0
    matches = 0
    for a, b in zip(left.upper(), right.upper()):
        if a in BASES and b in BASES:
            compared += 1
            if a == b:
                matches += 1
    if compared < 20:
        return 1.0
    return max(0.0, min(1.0, 1.0 - matches / compared))


def build_distance_tree(aligned_records: list[FastaRecord]):
    names = [record.name for record in aligned_records]
    matrix: list[list[float]] = []
    for i, left in enumerate(aligned_records):
        row: list[float] = []
        for j in range(i + 1):
            right = aligned_records[j]
            row.append(0.0 if i == j else pairwise_alignment_distance(left.sequence, right.sequence))
        matrix.append(row)
    dm = DistanceMatrix(names, matrix)
    return DistanceTreeConstructor().nj(dm)


def _canonical_tree_split(
    members: frozenset[str],
    all_names: frozenset[str],
) -> frozenset[str] | None:
    complement = all_names - members
    if len(members) < 2 or len(complement) < 2:
        return None
    if len(members) < len(complement):
        return members
    if len(complement) < len(members):
        return complement
    return min((members, complement), key=lambda values: tuple(sorted(values)))


def _tree_splits(tree, all_names: frozenset[str]) -> set[frozenset[str]]:
    splits: set[frozenset[str]] = set()
    for clade in tree.find_clades(order="preorder"):
        split = _canonical_tree_split(
            frozenset(terminal.name or "" for terminal in clade.get_terminals()),
            all_names,
        )
        if split is not None:
            splits.add(split)
    return splits


def _bootstrap_distance_tree(
    names: list[str],
    encoded_alignment: np.ndarray,
    rng: np.random.Generator,
):
    column_count = encoded_alignment.shape[1]
    sampled = encoded_alignment[:, rng.integers(0, column_count, size=column_count)]
    valid = np.isin(sampled, np.array(list(BASES), dtype="U1"))
    matrix: list[list[float]] = []
    for left_index in range(len(names)):
        row: list[float] = []
        for right_index in range(left_index + 1):
            if left_index == right_index:
                row.append(0.0)
                continue
            comparable = valid[left_index] & valid[right_index]
            compared = int(np.count_nonzero(comparable))
            if compared < 20:
                row.append(1.0)
                continue
            matches = int(
                np.count_nonzero(
                    (sampled[left_index] == sampled[right_index]) & comparable
                )
            )
            row.append(max(0.0, min(1.0, 1.0 - matches / compared)))
        matrix.append(row)
    return DistanceTreeConstructor().nj(DistanceMatrix(names, matrix))


def add_bootstrap_support(
    tree,
    aligned_records: Sequence[FastaRecord],
    replicates: int = 100,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_callback: Callable[[], None] | None = None,
):
    """Annotate an NJ tree with deterministic site-resampling bootstrap support."""
    replicates = max(0, int(replicates))
    if replicates == 0:
        return tree
    records = list(aligned_records)
    names = [record.name for record in records]
    if len(names) != len(set(names)):
        raise ValueError("duplicate sequence ID in bootstrap alignment")
    lengths = {len(record.sequence) for record in records}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise ValueError("bootstrap alignment sequences must have one non-zero length")

    encoded_alignment = np.array(
        [list(record.sequence.upper()) for record in records],
        dtype="U1",
    )
    digest = hashlib.sha256()
    for record in records:
        digest.update(record.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(record.sequence.upper().encode("ascii", errors="ignore"))
        digest.update(b"\0")
    seed = int.from_bytes(digest.digest()[:8], "big", signed=False)
    rng = np.random.default_rng(seed)
    all_names = frozenset(names)
    split_counts: Counter[frozenset[str]] = Counter()

    for replicate in range(1, replicates + 1):
        if cancel_callback is not None:
            cancel_callback()
        bootstrap_tree = _bootstrap_distance_tree(names, encoded_alignment, rng)
        split_counts.update(_tree_splits(bootstrap_tree, all_names))
        if progress_callback is not None and (
            replicate == 1 or replicate == replicates or replicate % 10 == 0
        ):
            progress_callback(replicate, replicates)

    for clade in tree.find_clades(order="preorder"):
        split = _canonical_tree_split(
            frozenset(terminal.name or "" for terminal in clade.get_terminals()),
            all_names,
        )
        if split is not None:
            clade.confidence = round(100.0 * split_counts[split] / replicates, 1)
    return tree


def sort_tree_increasing(tree) -> None:
    for clade in tree.find_clades(order="postorder"):
        if clade.clades:
            clade.clades.sort(key=lambda child: (len(child.get_terminals()), terminal_sort_name(child)))


def terminal_sort_name(clade) -> str:
    terminals = clade.get_terminals()
    if not terminals:
        return clade.name or ""
    return min(term.name or "" for term in terminals)


def midpoint_root_and_sort(tree):
    terminals = tree.get_terminals()
    maximum_distance = max(
        (tree.distance(left, right) for index, left in enumerate(terminals) for right in terminals[index + 1 :]),
        default=0.0,
    )
    if maximum_distance > 1e-12:
        tree.root_at_midpoint()
    sort_tree_increasing(tree)
    return tree


def infer_tree_typing_results(
    tree,
    sample_ids: Iterable[str],
    reference_names: Iterable[str],
    genotype_map: GenotypeMap | None,
) -> dict[str, TreeTypingResult]:
    """Type query leaves using root-independent reference-anchor bipartitions.

    Each edge of an unrooted tree defines a bipartition.  A genotype is an
    eligible anchor only when one side of an edge contains *all and only* the
    reference leaves assigned to that genotype (query leaves are ignored when
    testing reference purity).  A query receives a candidate only when it lies
    on exactly one such pure side.  The candidate is automatically accepted
    only when the defining IQ-TREE UFBoot edge has support >= 95.

    Midpoint rooting is deliberately irrelevant to this function.  Callers
    should pass the original IQ-TREE tree for typing and root only a copy used
    for display.
    """
    sample_id_list = list(sample_ids)
    reference_name_set = set(reference_names)
    terminals = tree.get_terminals()
    terminal_names = [terminal.name or "" for terminal in terminals]
    if any(not name for name in terminal_names):
        raise ValueError("Tree contains an unnamed terminal")
    duplicate_names = sorted(
        name for name, count in Counter(terminal_names).items() if count > 1
    )
    if duplicate_names:
        raise ValueError(
            "Tree contains duplicate terminal names: "
            + ", ".join(duplicate_names[:5])
        )
    terminal_by_name = dict(zip(terminal_names, terminals))
    missing_references = sorted(reference_name_set - set(terminal_by_name))

    metadata_by_reference = {
        name: lookup_reference_metadata(name, genotype_map)
        for name in sorted(reference_name_set & set(terminal_by_name))
    }
    anchors_by_genotype: dict[str, frozenset[str]] = {}
    grouped_anchors: dict[str, set[str]] = {}
    for name, metadata in metadata_by_reference.items():
        grouped_anchors.setdefault(metadata.genotype_new, set()).add(name)
    anchors_by_genotype = {
        genotype: frozenset(names)
        for genotype, names in grouped_anchors.items()
        if genotype
    }

    all_leaf_names = frozenset(terminal_names)
    # genotype -> component leaf set -> UFBoot support on its boundary edge
    boundary_components: dict[str, dict[frozenset[str], float | None]] = {
        genotype: {} for genotype in anchors_by_genotype
    }

    def edge_support(clade) -> float | None:
        if clade.confidence is None:
            return None
        try:
            return float(clade.confidence)
        except (TypeError, ValueError):
            return None

    def retain_boundary_support(
        genotype: str,
        component: frozenset[str],
        support: float | None,
    ) -> None:
        existing = boundary_components[genotype].get(component)
        if component not in boundary_components[genotype]:
            boundary_components[genotype][component] = support
        elif support is not None and existing is None:
            boundary_components[genotype][component] = support
        elif support is not None and existing is not None:
            # A degree-two display root can expose the same unrooted split
            # twice.  Keep the conservative support if labels disagree.
            boundary_components[genotype][component] = min(existing, support)

    # Every parent-child edge is considered in both directions; the resulting
    # split set is invariant to the arbitrary Newick root.
    for parent in tree.find_clades(order="level"):
        for child in parent.clades:
            child_side = frozenset(
                terminal.name or "" for terminal in child.get_terminals()
            )
            complement_side = all_leaf_names - child_side
            support = edge_support(child)
            for component in (child_side, complement_side):
                references_on_side = frozenset(component & reference_name_set)
                if not references_on_side:
                    continue
                for genotype, genotype_anchors in anchors_by_genotype.items():
                    if references_on_side == genotype_anchors:
                        retain_boundary_support(genotype, component, support)

    reference_monophyly = {
        genotype: bool(components)
        for genotype, components in boundary_components.items()
    }

    # Reference-derived distance envelopes provide an auditable provisional
    # guard while the formal leave-one-reference-out (LORO) calibration is
    # still pending.  Tg is the largest within-genotype patristic distance.
    # Mg is the smallest observed separation margin between a reference's
    # nearest other-genotype and nearest same-genotype neighbour.  Both are
    # recomputed on the exact IQ-TREE used for the call and are returned in the
    # result so this provisional rule is never mistaken for a validated LORO
    # threshold.
    reference_distance_thresholds: dict[str, float | None] = {}
    reference_margin_thresholds: dict[str, float | None] = {}
    for genotype, genotype_anchors in anchors_by_genotype.items():
        genotype_anchor_names = sorted(genotype_anchors)
        within_distances = [
            float(
                tree.distance(
                    terminal_by_name[left],
                    terminal_by_name[right],
                )
            )
            for index, left in enumerate(genotype_anchor_names)
            for right in genotype_anchor_names[index + 1 :]
        ]
        reference_distance_thresholds[genotype] = (
            max(within_distances) if within_distances else None
        )

        margins: list[float] = []
        other_reference_names = sorted(
            name
            for name, metadata in metadata_by_reference.items()
            if metadata.genotype_new != genotype
        )
        for anchor_name in genotype_anchor_names:
            same_distances = [
                float(
                    tree.distance(
                        terminal_by_name[anchor_name],
                        terminal_by_name[other_name],
                    )
                )
                for other_name in genotype_anchor_names
                if other_name != anchor_name
            ]
            other_distances = [
                float(
                    tree.distance(
                        terminal_by_name[anchor_name],
                        terminal_by_name[other_name],
                    )
                )
                for other_name in other_reference_names
            ]
            if same_distances and other_distances:
                margins.append(min(other_distances) - min(same_distances))
        reference_margin_thresholds[genotype] = (
            max(0.0, min(margins)) if margins else None
        )

    results: dict[str, TreeTypingResult] = {}
    for sample_id in sample_id_list:
        terminal = terminal_by_name.get(sample_id)
        if terminal is None:
            results[sample_id] = TreeTypingResult(
                sample_id=sample_id,
                predicted_new_genotype="unclassified",
                predicted_old_genotype="unclassified",
                predicted_new_group="unclassified",
                predicted_old_group="unclassified",
                confidence="review",
                status="sample_not_found",
                support=None,
                method="unrooted_reference_anchor_bipartition",
                reference_names=(),
                candidate_genotypes=(),
                nearest_reference=None,
                nearest_distance=None,
                reference_monophyletic=False,
                auto_accepted=False,
                decision_reason="sample_terminal_not_found",
            )
            continue

        reference_distances = sorted(
            (
                float(tree.distance(terminal, terminal_by_name[name])),
                name,
            )
            for name in metadata_by_reference
        )
        nearest_reference: str | None = None
        nearest_distance: float | None = None
        tied_nearest_references: tuple[str, ...] = ()
        nearest_genotypes: tuple[str, ...] = ()
        second_nearest_genotype: str | None = None
        second_nearest_distance: float | None = None
        distance_margin: float | None = None
        if reference_distances:
            nearest_distance = reference_distances[0][0]
            tied_nearest_references = tuple(
                name
                for distance, name in reference_distances
                if math.isclose(distance, nearest_distance, rel_tol=1e-9, abs_tol=1e-12)
            )
            nearest_reference = min(tied_nearest_references)
            nearest_genotypes = tuple(
                sorted(
                    {
                        metadata_by_reference[name].genotype_new
                        for name in tied_nearest_references
                    }
                )
            )

            minimum_distance_by_genotype: dict[str, float] = {}
            for distance, name in reference_distances:
                genotype = metadata_by_reference[name].genotype_new
                minimum_distance_by_genotype.setdefault(genotype, distance)
            ranked_genotype_distances = sorted(
                (
                    (distance, genotype)
                    for genotype, distance in minimum_distance_by_genotype.items()
                ),
                key=lambda item: (item[0], item[1]),
            )
            if len(ranked_genotype_distances) > 1:
                second_nearest_distance, second_nearest_genotype = (
                    ranked_genotype_distances[1]
                )
                distance_margin = second_nearest_distance - nearest_distance

        if missing_references:
            results[sample_id] = TreeTypingResult(
                sample_id=sample_id,
                predicted_new_genotype="unclassified",
                predicted_old_genotype="unclassified",
                predicted_new_group="unclassified",
                predicted_old_group="unclassified",
                confidence="review",
                status="reference_set_incomplete",
                support=None,
                method="unrooted_reference_anchor_bipartition",
                reference_names=(),
                candidate_genotypes=(),
                nearest_reference=nearest_reference,
                nearest_distance=nearest_distance,
                reference_monophyletic=False,
                auto_accepted=False,
                decision_reason=(
                    "reference_terminals_missing:"
                    + ",".join(missing_references[:5])
                ),
                nearest_genotypes=nearest_genotypes,
                second_nearest_genotype=second_nearest_genotype,
                second_nearest_distance=second_nearest_distance,
                distance_margin=distance_margin,
            )
            continue

        placements: dict[
            str,
            list[tuple[frozenset[str], float | None]],
        ] = {}
        for genotype, components in boundary_components.items():
            containing = [
                (component, support)
                for component, support in components.items()
                if sample_id in component
            ]
            if containing:
                placements[genotype] = containing

        pure_candidates = tuple(sorted(placements))
        if len(pure_candidates) != 1:
            if pure_candidates:
                candidate_genotypes = pure_candidates
                status = "ambiguous_reference_anchor"
                decision_reason = "query_in_multiple_pure_reference_components"
                candidate_references = tuple(
                    sorted(
                        {
                            name
                            for genotype in pure_candidates
                            for name in anchors_by_genotype[genotype]
                        }
                    )
                )
                monophyletic = all(
                    reference_monophyly.get(genotype, False)
                    for genotype in pure_candidates
                )
            else:
                candidate_genotypes = ()
                mixed_nearest = len(nearest_genotypes) > 1
                status = (
                    "ambiguous_reference_anchor"
                    if mixed_nearest
                    else "no_reference_anchor"
                )
                decision_reason = (
                    "nearest_reference_tie_spans_multiple_genotypes"
                    if mixed_nearest
                    else "query_not_inside_any_complete_pure_reference_component"
                )
                candidate_references = ()
                monophyletic = False
            results[sample_id] = TreeTypingResult(
                sample_id=sample_id,
                predicted_new_genotype="unclassified",
                predicted_old_genotype="unclassified",
                predicted_new_group="unclassified",
                predicted_old_group="unclassified",
                confidence="review",
                status=status,
                support=None,
                method="unrooted_reference_anchor_bipartition",
                reference_names=candidate_references,
                candidate_genotypes=candidate_genotypes,
                nearest_reference=nearest_reference,
                nearest_distance=nearest_distance,
                reference_monophyletic=monophyletic,
                auto_accepted=False,
                decision_reason=decision_reason,
                nearest_genotypes=nearest_genotypes,
                second_nearest_genotype=second_nearest_genotype,
                second_nearest_distance=second_nearest_distance,
                distance_margin=distance_margin,
            )
            continue

        genotype = pure_candidates[0]
        # Multiple edges can expose the same pure reference set when query-only
        # subclades occur inside it.  The largest component containing the query
        # is the outer genotype boundary; its support separates the candidate
        # from every other reference genotype.
        component, support = max(
            placements[genotype],
            key=lambda item: (
                len(item[0]),
                -1.0 if item[1] is None else item[1],
                tuple(sorted(item[0])),
            ),
        )
        candidate_references = tuple(sorted(anchors_by_genotype[genotype]))
        reference_distance_threshold = reference_distance_thresholds.get(genotype)
        reference_margin_threshold = reference_margin_thresholds.get(genotype)
        topology_distance_concordant = nearest_genotypes == (genotype,)
        nearest_candidate_reference = min(
            candidate_references,
            key=lambda name: (
                float(tree.distance(terminal, terminal_by_name[name])),
                name,
            ),
        )
        candidate_metadata = metadata_by_reference[nearest_candidate_reference]
        distance_within_reference_envelope = bool(
            nearest_distance is not None
            and reference_distance_threshold is not None
            and nearest_distance <= reference_distance_threshold + 1e-12
        )
        distance_margin_sufficient = bool(
            distance_margin is not None
            and reference_margin_threshold is not None
            and distance_margin >= reference_margin_threshold - 1e-12
        )
        auto_accepted = bool(
            support is not None
            and support >= TREE_AUTO_ACCEPT_MIN_SUPPORT
            and topology_distance_concordant
            and distance_within_reference_envelope
            and distance_margin_sufficient
        )
        predicted_new_genotype = genotype
        predicted_old_genotype = candidate_metadata.genotype_old
        predicted_new_group = candidate_metadata.group_new
        predicted_old_group = candidate_metadata.group_old
        if not topology_distance_concordant:
            status = "review_topology_distance_conflict"
            confidence = "review"
            decision_reason = "topology_candidate_disagrees_with_nearest_genotype_distance"
            predicted_new_genotype = "unclassified"
            predicted_old_genotype = "unclassified"
            predicted_new_group = "unclassified"
            predicted_old_group = "unclassified"
        elif not distance_within_reference_envelope:
            status = "unassigned_distant"
            confidence = "review"
            decision_reason = "nearest_candidate_distance_exceeds_reference_genotype_envelope"
            predicted_new_genotype = "unclassified"
            predicted_old_genotype = "unclassified"
            predicted_new_group = "unclassified"
            predicted_old_group = "unclassified"
        elif not distance_margin_sufficient:
            status = "review_distance_margin"
            confidence = "review"
            decision_reason = "distance_margin_below_reference_separation_envelope"
        elif auto_accepted:
            status = "matched"
            confidence = "clear"
            decision_reason = (
                "pure_complete_reference_anchor_with_ufboot_and_"
                "reference_distance_envelope_pass"
            )
        elif support is None:
            status = "review_missing_support"
            confidence = "review"
            decision_reason = "pure_complete_reference_anchor_but_boundary_support_missing"
        else:
            status = "review_low_support"
            confidence = "review"
            decision_reason = "pure_complete_reference_anchor_but_ufboot_below_95"
        if (
            nearest_distance is not None
            and math.isclose(nearest_distance, 0.0, abs_tol=1e-12)
            and len(nearest_genotypes) == 1
        ):
            decision_reason += ";zero_distance_to_unique_genotype_reference"

        results[sample_id] = TreeTypingResult(
            sample_id=sample_id,
            predicted_new_genotype=predicted_new_genotype,
            predicted_old_genotype=predicted_old_genotype,
            predicted_new_group=predicted_new_group,
            predicted_old_group=predicted_old_group,
            confidence=confidence,
            status=status,
            support=support,
            method="unrooted_reference_anchor_bipartition",
            reference_names=candidate_references,
            candidate_genotypes=(genotype,),
            nearest_reference=nearest_reference,
            nearest_distance=nearest_distance,
            reference_monophyletic=reference_monophyly.get(genotype, False),
            auto_accepted=auto_accepted,
            decision_reason=decision_reason,
            nearest_genotypes=nearest_genotypes,
            second_nearest_genotype=second_nearest_genotype,
            second_nearest_distance=second_nearest_distance,
            distance_margin=distance_margin,
            reference_distance_threshold=reference_distance_threshold,
            reference_margin_threshold=reference_margin_threshold,
            topology_distance_concordant=topology_distance_concordant,
        )
    return results


def normalize_tree_display_genotype(value: str) -> str | None:
    compact = "".join(char for char in value.strip().lower() if char.isalnum())
    if compact in TREE_GENOTYPE_COLORS:
        return compact
    return None


def tree_reference_display_genotype(
    reference_id: str,
    genotype_map: GenotypeMap | None = None,
) -> str | None:
    if genotype_map is not None:
        metadata = lookup_reference_metadata(reference_id, genotype_map)
        mapped = normalize_tree_display_genotype(metadata.genotype_new)
        if mapped is not None:
            return mapped

    tokens = reference_id.strip().split("_")
    for token in tokens[1:]:
        parsed = normalize_tree_display_genotype(token)
        if parsed is not None:
            return parsed
    for index in range(1, len(tokens) - 1):
        number = tokens[index].strip()
        letter = tokens[index + 1].strip().lower()
        if number.isdigit() and len(letter) == 1 and letter.isalpha():
            parsed = normalize_tree_display_genotype(number + letter)
            if parsed is not None:
                return parsed
    return None


def tree_scale_length(max_depth: float) -> float:
    if not math.isfinite(max_depth) or max_depth <= 0:
        return 0.1
    target = max_depth / 3.0
    exponent = math.floor(math.log10(target))
    unit = 10**exponent
    candidates = [unit, 2 * unit, 5 * unit, 10 * unit]
    return max(candidate for candidate in candidates if candidate <= target)


def render_tree_svg(
    tree,
    sample_id: str | Iterable[str],
    output_path: Path,
    title: str,
    genotype_map: GenotypeMap | None = None,
) -> None:
    sample_ids = {sample_id} if isinstance(sample_id, str) else set(sample_id)
    # The exported Newick keeps the deterministic increasing order used by the
    # analysis pipeline. Mirror only the rendered y-axis so the first clade is
    # displayed at the bottom, matching the requested publication orientation.
    terminals = list(reversed(tree.get_terminals()))
    row_height = 22
    left_margin = 28
    top_margin = 70
    plot_width = 490
    label_column_x = left_margin + plot_width + 48
    label_text_x = label_column_x + 20
    guide_end_x = label_column_x - 12
    longest_label = max((len(terminal.name or "") for terminal in terminals), default=0)
    width = max(1050, int(label_text_x + longest_label * 7.2 + 56))
    height = max(320, top_margin * 2 + row_height * max(1, len(terminals)) + 30)
    depths = tree.depths()
    max_depth = max(depths.values()) or 1.0

    y_positions = {
        terminal: top_margin + index * row_height
        for index, terminal in enumerate(terminals)
    }
    y_cache: dict[object, float] = {}

    def clade_y(clade) -> float:
        cached = y_cache.get(clade)
        if cached is not None:
            return cached
        if clade in y_positions:
            value = y_positions[clade]
        else:
            child_values = [clade_y(child) for child in clade.clades]
            value = sum(child_values) / len(child_values)
        y_cache[clade] = value
        return value

    def clade_x(clade) -> float:
        return left_margin + depths[clade] / max_depth * plot_width

    clades = list(tree.find_clades(order="preorder"))
    parent_by_clade = {
        child: parent
        for parent in clades
        for child in parent.clades
    }
    terminal_genotypes = {
        terminal: (
            None
            if terminal.name in sample_ids
            else tree_reference_display_genotype(terminal.name or "", genotype_map)
        )
        for terminal in terminals
    }
    terminals_by_clade = {clade: clade.get_terminals() for clade in clades}
    reference_genotypes_by_clade: dict[object, set[str]] = {}
    pure_reference_genotype_by_clade: dict[object, str | None] = {}
    reference_count_by_clade: dict[object, int] = {}

    for clade in clades:
        descendants = terminals_by_clade[clade]
        references = [
            terminal
            for terminal in descendants
            if terminal.name not in sample_ids
        ]
        recognized = {
            genotype
            for terminal in references
            if (genotype := terminal_genotypes[terminal]) is not None
        }
        reference_count_by_clade[clade] = len(references)
        reference_genotypes_by_clade[clade] = recognized
        pure_reference_genotype_by_clade[clade] = (
            next(iter(recognized))
            if references
            and len(references) == len(descendants)
            and len(recognized) == 1
            and all(terminal_genotypes[terminal] is not None for terminal in references)
            else None
        )

    support_rows: list[tuple[object, float, int, int, int, bool]] = []
    for clade in clades:
        if not clade.clades:
            continue
        label = clade_support_label(clade)
        if not label:
            continue
        support = float(label)
        reference_genotypes = reference_genotypes_by_clade[clade]
        parent = parent_by_clade.get(clade)
        parent_genotypes = (
            reference_genotypes_by_clade[parent]
            if parent is not None
            else set()
        )
        anchor_boundary = (
            len(reference_genotypes) == 1
            and reference_count_by_clade[clade] >= 2
            and len(parent_genotypes) > 1
        )
        support_rows.append(
            (
                clade,
                support,
                len(terminals_by_clade[clade]),
                reference_count_by_clade[clade],
                len(reference_genotypes),
                anchor_boundary,
            )
        )

    anchor_support = [
        row for row in support_rows if row[5] and row[1] >= 50
    ]
    major_support = [
        row
        for row in support_rows
        if not row[5]
        and row[1] >= 75
        and row[4] > 1
        and row[3] >= 6
        and row[2] >= 12
    ]
    major_support.sort(key=lambda row: (-row[2], -row[1]))
    support_clades = {row[0] for row in [*anchor_support, *major_support[:12]]}
    if not support_clades:
        fallback_support = [row for row in support_rows if row[1] >= 70]
        fallback_support.sort(key=lambda row: (-row[2], -row[1]))
        support_clades = {row[0] for row in fallback_support[:12]}

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" class="tree-style-v2" '
            f'role="img" aria-labelledby="tree-svg-title tree-svg-desc" '
            f'data-label-column-x="{label_column_x:.2f}" '
            f'data-vertical-order="bottom-to-top" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
        ),
        f'<title id="tree-svg-title">{escape_xml(title)}</title>',
        (
            '<desc id="tree-svg-desc">Midpoint-rooted phylogenetic tree with '
            f'{len(sample_ids)} query samples shown as aligned black triangles and '
            f'{len(terminals) - len(sample_ids)} genotype reference sequences shown in colour.</desc>'
        ),
        '<rect width="100%" height="100%" fill="white"/>',
        (
            f'<text class="tree-title" x="{left_margin}" y="26" '
            'font-family="Arial, sans-serif" font-size="18" font-weight="700" '
            f'fill="#111111">{escape_xml(title)}</text>'
        ),
        (
            f'<text class="tree-subtitle" x="{left_margin}" y="48" '
            'font-family="Arial, sans-serif" font-size="13" fill="#555555">'
            'rooting: midpoint; order: increasing; display: bottom-to-top; '
            '&#9650; samples; coloured labels: '
            'genotype references; key branch labels: bootstrap %</text>'
        ),
        '<g class="tree-branches" fill="none" stroke-linecap="square">',
    ]

    for clade in clades:
        x = clade_x(clade)
        if not clade.clades:
            continue
        clade_genotype = pure_reference_genotype_by_clade[clade]
        vertical_color = (
            TREE_GENOTYPE_COLORS[clade_genotype]
            if clade_genotype is not None
            else TREE_BACKBONE_COLOR
        )
        vertical_class = "genotype-branch" if clade_genotype is not None else "tree-backbone"
        vertical_data = (
            f' data-genotype="{escape_xml(clade_genotype)}"'
            if clade_genotype is not None
            else ""
        )
        child_ys = [clade_y(child) for child in clade.clades]
        parts.append(
            f'<line class="{vertical_class}"{vertical_data} '
            f'x1="{x:.2f}" y1="{min(child_ys):.2f}" '
            f'x2="{x:.2f}" y2="{max(child_ys):.2f}" '
            f'stroke="{vertical_color}" stroke-width="'
            f'{"2.0" if clade_genotype is not None else "1.45"}"/>'
        )
        for child in clade.clades:
            child_genotype = pure_reference_genotype_by_clade[child]
            color = (
                TREE_GENOTYPE_COLORS[child_genotype]
                if child_genotype is not None
                else TREE_BACKBONE_COLOR
            )
            line_class = "genotype-branch" if child_genotype is not None else "tree-backbone"
            line_data = (
                f' data-genotype="{escape_xml(child_genotype)}"'
                if child_genotype is not None
                else ""
            )
            parts.append(
                f'<line class="{line_class}"{line_data} '
                f'x1="{x:.2f}" y1="{clade_y(child):.2f}" '
                f'x2="{clade_x(child):.2f}" y2="{clade_y(child):.2f}" '
                f'stroke="{color}" stroke-width="'
                f'{"2.0" if child_genotype is not None else "1.45"}"/>'
            )
    parts.append("</g>")

    for clade in clades:
        if clade not in support_clades:
            continue
        label = clade_support_label(clade)
        parts.append(
            f'<text class="bootstrap-label" x="{max(12, clade_x(clade) - 5):.2f}" '
            f'y="{clade_y(clade) - 5:.2f}" text-anchor="end" '
            'font-family="Arial, sans-serif" font-size="14" font-style="italic" '
            'fill="#111111" stroke="white" stroke-width="3" '
            f'paint-order="stroke">{label}</text>'
        )

    for terminal in terminals:
        name = terminal.name or ""
        escaped_name = escape_xml(name)
        branch_end_x = clade_x(terminal)
        marker_y = clade_y(terminal)
        text_y = marker_y + 4
        is_sample = name in sample_ids
        genotype = terminal_genotypes[terminal]
        if is_sample:
            guide_color = "#8A8A8A"
            marker_points = " ".join(
                [
                    f"{label_column_x:.2f},{marker_y - 5.6:.2f}",
                    f"{label_column_x - 5.4:.2f},{marker_y + 4.6:.2f}",
                    f"{label_column_x + 5.4:.2f},{marker_y + 4.6:.2f}",
                ]
            )
            parts.extend(
                [
                    (
                        f'<g class="tip sample-tip" data-tip-type="sample" '
                        f'data-label="{escaped_name}"><title>{escaped_name} — sample; '
                        'typing status is reported in the result table</title>'
                    ),
                    (
                        f'<line class="tip-guide sample-tip-guide" x1="{branch_end_x + 4:.2f}" '
                        f'y1="{marker_y:.2f}" x2="{guide_end_x:.2f}" y2="{marker_y:.2f}" '
                        f'stroke="{guide_color}" stroke-width="0.7" stroke-dasharray="2 5" '
                        'stroke-opacity="0.65"/>'
                    ),
                    (
                        f'<polygon class="sample-marker" aria-label="query sample" '
                        f'data-cy="{marker_y:.2f}" points="{marker_points}" '
                        f'fill="{TREE_BACKBONE_COLOR}"/>'
                    ),
                    (
                        f'<text class="tip-label sample-label" x="{label_text_x:.2f}" '
                        f'y="{text_y:.2f}" font-family="Arial, sans-serif" font-size="12" '
                        f'font-weight="500" fill="{TREE_BACKBONE_COLOR}">{escaped_name}</text>'
                    ),
                    "</g>",
                ]
            )
        else:
            label_color = (
                TREE_GENOTYPE_COLORS[genotype]
                if genotype is not None
                else TREE_REFERENCE_FALLBACK_COLOR
            )
            genotype_data = (
                f' data-genotype="{escape_xml(genotype)}"'
                if genotype is not None
                else ""
            )
            genotype_title = genotype if genotype is not None else "unmapped"
            parts.extend(
                [
                    (
                        f'<g class="tip reference-tip" data-tip-type="reference"{genotype_data} '
                        f'data-label="{escaped_name}"><title>{escaped_name} — reference genotype '
                        f'{escape_xml(genotype_title)}</title>'
                    ),
                    (
                        f'<line class="tip-guide reference-tip-guide"{genotype_data} '
                        f'x1="{branch_end_x + 4:.2f}" y1="{marker_y:.2f}" '
                        f'x2="{guide_end_x:.2f}" y2="{marker_y:.2f}" '
                        f'stroke="{label_color}" stroke-width="0.7" stroke-dasharray="2 5" '
                        'stroke-opacity="0.55"/>'
                    ),
                    (
                        f'<text class="tip-label reference-label"{genotype_data} '
                        f'x="{label_text_x:.2f}" y="{text_y:.2f}" '
                        'font-family="Arial, sans-serif" font-size="12" font-weight="500" '
                        f'fill="{label_color}">{escaped_name}</text>'
                    ),
                    "</g>",
                ]
            )

    scale_value = tree_scale_length(max_depth)
    scale_width = scale_value / max_depth * plot_width
    scale_x = left_margin
    scale_y = height - 36
    scale_label = f"{scale_value:.3g}"
    parts.extend(
        [
            '<g class="tree-scale" aria-label="branch length scale">',
            (
                f'<line x1="{scale_x:.2f}" y1="{scale_y:.2f}" '
                f'x2="{scale_x + scale_width:.2f}" y2="{scale_y:.2f}" '
                f'stroke="{TREE_BACKBONE_COLOR}" stroke-width="2"/>'
            ),
            (
                f'<text x="{scale_x + scale_width / 2:.2f}" y="{scale_y - 8:.2f}" '
                'text-anchor="middle" font-family="Arial, sans-serif" font-size="12" '
                f'fill="{TREE_BACKBONE_COLOR}">{scale_label}</text>'
            ),
            "</g>",
            "</svg>\n",
        ]
    )
    output_path.write_text("\n".join(parts), encoding="utf-8")


def contains_terminal(clade, sample_id: str | Iterable[str]) -> bool:
    sample_ids = {sample_id} if isinstance(sample_id, str) else set(sample_id)
    return any(term.name in sample_ids for term in clade.get_terminals())


def clade_support_label(clade) -> str:
    if clade.confidence is not None:
        label = format_confidence(clade.confidence)
        if label:
            return label
    if getattr(clade, "name", None):
        return format_confidence(clade.name)
    return ""


def format_confidence(value) -> str:
    try:
        raw = str(value).strip()
        if "/" in raw:
            parts = [format_confidence(part) for part in raw.split("/")]
            return next((part for part in reversed(parts) if part), "")
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if 0 < number < 1 or (number == 1 and "." in raw):
        return str(int(round(number * 100)))
    if number >= 1:
        return str(int(round(number)))
    return ""


def escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_batch_tree(
    samples: Sequence[BatchTreeSample],
    references: Sequence[FastaRecord],
    output_dir: Path,
    progress_callback: Callable[[str], None] | None = None,
) -> BatchTreeResult:
    if not samples:
        raise ValueError("at least one sample is required for a batch tree")
    if not references:
        raise ValueError("at least one reference is required for a batch tree")

    sample_ids = tuple(sample.sample_id for sample in samples)
    if any(not sample_id.strip() for sample_id in sample_ids):
        raise ValueError("empty sample ID in batch tree")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate sample ID in batch tree")

    reference_records = list(references)
    reference_lengths = {len(record.sequence) for record in reference_records}
    if len(reference_lengths) != 1:
        raise ValueError("reference sequences must have the same aligned length")
    reference_names = {record.name for record in reference_records}
    if len(reference_names) != len(reference_records):
        raise ValueError("duplicate reference ID in batch tree")
    collisions = reference_names.intersection(sample_ids)
    if collisions:
        raise ValueError(f"sample ID conflicts with reference ID: {sorted(collisions)[0]}")

    aligned_samples: list[FastaRecord] = []
    for sample in samples:
        if not sample.consensus.strip():
            raise ValueError(f"empty consensus sequence for sample: {sample.sample_id}")
        if sample.best_hit not in reference_names:
            raise ValueError(f"best reference not found for sample {sample.sample_id}: {sample.best_hit}")
        aligned = align_query_to_reference_alignment(sample.consensus, reference_records, sample.best_hit)
        aligned.name = sample.sample_id
        aligned_samples.append(aligned)

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    alignment_fasta = output_dir / "YC2_355_joint_alignment.fasta"
    tree_newick = output_dir / "YC2_355_joint_tree.nwk"
    tree_svg = output_dir / "YC2_355_joint_tree.svg"

    aligned_records = [*reference_records, *aligned_samples]
    write_fasta(aligned_records, alignment_fasta)
    if progress_callback is not None:
        progress_callback("alignment_completed")
    tree = build_distance_tree(aligned_records)
    midpoint_root_and_sort(tree)
    Phylo.write(tree, str(tree_newick), "newick")
    render_tree_svg(
        tree,
        sample_ids,
        tree_svg,
        f"YC2 batch joint NJ tree ({len(sample_ids)} samples + {len(reference_records)} references)",
    )
    if progress_callback is not None:
        progress_callback("tree_completed")
    return BatchTreeResult(
        alignment_fasta=alignment_fasta,
        tree_newick=tree_newick,
        tree_svg=tree_svg,
        sample_ids=sample_ids,
    )


def safe_sample_id(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in value.strip())
    safe = safe.strip("._-")
    return safe[:80] or "sample"


def run_pipeline(
    sample_id: str,
    forward_ab1: Path,
    reverse_ab1: Path | None,
    full_reference_fasta: Path,
    genotype_xlsx: Path,
    output_dir: Path,
    cache_dir: Path,
    force_rebuild_reference: bool = False,
    analysis_mode: str = "tree",
    quality_threshold: int | None = 20,
    min_overlap: int = 40,
    allow_single_read: bool = False,
    single_read_label: str = "forward",
    cancel_callback: Callable[[], None] | None = None,
) -> PipelineResult:
    def check_cancelled() -> None:
        if cancel_callback is not None:
            cancel_callback()

    check_cancelled()
    if analysis_mode not in {"fast", "tree"}:
        raise ValueError("analysis_mode must be 'fast' or 'tree'")
    if quality_threshold is not None:
        quality_threshold = max(0, min(40, int(quality_threshold)))
    min_overlap = max(20, min(200, int(min_overlap)))
    sample_id = safe_sample_id(sample_id)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir).resolve()

    forward_path = Path(forward_ab1).resolve()
    reverse_path = Path(reverse_ab1).resolve() if reverse_ab1 is not None else None
    if reverse_path is None:
        single_read_label = single_read_label.strip().lower()
        if single_read_label not in {"read1", "read2", "forward", "reverse"}:
            raise ValueError("single_read_label must identify read1/read2 (legacy forward/reverse is also accepted)")
    trace_quality_threshold = 20 if quality_threshold is None else quality_threshold
    first_label = single_read_label if reverse_path is None else "read1"
    inspected = [(forward_path, assess_ab1_quality(forward_path, first_label, trace_quality_threshold))]
    if reverse_path is not None:
        check_cancelled()
        inspected.append((reverse_path, assess_ab1_quality(reverse_path, "read2", trace_quality_threshold)))
    check_cancelled()
    trace_quality = [metrics for _, metrics in inspected]
    passing = [(path, metrics) for path, metrics in inspected if metrics.passed]

    if not passing:
        raise TraceQualityError(_trace_quality_failure_message(trace_quality), trace_quality)
    if len(inspected) == 2 and len(passing) == 1 and not allow_single_read:
        failed_metrics = next(metrics for _, metrics in inspected if not metrics.passed)
        raise TraceQualityError(_directional_trace_failure_message(failed_metrics), trace_quality)

    if reverse_path is None:
        if not allow_single_read:
            raise ValueError("reverse_ab1 is required unless allow_single_read is true")
        read_mode = "single"
        quality_status = "passed"
        quality_note = f"{_trace_read_direction(first_label)}读段质控通过"
    elif len(passing) == 1:
        _, accepted_metrics = passing[0]
        failed_metrics = next(metrics for _, metrics in inspected if not metrics.passed)
        read_mode = "single_qc_fallback"
        quality_status = "degraded"
        quality_note = (
            f"{_directional_trace_failure_message(failed_metrics)}，"
            f"已使用{_trace_read_direction(accepted_metrics.read_label)}单向分析"
        )
    else:
        quality_status = "passed"
        quality_note = "两个测序文件质控通过"

    genotype_map = load_genotype_map(genotype_xlsx)
    passing_sequences: list[tuple[str, TrimmedTraceRead]] = []
    for path, metrics in passing:
        check_cancelled()
        passing_sequences.append(
            (
                metrics.read_label,
                read_ab1_trimmed_with_qualities(path, quality_threshold=quality_threshold),
            )
        )
    check_cancelled()
    placement_query = passing_sequences[0][1].sequence
    trimmed_path = cache_dir / TRIMMED_REFERENCE_NAME
    if force_rebuild_reference or not trimmed_path.exists():
        full_records = load_fasta_records(Path(full_reference_fasta).resolve())
        placement_query = auto_orient_single_read(placement_query, full_records).sequence
    reference_database = prepare_reference_database(
        query_sequence=placement_query,
        full_reference_fasta=Path(full_reference_fasta).resolve(),
        cache_dir=cache_dir,
        force_rebuild=force_rebuild_reference,
    )
    check_cancelled()
    oriented_reads: list[tuple[str, SingleReadOrientation, tuple[int, ...]]] = []
    for label, trace_read in passing_sequences:
        check_cancelled()
        orientation = auto_orient_single_read(trace_read.sequence, reference_database.records)
        oriented_qualities = (
            tuple(reversed(trace_read.qualities))
            if orientation.orientation == "reverse_complement"
            else trace_read.qualities
        )
        oriented_reads.append(
            (label, orientation, oriented_qualities)
        )
    check_cancelled()

    if len(oriented_reads) == 1:
        label, oriented, _qualities = oriented_reads[0]
        consensus = oriented.sequence
        read_orientation = f"{label}:{oriented.orientation}"
        orientation_identity: float | None = oriented.best_identity
        direction_note = "反向互补为正向序列" if oriented.orientation == "reverse_complement" else "已是参考正向"
        quality_note = f"{quality_note}；参考库双链比对：{direction_note}"
    else:
        (
            first_read_label,
            first_oriented,
            first_qualities,
        ), (
            second_read_label,
            second_oriented,
            second_qualities,
        ) = oriented_reads[:2]
        try:
            consensus = best_oriented_overlap_consensus(
                first_oriented.sequence,
                second_oriented.sequence,
                min_overlap=min_overlap,
                read1_qualities=first_qualities,
                read2_qualities=second_qualities,
            )
        except ValueError as overlap_error:
            if not allow_single_read:
                raise
            selected_label, selected, _selected_qualities = max(
                oriented_reads,
                key=lambda item: (item[1].best_identity, item[1].aligned_length),
            )
            if selected.best_identity < 0.8:
                raise overlap_error
            consensus = selected.sequence
            read_mode = "single_fallback"
            quality_status = "degraded"
            quality_note = (
                "两个测序文件质控通过但无法可靠拼接；"
                f"已保留{_trace_read_direction(selected_label)}的参考定向单向序列"
            )
            read_orientation = f"{selected_label}:{selected.orientation}"
            orientation_identity = selected.best_identity
        else:
            consensus_orientation = auto_orient_single_read(consensus, reference_database.records)
            consensus = consensus_orientation.sequence
            read_mode = "paired"
            read_orientation = (
                f"{first_read_label}:{first_oriented.orientation};"
                f"{second_read_label}:{second_oriented.orientation}"
            )
            orientation_identity = min(
                first_oriented.best_identity,
                second_oriented.best_identity,
            )
            quality_note = "两个测序文件质控通过；参考库双链比对定向后完成缺口感知、质量值加权拼接"
    classification = classify_sequence(consensus, reference_database.records, genotype_map)
    check_cancelled()

    consensus_fasta = output_dir / f"{sample_id}_consensus.fasta"
    alignment_fasta = output_dir / f"{sample_id}_alignment.fasta" if analysis_mode == "tree" else None
    tree_newick = output_dir / f"{sample_id}_tree_midpoint_increasing.nwk" if analysis_mode == "tree" else None
    tree_svg = output_dir / f"{sample_id}_tree_midpoint_increasing.svg" if analysis_mode == "tree" else None
    report_json = output_dir / f"{sample_id}_report.json"
    report_html = output_dir / f"{sample_id}_report.html"

    write_fasta([FastaRecord(sample_id, consensus)], consensus_fasta)
    check_cancelled()
    if analysis_mode == "tree":
        assert alignment_fasta is not None
        assert tree_newick is not None
        assert tree_svg is not None
        aligned_records = build_python_alignment(sample_id, consensus, reference_database.records, classification)
        write_fasta(aligned_records, alignment_fasta)
        tree = build_distance_tree(aligned_records)
        midpoint_root_and_sort(tree)
        Phylo.write(tree, str(tree_newick), "newick")
        render_tree_svg(
            tree,
            sample_id,
            tree_svg,
            f"{sample_id} YC2 355-reference NJ tree",
            genotype_map=genotype_map,
        )

    result = PipelineResult(
        sample_id=sample_id,
        consensus=consensus,
        consensus_length=len(consensus),
        analysis_mode=analysis_mode,
        read_mode=read_mode,
        read_orientation=read_orientation,
        orientation_identity=orientation_identity,
        quality_threshold=quality_threshold,
        min_overlap=min_overlap,
        trace_quality=trace_quality,
        quality_status=quality_status,
        quality_note=quality_note,
        classification=classification,
        reference_database=reference_database,
        output_dir=output_dir,
        consensus_fasta=consensus_fasta,
        alignment_fasta=alignment_fasta,
        tree_newick=tree_newick,
        tree_svg=tree_svg,
        report_json=report_json,
        report_html=report_html,
    )
    check_cancelled()
    write_report(result)
    return result


def write_report(result: PipelineResult) -> None:
    placement = result.reference_database.placement
    tree_typing = result.tree_typing
    if result.typing_pending:
        final_new_genotype = ""
        final_old_genotype = ""
        final_new_group = ""
        final_old_group = ""
        confidence = "pending"
        is_target = False
        target_status = "pending_phylogeny"
        typing_source = "pending_phylogeny"
    else:
        final_new_genotype = tree_typing.predicted_new_genotype if tree_typing else result.classification.predicted_new_genotype
        final_old_genotype = tree_typing.predicted_old_genotype if tree_typing else result.classification.predicted_old_genotype
        final_new_group = tree_typing.predicted_new_group if tree_typing else result.classification.predicted_new_group
        final_old_group = tree_typing.predicted_old_group if tree_typing else result.classification.predicted_old_group
        confidence = tree_typing.confidence if tree_typing else result.classification.confidence
        is_target = tree_typing.auto_accepted if tree_typing else result.classification.is_target
        target_status = tree_typing.status if tree_typing else result.classification.target_status
        typing_source = "phylogenetic_tree" if tree_typing else "similarity_legacy"
    expose_similarity = tree_typing is None and not result.typing_pending
    files = {"consensus_fasta": result.consensus_fasta.name}
    if result.alignment_fasta is not None:
        files["alignment_fasta"] = result.alignment_fasta.name
    if result.tree_newick is not None:
        files["tree_newick"] = result.tree_newick.name
    if result.tree_svg is not None:
        files["tree_svg"] = result.tree_svg.name
    report = {
        "sample_id": result.sample_id,
        "consensus_length": result.consensus_length,
        "analysis_mode": result.analysis_mode,
        "read_mode": result.read_mode,
        "read_orientation": result.read_orientation,
        "orientation_identity": round(result.orientation_identity, 6) if result.orientation_identity is not None else None,
        "orientation_method": "reference_dual_strand_seed_extend",
        "quality_threshold": result.quality_threshold,
        "min_overlap": result.min_overlap,
        "trace_quality": [asdict(metrics) for metrics in result.trace_quality],
        "quality_status": result.quality_status,
        "quality_note": result.quality_note,
        "predicted_new_genotype": final_new_genotype,
        "predicted_old_genotype": final_old_genotype,
        "predicted_new_group": final_new_group,
        "predicted_old_group": final_old_group,
        "confidence": confidence,
        "is_target": is_target,
        "target_status": target_status,
        "typing_source": typing_source,
        "typing_rule_id": TYPING_RULE_ID,
        "typing_rule_version": TYPING_RULE_VERSION,
        "tree_typing_candidate_genotypes": list(tree_typing.candidate_genotypes) if tree_typing else [],
        "tree_typing_nearest_reference": tree_typing.nearest_reference if tree_typing else None,
        "tree_typing_nearest_distance": tree_typing.nearest_distance if tree_typing else None,
        "tree_typing_reference_monophyletic": tree_typing.reference_monophyletic if tree_typing else False,
        "tree_typing_auto_accepted": tree_typing.auto_accepted if tree_typing else False,
        "tree_typing_decision_reason": tree_typing.decision_reason if tree_typing else "pending_phylogeny" if result.typing_pending else None,
        "similarity_predicted_new_genotype": result.classification.predicted_new_genotype if expose_similarity else None,
        "similarity_predicted_old_genotype": result.classification.predicted_old_genotype if expose_similarity else None,
        "similarity_predicted_new_group": result.classification.predicted_new_group if expose_similarity else None,
        "similarity_predicted_old_group": result.classification.predicted_old_group if expose_similarity else None,
        "tree_typing": asdict(tree_typing) if tree_typing else None,
        "best_hit": result.classification.best_hit if expose_similarity else None,
        "best_identity": round(result.classification.best_identity, 6) if expose_similarity else None,
        "second_new_genotype": result.classification.second_new_genotype if expose_similarity else None,
        "second_old_genotype": result.classification.second_old_genotype if expose_similarity else None,
        "second_identity": round(result.classification.second_identity, 6) if expose_similarity else None,
        "reference_database": {
            "path": str(result.reference_database.path),
            "metadata_path": str(result.reference_database.metadata_path),
            "was_rebuilt": result.reference_database.was_rebuilt,
            "placement": asdict(placement) if placement else None,
        },
        "top_hits": [
            {
                "name": hit.name,
                "new_genotype": hit.new_genotype,
                "old_genotype": hit.old_genotype,
                "new_group": hit.new_group,
                "old_group": hit.old_group,
                "identity": round(hit.identity, 6),
                "aligned_length": hit.aligned_length,
                "query_cover_bp": hit.query_cover_bp,
                "query_cover_pct": round(hit.query_cover_pct, 6),
                "evalue": hit.evalue,
                "bitscore": round(hit.bitscore, 3),
            }
            for hit in (result.classification.hits if expose_similarity else [])
        ],
        "files": files,
    }
    result.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    result.report_html.write_text(render_report_html(result, report), encoding="utf-8")


def render_report_html(result: PipelineResult, report: dict) -> str:
    trace_quality_rows = "\n".join(
        "<tr>"
        f"<td>{escape_xml(metrics['read_label'])}</td>"
        f"<td>{'pass' if metrics['passed'] else 'fail'}</td>"
        f"<td>{escape_xml(metrics['failure_code'] or '')}</td>"
        f"<td>{escape_xml(metrics['message'])}</td>"
        f"<td>{metrics['core_length']}</td>"
        f"<td>{metrics['q_pass_fraction']:.3%}</td>"
        f"<td>{metrics['mixed_peak_fraction']:.3%}</td>"
        f"<td>{metrics['n_fraction']:.3%}</td>"
        "</tr>"
        for metrics in report["trace_quality"]
    )
    tree_section = ""
    if result.tree_svg is not None:
        tree_section = f"""<h2>Tree</h2>
<iframe src="{result.tree_svg.name}"></iframe>"""
    else:
        tree_section = "<h2>Tree</h2><p>QC, orientation and assembly are complete; joint IQ-TREE typing is pending.</p>"
    tree_support = (report.get("tree_typing") or {}).get("support")
    tree_support_label = "—" if tree_support is None else str(tree_support)
    return f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>{escape_xml(result.sample_id)} YC2 typing report</title>
<style>
body {{ font-family: Arial, "Microsoft YaHei", sans-serif; margin: 28px; color: #222; }}
.summary {{ display: grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 10px 26px; max-width: 920px; }}
.summary div {{ border-bottom: 1px solid #ddd; padding: 8px 0; }}
table {{ border-collapse: collapse; margin-top: 22px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 9px; font-size: 13px; }}
th {{ background: #f3f4f6; }}
iframe {{ width: 100%; height: 900px; border: 1px solid #ddd; margin-top: 20px; }}
</style>
<h1>{escape_xml(result.sample_id)} phylogenetic typing report</h1>
<section class="summary">
<div><b>Candidate new genotype</b><br>{escape_xml(report['predicted_new_genotype'])}</div>
<div><b>Candidate old genotype</b><br>{escape_xml(report['predicted_old_genotype'])}</div>
<div><b>Candidate new group</b><br>{escape_xml(report['predicted_new_group'])}</div>
<div><b>Candidate old group</b><br>{escape_xml(report['predicted_old_group'])}</div>
<div><b>Typing source</b><br>{escape_xml(report['typing_source'])}</div>
<div><b>Tree support</b><br>{escape_xml(tree_support_label)}</div>
<div><b>Automatic decision</b><br>{escape_xml(str(report.get('tree_typing_auto_accepted', False)))}</div>
<div><b>Decision reason</b><br>{escape_xml(str(report.get('tree_typing_decision_reason') or '—'))}</div>
<div><b>Confidence</b><br>{escape_xml(report['confidence'])}</div>
<div><b>Analysis mode</b><br>{escape_xml(report['analysis_mode'])}</div>
<div><b>Read mode</b><br>{escape_xml(report['read_mode'])}</div>
<div><b>Read orientation</b><br>{escape_xml(report['read_orientation'])}</div>
<div><b>Orientation method</b><br>Reference dual-strand seed-and-extend orientation only; not used for genotype calling</div>
<div><b>Orientation identity</b><br>{f"{report['orientation_identity']:.3%}" if report['orientation_identity'] is not None else "—"}</div>
<div><b>Quality threshold</b><br>{escape_xml(str(report['quality_threshold']))}</div>
<div><b>Quality status</b><br>{escape_xml(report['quality_status'])}</div>
<div><b>Quality note</b><br>{escape_xml(report['quality_note'])}</div>
<div><b>Minimum overlap</b><br>{report['min_overlap']} bp</div>
<div><b>质控拼接输出长度</b><br>{result.consensus_length} bp</div>
</section>
<h2>Trace quality</h2>
<table>
<tr><th>Read</th><th>Status</th><th>Failure code</th><th>Message</th><th>Core bp</th><th>Q-pass</th><th>Mixed peaks</th><th>N bases</th></tr>
{trace_quality_rows}
</table>
{tree_section}
</html>
"""


def find_default_reference_paths(tool_root: Path) -> tuple[Path | None, Path | None]:
    for ancestor in [tool_root, *tool_root.parents]:
        reference_dir = ancestor / "校正参考序列"
        full_reference = reference_dir / "0104-unique_355.rename.rename.map.fas"
        genotype_xlsx = reference_dir / "ID-基因型参照.xlsx"
        if full_reference.exists() and genotype_xlsx.exists():
            return full_reference, genotype_xlsx
    return None, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone YC2 typing with the 355-reference database.")
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--forward-ab1", required=True, type=Path)
    parser.add_argument("--reverse-ab1", required=True, type=Path)
    parser.add_argument("--full-reference-fasta", type=Path)
    parser.add_argument("--genotype-xlsx", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--force-rebuild-reference", action="store_true")
    parser.add_argument("--analysis-mode", choices=["fast", "tree"], default="tree")
    parser.add_argument("--quality-threshold", type=int, default=20)
    parser.add_argument("--min-overlap", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tool_root = Path(__file__).resolve().parent
    default_reference, default_genotypes = find_default_reference_paths(tool_root)
    full_reference = args.full_reference_fasta or default_reference
    genotype_xlsx = args.genotype_xlsx or default_genotypes
    if full_reference is None or genotype_xlsx is None:
        raise SystemExit("Reference FASTA and genotype workbook were not found. Pass --full-reference-fasta and --genotype-xlsx.")
    output_dir = args.output_dir or (tool_root / "results" / safe_sample_id(args.sample_id))
    cache_dir = args.cache_dir or (tool_root / "reference_cache")
    result = run_pipeline(
        sample_id=args.sample_id,
        forward_ab1=args.forward_ab1,
        reverse_ab1=args.reverse_ab1,
        full_reference_fasta=full_reference,
        genotype_xlsx=genotype_xlsx,
        output_dir=output_dir,
        cache_dir=cache_dir,
        force_rebuild_reference=args.force_rebuild_reference,
        analysis_mode=args.analysis_mode,
        quality_threshold=args.quality_threshold,
        min_overlap=args.min_overlap,
    )
    print(result.report_json.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
