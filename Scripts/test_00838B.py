#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx ETF Official API Contract Diagnostic V6

Purpose:
1. Inspect the official TPEx ETF historical daily page.
2. Discover the JavaScript configuration used by the page.
3. Download tables.js.
4. Discover API_PATTERN and ETFReport/historical.
5. Inspect JavaScript request functions and request contracts.
6. Test discovered endpoint/method/parameter combinations.
7. Search all successful JSON responses for 00838B.
8. Do NOT modify production price files.

Production files NOT touched:
- Scripts/fetch_prices.py
- Data/prices.json
- Data/universe.json
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

TABLES_JS_URL = (
    "https://www.tpex.org.tw/rsrc/js/tables.js"
)

API_BASE = "https://www.tpex.org.tw"

LANG = "zh-tw"
ACTION = "ETFReport/historical"

DEFAULT_START_DATE = date(2026, 8, 28)
TEST_DAYS = 10

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Referer": PAGE_URL,
}

JSON_HEADERS = {
    **HEADERS,
    "Accept": "application/json,text/plain,*/*",
    "X-Requested-With": "XMLHttpRequest",
}


def print_line(char="=", length=80):
    print(char * length)


def roc_date_slash(d: date) -> str:
    return f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}"


def roc_date_compact(d: date) -> str:
    return f"{d.year - 1911:03d}{d.month:02d}{d.day:02d}"


def gregorian_compact(d: date) -> str:
    return d.strftime("%Y%m%d")


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


def request_page(session: requests.Session):
    print_line()
    print("STEP 1 - OFFICIAL TPEx ETF HISTORICAL PAGE")
    print_line()

    print(f"URL: {PAGE_URL}")

    try:
        response = session.get(
            PAGE_URL,
            headers=HEADERS,
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
        print("ERROR: page status is not 200")
        return None

    response.encoding = response.apparent_encoding or "utf-8"

    html = response.text

    print()
    print(f"HTML LENGTH: {len(html)}")

    if "ETF" not in html.upper():
        print("WARNING: ETF keyword not found in page")

    return html


def extract_script_urls(html: str):
    print_line()
    print("STEP 2 - LINKED JAVASCRIPT FILES")
    print_line()

    urls = []

    pattern = re.compile(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )

    for match in pattern.finditer(html):
        raw = unescape(match.group(1).strip())

        if not raw:
            continue

        full_url = urljoin(PAGE_URL, raw)

        if full_url not in urls:
            urls.append(full_url)

    for url in urls:
        print(f"  {url}")

    print()
    print(f"TOTAL SCRIPT URLS: {len(urls)}")

    return urls


def download_script(session, url: str):
    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"ERROR: {url}")
        print(f"  {exc}")
        return None

    if response.status_code != 200:
        print(f"ERROR: {url}")
        print(f"  HTTP {response.status_code}")
        return None

    response.encoding = response.apparent_encoding or "utf-8"

    return response.text


def download_all_scripts(session, script_urls):
    print_line()
    print("STEP 3 - DOWNLOAD JAVASCRIPT")
    print_line()

    scripts = {}

    for url in script_urls:
        content = download_script(session, url)

        if content is None:
            continue

        scripts[url] = content

        print()
        print(f"OK: {url}")
        print(
            "  content_type: "
            "downloaded"
        )
        print(f"  length: {len(content)}")

    print()
    print(f"JAVASCRIPT FILES LOADED: {len(scripts)}")

    return scripts


def extract_inline_api_config(html: str):
    print_line()
    print("STEP 4 - EXTRACT INLINE API CONFIGURATION")
    print_line()

    results = []

    api_pattern_matches = re.findall(
        r'API_PATTERN\s*=\s*["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )

    action_matches = re.findall(
        r'action\s*:\s*["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )

    init_matches = re.findall(
        r'tables\.init\s*\((.*?)\)\s*;',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    print()
    print("API_PATTERN FOUND IN HTML:")

    if api_pattern_matches:
        for item in api_pattern_matches:
            print(f"  {item}")
            results.append(("html_api_pattern", item))
    else:
        print("  NONE")

    print()
    print("ACTION VALUES FOUND IN HTML:")

    action_values = []

    for item in action_matches:
        if "ETF" in item or "Report" in item or "historical" in item:
            if item not in action_values:
                action_values.append(item)

    if action_values:
        for item in action_values:
            print(f"  {item}")
            results.append(("html_action", item))
    else:
        print("  NONE")

    print()
    print("TABLES.INIT BLOCKS:")

    if init_matches:
        for index, block in enumerate(init_matches, start=1):
            print()
            print(f"--- TABLES.INIT BLOCK {index} ---")
            print(block[:5000])
    else:
        print("  NONE")

    return results


def find_keyword_context(
    source: str,
    keyword: str,
    context=1800,
):
    matches = []

    start = 0

    while True:
        index = source.lower().find(
            keyword.lower(),
            start,
        )

        if index < 0:
            break

        left = max(0, index - context)
        right = min(
            len(source),
            index + len(keyword) + context,
        )

        matches.append(
            source[left:right]
        )

        start = index + len(keyword)

        if len(matches) >= 20:
            break

    return matches


def inspect_javascript_sources(scripts):
    print_line()
    print("STEP 5 - SEARCH JAVASCRIPT REQUEST CONTRACT")
    print_line()

    keywords = [
        "API_PATTERN",
        "ETFReport/historical",
        "$.ajax",
        "$.post",
        "$.get",
        "fetch(",
        "XMLHttpRequest",
        "method:",
        "type:",
        "url:",
        "data:",
        "contentType:",
        "ajax",
        "post",
        "get",
    ]

    relevant = []

    for url, source in scripts.items():
        for keyword in keywords:
            contexts = find_keyword_context(
                source,
                keyword,
            )

            if not contexts:
                continue

            print()
            print("-" * 80)
            print(f"SOURCE: {url}")
            print(f"KEYWORD: {keyword}")
            print(f"MATCHES: {len(contexts)}")

            for index, context in enumerate(
                contexts[:5],
                start=1,
            ):
                print()
                print(
                    f"--- MATCH {index} ---"
                )
                print(context)

            relevant.append(
                {
                    "url": url,
                    "keyword": keyword,
                    "contexts": contexts,
                }
            )

    print()
    print(
        f"REQUEST/CONFIG SEARCH BLOCKS: "
        f"{len(relevant)}"
    )

    return relevant


def extract_request_candidates(scripts):
    print_line()
    print("STEP 6 - EXTRACT REQUEST CANDIDATES")
    print_line()

    candidates = []

    for url, source in scripts.items():

        # ---------------------------------------------------------
        # Candidate 1: explicit URL strings containing ETFReport
        # ---------------------------------------------------------
        url_patterns = [
            r'url\s*:\s*["\']([^"\']*ETFReport[^"\']*)["\']',
            r'["\']([^"\']*ETFReport/historical[^"\']*)["\']',
        ]

        for pattern in url_patterns:
            for match in re.finditer(
                pattern,
                source,
                flags=re.IGNORECASE,
            ):
                value = match.group(1)

                if value not in {
                    c["url"]
                    for c in candidates
                }:
                    candidates.append(
                        {
                            "source": url,
                            "kind": "url",
                            "url": value,
                        }
                    )

        # ---------------------------------------------------------
        # Candidate 2: strings containing API_PATTERN replacement
        # ---------------------------------------------------------
        if "ETFReport/historical" in source:
            candidates.append(
                {
                    "source": url,
                    "kind": "known-action",
                    "url": (
                        f"/www/{LANG}/"
                        f"{ACTION}"
                    ),
                }
            )

        # ---------------------------------------------------------
        # Candidate 3: ajax blocks containing ETFReport
        # ---------------------------------------------------------
        for match in re.finditer(
            r'\$\.ajax\s*\((.*?)\)',
            source,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            block = match.group(1)

            if "ETFReport" not in block:
                continue

            candidates.append(
                {
                    "source": url,
                    "kind": "ajax-block",
                    "block": block[:10000],
                }
            )

        # ---------------------------------------------------------
        # Candidate 4: $.post blocks containing ETFReport
        # ---------------------------------------------------------
        for match in re.finditer(
            r'\$\.post\s*\((.*?)\)',
            source,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            block = match.group(1)

            if "ETFReport" not in block:
                continue

            candidates.append(
                {
                    "source": url,
                    "kind": "post-block",
                    "block": block[:10000],
                }
            )

        # ---------------------------------------------------------
        # Candidate 5: fetch blocks containing ETFReport
        # ---------------------------------------------------------
        for match in re.finditer(
            r'fetch\s*\((.*?)\)',
            source,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            block = match.group(1)

            if "ETFReport" not in block:
                continue

            candidates.append(
                {
                    "source": url,
                    "kind": "fetch-block",
                    "block": block[:10000],
                }
            )

    # Deduplicate textual representation.
    unique = []
    seen = set()

    for candidate in candidates:
        key = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(candidate)

    print()
    print(f"RAW REQUEST CANDIDATES: {len(unique)}")

    for index, candidate in enumerate(
        unique,
        start=1,
    ):
        print()
        print(f"[CANDIDATE {index}]")
        print(
            json.dumps(
                candidate,
                ensure_ascii=False,
                indent=2,
            )[:12000]
        )

    return unique


def build_endpoint_candidates():
    endpoints = []

    known_paths = [
        f"/www/{LANG}/{ACTION}",
        f"/www/{LANG}/{ACTION}/",
        f"/{LANG}/{ACTION}",
        f"/{ACTION}",
        "/ETFReport/historical",
    ]

    for path in known_paths:
        full = urljoin(
            API_BASE,
            path,
        )

        if full not in endpoints:
            endpoints.append(full)

    return endpoints


def date_parameter_candidates(d: date):
    roc_slash = roc_date_slash(d)
    roc_compact = roc_date_compact(d)
    gregorian = gregorian_compact(d)
    iso = d.isoformat()

    candidates = [
        {"d": roc_slash},
        {"d": roc_compact},
        {"d": gregorian},
        {"d": iso},
        {"date": roc_slash},
        {"date": roc_compact},
        {"date": gregorian},
        {"date": iso},
        {"queryDate": roc_slash},
        {"queryDate": roc_compact},
        {"queryDate": gregorian},
        {"queryDate": iso},
        {"startDate": roc_slash},
        {"startDate": roc_compact},
        {"startDate": gregorian},
        {"date": roc_slash, "l": LANG},
        {"d": roc_slash, "l": LANG},
        {"d": roc_slash, "l": LANG, "o": "json"},
        {
            "d": roc_slash,
            "l": LANG,
            "o": "json",
            "s": "0,asc,0",
        },
    ]

    unique = []
    seen = set()

    for item in candidates:
        key = json.dumps(
            item,
            sort_keys=True,
        )

        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


def response_contains_symbol(obj, symbol):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if normalize(key) == symbol:
                return True

            if response_contains_symbol(
                value,
                symbol,
            ):
                return True

        return False

    if isinstance(obj, list):
        for value in obj:
            if response_contains_symbol(
                value,
                symbol,
            ):
                return True

        return False

    return normalize(obj) == symbol


def collect_rows_containing_symbol(
    obj,
    symbol,
    path="root",
):
    matches = []

    if isinstance(obj, dict):
        for key, value in obj.items():

            next_path = (
                f"{path}.{key}"
            )

            if normalize(key) == symbol:
                matches.append(
                    {
                        "path": next_path,
                        "value": value,
                    }
                )

            matches.extend(
                collect_rows_containing_symbol(
                    value,
                    symbol,
                    next_path,
                )
            )

    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            next_path = (
                f"{path}[{index}]"
            )

            if (
                isinstance(value, list)
                or isinstance(value, dict)
            ):
                if response_contains_symbol(
                    value,
                    symbol,
                ):
                    matches.append(
                        {
                            "path": next_path,
                            "value": value,
                        }
                    )

            matches.extend(
                collect_rows_containing_symbol(
                    value,
                    symbol,
                    next_path,
                )
            )

    return matches


def summarize_json(payload):
    if isinstance(payload, dict):
        print(
            "ROOT KEYS:"
        )

        for key in payload.keys():
            print(f"  {key}")

        print()

        for key, value in payload.items():
            if isinstance(value, list):
                print(
                    f"KEY [{key}] -> "
                    f"list rows={len(value)}"
                )

            elif isinstance(value, dict):
                print(
                    f"KEY [{key}] -> dict "
                    f"keys={len(value)}"
                )

            else:
                print(
                    f"KEY [{key}] -> "
                    f"{type(value).__name__}: "
                    f"{str(value)[:300]}"
                )

    elif isinstance(payload, list):
        print(
            f"ROOT LIST LENGTH: "
            f"{len(payload)}"
        )

    else:
        print(
            f"ROOT VALUE: "
            f"{type(payload).__name__}"
        )


def request_json(
    session,
    method,
    url,
    params,
    referer=PAGE_URL,
):
    headers = {
        **JSON_HEADERS,
        "Referer": referer,
    }

    try:
        if method == "GET":
            response = session.get(
                url,
                params=params,
                headers=headers,
                timeout=TIMEOUT,
            )
        else:
            response = session.post(
                url,
                data=params,
                headers=headers,
                timeout=TIMEOUT,
            )
    except requests.RequestException as exc:
        print(
            f"REQUEST ERROR: {exc}"
        )
        return None

    print(
        f"HTTP STATUS: "
        f"{response.status_code}"
    )

    content_type = response.headers.get(
        "Content-Type",
        "",
    )

    print(
        f"CONTENT TYPE: {content_type}"
    )

    print(
        f"CONTENT LENGTH: "
        f"{len(response.content)}"
    )

    if response.status_code != 200:
        return None

    response.encoding = (
        response.apparent_encoding
        or "utf-8"
    )

    text = response.text.strip()

    if not text:
        print("EMPTY RESPONSE")
        return None

    try:
        payload = response.json()
    except Exception:
        print("NOT JSON")

        preview = text[:1000]

        if "參數輸入錯誤" in text:
            print(
                "RESPONSE CONTAINS: "
                "參數輸入錯誤"
            )

        print(
            "RESPONSE PREVIEW:"
        )
        print(preview)

        return None

    print("JSON DECODE: OK")

    return payload


def test_endpoint(
    session,
    endpoint,
    d,
    successful_responses,
):
    print()
    print_line("-")
    print("TEST API")
    print_line("-")

    print(
        f"URL: {endpoint}"
    )
    print(
        f"DATE: {d.isoformat()}"
    )
    print(
        f"ROC DATE: "
        f"{roc_date_slash(d)}"
    )

    params_list = date_parameter_candidates(d)

    methods = [
        "GET",
        "POST",
    ]

    for method in methods:
        for params in params_list:

            print()
            print("-" * 80)
            print(
                f"METHOD: {method}"
            )
            print(
                f"URL: {endpoint}"
            )
            print("PARAMS:")
            print(
                json.dumps(
                    params,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            payload = request_json(
                session,
                method,
                endpoint,
                params,
            )

            if payload is None:
                continue

            if isinstance(payload, dict):
                stat = payload.get("stat")

                if stat:
                    print(
                        f"STAT: {stat}"
                    )

                    if (
                        str(stat).strip()
                        == "參數輸入錯誤"
                    ):
                        print(
                            "INVALID PARAMETER "
                            "CONTRACT"
                        )
                        continue

            if response_contains_symbol(
                payload,
                SYMBOL,
            ):
                print()
                print_line()
                print(
                    "SUCCESS: SYMBOL FOUND"
                )
                print_line()

                matches = (
                    collect_rows_containing_symbol(
                        payload,
                        SYMBOL,
                    )
                )

                print(
                    f"MATCH COUNT: "
                    f"{len(matches)}"
                )

                for match in matches[:20]:
                    print()
                    print(
                        f"PATH: "
                        f"{match['path']}"
                    )
                    print(
                        json.dumps(
                            match["value"],
                            ensure_ascii=False,
                            indent=2,
                        )[:10000]
                    )

                successful_responses.append(
                    {
                        "date": d.isoformat(),
                        "method": method,
                        "url": endpoint,
                        "params": params,
                        "payload": payload,
                    }
                )

                return True

            # A JSON response without the symbol is
            # still useful. Record only non-error JSON.
            successful_responses.append(
                {
                    "date": d.isoformat(),
                    "method": method,
                    "url": endpoint,
                    "params": params,
                    "payload": payload,
                }
            )

            print()
            print(
                "JSON RESPONSE ACCEPTED "
                "BUT SYMBOL NOT FOUND"
            )

            summarize_json(payload)

    return False


def test_discovered_contracts(
    session,
    candidates,
    successful_responses,
):
    print_line()
    print("STEP 7 - TEST DISCOVERED API CONTRACTS")
    print_line()

    endpoints = build_endpoint_candidates()

    # Add explicit URL candidates extracted from JS.
    for candidate in candidates:
        if candidate.get("kind") != "url":
            continue

        raw = candidate.get("url", "")

        if not raw:
            continue

        if "{" in raw:
            continue

        full = urljoin(
            API_BASE,
            raw,
        )

        if full not in endpoints:
            endpoints.append(full)

    print()
    print("ENDPOINT CANDIDATES:")

    for endpoint in endpoints:
        print(f"  {endpoint}")

    print()
    print(
        f"TOTAL ENDPOINT CANDIDATES: "
        f"{len(endpoints)}"
    )

    success_dates = []

    for offset in range(TEST_DAYS):
        d = (
            DEFAULT_START_DATE
            - timedelta(days=offset)
        )

        print()
        print_line()
        print(
            f"TEST DATE: "
            f"{d.isoformat()}"
        )
        print_line()

        date_found = False

        for endpoint in endpoints:
            found = test_endpoint(
                session,
                endpoint,
                d,
                successful_responses,
            )

            if found:
                date_found = True

        if date_found:
            success_dates.append(
                d.isoformat()
            )

    return success_dates


def print_success_summary(
    success_dates,
    successful_responses,
):
    print()
    print_line()
    print("STEP 8 - SUCCESS RESPONSE ANALYSIS")
    print_line()

    if not success_dates:
        print(
            "No response containing "
            f"{SYMBOL} was found."
        )
        return

    print(
        f"SUCCESS DATES: "
        f"{len(success_dates)}"
    )

    for item in success_dates:
        print(f"  {item}")

    print()
    print(
        f"SUCCESSFUL JSON RESPONSES: "
        f"{len(successful_responses)}"
    )

    symbol_responses = []

    for item in successful_responses:
        if response_contains_symbol(
            item["payload"],
            SYMBOL,
        ):
            symbol_responses.append(item)

    print(
        f"RESPONSES CONTAINING "
        f"{SYMBOL}: "
        f"{len(symbol_responses)}"
    )

    for item in symbol_responses[:10]:
        print()
        print(
            f"DATE: {item['date']}"
        )
        print(
            f"METHOD: {item['method']}"
        )
        print(
            f"URL: {item['url']}"
        )
        print("PARAMS:")
        print(
            json.dumps(
                item["params"],
                ensure_ascii=False,
                indent=2,
            )
        )


def main():
    print_line()
    print(
        "00838B TPEx ETF OFFICIAL "
        "API CONTRACT DIAGNOSTIC V6"
    )
    print_line()

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
    print("TEST STRATEGY:")
    print("  1. Download official ETF historical page")
    print("  2. Discover tables.init()")
    print("  3. Discover API_PATTERN")
    print("  4. Download tables.js")
    print("  5. Inspect request functions")
    print("  6. Build endpoint candidates")
    print("  7. Test GET and POST contracts")
    print("  8. Search JSON recursively for 00838B")

    session = requests.Session()

    # -------------------------------------------------------------
    # STEP 1
    # -------------------------------------------------------------
    html = request_page(session)

    if html is None:
        print()
        print("FAILED: official page unavailable")
        return 1

    # -------------------------------------------------------------
    # STEP 2
    # -------------------------------------------------------------
    script_urls = extract_script_urls(
        html
    )

    # -------------------------------------------------------------
    # STEP 3
    # -------------------------------------------------------------
    scripts = download_all_scripts(
        session,
        script_urls,
    )

    # Always explicitly attempt tables.js.
    if TABLES_JS_URL not in scripts:
        print()
        print(
            "Explicit tables.js download:"
        )

        tables_js = download_script(
            session,
            TABLES_JS_URL,
        )

        if tables_js is not None:
            scripts[TABLES_JS_URL] = (
                tables_js
            )

            print(
                "OK: tables.js loaded"
            )
            print(
                f"LENGTH: "
                f"{len(tables_js)}"
            )
        else:
            print(
                "ERROR: unable to load "
                "tables.js"
            )

    # -------------------------------------------------------------
    # STEP 4
    # -------------------------------------------------------------
    extract_inline_api_config(
        html
    )

    # -------------------------------------------------------------
    # STEP 5
    # -------------------------------------------------------------
    inspect_javascript_sources(
        scripts
    )

    # -------------------------------------------------------------
    # STEP 6
    # -------------------------------------------------------------
    candidates = (
        extract_request_candidates(
            scripts
        )
    )

    # -------------------------------------------------------------
    # STEP 7
    # -------------------------------------------------------------
    successful_responses = []

    success_dates = (
        test_discovered_contracts(
            session,
            candidates,
            successful_responses,
        )
    )

    # -------------------------------------------------------------
    # STEP 8
    # -------------------------------------------------------------
    print_success_summary(
        success_dates,
        successful_responses,
    )

    # -------------------------------------------------------------
    # FINAL
    # -------------------------------------------------------------
    print()
    print_line()
    print("FINAL RESULT")
    print_line()

    print(
        f"HTTP / JSON test dates: "
        f"{TEST_DAYS}"
    )

    print(
        f"Successful responses containing "
        f"{SYMBOL}: "
        f"{len(success_dates)} dates"
    )

    if success_dates:
        print()
        print(
            "SUCCESS: official TPEx ETF "
            "API contract found."
        )

        print()
        print("SUCCESS DATES:")

        for item in success_dates:
            print(f"  {item}")

        print()
        print(
            "IMPORTANT:"
        )
        print(
            "The diagnostic has identified "
            "a working official response "
            "containing 00838B."
        )

        print()
        print(
            "Production files remain "
            "untouched."
        )

        return 0

    print()
    print(
        f"NOT FOUND: {SYMBOL}"
    )

    print()
    print(
        "The diagnostic did not identify "
        "a working official JSON response "
        "containing this ETF."
    )

    print()
    print(
        "The next investigation should use "
        "the request blocks printed above."
    )

    print()
    print(
        "Production files remain untouched."
    )

    return 1


if __name__ == "__main__":
    sys.exit(main())