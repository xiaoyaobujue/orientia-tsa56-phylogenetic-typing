# Orientia TSA56 Phylogenetic Typing V1

[English](README.md) | [简体中文](README.zh-CN.md)

A local, article-standard phylogenetic typing tool for *Orientia tsutsugamushi* TSA56 sequences. This publication edition exposes one locked V1.0 workflow and does not provide rapid, BLAST-based or placement-based genotype calls.

## Analysis workflow

1. AB1 input undergoes chromatogram QC, orientation and paired-read assembly; FASTA input starts at alignment.
2. Sample sequences are added to the locked 60-reference alignment with `mafft --add --keeplength`.
3. IQ-TREE uses `TVM+F+R5`, 1,000 ultrafast bootstrap replicates, device-adaptive `-T AUTO` and `-bnni`.
4. Genotypes are inferred only from reference-anchored phylogenetic evidence. Unsupported or discordant calls require manual review.

Similarity comparison may support AB1 orientation and assembly QC but cannot determine the final genotype. See [Methods](docs/METHODS.md).

## Recommended local installation: Docker

Docker is the supported path for computers without Python, Node.js, MAFFT or IQ-TREE. The first build requires internet access; analysis then runs on the user's own computer and does not require a public IP.

### Windows

1. Install and start [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/).
2. Download and extract the tagged GitHub Release ZIP.
3. Open PowerShell in the extracted folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_environment.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_docker.ps1
```

### macOS or Linux

Install Docker Engine/Desktop with Compose, then run:

```sh
sh ./scripts/check_environment.sh
sh ./scripts/start_docker.sh
```

Open `http://localhost:3200/`. The API listens only on `127.0.0.1:8200`. Stop the containers with the matching `stop_docker` script. Results remain in the Docker volume after stopping.

Full instructions and the advanced native path are in [Local installation](docs/LOCAL_INSTALLATION.md). Troubleshooting is in [Troubleshooting](docs/TROUBLESHOOTING.md).

## Input and results

- Single AB1: one or two `.ab1` files.
- Batch AB1: multiple `.ab1` files, grouped by filename and analysed in one joint tree.
- FASTA: one `.fa/.fas/.fasta/.fna` file containing 1–100 sequences.
- Auditable outputs: JSON/HTML report, locked-reference alignment, IQ-TREE treefile/report/log/UFBoot/consensus tree, display Newick and SVG.

See [Input and output](docs/INPUT_OUTPUT.md). This research tool does not replace manual inspection of the phylogeny.

## Language

The website provides an `EN / 中文` switch and remembers the user's choice in the browser. English is the journal-facing default for non-Chinese browser locales. The documentation is available in [English](docs/) and [Chinese](docs/zh-CN/).

## Release model

The repository and tagged GitHub Releases distribute source code, fixed references, checksums, containers, documentation and tests. Users download a release and run it locally; GitHub Pages and a public analysis server are not required. A Zenodo archive may be connected to tagged releases to obtain a software DOI.

Before publication, update `CITATION.cff` with the final authors, repository URL and article/preprint DOI. See [Citation and manuscript wording](docs/CITATION.md) and [Validation](docs/VALIDATION.md).

## Tests

```powershell
..\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp
cd frontend
node_modules\.bin\vinext.cmd build
node --test tests\*.test.mjs
```

## Licence

Project code is released under the MIT License. MAFFT, IQ-TREE and reference sequence data retain their own licences or database terms; see [Third-party notices](THIRD_PARTY_NOTICES.md).
