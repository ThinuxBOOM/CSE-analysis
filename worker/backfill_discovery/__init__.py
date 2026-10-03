"""
Phase 2 HB-3: discovery and issuer evidence (design sections 6-8, 14-15 and 27; the HB-3 design gate with owner
decisions D-HB3-1, D-HB3-2 / G2 and G10). A library only: no command, entry point or timer (HB-6 owns those).

    plan            the 66 Colombo feed months of the armed window W; listing items only from the verified security
                    master (HB-U2, HB-U3)
    security_master the HB-P1 runtime gate: the latest DERIVED P2 market capture with an archived, verified
                    allSecurityCode, within the freshness bound, and the securities it put into `companies`
    f1_cycle        F1's own run helpers around exactly one governed HTTP attempt (HB-U4, D-HB3-1)
    accounting      claims (C) and actual HTTP attempts (A) since an item's last re-queue
    discovery       the discovery slice: the G2 guard around HB-2's slice, start-of-slice reconciliation, G10
                    terminalisation and one governed request per claim
    identity        the issuer-evidence acquisition of hb.acquire.1: the IE-2 import (path b), the IE-4 batch, the
                    hold rule (HB-I-HOLD), owner hold resolutions and the link passes
    preflight       HB-3's own checks (frozen pins, static boundaries, compatibility, database reads), in addition to
                    HB-1's and HB-2's preflights

HB-P1 is a DEPLOYMENT / RUNTIME prerequisite, not an implementation prerequisite. This package may be implemented and
tested offline before any production security-master capture exists, but every entry point that could make a live
discovery request (a discovery slice, the listing plan, the issuer import and link passes) refuses unless the HB-P1
evidence is actually present in the database it runs against (security_master.require). Nothing here creates that
evidence: `companies` is written only by P2's ensure_companies inside a derived P2 market capture.

Deployment sequence (owner-run, after the software freeze): server setup -> HB-X2(b) -> CSE_CAPTURE_CONTACT_EMAIL ->
the first governed P2 capture containing allSecurityCode -> verification of the derived security master -> HB-P1
satisfied -> live HB-3 discovery.

Every CSE request goes through HB-2's frozen governed transport (worker/backfill_transport), with the owner's arming
of attempts_per_json_request = 1 (D-HB3-1): one claim, one F1 run, one HTTP attempt.
"""
TOOL_VERSION = "hb.discovery.1"
RULE_VERSION = "hb.discovery.1"
ACQUIRE_RULE_VERSION = "hb.acquire.1"         # the acquisition order and hold rule (design section 7.4 and 7.7)

STAGE = "HB-S2"                               # the only Phase 2 JSON stage (HB-2 STAGE_KINDS)
IDENTITY_STAGE = "HB-S1"                      # the IE-2 import (no CSE request)

# D-HB3-1 (owner decision, Option B): one HTTP attempt per governed call; the settled HB-Q8 item maximum of three
# counts claims, each claim being one separately governed request with its own F1 run.
ATTEMPTS_PER_JSON_REQUEST = 1
ITEM_MAX_ATTEMPTS = 3

# HB-Q8: the latest derived run's allSecurityCode must be at most this old when discovery starts. An arming may only
# tighten it (stop_conditions entry {"security_master_max_age_days": n}), never loosen it.
SECURITY_MASTER_MAX_AGE_DAYS = 7

# HB-U2: a feed month above twice the largest F0 monthly count (346) is flagged, never split.
FEED_WINDOW_LARGE = 692

# Link-pass sequence numbers (natural keys link_pass:<n>).
LINK_PASS_IDENTITY = 1                        # IE-2 import + resolve_securities (HB-S1)
LINK_PASS_CLOSURE = 2                         # after discovery closure: IE-4 batch + resolve + link (HB-S2)
