"""
§5.5: which result of an article version counts at T.

**Stored results are admitted by their own clock** (revision 2, M4): a stored `ns_article_results` row is used as it
is only if its own `recorded_at` is admitted (<= T in KNOWN, <= H in AVAILABLE and CURRENT) **and** every alias it
used is admitted at T (§5.2), under the configuration in force (named or designated, NL-6).

**Recomputation** (revision 3, N3): a processor is pure, so under `full_text` the result may instead be recomputed
at query time from inputs admitted at T (the article version, the aliases admitted at T, the configuration); it is
not stored and needs no `recorded_at`. Under `metadata_only` there is no text to recompute from: an article with no
admissible stored result has **no result** for that issuer. It is still counted in `news_count_*`, contributes no
score, and is reported; above the policy's share the row is `unlabelled` (§8).
"""
from dataclasses import dataclass
from datetime import datetime

from worker.market_features.modes import KNOWN

from . import aliases as al
from . import process as pr


@dataclass(frozen=True)
class StoredResult:
    source_id: str
    reference: str
    version_no: int
    configuration_id: str
    result: object                      # process.ArticleResult
    recorded_at: datetime


def _result_admitted(query, recorded_at):
    if query.mode == KNOWN:
        return recorded_at <= query.cutoff
    return recorded_at <= query.horizon             # AVAILABLE and CURRENT: known by H


def admitted_result(version, stored, aliases, ends, query, cfg, lexicon=None):
    """(ArticleResult or None, how): how is `stored`, `recomputed` or `none`."""
    ok_ids = {a.alias_id for a in al.admitted(aliases, ends, query)}
    cid = cfg.configuration_id()
    usable = [r for r in stored if (r.source_id, r.reference, r.version_no) ==
              (version.source_id, version.reference, version.version_no) and r.configuration_id == cid
              and _result_admitted(query, r.recorded_at) and set(r.result.alias_ids) <= ok_ids]
    if usable:
        return max(usable, key=lambda r: r.recorded_at).result, "stored"
    if version.extracted_text is not None:                                  # full_text: recompute at T
        return pr.process(version.title, version.extracted_text, version.language,
                          al.admitted(aliases, ends, query), cfg, lexicon, full_text=True), "recomputed"
    return None, "none"
