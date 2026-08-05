# Validation record

## Local acceptance baseline

- Date: 2026-08-05
- Python packages: `requirements.lock`
- Frontend runtime: Node.js 24.14.0 for local acceptance; package policy requires Node.js 22.13 or later.
- MAFFT: 7.525
- IQ-TREE: 3.0.1
- IQ-TREE CPU allocation: `-T AUTO` by default; positive-integer environment override supported
- Fixed tree reference: 60 records, 898 aligned columns
- Reference integrity: `data/manifest.json`

## Automated checks

- Backend/API, reference contract, alignment command construction, IQ-TREE command construction, AB1/FASTA validation, cancellation, tree assignment and public-route/security tests.
- Frontend production build.
- Filename grouping, dropped-file recursion and FASTA helper tests.
- Live health/config/OpenAPI/frontend smoke test on ports 8200/3200.
- Bilingual interface source contract, English/Chinese browser state and responsive layout.
- Environment doctor, PowerShell parser compatibility and Docker Compose local-bind/health configuration.

## Scientific validation boundary

Passing software tests shows that the implementation follows the specified workflow and preserves the evidence chain. It does not by itself establish diagnostic performance, clinical validity, generalisability to new populations, or an externally validated classification cutoff. Manuscript claims should be limited to the study's own validation design and results.

Release-level reproducibility records comprise the validation set, expected genotype calls, software/container digests and a version-specific test report. Representative trees and all ambiguous or discordant results require manual review.

Thread allocation is a runtime resource choice rather than a genotype parameter. A release validation should still record the machine CPU count, effective IQ-TREE thread specification and wall-clock time.
