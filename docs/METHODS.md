# Methods

## Scope

The V1.0 public workflow performs TSA56 phylogenetic typing for *Orientia tsutsugamushi*. The server fixes all phylogenetic parameters; users cannot select a rapid, similarity-only, placement, or alternative genotyping method.

## AB1 preprocessing

Single- or paired-read AB1 chromatograms are checked using the locked defaults Q35 and a minimum overlap of 80 bp. A bundled 355-sequence TSA56 reference set is used only to orient reads and support assembly quality control. This comparison cannot populate the final genotype fields. Paired reads are oriented independently and merged when they meet the quality and overlap requirements.

## Fixed-reference alignment

One or more accepted sample sequences are added to the bundled 60-reference alignment without changing the reference alignment length:

```text
mafft --add samples.fasta --keeplength 60ref_151-850.fas
```

The locked reference contains 60 records and 898 aligned columns. Its SHA-256 checksum is recorded in `data/manifest.json` and verified at application import.

## Phylogenetic inference

The aligned reference-plus-sample matrix is analysed with the article-locked inferential settings and device-adaptive CPU allocation:

```text
iqtree -s alignment.fasta -m TVM+F+R5 -bb 1000 -T AUTO -bnni
```

`-T AUTO` is an IQ-TREE computational resource setting: IQ-TREE selects cores for the current data and computer. It does not change the locked substitution model, bootstrap count, BNNI setting, reference panel or genotype rule. Advanced users may set `PHYLO_PUBLIC_IQTREE_THREADS` to a positive integer for a local resource limit; the executed value is recorded in the report and API configuration.

The locally validated tool versions are MAFFT 7.525 and IQ-TREE 3.0.1. The tool retains the alignment, treefile, IQ-TREE report/log, bootstrap trees, consensus tree, midpoint-rooted display tree and SVG where produced.

## Genotype assignment

Genotypes are inferred from the sample's placement relative to curated genotype reference anchors in the IQ-TREE topology. Automated acceptance requires a uniquely supported reference-anchored topology, the configured UFBoot criterion, and concordance with reference-derived patristic-distance and separation-margin envelopes. Ambiguous, discordant, incompletely anchored or weakly supported samples are reported as requiring manual review.

Nearest-reference distance is supporting tree evidence, not an independent similarity classifier. The distance thresholds are reference-derived and have not yet been established by leave-one-reference-out external validation; this limitation is reported in the machine-readable result.

## Rooting and interpretation

Rooting and tip ordering are used for consistent display and do not create a genotype call. Scientific interpretation should consider the unrooted IQ-TREE topology, branch support, reference composition, sequence quality and the full retained artifacts.
