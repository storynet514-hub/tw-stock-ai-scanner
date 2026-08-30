#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx ETF Official Price Diagnostic V5

Scope:
- Diagnostic only.
- Does NOT modify fetch_prices.py.
- Does NOT modify Data/prices.json.
- Does NOT modify Data/universe.json.
- Does NOT use stk_wn1430_result.php.
- Does NOT use the normal OTC stock quote endpoint.

Strategy:
1. Open the official TPEx ETF historical daily page.
2. Inspect HTML and linked JavaScript.
3. Locate the actual ETF historical data request configuration.
4. Test discovered candidate API endpoints.
5. Accept JSON only when the response is actually JSON.
6. Search recursively for symbol 00838B.
7. Print the exact response structure and matching row.
8. Never modify the production price pipeline.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse

import requests


SYMBOL = "00838B"

OFFICIAL_PAGE = (
    "https://www.tpex.org.tw/zh-tw/product/etf/info/historical/day.html"
)

BASE_URL = "https://www.tpex.org.tw"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/json,text/plain,*/*"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": OFFICIAL_PAGE,
}


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


def roc_date(value: date) -> str:
    return f"{value.year - 1911:03d}/{value.month:02d}/{value.day:02d}"


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def request_page(session: requests.Session) -> str | None:
    print("=" * 80)
    print("STEP 1 - OFFICIAL TPEx ETF HISTORICAL PAGE")
    print("=" * 80)
    print(f"URL: {OFFICIAL_PAGE}")
    print()

    try:
        response = session.get(
            OFFICIAL_PAGE,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"REQUEST ERROR: {exc}")
        return None

    print(f"HTTP STATUS: {response.status_code}")
    print(
        "CONTENT TYPE: "
        f"{response.headers.get('Content-Type', '')}"
    )
    print(f"CONTENT LENGTH: {len(response.content)}")

    if response.status_code != 200:
        print("ERROR: HTTP status is not 200")
        return None

    response.encoding = response.apparent_encoding or "utf-8"

    html = response.text

    print()
    print("PAGE CHECK")
    print(f"HTML LENGTH: {len(html)}")

    if "ETF" not in html.upper():
        print("WARNING: ETF text not found in page")

    return html


def extract_script_urls(html: str) -> list[str]:
    print()
    print("=" * 80)
    print("STEP 2 - LINKED JAVASCRIPT FILES")
    print("=" * 80)

    urls: list[str] = []

    patterns = [
        r'<script[^>]+src=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+\.js[^"\']*)["\']',
    ]

    for pattern in patterns:
        for match in re.finditer(
            pattern,
            html,
            flags=re.IGNORECASE,
        ):
            raw = match.group(1).strip()

            if not raw:
                continue

            absolute = urljoin(BASE_URL, raw)

            if absolute not in urls:
                urls.append(absolute)

    for url in urls:
        print(f"  {url}")

    print()
    print(f"TOTAL SCRIPT URLS: {len(urls)}")

    return urls


def download_scripts(
    session: requests.Session,
    urls: list[str],
) -> dict[str, str]:
    print()
    print("=" * 80)
    print("STEP 3 - DOWNLOAD JAVASCRIPT")
    print("=" * 80)

    results: dict[str, str] = {}

    for url in urls:
        try:
            response = session.get(
                url,
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            print(f"ERROR: {url}")
            print(f"  {exc}")
            continue

        content_type = response.headers.get(
            "Content-Type",
            "",
        )

        if response.status_code != 200:
            print(
                f"SKIP: {url} "
                f"HTTP {response.status_code}"
            )
            continue

        text = response.text

        results[url] = text

        print()
        print(f"OK: {url}")
        print(f"  content_type: {content_type}")
        print(f"  length: {len(text)}")

    print()
    print(f"JAVASCRIPT FILES LOADED: {len(results)}")

    return results


def find_relevant_fragments(
    html: str,
    scripts: dict[str, str],
) -> list[tuple[str, str]]:
    print()
    print("=" * 80)
    print("STEP 4 - SEARCH ETF API CONFIGURATION")
    print("=" * 80)

    keywords = [
        "ETFReport",
        "historical",
        "tables.init",
        "API_PATTERN",
        "api_pattern",
        "action",
        "ETF",
        "download",
        "csv",
    ]

    fragments: list[tuple[str, str]] = []

    sources = [("HTML", html)]

    for url, text in scripts.items():
        sources.append((url, text))

    for source_name, text in sources:
        upper = text.upper()

        matched = False

        for keyword in keywords:
            position = upper.find(keyword.upper())

            if position < 0:
                continue

            matched = True

            start = max(0, position - 500)
            end = min(
                len(text),
                position + 1500,
            )

            fragment = text[start:end]

            fragments.append(
                (
                    source_name,
                    fragment,
                )
            )

            print()
            print("-" * 80)
            print(f"SOURCE: {source_name}")
            print(f"KEYWORD: {keyword}")
            print("-" * 80)
            print(fragment)

            break

        if not matched:
            continue

    print()
    print(
        f"RELEVANT CONFIGURATION BLOCKS: "
        f"{len(fragments)}"
    )

    return fragments


def extract_api_candidates(
    html: str,
    scripts: dict[str, str],
) -> list[str]:
    print()
    print("=" * 80)
    print("STEP 5 - EXTRACT REAL HTTP CANDIDATES")
    print("=" * 80)

    candidates: list[str] = []

    all_text = [html]

    for text in scripts.values():
        all_text.append(text)

    combined = "\n".join(all_text)

    # Absolute HTTP URLs.
    absolute_pattern = re.compile(
        r'https?://[^\s"\'<>]+',
        flags=re.IGNORECASE,
    )

    for match in absolute_pattern.finditer(combined):
        url = match.group(0).rstrip(
            ".,;)]}"
        )

        lower = url.lower()

        if (
            "tpex.org.tw" not in lower
            and "tpex.org.tw" not in lower.replace(
                "https://",
                "",
            )
        ):
            continue

        if url not in candidates:
            candidates.append(url)

    # Relative PHP / API paths.
    relative_patterns = [
        r'["\']([^"\']+\.php(?:\?[^"\']*)?)["\']',
        r'["\']([^"\']+/api/[^"\']*)["\']',
        r'["\']([^"\']*ETFReport[^"\']*)["\']',
        r'["\']([^"\']*historical[^"\']*)["\']',
    ]

    for pattern in relative_patterns:
        for match in re.finditer(
            pattern,
            combined,
            flags=re.IGNORECASE,
        ):
            raw = match.group(1).strip()

            if not raw:
                continue

            if raw.startswith(
                (
                    "javascript:",
                    "#",
                )
            ):
                continue

            absolute = urljoin(
                BASE_URL,
                raw,
            )

            parsed = urlparse(absolute)

            if parsed.scheme not in (
                "http",
                "https",
            ):
                continue

            if "tpex.org.tw" not in parsed.netloc:
                continue

            if absolute not in candidates:
                candidates.append(absolute)

    # Important:
    # Do NOT treat arbitrary JavaScript text such as
    # tables.init(...) as an endpoint.
    filtered: list[str] = []

    for candidate in candidates:
        lower = candidate.lower()

        if "tables.init" in lower:
            continue

        if "function(" in lower:
            continue

        if candidate not in filtered:
            filtered.append(candidate)

    for index, candidate in enumerate(
        filtered,
        start=1,
    ):
        print(
            f"{index:02d}. {candidate}"
        )

    print()
    print(
        f"REAL HTTP CANDIDATES: "
        f"{len(filtered)}"
    )

    return filtered


def build_date_params(d: date) -> list[dict[str, str]]:
    roc = roc_date(d)

    return [
        {
            "l": "zh-tw",
            "d": roc,
            "o": "json",
        },
        {
            "l": "zh-tw",
            "d": roc,
        },
        {
            "date": roc,
            "l": "zh-tw",
        },
        {
            "date": roc,
        },
        {
            "d": roc,
        },
    ]


def contains_symbol(value, symbol: str) -> bool:
    target = normalize(symbol)

    if isinstance(value, dict):
        return any(
            contains_symbol(
                item,
                symbol,
            )
            for item in value.values()
        )

    if isinstance(value, list):
        return any(
            contains_symbol(
                item,
                symbol,
            )
            for item in value
        )

    return normalize(value) == target


def find_symbol_rows(
    value,
    symbol: str,
    path: str = "root",
) -> list[tuple[str, object]]:
    matches: list[tuple[str, object]] = []

    if isinstance(value, dict):
        for key, item in value.items():
            child_path = (
                f"{path}.{key}"
            )

            if contains_symbol(
                item,
                symbol,
            ):
                matches.append(
                    (
                        child_path,
                        item,
                    )
                )

            matches.extend(
                find_symbol_rows(
                    item,
                    symbol,
                    child_path,
                )
            )

    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_path = (
                f"{path}[{index}]"
            )

            if contains_symbol(
                item,
                symbol,
            ):
                matches.append(
                    (
                        child_path,
                        item,
                    )
                )

            matches.extend(
                find_symbol_rows(
                    item,
                    symbol,
                    child_path,
                )
            )

    return matches


def inspect_json_payload(
    payload,
    symbol: str,
) -> bool:
    print()
    print("=" * 80)
    print("JSON STRUCTURE")
    print("=" * 80)

    if isinstance(payload, dict):
        print("ROOT TYPE: dict")
        print("ROOT KEYS:")

        for key in payload.keys():
            print(f"  {key}")

    elif isinstance(payload, list):
        print("ROOT TYPE: list")
        print(f"ROOT LENGTH: {len(payload)}")

    else:
        print(
            "ROOT TYPE: "
            f"{type(payload).__name__}"
        )

    matches = find_symbol_rows(
        payload,
        symbol,
    )

    # De-duplicate by path + serialized value.
    unique: list[tuple[str, object]] = []
    seen: set[str] = set()

    for path, value in matches:
        try:
            marker = (
                path
                + "|"
                + json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            )
        except Exception:
            marker = path + "|" + str(value)

        if marker in seen:
            continue

        seen.add(marker)
        unique.append(
            (
                path,
                value,
            )
        )

    print()
    print(
        f"SYMBOL SEARCH: {symbol}"
    )
    print(
        f"MATCH COUNT: {len(unique)}"
    )

    if not unique:
        return False

    print()
    print("=" * 80)
    print(f"FOUND {symbol}")
    print("=" * 80)

    for index, (path, value) in enumerate(
        unique[:20],
        start=1,
    ):
        print()
        print(
            f"MATCH {index}"
        )
        print(
            f"PATH: {path}"
        )
        print("VALUE:")

        print(
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

    return True


def test_candidate(
    session: requests.Session,
    url: str,
    d: date,
) -> bool:
    print()
    print("=" * 80)
    print("TEST CANDIDATE")
    print("=" * 80)
    print(f"URL: {url}")
    print(f"DATE: {d.isoformat()}")
    print(f"ROC DATE: {roc_date(d)}")

    for params in build_date_params(d):
        print()
        print("PARAMS:")
        print(
            json.dumps(
                params,
                ensure_ascii=False,
            )
        )

        try:
            response = session.get(
                url,
                params=params,
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            print(
                f"REQUEST ERROR: {exc}"
            )
            continue

        content_type = response.headers.get(
            "Content-Type",
            "",
        )

        print(
            f"HTTP STATUS: "
            f"{response.status_code}"
        )
        print(
            f"CONTENT TYPE: "
            f"{content_type}"
        )
        print(
            f"CONTENT LENGTH: "
            f"{len(response.content)}"
        )

        if response.status_code != 200:
            continue

        # First test the actual HTTP content.
        stripped = response.text.lstrip()

        looks_json = (
            "json" in content_type.lower()
            or stripped.startswith("{")
            or stripped.startswith("[")
        )

        if not looks_json:
            print(
                "NOT JSON - skip this response"
            )
            continue

        try:
            payload = response.json()
        except Exception as exc:
            print(
                f"JSON DECODE FAILED: {exc}"
            )
            continue

        print()
        print("JSON RESPONSE ACCEPTED")

        found = inspect_json_payload(
            payload,
            SYMBOL,
        )

        if found:
            print()
            print(
                "SUCCESS: "
                "candidate contains "
                f"{SYMBOL}"
            )
            return True

    return False


def fallback_page_search(
    html: str,
) -> bool:
    print()
    print("=" * 80)
    print("STEP 6 - DIRECT PAGE SYMBOL CHECK")
    print("=" * 80)

    normalized_html = normalize(html)

    if SYMBOL in normalized_html:
        print(
            f"FOUND {SYMBOL} directly "
            "inside official HTML"
        )
        return True

    print(
        f"{SYMBOL} not present directly "
        "in official HTML"
    )

    return False


def main() -> int:
    print("=" * 80)
    print("00838B TPEx ETF OFFICIAL PRICE DIAGNOSTIC V5")
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
    print("IMPORTANT:")
    print(
        "This script does not assume that "
        "etf_statistics.php is a JSON API."
    )

    session = get_session()

    html = request_page(session)

    if html is None:
        print()
        print(
            "FINAL RESULT: "
            "OFFICIAL PAGE REQUEST FAILED"
        )
        return 1

    script_urls = extract_script_urls(
        html
    )

    scripts = download_scripts(
        session,
        script_urls,
    )

    find_relevant_fragments(
        html,
        scripts,
    )

    candidates = extract_api_candidates(
        html,
        scripts,
    )

    if fallback_page_search(html):
        print()
        print(
            "FINAL RESULT: "
            f"{SYMBOL} found in official page."
        )
        return 0

    print()
    print("=" * 80)
    print("STEP 7 - TEST DISCOVERED API CANDIDATES")
    print("=" * 80)

    # Use the latest known trading date first.
    start_date = date(
        2026,
        8,
        28,
    )

    # Do not test arbitrary candidates forever.
    # Only actual HTTP candidates extracted from
    # the official page / scripts are tested.
    max_candidates = 30

    candidates = candidates[:max_candidates]

    if not candidates:
        print(
            "NO REAL HTTP API CANDIDATES FOUND."
        )
        print()
        print(
            "The official page may construct "
            "the request dynamically."
        )
        print()
        print(
            "FINAL RESULT: "
            "API endpoint still unresolved."
        )
        return 1

    success = False

    for candidate in candidates:
        if test_candidate(
            session,
            candidate,
            start_date,
        ):
            success = True
            break

    if success:
        print()
        print("=" * 80)
        print("FINAL RESULT")
        print("=" * 80)
        print(
            f"SUCCESS: "
            f"{SYMBOL} found in a TPEx ETF "
            "official data response."
        )
        print()
        print(
            "Production fetch_prices.py "
            "was NOT modified."
        )
        return 0

    # If the latest date did not work, test
    # the previous nine calendar dates using
    # the same discovered candidates.
    print()
    print("=" * 80)
    print("STEP 8 - 10-DAY CONFIRMATION")
    print("=" * 80)

    tested_dates = 0

    for offset in range(1, 10):
        d = start_date - timedelta(
            days=offset
        )

        tested_dates += 1

        for candidate in candidates:
            if test_candidate(
                session,
                candidate,
                d,
            ):
                success = True
                break

        if success:
            break

    print()
    print("=" * 80)
    print("FINAL RESULT")
    print("=" * 80)

    if success:
        print(
            f"SUCCESS: "
            f"{SYMBOL} found."
        )
        print()
        print(
            "The TPEx ETF official data "
            "route is now confirmed."
        )
        print()
        print(
            "Production fetch_prices.py "
            "was NOT modified."
        )
        return 0

    print(
        f"NOT FOUND: "
        f"{SYMBOL}"
    )
    print()
    print(
        "The diagnostic could not identify "
        "a working official JSON endpoint "
        "containing this ETF."
    )
    print()
    print(
        "NO production files were modified."
    )

    # Diagnostic failure is intentional here:
    # the workflow can inspect the output without
    # accidentally changing the production pipeline.
    return 1


if __name__ == "__main__":
    sys.exit(main())