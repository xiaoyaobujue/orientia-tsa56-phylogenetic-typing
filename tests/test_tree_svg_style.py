from io import StringIO
from pathlib import Path
import xml.etree.ElementTree as ET

from Bio import Phylo
import pytest

from backend.pipeline import (
    TREE_BACKBONE_COLOR,
    TREE_GENOTYPE_COLORS,
    render_tree_svg,
    tree_reference_display_genotype,
)


@pytest.mark.parametrize(
    ("reference_id", "expected"),
    [
        ("AB536749_1_b_Shimokoshi", "1b"),
        ("GU120163_2_a_Kato_A", "2a"),
        ("GU120148_2_b_Kato_B", "2b"),
        ("GU120141_3_a_TA763_A", "3a"),
        ("GU446604_3_b_TA763_B", "3b"),
        ("GU120168_4_a_TD", "4a"),
        ("EA433704_4_b", "4b"),
        ("JX235719_4_c_JG_A", "4c"),
        ("MW495600_4_d", "4d"),
        ("JQ898358_4_e_Kawasaki", "4e"),
        ("GQ332758_4_f_JG_C", "4f"),
        ("GQ495610_5_a_Boryong", "5a"),
        ("AF302991_5_b_Saitama", "5b"),
        ("GU446597_5_c_Karp_B", "5c"),
        ("AF430143_5_d_Karp_C", "5d"),
        ("KJ001159_5_e_Karp_A", "5e"),
    ],
)
def test_reference_labels_resolve_to_display_genotypes(reference_id, expected):
    assert tree_reference_display_genotype(reference_id) == expected


def test_tree_svg_uses_aligned_black_triangles_and_coloured_references(tmp_path: Path):
    tree = Phylo.read(
        StringIO(
            "((sample_A:0.01,KJ001159_5_e_Karp_A:0.01)96:0.02,"
            "(sample_B:0.02,AF430143_5_d_Karp_C:0.01)83:0.02)100;"
        ),
        "newick",
    )
    output = tmp_path / "styled-tree.svg"

    render_tree_svg(tree, {"sample_A", "sample_B"}, output, "Styled tree")

    svg = output.read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    sample_markers = root.findall(".//svg:polygon[@class='sample-marker']", namespace)
    sample_guides = root.findall(".//svg:line[@class='tip-guide sample-tip-guide']", namespace)
    reference_labels = root.findall(".//svg:text[@class='tip-label reference-label']", namespace)
    bootstrap_labels = root.findall(".//svg:text[@class='bootstrap-label']", namespace)

    assert root.attrib["class"] == "tree-style-v2"
    assert root.attrib["role"] == "img"
    assert root.attrib["data-vertical-order"] == "bottom-to-top"
    assert root.attrib["width"] == "1050"
    assert len(sample_markers) == 2
    assert len(sample_guides) == 2
    assert all(marker.attrib["fill"] == TREE_BACKBONE_COLOR for marker in sample_markers)
    label_column_x = float(root.attrib["data-label-column-x"])
    marker_x_values = {
        float(marker.attrib["points"].split()[0].split(",")[0])
        for marker in sample_markers
    }
    assert label_column_x == 566.0
    assert marker_x_values == {label_column_x}
    sample_y_by_label = {
        group.attrib["data-label"]: float(
            group.find("svg:polygon[@class='sample-marker']", namespace).attrib["data-cy"]
        )
        for group in root.findall(".//svg:g[@class='tip sample-tip']", namespace)
    }
    assert sample_y_by_label["sample_A"] > sample_y_by_label["sample_B"]
    assert {label.attrib["data-genotype"] for label in reference_labels} == {"5d", "5e"}
    assert {label.attrib["fill"] for label in reference_labels} == {
        TREE_GENOTYPE_COLORS["5d"],
        TREE_GENOTYPE_COLORS["5e"],
    }
    assert len(bootstrap_labels) == 3
    assert all(label.attrib["font-size"] == "14" for label in bootstrap_labels)
    assert 'class="tree-scale"' in svg
    assert 'class="tree-backbone"' in svg
    assert 'class="genotype-branch"' in svg
    assert "#d62728" not in svg.lower()


def test_tree_svg_keeps_unmapped_references_neutral(tmp_path: Path):
    tree = Phylo.read(StringIO("(sample_A:0.1,reference_without_genotype:0.2);"), "newick")
    output = tmp_path / "neutral-tree.svg"

    render_tree_svg(tree, "sample_A", output, "Neutral fallback")

    svg = output.read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    reference_label = root.find(".//svg:text[@class='tip-label reference-label']", namespace)

    assert reference_label is not None
    assert "data-genotype" not in reference_label.attrib
    assert reference_label.text == "reference_without_genotype"
