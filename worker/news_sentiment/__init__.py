"""
Phase 6, news sentiment (docs/PHASE6_NEWS_SENTIMENT_DESIGN.md §5 to §8): pure, deterministic, versioned processors
(entity, relevance, events, sentiment), deduplication, decay and the snapshot. Nothing here opens a connection,
reads a Route 1 model, forecast or feature, or imports MF's feature or target modules, F7 or the ML packages (NL-10):
the only inputs from other layers are MF's session sequence, calendar and security identity and F5's issuer identity.
"""
