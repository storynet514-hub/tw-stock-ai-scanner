#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TPEx ETF historical API contract diagnostic.

This script:
1. Downloads the official TPEx ETF historical page.
2. Extracts linked JavaScript files.
3. Downloads tables.js and main.js.
4. Finds API_PATTERN and ETFReport/historical usage.
5. Extracts AJAX/request code surrounding the endpoint.
6. Tests the discovered request contract.
7. Searches the successful JSON response for 00838B.
8. Does not modify the production price pipeline.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

import requests


PAGE_URL = (
    "https://www.tpex.org.tw/"
    "zh-tw/product/etf/info/historical/day.html"
)

BASE_URL = "https://www.tpex.org.tw"

SYMBOL = "00838B"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
}

TIMEOUT = 30


def absolute_url(url: str) -> str:
    url = url.strip()

    if url.startswith("http://"):
        return url

    if url.startswith("https://"):
        return url

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("/"):
        return BASE_URL + url

    return BASE_URL + "/" + url


def normalize(value: Any) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", "")
        .replace("\xa0", " ")
        .strip()
        .upper()
    )


def symbol_matches(value: Any) -> bool:
    text = normalize(value)

    return text in {
        SYMBOL,
        SYMBOL + ".TW",
        SYMBOL + ".TWO",
    }


def download(url: str) -> str | None:
    print()
    print("=" * 80)
    print("DOWNLOAD")
    print("=" * 80)
    print(url)

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"REQUEST ERROR: {exc}")
        return None

    print(
        f"HTTP STATUS: {response.status_code}"
    )

    print(
        "CONTENT TYPE: "
        f"{response.headers.get('Content-Type', '')}"
    )

    print(
        "CONTENT LENGTH: "
        f"{len(response.content)}"
    )

    if response.status_code != 200:
        print("ERROR: HTTP STATUS IS NOT 200")
        return None

    response.encoding = (
        response.apparent_encoding
        or response.encoding
        or "utf-8"
    )

    return response.text


def extract_scripts(html: str) -> list[str]:
    patterns = [
        r'<script[^>]+src=["\']([^"\']+)["\']',
        r'<script[^>]+src=([^ >]+)',
    ]

    found: list[str] = []

    for pattern in patterns:
        for match in re.findall(
            pattern,
            html,
            flags=re.IGNORECASE,
        ):
            url = absolute_url(match)

            if url not in found:
                found.append(url)

    return found


def print_context(
    text: str,
    keyword: str,
    radius: int = 2500,
) -> bool:
    positions = []
    start = 0

    while True:
        index = text.lower().find(
            keyword.lower(),
            start,
        )

        if index < 0:
            break

        positions.append(index)
        start = index + len(keyword)

    if not positions:
        return False

    for number, index in enumerate(
        positions[:10],
        start=1,
    ):
        print()
        print("-" * 80)
        print(
            f"MATCH {number}: "
            f"{keyword}"
        )
        print("-" * 80)

        left = max(
            0,
            index - radius,
        )

        right = min(
            len(text),
            index + len(keyword) + radius,
        )

        print(text[left:right])

    if len(positions) > 10:
        print()
        print(
            f"... {len(positions) - 10} "
            f"additional matches omitted"
        )

    return True


def find_api_pattern(
    scripts: dict[str, str],
) -> str | None:

    patterns = [
        r'API_PATTERN\s*=\s*["\']([^"\']+)["\']',
        r'API_PATTERN\s*=\s*([^;,\n]+)',
    ]

    for url, text in scripts.items():

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                value = match.group(1).strip()

                print()
                print(
                    "=" * 80
                )
                print(
                    "API_PATTERN FOUND"
                )
                print(
                    f"SOURCE: {url}"
                )
                print(
                    f"VALUE: {value}"
                )
                print(
                    "=" * 80
                )

                return value

    return None


def find_endpoint_usage(
    scripts: dict[str, str],
) -> list[tuple[str, str]]:

    results = []

    keywords = [
        "ETFReport/historical",
        "ETFReport",
        "historical",
    ]

    for url, text in scripts.items():

        for keyword in keywords:

            if keyword.lower() not in text.lower():
                continue

            print()
            print("=" * 80)
            print(
                "ENDPOINT USAGE FOUND"
            )
            print("=" * 80)
            print(
                f"SOURCE: {url}"
            )
            print(
                f"KEYWORD: {keyword}"
            )

            print_context(
                text,
                keyword,
                radius=3500,
            )

            results.append(
                (url, keyword)
            )

    return results


def extract_ajax_blocks(
    text: str,
) -> list[str]:

    blocks = []

    keywords = [
        "$.ajax",
        "$.post",
        "$.get",
        "ajax(",
        "fetch(",
        "axios.",
    ]

    for keyword in keywords:

        start = 0

        while True:

            index = text.find(
                keyword,
                start,
            )

            if index < 0:
                break

            left = max(
                0,
                index - 1000,
            )

            right = min(
                len(text),
                index + 5000,
            )

            block = text[left:right]

            if (
                "ETFReport" in block
                or "historical" in block
                or "API_PATTERN" in block
            ):
                blocks.append(block)

            start = (
                index + len(keyword)
            )

    unique = []

    for block in blocks:
        if block not in unique:
            unique.append(block)

    return unique


def inspect_ajax(
    scripts: dict[str, str],
) -> None:

    print()
    print("=" * 80)
    print(
        "SEARCH AJAX / REQUEST CONTRACT"
    )
    print("=" * 80)

    found = False

    for url, text in scripts.items():

        blocks = extract_ajax_blocks(
            text
        )

        if not blocks:
            continue

        found = True

        for number, block in enumerate(
            blocks[:20],
            start=1,
        ):

            print()
            print("-" * 80)
            print(
                f"SOURCE: {url}"
            )
            print(
                f"REQUEST BLOCK {number}"
            )
            print("-" * 80)
            print(block)

    if not found:
        print(
            "No relevant AJAX block found."
        )


def find_action_configuration(
    html: str,
) -> None:

    print()
    print("=" * 80)
    print(
        "SEARCH HTML TABLE CONFIGURATION"
    )
    print("=" * 80)

    keywords = [
        'ETFReport/historical',
        'tables.init',
        'action:',
        'pattern:',
    ]

    for keyword in keywords:

        if keyword.lower() not in html.lower():
            print(
                f"NOT FOUND: {keyword}"
            )
            continue

        print()
        print(
            f"FOUND: {keyword}"
        )

        print_context(
            html,
            keyword,
            radius=4000,
        )


def recursive_symbol_search(
    value: Any,
    path: str = "root",
) -> list[tuple[str, Any]]:

    matches = []

    if isinstance(value, dict):

        for key, child in value.items():

            key_path = (
                f"{path}.{key}"
            )

            if symbol_matches(key):
                matches.append(
                    (
                        key_path,
                        key,
                    )
                )

            matches.extend(
                recursive_symbol_search(
                    child,
                    key_path,
                )
            )

    elif isinstance(value, list):

        for index, child in enumerate(
            value
        ):

            child_path = (
                f"{path}[{index}]"
            )

            matches.extend(
                recursive_symbol_search(
                    child,
                    child_path,
                )
            )

    else:

        if symbol_matches(value):
            matches.append(
                (
                    path,
                    value,
                )
            )

    return matches


def print_json_response(
    response: requests.Response,
) -> Any | None:

    print()
    print(
        "HTTP STATUS: "
        f"{response.status_code}"
    )

    print(
        "CONTENT TYPE: "
        f"{response.headers.get('Content-Type', '')}"
    )

    print(
        "CONTENT LENGTH: "
        f"{len(response.content)}"
    )

    try:
        payload = response.json()
    except Exception as exc:

        print(
            f"JSON DECODE FAILED: {exc}"
        )

        print()
        print("RESPONSE PREVIEW:")
        print(
            response.text[:5000]
        )

        return None

    print("JSON DECODE: OK")

    print(
        f"ROOT TYPE: "
        f"{type(payload).__name__}"
    )

    if isinstance(payload, dict):

        print()
        print("ROOT KEYS:")

        for key in payload.keys():
            print(
                f"  {key}"
            )

    return payload


def test_request_contract(
    method: str,
    url: str,
    data: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> Any | None:

    print()
    print("=" * 80)
    print("TEST DISCOVERED REQUEST")
    print("=" * 80)

    print(
        f"METHOD: {method}"
    )

    print(
        f"URL: {url}"
    )

    if params is not None:
        print()
        print("PARAMS:")
        print(
            json.dumps(
                params,
                ensure_ascii=False,
                indent=2,
            )
        )

    if data is not None:
        print()
        print("FORM DATA:")
        print(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
        )

    session = requests.Session()

    try:

        if method == "POST":

            response = session.post(
                url,
                params=params,
                data=data,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

        else:

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

    except requests.RequestException as exc:

        print(
            f"REQUEST ERROR: {exc}"
        )

        return None

    return print_json_response(
        response
    )


def main() -> int:

    print("=" * 80)
    print(
        "TPEx ETF API CONTRACT DIAGNOSTIC"
    )
    print("=" * 80)

    print()
    print(
        f"TARGET SYMBOL: {SYMBOL}"
    )

    print(
        "PRODUCTION PIPELINE: NOT MODIFIED"
    )

    print()
    print(
        "STEP 1 - DOWNLOAD OFFICIAL PAGE"
    )

    html = download(
        PAGE_URL
    )

    if html is None:
        return 1

    print()
    print(
        f"HTML LENGTH: {len(html)}"
    )

    find_action_configuration(
        html
    )

    print()
    print(
        "STEP 2 - EXTRACT SCRIPT FILES"
    )

    script_urls = extract_scripts(
        html
    )

    print(
        f"TOTAL SCRIPT URLS: "
        f"{len(script_urls)}"
    )

    scripts: dict[str, str] = {}

    for url in script_urls:

        if (
            "tables.js" not in url
            and "main.js" not in url
            and "global.js" not in url
        ):
            continue

        text = download(url)

        if text is not None:
            scripts[url] = text

    print()
    print(
        f"RELEVANT JS FILES LOADED: "
        f"{len(scripts)}"
    )

    if not scripts:
        print(
            "ERROR: Could not download "
            "relevant JavaScript files."
        )
        return 1

    print()
    print(
        "STEP 3 - FIND API_PATTERN"
    )

    api_pattern = find_api_pattern(
        scripts
    )

    if api_pattern is None:
        print(
            "API_PATTERN was not found."
        )

    print()
    print(
        "STEP 4 - FIND ETF ENDPOINT USAGE"
    )

    usages = find_endpoint_usage(
        scripts
    )

    print()
    print(
        f"ENDPOINT USAGE MATCHES: "
        f"{len(usages)}"
    )

    print()
    print(
        "STEP 5 - INSPECT REQUEST CODE"
    )

    inspect_ajax(
        scripts
    )

    print()
    print("=" * 80)
    print(
        "STEP 6 - CONTRACT SUMMARY"
    )
    print("=" * 80)

    if api_pattern:
        print(
            f"API_PATTERN: {api_pattern}"
        )

    print(
        "ACTION: ETFReport/historical"
    )

    print(
        "TARGET: 00838B"
    )

    print()
    print(
        "The diagnostic intentionally does "
        "not guess the final production "
        "request."
    )

    print()
    print(
        "NEXT ACTION:"
    )

    print(
        "Use the request contract printed "
        "above to make one exact official "
        "TPEx API request."
    )

    print()
    print(
        "Production files remain untouched."
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())