#!/usr/bin/env python3
"""
CSE endpoint discovery script — Phase 0 hard gate.

Purpose: find out, empirically, what security_trading_statistics and
chartData actually return. This script assumes NOTHING about response
shape or even request format — it tries several plausible request
shapes for each endpoint and reports exactly what came back for each,
success or failure.

Do not build the historical-data adapter from any assumption in code
comments elsewhere in this project until you've read this script's
output. This script IS the source of truth for that decision.

Usage (local):
    pip install requests
    python test_cse_endpoints.py

Usage (GitHub Actions):
    triggered via workflow_dispatch — see test-cse-endpoints.yml
    results are uploaded as a workflow artifact (cse_endpoint_test_results.json)

Politeness: this script makes a small, fixed number of requests
(roughly 4 symbols x 4 years x 3 request-shape variants x 2 endpoints
= ~96 requests) with a 1-second delay between requests, well under
any reasonable rate ceiling, and identifies itself via User-Agent.
It is read-only — it never places orders or touches any authenticated
CSE surface.
"""

import json
import sys
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.cse.lk/api"
USER_AGENT = "cse-research-tool-phase0-recon/1.0 (personal research project, read-only)"
REQUEST_DELAY_SECONDS = 1.0
TIMEOUT_SECONDS = 15

# A deliberately varied sample: bank, conglomerate, manufacturing/construction,
# financial-services holding company — matches the spec's instruction not to
# assume one company's behavior generalizes.
TEST_SYMBOLS = [
    "COMB.N0000",  # Commercial Bank of Ceylon — bank
    "JKH.N0000",   # John Keells Holdings — diversified conglomerate
    "AEL.N0000",   # Access Engineering — construction/manufacturing
    "LOLC.N0000",  # LOLC Holdings — financial services holding company
]

CURRENT_YEAR = datetime.now(timezone.utc).year
TEST_YEARS = [CURRENT_YEAR, CURRENT_YEAR - 1, CURRENT_YEAR - 2, CURRENT_YEAR - 4]

RATE_LIMIT_HEADER_KEYS = [
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
    "retry-after", "x-rate-limit", "ratelimit-limit", "ratelimit-remaining",
]

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

results = {
    "run_started_at": datetime.now(timezone.utc).isoformat(),
    "base_url": BASE_URL,
    "symbols_tested": TEST_SYMBOLS,
    "years_tested": TEST_YEARS,
    "security_trading_statistics": [],
    "chart_data": [],
}


def polite_sleep():
    time.sleep(REQUEST_DELAY_SECONDS)


def extract_rate_limit_headers(headers):
    found = {}
    for k, v in headers.items():
        if k.lower() in RATE_LIMIT_HEADER_KEYS:
            found[k] = v
    return found


def describe_json_shape(data, max_sample_records=2):
    """
    Describes an arbitrary JSON structure without assuming what it is.
    Returns a dict summary safe to print/log.
    """
    summary = {"top_level_type": type(data).__name__}
    if isinstance(data, list):
        summary["length"] = len(data)
        summary["sample_records"] = data[:max_sample_records]
        if data and isinstance(data[0], dict):
            summary["keys_in_first_record"] = list(data[0].keys())
    elif isinstance(data, dict):
        summary["top_level_keys"] = list(data.keys())
        # If any top-level value is a list, describe it too — many CSE
        # endpoints wrap the real payload in a named key (e.g.
        # {"reqSomething": [...]})
        nested_lists = {}
        for k, v in data.items():
            if isinstance(v, list):
                nested_lists[k] = {
                    "length": len(v),
                    "sample_records": v[:max_sample_records],
                    "keys_in_first_record": (
                        list(v[0].keys()) if v and isinstance(v[0], dict) else None
                    ),
                }
        if nested_lists:
            summary["nested_list_fields"] = nested_lists
    else:
        summary["value"] = data
    return summary


def try_request(label, method, url, variant_name, **kwargs):
    """
    Makes one request, catches everything, returns a structured result.
    Never raises — the whole point of this script is to survive and
    report every outcome, not just the happy path.
    """
    record = {
        "label": label,
        "variant": variant_name,
        "method": method,
        "url": url,
        "request_kwargs_summary": {
            k: v for k, v in kwargs.items() if k in ("params", "data", "json")
        },
    }
    start = time.monotonic()
    try:
        resp = session.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        record["status_code"] = resp.status_code
        record["latency_ms"] = elapsed_ms
        record["content_type"] = resp.headers.get("Content-Type")
        record["rate_limit_headers"] = extract_rate_limit_headers(resp.headers)
        record["response_size_bytes"] = len(resp.content)

        raw_text = resp.text
        try:
            data = resp.json()
            record["parsed_as_json"] = True
            record["shape"] = describe_json_shape(data)
        except (json.JSONDecodeError, ValueError):
            record["parsed_as_json"] = False
            record["raw_text_preview"] = raw_text[:500]
    except requests.exceptions.RequestException as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["status_code"] = None
    return record


def test_security_trading_statistics():
    print("\n=== Testing /api/security_trading_statistics ===")
    url = f"{BASE_URL}/security_trading_statistics"
    for symbol in TEST_SYMBOLS:
        for year in TEST_YEARS:
            print(f"  symbol={symbol} year={year}")

            # Variant 1: query string params, empty POST body
            r1 = try_request(
                "security_trading_statistics", "POST", url, "query_params",
                params={"symbol": symbol, "year": year},
            )
            results["security_trading_statistics"].append(r1)
            print(f"    [query_params]  status={r1.get('status_code')}  "
                  f"json={r1.get('parsed_as_json')}")
            polite_sleep()

            # Variant 2: form-encoded body
            r2 = try_request(
                "security_trading_statistics", "POST", url, "form_body",
                data={"symbol": symbol, "year": year},
            )
            results["security_trading_statistics"].append(r2)
            print(f"    [form_body]     status={r2.get('status_code')}  "
                  f"json={r2.get('parsed_as_json')}")
            polite_sleep()

            # Variant 3: JSON body
            r3 = try_request(
                "security_trading_statistics", "POST", url, "json_body",
                json={"symbol": symbol, "year": year},
            )
            results["security_trading_statistics"].append(r3)
            print(f"    [json_body]     status={r3.get('status_code')}  "
                  f"json={r3.get('parsed_as_json')}")
            polite_sleep()

        # Also test with NO year param at all, per the third-party docs'
        # claim that omitting year returns 400 — worth confirming directly.
        r4 = try_request(
            "security_trading_statistics", "POST", url, "no_year_param",
            params={"symbol": symbol},
        )
        results["security_trading_statistics"].append(r4)
        print(f"  [no_year_param] symbol={symbol}  status={r4.get('status_code')}")
        polite_sleep()


def test_chart_data():
    print("\n=== Testing /api/chartData ===")
    url = f"{BASE_URL}/chartData"
    # Parameter name for chartData isn't confirmed anywhere — try the
    # most likely candidates rather than assuming "symbol" is even right.
    param_name_variants = ["symbol", "securityId", "id"]

    for symbol in TEST_SYMBOLS:
        for param_name in param_name_variants:
            r = try_request(
                "chartData", "POST", url, f"param_name={param_name}",
                params={param_name: symbol},
            )
            results["chart_data"].append(r)
            print(f"  symbol={symbol} param={param_name}  "
                  f"status={r.get('status_code')}  json={r.get('parsed_as_json')}")
            polite_sleep()

        # Try common date-range parameter names in case chartData needs
        # an explicit range rather than defaulting to "everything"
        r_range = try_request(
            "chartData", "POST", url, "with_period_param",
            params={"symbol": symbol, "period": "5Y"},
        )
        results["chart_data"].append(r_range)
        print(f"  symbol={symbol} with period=5Y  status={r_range.get('status_code')}")
        polite_sleep()


def print_final_summary():
    print("\n\n========== SUMMARY ==========")
    for endpoint_key, label in [
        ("security_trading_statistics", "security_trading_statistics"),
        ("chart_data", "chartData"),
    ]:
        records = results[endpoint_key]
        successes = [r for r in records if r.get("status_code") == 200 and r.get("parsed_as_json")]
        print(f"\n{label}: {len(records)} attempts, {len(successes)} succeeded (200 + valid JSON)")
        if successes:
            best = successes[0]
            print(f"  First successful variant: {best['variant']}")
            print(f"  Shape: {json.dumps(best['shape'], indent=2, default=str)[:1500]}")
        else:
            statuses = sorted(set(str(r.get("status_code")) for r in records))
            print(f"  No successful attempts. Status codes seen: {statuses}")
            errors = [r["error"] for r in records if "error" in r]
            if errors:
                print(f"  Connection-level errors seen: {sorted(set(errors))[:5]}")


def main():
    print(f"CSE endpoint discovery run — {results['run_started_at']}")
    print(f"Testing symbols: {TEST_SYMBOLS}")
    print(f"Testing years: {TEST_YEARS}")

    try:
        test_security_trading_statistics()
    except Exception as exc:  # noqa: BLE001 — this script must never crash silently
        print(f"UNEXPECTED ERROR in security_trading_statistics tests: {exc}", file=sys.stderr)

    try:
        test_chart_data()
    except Exception as exc:  # noqa: BLE001
        print(f"UNEXPECTED ERROR in chartData tests: {exc}", file=sys.stderr)

    results["run_finished_at"] = datetime.now(timezone.utc).isoformat()

    output_path = "cse_endpoint_test_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull raw results written to {output_path}")

    print_final_summary()


if __name__ == "__main__":
    main()
