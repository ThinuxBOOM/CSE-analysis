# News source governance record: GDELT DOC 2.0 API (`gdelt_doc`)

**Status: SIGNED by the owner, 2026-11-23 (C-0125).** Drafted 2026-11-23 at the owner's request (C-0122). The
owner accepted the checks below. Signing changed this file, and so its SHA-256.
Registration (`cse-news register-source --record docs/governance/NS-GDELT-DOC.md --ref NS-GDELT-DOC.md`) pins
the signed version. Any later edit stops the source until it is registered again.

## What the source is

The GDELT Project's DOC 2.0 API. It is an index of world news. One request returns a list of articles matching a
query: each article's URL, title, the time GDELT first saw it, its domain, language and source country. The adapter
(`worker/news_ingest/gdelt.py`) asks only for that list, with the query
`sourcecountry:srilanka sourcelang:english`. It **never requests a publisher's page**: Sri Lankan publishers'
sites are not contacted.

## Terms (P6-R1): checked and accepted by the owner

**Read at source on 2026-11-23** (C-0124), through a network environment the owner approved for it:
- **Terms** (GDELT's About page, "Terms of Use"): *"all datasets released by the GDELT Project are available for
  unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee."* And:
  *"any use or redistribution of the data must include a citation to the GDELT Project and a link to this website
  (https://www.gdeltproject.org/)."*
- **Robots:** the API host serves no robots.txt (HTTP 404), which the transport treats as no restriction. The
  website's robots.txt disallows only `/data/`, which the adapter never requests.
- **The DOC 2.0 API** (GDELT's announcement and documentation post), as the adapter assumes:
  - the endpoint is `/api/v2/doc/doc`;
  - `mode=artlist`, `format=json`, `sort=datedesc`;
  - `maxrecords` defaults to 75, with a maximum of 250;
  - the `sourcecountry:` and `sourcelang:` operators exist (full country name without spaces, or the FIPS code);
  - `startdatetime` is `YYYYMMDDHHMMSS` and must be within the last 3 months; the API searches a rolling 3-month
    window.
- **Not stated anywhere read:** a request rate limit, and the JSON field names (`seendate` and the others come from
  client libraries). The discipline below is deliberately slow.
- Titles and URLs in the index are the publishers'. GDELT's terms cover GDELT's data. A publisher's headline may
  still be that publisher's copyright. This record stores titles and URLs only, never article text, uses them
  internally, and discloses nothing onward.

**Owner checks (accepted 2026-11-23):**
1. Accept the terms above for this use.
2. Accept the residual risk on the publishers' headlines (internal use, titles and URLs only).
3. The citation GDELT requires goes on the dashboard's sources page (Phase 12) and in any exported report, with
   the link to https://www.gdeltproject.org/ (the recommendation, accepted).
4. Know that the JSON field names are unverified. The first live listing is inspected by hand before anything is
   ingested.

A record that still carries the draft marker the parser looks for (`governance.UNSIGNED`) is refused; this one
carries none.

## The choices in this record (C-0121, unless the owner changes them)

- **Storage (P6-R2):** `metadata_only`: title, URL, language, times. No text.
- **Onward disclosure:** `none`. Nothing from this source goes to an AI provider.
- **Identification (P6-R8):** the agreed User-Agent; no contact address.
- **Robots:** obeyed.
- **Request discipline:** at least 6 seconds between requests; at most 200 requests a Colombo day; 2 retries with
  back-off; the circuit breaker after 4 consecutive failures; bodies up to 5 MB.
- **Hosts:** the API's host only. No fetch-on-behalf service.
- **G-1 (C-0121):** an article whose URL names CSE anywhere is dropped by the adapter and never stored. Market
  reports quoting CSE prices are kept only as titles, never parsed as market data.
- **Withdrawals:** GDELT gives no evidence of a withdrawal, so none is inferred and nothing is re-fetched.
- **Stop and cessation:** on an objection from GDELT or a publisher, stop at once (a recorded stop), tell the owner,
  and follow the purge procedure (runbook, "Purge (P6-R3)").
- **Retention:** kept while the source is registered; purged on a deletion request.

```ns-source
{
 "source_id": "gdelt_doc",
 "allowed_hosts": ["api.gdeltproject.org"],
 "fetch_on_behalf_hosts": [],
 "storage_level": "metadata_only",
 "onward_disclosure": "none",
 "availability_policy": {"time_zone_offset_minutes": 0, "date_only_counts_from": "end_of_day",
                         "availability_lag_minutes": 0},
 "identification": {"user_agent": "research-news-reader/1 (non-commercial; obeys robots.txt)", "contact": null},
 "request_discipline": {"min_interval_seconds": 6.0, "daily_budget": 200, "max_retries": 2,
                        "backoff_base_seconds": 10.0, "backoff_max_seconds": 300.0,
                        "retry_after_max_seconds": 600.0, "max_consecutive_failures": 4,
                        "max_body_bytes": 5000000},
 "robots": "obey"
}
```

## Owner sign-off

- Terms read and accepted (checks 1 to 4 above): **accepted**
- Signed by / date: **the owner, 2026-11-23**, by instruction in the cloud session ("accepted, do that for me",
  after the terms were quoted at source, C-0124); entered by the cloud session (C-0125)
