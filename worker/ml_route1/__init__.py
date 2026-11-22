"""
Phase 5 step M5-7 (docs/PHASE5_ML_DESIGN.md revision 5, §10, §11): Route 1 combinations, designation lists, the
validated domain with its refusals, and the `r1_predictions` record.

  combine     combine_mean_v1 (the mean of the components' calibrated probabilities with its own vector scaling on
              the log of the mean; the mean of point estimates; its own conformal quantile), fitted only on
              out-of-fold component predictions that resolved by the combiner's own cutoff (MLK-10)
  routing     designation lists: the one in force at a prediction's cutoff (MLK-19); a row goes to the first
              combination whose components are all available; metrics per position on the rows routed to it (MLK-21)
  domain      the validated domain recorded with a model, and §11.2's refusal table (MLK-15)
  record      the §11.1 record of one Route 1 prediction and its row hash; the store writes it to r1_predictions

No network; nothing here reads a mutable table.
"""
VERSION = "ml_route1.1"
COMBINE_MEAN = "combine_mean_v1"
