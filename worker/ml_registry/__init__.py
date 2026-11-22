"""
Phase 5 step M5-6 (docs/PHASE5_ML_DESIGN.md revision 5, §5.4 - §5.6, §9): the ML registry.

  sufficiency   §5.5's minimums: is a protocol `sufficient` for a horizon, or `exploratory`
  gates         gates 2 to 8 (candidate -> validated) and 9 to 12 (promotion), evaluated in application code into a
                report whose rows the database requires (migration 0021) before a status changes
  store         the registry's reads and writes against migration 0021 (worker login; the owner path for owner acts)

The database itself enforces gate 1, the lifecycle, the protocol-before-run clock, disjoint test periods and the
owner path (0021). No network.
"""
VERSION = "ml_registry.1"
