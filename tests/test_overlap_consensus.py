from __future__ import annotations

from pathlib import Path

from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
import pytest

from backend import pipeline


REFERENCE = (
    "ACGTTGCAAGTCCGATGCTACCTGAGTACGATCGTTAAGCGGCTACATGACCTGATCGTAC"
    "CGATGGTACCACTGAGTCGATACGCTTAGGCTACGATGCCAATCGTACGGTTCAGATGCTA"
)


def test_gap_aware_consensus_preserves_single_base_insertion() -> None:
    deletion_index = 57
    read_with_deletion = REFERENCE[:deletion_index] + REFERENCE[deletion_index + 1 :]

    consensus = pipeline.best_oriented_overlap_consensus(
        read_with_deletion,
        REFERENCE,
        min_overlap=80,
        read1_qualities=(40,) * len(read_with_deletion),
        read2_qualities=(40,) * len(REFERENCE),
    )

    assert consensus == REFERENCE


def test_quality_weighted_conflict_uses_higher_phred_base() -> None:
    conflict_index = 73
    alternative = "A" if REFERENCE[conflict_index] != "A" else "C"
    read1 = REFERENCE[:conflict_index] + alternative + REFERENCE[conflict_index + 1 :]
    read1_qualities = [40] * len(read1)
    read2_qualities = [40] * len(REFERENCE)
    read1_qualities[conflict_index] = 12
    read2_qualities[conflict_index] = 55

    consensus = pipeline.best_oriented_overlap_consensus(
        read1,
        REFERENCE,
        min_overlap=80,
        read1_qualities=read1_qualities,
        read2_qualities=read2_qualities,
    )

    assert consensus == REFERENCE


def test_equal_quality_conflict_remains_deterministic_to_read1() -> None:
    conflict_index = 73
    alternative = "A" if REFERENCE[conflict_index] != "A" else "C"
    read1 = REFERENCE[:conflict_index] + alternative + REFERENCE[conflict_index + 1 :]

    consensus = pipeline.best_oriented_overlap_consensus(
        read1,
        REFERENCE,
        min_overlap=80,
        read1_qualities=(40,) * len(read1),
        read2_qualities=(40,) * len(REFERENCE),
    )

    assert consensus[conflict_index] == alternative


def test_quality_vector_length_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="forward sequence/quality length mismatch"):
        pipeline.best_oriented_overlap_consensus(
            REFERENCE,
            REFERENCE,
            min_overlap=80,
            read1_qualities=(40,) * (len(REFERENCE) - 1),
            read2_qualities=(40,) * len(REFERENCE),
        )


def test_ab1_trimmed_reader_keeps_quality_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    record = SeqRecord(Seq("ARCGTY"))
    record.letter_annotations["phred_quality"] = [30, 25, 35, 40, 22, 30]
    monkeypatch.setattr(pipeline.SeqIO, "read", lambda *_args, **_kwargs: record)

    trace_read = pipeline.read_ab1_trimmed_with_qualities(
        Path("synthetic.ab1"),
        quality_threshold=20,
    )

    assert trace_read.sequence == "ANCGTN"
    assert trace_read.qualities == (30, 25, 35, 40, 22, 30)
    assert len(trace_read.sequence) == len(trace_read.qualities)
