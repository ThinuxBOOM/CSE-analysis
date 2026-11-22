"""
Phase 5 step M5-4 (docs/PHASE5_ML_DESIGN.md revision 5, §7): adapters and baselines.

  data        the training and prediction matrices from a built dataset (M5-2): ML adapters read MF and F7 features,
              never an engine score; labels only of the trained states (scoring.1)
  artifact    canonical text artifacts: a model is JSON text defined here (never a pickle); its SHA-256 is its
              identity; the environment tuple (library versions, CPU features, one thread, the seed)
  baselines   the `ref` adapter: base_no_change, base_rate_252, base_momentum_sign, base_prior, base_vol_trailing,
              base_interval (§7.3)
  linear      regularised multinomial logistic regression, ridge on return and volatility; contributions are
              coefficient x standardised value
  gbt         gradient-boosted trees (LightGBM); contributions are the library's exact tree contributions

Every adapter has fit(train, hyperparameters, seed) -> Artifact, predict(artifact, matrix) -> Predictions and
describe(artifact). Training rows are admitted through ml_protocol's FitScope (MLK-3). No network; the capture,
backfill and financial-truth packages import none of these libraries (MLK-14).
"""
VERSION = "ml_models.1"
CLASSES = ("rise", "flat", "fall")          # the tie order of the evaluation library (M5-1)
OUTPUTS = ("direction", "return", "volatility")
