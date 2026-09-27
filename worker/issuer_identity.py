"""
Stage F5: issuer identity (pure logic; persistence in issuer_store.py, migration 0007).

An issuer (reporting entity) is not a security: COMB.N0000 and COMB.X0000 are two
`companies` rows, one issuer, one set of filings. F5.0 evidence (live CSE
metadata): companyInfoSummery reqSymbolBetaInfo.securityId = reqLogo.secId =
/api/financials reqFinancial[].secId is shared by an issuer's share classes and
equals the numeric prefix of its document paths; reqSymbolInfo.id and
allSecurityCode.id are per security.

Rules (rule_version ISSUER_RULE_VERSION):
- Observations: every identifier field CSE returned is recorded as-is, one row
  per field that carries an issuer secId (so two disagreeing fields stay visible).
- Security -> issuer: 'evidenced' when every secId ever observed for the security
  is one value; 'conflict' when more than one (issuer cleared - never guessed);
  no row when none was observed.
- Filing -> issuer: the document path prefix and the listing symbols' evidenced
  secIds must name exactly one secId whose issuer exists -> 'evidenced'
  (basis: path, listing or both). Disagreement, or a listing security in conflict
  -> 'conflict'. No usable evidence, or a secId with no issuer yet -> 'unresolved'.
  An issuer is never created from a path prefix alone.
- secId is the primary CURRENT evidence of identity, not proof of permanence:
  issuers are never merged and predecessor/successor lineage is not modelled (F8).
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from .financial_candidates import path_sec_id

ISSUER_RULE_VERSION = "f5.issuer.1"


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _int(v):
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _obs(endpoint, fld, query_symbol, symbol, security_id, sec_id, isin, name, active, payload, observed_at, source_ref):
    return {"source_endpoint": endpoint, "source_field": fld, "query_symbol": query_symbol, "symbol": symbol,
            "cse_security_id": security_id, "cse_sec_id": sec_id, "isin": isin, "name": name, "active": active,
            "payload_sha256": _sha(payload), "observed_at": observed_at, "source_ref": source_ref}


def observations_from_company_info(body, query_symbol, observed_at, source_ref=None):
    """companyInfoSummery body -> one observation per secId-bearing field (securityId, reqLogo.secId);
    a single secId-less observation when neither field is present."""
    body = body if isinstance(body, dict) else {}
    info = body.get("reqSymbolInfo") or {}
    symbol, security_id = info.get("symbol"), _int(info.get("id"))
    isin, name = info.get("isin"), info.get("name")
    ident = {"symbol": symbol, "id": security_id, "isin": isin, "name": name}
    out = []
    for fld, val in (("reqSymbolBetaInfo.securityId", (body.get("reqSymbolBetaInfo") or {}).get("securityId")),
                     ("reqLogo.secId", (body.get("reqLogo") or {}).get("secId"))):
        sec = _int(val)
        if sec is not None:
            out.append(_obs("companyInfoSummery", fld, query_symbol, symbol, security_id, sec, isin, name, None,
                            {**ident, fld: sec}, observed_at, source_ref))
    if not out and (symbol or security_id is not None):
        out.append(_obs("companyInfoSummery", "reqSymbolInfo", query_symbol, symbol, security_id, None, isin, name,
                        None, ident, observed_at, source_ref))
    return out


def observations_from_financials(body, query_symbol, observed_at, source_ref=None):
    """/api/financials body -> one observation per distinct reqFinancial[].secId (the query symbol is the security)."""
    body = body if isinstance(body, dict) else {}
    secs = sorted({s for s in (_int(x.get("secId")) for x in body.get("reqFinancial") or [] if isinstance(x, dict))
                   if s is not None})
    return [_obs("financials", "reqFinancial.secId", query_symbol, query_symbol, None, s, None, None, None,
                 {"symbol": query_symbol, "secId": s}, observed_at, source_ref) for s in secs]


def observations_from_all_security_codes(items, observed_at, source_ref=None):
    """allSecurityCode list -> one observation per security (per-security id, name, active; no issuer secId)."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        ident = {k: it.get(k) for k in ("id", "symbol", "name", "active")}
        active = it.get("active")
        out.append(_obs("allSecurityCode", "allSecurityCode.item", None, it["symbol"], _int(it.get("id")), None, None,
                        it.get("name"), bool(active) if isinstance(active, (bool, int)) else None, ident, observed_at,
                        source_ref))
    return out


@dataclass
class SecurityDecision:
    symbol: str
    link_status: str                  # evidenced | conflict
    sec_id: Optional[int]             # the single evidenced secId
    observed_sec_ids: list
    evidence_observation_ids: list
    first_evidence_at: Optional[str]
    last_evidence_at: Optional[str]
    rule_version: str = ISSUER_RULE_VERSION

    @property
    def evidence_sha256(self):
        return _sha({"symbol": self.symbol, "status": self.link_status, "sec_ids": self.observed_sec_ids,
                     "rule": self.rule_version})


def decide_security(symbol, observations):
    """observations: dicts with id, cse_sec_id, observed_at (any source). None when no secId was observed."""
    ev = [o for o in observations if o.get("cse_sec_id") is not None]
    if not ev:
        return None
    secs = sorted({o["cse_sec_id"] for o in ev})
    times = sorted(str(o["observed_at"]) for o in ev if o.get("observed_at") is not None)
    return SecurityDecision(symbol, "evidenced" if len(secs) == 1 else "conflict", secs[0] if len(secs) == 1 else None,
                            secs, sorted(o["id"] for o in ev if o.get("id") is not None),
                            times[0] if times else None, times[-1] if times else None)


@dataclass
class FilingDecision:
    cse_filing_id: int
    status: str                       # evidenced | conflict | unresolved
    basis: str                        # document_path_prefix | listing_symbol_sec_id | both | none
    sec_id: Optional[int]
    issuer_id: Optional[str]
    path_sec_id: Optional[int]
    listing_symbols: list
    listing_sec_ids: list
    listing_conflicts: list
    reasons: list = field(default_factory=list)
    rule_version: str = ISSUER_RULE_VERSION

    @property
    def evidence_sha256(self):
        return _sha({"filing": self.cse_filing_id, "status": self.status, "basis": self.basis, "issuer": self.issuer_id,
                     "path": self.path_sec_id, "symbols": self.listing_symbols, "secs": self.listing_sec_ids,
                     "conflicts": self.listing_conflicts, "reasons": self.reasons, "rule": self.rule_version})


def decide_filing(cse_filing_id, path, listing_symbols, security_links, issuers_by_sec_id):
    """security_links: symbol -> (link_status, sec_id) of the CURRENT security decision (absent = none);
    issuers_by_sec_id: secId -> issuer_id of existing issuers."""
    symbols = sorted(set(listing_symbols or []))
    psec = path_sec_id(path)
    listing, conflicts, reasons = set(), [], []
    for s in symbols:
        got = security_links.get(s)
        if got is None:
            reasons.append(f"listing_symbol_without_issuer_evidence:{s}")
        elif got[0] == "conflict":
            conflicts.append(s)
        else:
            listing.add(got[1])
    secs = set(listing) | ({psec} if psec is not None else set())
    if psec is None:
        reasons.append("no_document_path_prefix")
    basis = ("both" if psec is not None and listing else "document_path_prefix" if psec is not None
             else "listing_symbol_sec_id" if listing else "none")
    dec = FilingDecision(int(cse_filing_id), "unresolved", basis, None, None, psec, symbols, sorted(listing), conflicts, reasons)
    if conflicts:
        dec.status = "conflict"
        dec.reasons.append("listing_security_in_conflict")
    elif len(secs) > 1:
        dec.status = "conflict"
        dec.reasons.append("sec_ids_disagree")
    elif len(secs) == 1:
        sec = next(iter(secs))
        dec.sec_id = sec
        if sec in issuers_by_sec_id:
            dec.status, dec.issuer_id = "evidenced", str(issuers_by_sec_id[sec])
        else:
            dec.reasons.append("no_issuer_for_sec_id")
    return dec
