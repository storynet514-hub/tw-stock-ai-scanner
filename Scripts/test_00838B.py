#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx ETF Official Price Diagnostic V6

Purpose
-------
1. Diagnose the official TPEx ETF historical-data API.
2. Do not modify fetch_prices.py.
3. Do not modify Data/prices.json.
4. Do not modify Data/universe.json.
5. Do not assume tables[].data.
6. Inspect the official historical ETF page.
7. Inspect linked JavaScript for the real API request contract.
8. Test GET and POST candidates against the confirmed API route.
9. Print the complete JSON structure returned by TPEx.
10. Search recursively for 00838B.
11. Stop only after the official ETF data path is identified.

IMPORTANT
---------
This is a diagnostic script only.
It does NOT modify the production price pipeline.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from html import unescape
from urllib.parse import urljoin

import requests


SYMBOL = "00838B"

PAGE_URL = (
    "https://www.tpex.org.tw/zh-tw/product/etf/info/historical/day.html"
)

API_URL = (
    "https://www.tpex.org.tw/www/zh-tw/ETFReport/historical"
)

BASE_URL = "https://www.tpex.org.tw"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,text/javascript,text/plain,"
        "text/html,*/*"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": PAGE_URL,
    "X-Requested-With": "XMLHttpRequest",
}

TIMEOUT = 30

session = requests.Session()
session.headers.update(HEADERS)


def roc_date(d: date) -> str:
    return f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}"


def normalize(value) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
        .upper()
    )


def print_separator(title: str = "") -> None:
    print()
    print("=" * 80)
    if title:
        print(title)
        print("=" * 80)


def fetch_page() -> str | None:
    print_separator("STEP 1 - OFFICIAL TPEx ETF HISTORICAL PAGE")

    print(f"URL: {PAGE_URL}")

    try:
        response = session.get(
            PAGE_URL,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"REQUEST ERROR: {exc}")
        return None

    print()
    print(f"HTTP STATUS: {response.status_code}")
    print(
        "CONTENT TYPE: "
        f"{response.headers.get('Content-Type', '')}"
    )
    print(f"CONTENT LENGTH: {len(response.content)}")

    if response.status_code != 200:
        print("ERROR: historical page HTTP status is not 200")
        return None

    response.encoding = response.apparent_encoding or "utf-8"

    html = response.text

    print(f"HTML LENGTH: {len(html)}")

    if "ETF" not in html:
        print("WARNING: ETF keyword not found in HTML")

    return html


def extract_script_urls(html: str) -> list[str]:
    print_separator("STEP 2 - LINKED JAVASCRIPT FILES")

    urls: list[str] = []

    pattern = re.compile(
        r'<script[^>]+src=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )

    for match in pattern.finditer(html):
        src = unescape(match.group(1).strip())

        if not src:
            continue

        absolute = urljoin(BASE_URL, src)

        if absolute not in urls:
            urls.append(absolute)

    for url in urls:
        print(f"  {url}")

    print()
    print(f"TOTAL SCRIPT URLS: {len(urls)}")

    return urls


def download_scripts(script_urls: list[str]) -> dict[str, str]:
    print_separator("STEP 3 - DOWNLOAD JAVASCRIPT")

    scripts: dict[str, str] = {}

    for url in script_urls:
        try:
            response = session.get(
                url,
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            print()
            print(f"ERROR: {url}")
            print(f"  {exc}")
            continue

        if response.status_code != 200:
            print()
            print(f"ERROR: {url}")
            print(f"  HTTP {response.status_code}")
            continue

        response.encoding = response.apparent_encoding or "utf-8"
        text = response.text

        scripts[url] = text

        print()
        print(f"OK: {url}")
        print(
            "  content_type: "
            f"{response.headers.get('Content-Type', '')}"
        )
        print(f"  length: {len(response.content)}")

    print()
    print(f"JAVASCRIPT FILES LOADED: {len(scripts)}")

    return scripts


def print_keyword_context(
    source_name: str,
    text: str,
    keywords: list[str],
) -> None:
    for keyword in keywords:
        positions = [
            match.start()
            for match in re.finditer(
                re.escape(keyword),
                text,
                re.IGNORECASE,
            )
        ]

        if not positions:
            continue

        print()
        print("-" * 80)
        print(f"SOURCE: {source_name}")
        print(f"KEYWORD: {keyword}")

        for position in positions[:10]:
            start = max(0, position - 700)
            end = min(len(text), position + 1800)

            print()
            print(text[start:end])


def inspect_api_configuration(
    html: str,
    scripts: dict[str, str],
) -> None:
    print_separator("STEP 4 - SEARCH ETF API CONFIGURATION")

    keywords = [
        "ETFReport",
        "API_PATTERN",
        "tables.init",
        "historical",
        "download",
        "csv",
    ]

    print_keyword_context(
        "HTML",
        html,
        keywords,
    )

    for url, text in scripts.items():
        print_keyword_context(
            url,
            text,
            keywords,
        )


def extract_action_candidates(
    html: str,
    scripts: dict[str, str],
) -> list[str]:
    print_separator("STEP 5 - EXTRACT ACTION CANDIDATES")

    combined = html + "\n" + "\n".join(scripts.values())

    candidates: list[str] = []

    patterns = [
        r'action\s*:\s*["\']([^"\']+)["\']',
        r'ETFReport/[A-Za-z0-9_./-]+',
        r'ETFReport\\/[A-Za-z0-9_./-]+',
    ]

    for pattern in patterns:
        for match in re.finditer(
            pattern,
            combined,
            re.IGNORECASE,
        ):
            value = match.group(1) if match.lastindex else match.group(0)

            value = value.replace("\\/", "/").strip()

            if value not in candidates:
                candidates.append(value)

    if not candidates:
        print("No action candidates extracted.")
    else:
        for candidate in candidates:
            print(f"  {candidate}")

    return candidates


def json_preview(payload) -> str:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
    except Exception:
        text = repr(payload)

    if len(text) > 20000:
        return text[:20000] + "\n... OUTPUT TRUNCATED ..."

    return text


def recursive_symbol_search(
    value,
    path: str = "root",
) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"

            if normalize(key) == SYMBOL:
                found.append(
                    (
                        child_path,
                        child,
                    )
                )

            found.extend(
                recursive_symbol_search(
                    child,
                    child_path,
                )
            )

    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"

            found.extend(
                recursive_symbol_search(
                    child,
                    child_path,
                )
            )

    elif normalize(value) == SYMBOL:
        found.append(
            (
                path,
                value,
            )
        )

    return found


def recursive_string_search(
    value,
    needle: str,
    path: str = "root",
) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []

    needle_norm = normalize(needle)

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"

            if needle_norm in normalize(key):
                found.append(
                    (
                        child_path,
                        key,
                    )
                )

            found.extend(
                recursive_string_search(
                    child,
                    needle,
                    child_path,
                )
            )

    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"

            found.extend(
                recursive_string_search(
                    child,
                    needle,
                    child_path,
                )
            )

    elif needle_norm in normalize(value):
        found.append(
            (
                path,
                value,
            )
        )

    return found


def request_candidate(
    method: str,
    params: dict,
    data: dict | None = None,
):
    print()
    print("-" * 80)
    print(f"METHOD: {method}")
    print(f"URL: {API_URL}")

    if params:
        print("PARAMS:")
        print(
            json.dumps(
                params,
                ensure_ascii=False,
                indent=2,
            )
        )

    if data:
        print("FORM DATA:")
        print(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
        )

    try:
        if method == "GET":
            response = session.get(
                API_URL,
                params=params,
                timeout=TIMEOUT,
            )
        else:
            response = session.post(
                API_URL,
                params=params,
                data=data,
                timeout=TIMEOUT,
            )
    except requests.RequestException as exc:
        print(f"REQUEST ERROR: {exc}")
        return None

    print()
    print(f"HTTP STATUS: {response.status_code}")
    print(
        "CONTENT TYPE: "
        f"{response.headers.get('Content-Type', '')}"
    )
    print(f"CONTENT LENGTH: {len(response.content)}")

    if response.status_code != 200:
        print("HTTP STATUS IS NOT 200")
        return None

    response.encoding = response.apparent_encoding or "utf-8"

    text = response.text.lstrip()

    if not text:
        print("EMPTY RESPONSE")
        return None

    try:
        payload = response.json()
    except Exception as exc:
        print(f"NOT JSON: {exc}")
        print()
        print("RESPONSE PREVIEW:")
        print(text[:3000])
        return None

    print("JSON DECODE: OK")
    print(f"ROOT TYPE: {type(payload).__name__}")

    if isinstance(payload, dict):
        print()
        print("ROOT KEYS:")
        for key in payload.keys():
            print(f"  {key}")

    print()
    print("JSON PREVIEW:")
    print(json_preview(payload))

    return payload


def candidate_parameter_sets(
    d: date,
) -> list[tuple[str, dict, dict | None]]:
    roc = roc_date(d)

    candidates: list[tuple[str, dict, dict | None]] = []

    date_values = [
        roc,
        d.strftime("%Y/%m/%d"),
        d.strftime("%Y-%m-%d"),
        d.strftime("%Y%m%d"),
    ]

    names = [
        "date",
        "d",
        "queryDate",
        "query_date",
        "dateValue",
        "dataDate",
    ]

    for name in names:
        for value in date_values:
            candidates.append(
                (
                    f"GET {name}={value}",
                    {
                        name: value,
                    },
                    None,
                )
            )

    # Common combinations used by TPEx table APIs.
    for value in date_values:
        candidates.append(
            (
                f"GET date={value}, type=day",
                {
                    "date": value,
                    "type": "day",
                },
                None,
            )
        )

        candidates.append(
            (
                f"GET d={value}, type=day",
                {
                    "d": value,
                    "type": "day",
                },
                None,
            )
        )

        candidates.append(
            (
                f"GET date={value}, period=day",
                {
                    "date": value,
                    "period": "day",
                },
                None,
            )
        )

    # POST equivalents.
    for value in date_values:
        candidates.append(
            (
                f"POST date={value}",
                {},
                {
                    "date": value,
                },
            )
        )

        candidates.append(
            (
                f"POST d={value}",
                {},
                {
                    "d": value,
                },
            )
        )

    return candidates


def test_api_candidates(dates: list[date]):
    print_separator("STEP 6 - TEST OFFICIAL ETF API")

    print()
    print("IMPORTANT:")
    print("These requests are diagnostic only.")
    print("Production files will not be modified.")

    successes = []

    for d in dates:
        print_separator(
            f"TEST DATE: {d.isoformat()} / TPEx: {roc_date(d)}"
        )

        candidates = candidate_parameter_sets(d)

        seen_response_fingerprints: set[str] = set()

        for label, params, data in candidates:
            print()
            print("=" * 80)
            print(f"TEST CANDIDATE: {label}")
            print("=" * 80)

            payload = request_candidate(
                "GET" if not data else "POST",
                params,
                data,
            )

            if payload is None:
                continue

            fingerprint = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )

            if fingerprint in seen_response_fingerprints:
                print()
                print("DUPLICATE RESPONSE: skip symbol scan")
                continue

            seen_response_fingerprints.add(fingerprint)

            symbol_matches = recursive_symbol_search(
                payload
            )

            if symbol_matches:
                print()
                print("!!! SYMBOL FOUND !!!")

                for path, value in symbol_matches:
                    print(f"PATH: {path}")
                    print(f"VALUE: {value}")

                successes.append(
                    (
                        d.isoformat(),
                        label,
                        payload,
                    )
                )

                # Do not stop the date immediately.
                # Continue checking this response for structure.
                continue

            # Search all textual occurrences as a secondary
            # diagnostic because the symbol may be embedded
            # inside a larger string.
            text_matches = recursive_string_search(
                payload,
                SYMBOL,
            )

            if text_matches:
                print()
                print("SYMBOL TEXT MATCH FOUND:")

                for path, value in text_matches:
                    print(
                        f"  {path}: {value}"
                    )

    return successes


def inspect_stat_responses(successes) -> None:
    print_separator("STEP 7 - SUCCESS RESPONSE ANALYSIS")

    if not successes:
        print("No successful symbol response to analyze.")
        return

    for date_text, label, payload in successes:
        print()
        print("-" * 80)
        print(f"DATE: {date_text}")
        print(f"REQUEST: {label}")

        if isinstance(payload, dict):
            print()
            print("ROOT KEYS:")
            for key in payload:
                print(f"  {key}")

            if "stat" in payload:
                print()
                print("STAT:")
                print(
                    json_preview(
                        payload["stat"]
                    )
                )

            for key, value in payload.items():
                if key == "stat":
                    continue

                print()
                print(f"FIELD: {key}")
                print(
                    json_preview(value)
                )


def main() -> int:
    print("=" * 80)
    print("00838B TPEx ETF OFFICIAL PRICE DIAGNOSTIC V6")
    print("=" * 80)

    print()
    print(f"SYMBOL: {SYMBOL}")
    print("SOURCE: TPEx OFFICIAL")
    print("DATA TYPE: ETF HISTORICAL PRICE")

    print()
    print("Yahoo: NO")
    print("Universe: NO")
    print("Production pipeline: NO")
    print("fetch_prices.py: NOT MODIFIED")
    print("Data/prices.json: NOT MODIFIED")
    print("Data/universe.json: NOT MODIFIED")

    print()
    print("OFFICIAL PAGE:")
    print(PAGE_URL)

    print()
    print("CONFIRMED API ROUTE:")
    print(API_URL)

    html = fetch_page()

    if html is None:
        return 1

    script_urls = extract_script_urls(html)

    scripts = download_scripts(
        script_urls
    )

    inspect_api_configuration(
        html,
        scripts,
    )

    action_candidates = extract_action_candidates(
        html,
        scripts,
    )

    print()
    print("ACTION CANDIDATES:")
    if action_candidates:
        for action in action_candidates:
            print(f"  {action}")
    else:
        print("  NONE")

    start_date = date(
        2026,
        8,
        28,
    )

    dates = [
        start_date - timedelta(days=offset)
        for offset in range(10)
    ]

    successes = test_api_candidates(
        dates
    )

    inspect_stat_responses(
        successes
    )

    print_separator("FINAL RESULT")

    print(
        f"HTTP / JSON test dates: {len(dates)}"
    )

    print(
        f"Successful responses containing "
        f"{SYMBOL}: {len(successes)}"
    )

    if successes:
        print()
        print("SUCCESS")
        print()
        print(
            "The official TPEx ETF API path has "
            "returned a response containing "
            f"{SYMBOL}."
        )
        print()
        print(
            "Production fetch_prices.py remains "
            "untouched."
        )
        print()
        print(
            "NEXT STEP:"
        )
        print(
            "Use the confirmed request contract "
            "to implement an isolated TPEx ETF "
            "branch in fetch_prices.py."
        )

        return 0

    print()
    print(
        f"00838B was NOT found in the tested "
        f"official API responses."
    )

    print()
    print(
        "IMPORTANT:"
    )
    print(
        "This does NOT mean the ETF is absent "
        "from TPEx."
    )
    print(
        "It means the exact query contract "
        "has not yet been identified."
    )

    print()
    print(
        "Production files remain untouched."
    )

    return 1


if __name__ == "__main__":
    sys.exit(main())