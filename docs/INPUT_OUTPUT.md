# Input and output

## Accepted input

| Mode | Files | Limits | Processing |
|---|---|---|---|
| Single AB1 | 1–2 `.ab1` files | 8 MB per upload | QC, orientation, optional merge, fixed-reference tree |
| Batch AB1 | Multiple `.ab1` files | 1–100 recognised samples, at most 2 reads per sample | Per-sample preparation, one joint fixed-reference tree |
| FASTA | One `.fa`, `.fas`, `.fasta` or `.fna` file | 1–100 unique names; DNA/IUPAC characters | Direct fixed-reference alignment and joint tree |

Do not upload patient names, medical-record numbers or other direct identifiers. Sample labels become tree tip labels and are included in downloaded reports.

## Batch filename recognition

The web interface removes common read-direction markers such as `NF`, `NR`, `F`, `R`, `forward` and `reverse` when grouping paired files. Always inspect the detected sample groups before starting the analysis. If a sample is assigned more than two files, rename or split the input.

## Primary outputs

- `report_json`: machine-readable quality, method, decision and tree evidence.
- `report_html`: human-readable analysis report.
- `consensus_fasta`: AB1-derived final sample sequence.
- `alignment_fasta`: sample sequences added to the fixed 60-reference alignment.
- `tree_file`/`tree_newick`: analysis and display Newick files.
- `tree_svg`: browser-readable system phylogeny.
- `iqtree_report`, `iqtree_log`, `iqtree_ufboot`, `iqtree_contree`: raw IQ-TREE evidence.

All downloads belong to an unguessable anonymous job ID. The default server policy deletes job files after 24 hours.
