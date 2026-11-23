"""
Phase 6 NS-7: the news store over migration 0022 (pending): the owner-path registrations, the database ledger the
governed transport writes through, the worker's article and result writes, the loader of what is admitted at T, the
snapshot writer and `verify`. Nothing here contacts any host: ingestion runs only through `news_ingest`'s transport,
and no source exists until the owner registers one (P6-R1).
"""
VERSION = "ns_store.1"
