"""
DE-4 (docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md §10): storage of F7 panels and feature rows and of DE runs and
outputs in the tables of pending migration 0020.

  codec    pure: canonical JSON, configuration ids, and the records with their hashes (panel_hash, row_hash,
           output_hash, run_hash); no database
  writer   inserts one run's records in one transaction (worker login; append-only tables)
  verify   recomputes every stored hash from the stored columns and reports disagreements (read-only)

No network and no CSE request: nothing here imports a network library.
"""
