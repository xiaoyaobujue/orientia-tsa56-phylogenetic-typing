from io import StringIO

from Bio import Phylo
import pytest

import backend.app  # noqa: F401 - makes the local pipeline module importable
from backend.pipeline import (
    GenotypeMap,
    ReferenceMetadata,
    infer_tree_typing_results,
)


REFERENCE_TYPES = {
    "A1": "gA",
    "A2": "gA",
    "A3": "gA",
    "B1": "gB",
    "B2": "gB",
    "B3": "gB",
    "C1": "gC",
    "C2": "gC",
    "C3": "gC",
}


def genotype_map() -> GenotypeMap:
    records = tuple(
        ReferenceMetadata(
            reference_id=name,
            matched_label=name,
            group_new=f"group_{genotype}",
            group_old=f"old_group_{genotype}",
            genotype_new=genotype,
            genotype_old=f"old_{genotype}",
        )
        for name, genotype in REFERENCE_TYPES.items()
    )
    return GenotypeMap(
        by_exact={record.reference_id: record for record in records},
        by_accession={record.reference_id: record for record in records},
        by_normalized={record.reference_id.lower(): record for record in records},
        records=records,
    )


def type_query(newick: str, reference_names=None):
    tree = Phylo.read(StringIO(newick), "newick")
    return infer_tree_typing_results(
        tree,
        ["Q"],
        reference_names or REFERENCE_TYPES.keys(),
        genotype_map(),
    )["Q"]


@pytest.mark.parametrize(
    ("support", "status", "accepted"),
    [
        ("", "review_missing_support", False),
        ("94", "review_low_support", False),
        ("95", "matched", True),
    ],
)
def test_reference_anchor_support_threshold(support, status, accepted):
    result = type_query(
        f"((A1:.1,A2:.1,A3:.1,Q:.1){support}:.2,"
        "(B1:.1,B2:.1,B3:.1)99:.2,"
        "(C1:.1,C2:.1,C3:.1)99:.2);"
    )

    assert result.predicted_new_genotype == "gA"
    assert result.candidate_genotypes == ("gA",)
    assert result.reference_monophyletic is True
    assert result.status == status
    assert result.auto_accepted is accepted
    assert result.support == (float(support) if support else None)
    assert result.nearest_genotypes == ("gA",)
    assert result.topology_distance_concordant is True
    assert result.reference_distance_threshold == pytest.approx(0.2)
    assert result.reference_margin_threshold == pytest.approx(0.4)
    assert result.distance_margin == pytest.approx(0.4)


def test_reference_anchor_typing_is_invariant_to_newick_root():
    first = type_query(
        "((A1:.1,A2:.1,A3:.1,Q:.1)97:.2,"
        "(B1:.1,B2:.1,B3:.1)99:.2,"
        "(C1:.1,C2:.1,C3:.1)99:.2);"
    )
    rerooted = type_query(
        "(((A1:.1,A2:.1,A3:.1,Q:.1)97:.2,"
        "(B1:.1,B2:.1,B3:.1)99:.2),"
        "C1:.1,C2:.1,C3:.1);"
    )

    assert rerooted.predicted_new_genotype == first.predicted_new_genotype == "gA"
    assert rerooted.candidate_genotypes == first.candidate_genotypes == ("gA",)
    assert rerooted.auto_accepted is first.auto_accepted is True
    assert rerooted.support == first.support == 97.0


def test_query_without_pure_anchor_is_rejected_and_nearest_is_not_a_candidate():
    result = type_query(
        "((A1:.1,A2:.1,A3:.1)99:.2,"
        "(B1:.1,B2:.1,B3:.1)99:.2,"
        "(C1:.1,C2:.1,C3:.1)99:.2,Q:.1);"
    )

    assert result.status == "ambiguous_reference_anchor"
    assert result.predicted_new_genotype == "unclassified"
    assert result.candidate_genotypes == ()
    assert result.reference_names == ()
    assert result.nearest_reference is not None
    assert result.nearest_distance is not None
    assert result.auto_accepted is False


def test_incomplete_reference_set_is_rejected_without_topology_candidate():
    result = type_query(
        "((A1:.1,A2:.1,A3:.1,Q:.1)99:.2,"
        "(B1:.1,B2:.1,B3:.1)99:.2,"
        "(C1:.1,C2:.1)99:.2);"
    )

    assert result.status == "reference_set_incomplete"
    assert result.candidate_genotypes == ()
    assert result.reference_names == ()
    assert result.auto_accepted is False


def test_zero_distance_unique_reference_is_recorded_but_still_requires_support():
    result = type_query(
        "((A1:0,Q:0,A2:.1,A3:.1)94:.2,"
        "(B1:.1,B2:.1,B3:.1)99:.2,"
        "(C1:.1,C2:.1,C3:.1)99:.2);"
    )

    assert result.nearest_reference == "A1"
    assert result.nearest_distance == 0.0
    assert "zero_distance_to_unique_genotype_reference" in result.decision_reason
    assert result.status == "review_low_support"
    assert result.auto_accepted is False


def test_topology_and_nearest_genotype_conflict_is_never_auto_accepted():
    result = type_query(
        "((A1:.1,A2:.1,A3:.1,Q:2)99:.01,"
        "(B1:.01,B2:.1,B3:.1)99:.01,"
        "(C1:.1,C2:.1,C3:.1)99:.2);"
    )

    assert result.candidate_genotypes == ("gA",)
    assert result.nearest_genotypes == ("gB",)
    assert result.topology_distance_concordant is False
    assert result.status == "review_topology_distance_conflict"
    assert result.predicted_new_genotype == "unclassified"
    assert result.auto_accepted is False


def test_candidate_outside_reference_distance_envelope_is_unassigned():
    result = type_query(
        "((A1:.1,A2:.1,A3:.1,Q:1)99:.2,"
        "(B1:.1,B2:.1,B3:.1)99:.2,"
        "(C1:.1,C2:.1,C3:.1)99:.2);"
    )

    assert result.candidate_genotypes == ("gA",)
    assert result.nearest_genotypes == ("gA",)
    assert result.nearest_distance == pytest.approx(1.1)
    assert result.reference_distance_threshold == pytest.approx(0.2)
    assert result.status == "unassigned_distant"
    assert result.predicted_new_genotype == "unclassified"
    assert result.auto_accepted is False
