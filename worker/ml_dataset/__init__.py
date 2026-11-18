"""
Phase 5 step M5-2 (docs/PHASE5_ML_DESIGN.md revision 5, §4, §12.2): the dataset builder, its manifest and version, and
the strata. Pure: the inputs are the stored rows of Phases 3 and 4, already read; no database, clock, network or file.

  dataset   Manifest, the inputs, build() -> Dataset (feature rows, labels apart, the build report, the version)
  strata    liquidity class, regime tags (a pinned provisional policy), model-age strata
"""
VERSION = "ml_dataset.5"                 # bound into every dataset version
