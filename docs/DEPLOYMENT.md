# GitHub release and local distribution

## Supported publication model

1. A public GitHub repository stores source, fixed references, checksums, bilingual documentation and tests.
2. A tagged GitHub Release provides a versioned source ZIP and release notes.
3. Users download the release and run it locally with Docker Compose.
4. The web interface and API bind to `127.0.0.1`; uploaded sequences remain on the user's computer.
5. Zenodo may archive tagged releases and issue a software DOI.

A public IP, GitHub Pages analysis backend or hosted compute service is not required. GitHub Pages cannot execute Python, MAFFT or IQ-TREE and is outside this release architecture.

## Release checklist

- Confirm the fixed reference checksums in `data/manifest.json`.
- Run backend tests, frontend build/tests, Compose configuration validation and a real reference-derived end-to-end analysis.
- Confirm OpenAPI contains no V2 endpoint and config advertises only `v1_strict_article`.
- Confirm final report fields state `typing_source=phylogenetic_tree` and similarity genotype fields are null.
- Confirm `PHYLO_PUBLIC_IQTREE_THREADS=AUTO` and that model/bootstrap/BNNI remain fixed.
- Test the Windows and macOS/Linux environment/start/stop scripts from a clean extracted Release ZIP.
- Add the official repository URL, software DOI, article/preprint DOI, author list and affiliations to `CITATION.cff`.
- Publish a tagged release and archive its test report and container image digest.

Do not publish local results, uploaded samples, job logs, caches, WSL paths or private filenames.
