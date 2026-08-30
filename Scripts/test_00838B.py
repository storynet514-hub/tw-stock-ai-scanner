#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx ETF Official Price Diagnostic V4

Purpose:
1. Use the official TPEx ETF historical data page.
2. Do NOT treat the HTML page itself as a JSON API.
3. Discover the actual data endpoint from the page and its JavaScript.
4. Test candidate API endpoints.
5. Search for ETF symbol 00838B.
6. Validate close price and volume when possible.
7. Do NOT modify the production price pipeline.

This is a diagnostic script only.
It does not use Yahoo, Universe, or fetch_prices.py.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from datetime import date, timedelta
from html import unescape
from urllib.parse import urljoin, urlparse

import requests


SYMBOL = "00838B"

PAGE_URL = (
    "https://www.tpex.org.tw/zh-tw/product/etf/info/historical/day.html"
)

LEGACY_PAGE_URL = (
    "https://wwwov.tpex.org.tw/web/etf/historical/etf_statistics.php"
)

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.tpex.org.tw/",
    "Connection": "keep-alive",
}


session = requests.Session()
session.headers.update(HEADERS)


def roc_date(value: date) -> str:
    return f"{value.year - 1911:03d}/{value.month:02d}/{value.day:02d}"


def normalize(value) -> str:
    if value is None:
        return ""

    text = str(value)

    replacements = {
        "\ufeff": "",
        "\u3000": " ",
        "\r": " ",
        "\n": " ",
        "\t": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return " ".join(text.strip().upper().split())


def is_symbol(value) -> bool:
    return normalize(value) == SYMBOL


def safe_request(url: str, params=None):
    try:
        response = session.get(
            url,
            params=params,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        return response
    except requests.RequestException as exc:
        print(f"REQUEST ERROR: {exc}")
        return None


def print_response_summary(response, label: str):
    print()
    print("=" * 80)
    print(label)
    print("=" * 80)

    print(f"requested_url: {response.request.url}")
    print(f"status_code: {response.status_code}")
    print(
        "content_type: "
        f"{response.headers.get('Content-Type', '')}"
    )
    print(f"content_length: {len(response.content)}")
    print(f"final_url: {response.url}")


def fetch_page() -> str | None:
    print()
    print("=" * 80)
    print("STEP 1 - FETCH TPEx OFFICIAL ETF HISTORICAL PAGE")
    print("=" * 80)

    print(f"PAGE_URL: {PAGE_URL}")

    response = safe_request(PAGE_URL)

    if response is None:
        print("FAILED: unable to request official page")
        return None

    print_response_summary(response, "ETF HISTORICAL PAGE")

    if response.status_code != 200:
        print("FAILED: HTTP status is not 200")
        return None

    content_type = response.headers.get("Content-Type", "").lower()

    if "html" not in content_type:
        print(
            "WARNING: official page did not return "
            "a normal HTML content type"
        )

    response.encoding = response.apparent_encoding or "utf-8"

    html = response.text

    if not html:
        print("FAILED: empty HTML response")
        return None

    print(f"html_length: {len(html)}")

    return html


def extract_script_urls(html: str) -> list[str]:
    print()
    print("=" * 80)
    print("STEP 2 - DISCOVER JAVASCRIPT FILES")
    print("=" * 80)

    urls: list[str] = []

    patterns = [
        r'<script[^>]+src=["\']([^"\']+)["\']',
        r'<script[^>]+src=([^\s>]+)',
    ]

    for pattern in patterns:
        for match in re.finditer(
            pattern,
            html,
            flags=re.IGNORECASE,
        ):
            raw_url = unescape(match.group(1)).strip()
            raw_url = raw_url.strip("\"'")

            if not raw_url:
                continue

            absolute_url = urljoin(PAGE_URL, raw_url)

            if absolute_url not in urls:
                urls.append(absolute_url)

    print(f"script_count: {len(urls)}")

    for index, url in enumerate(urls, start=1):
        print(f"  [{index:02d}] {url}")

    return urls


def extract_inline_javascript(html: str) -> list[str]:
    scripts: list[str] = []

    for match in re.finditer(
        r"<script\b[^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        content = match.group(1).strip()

        if content:
            scripts.append(content)

    return scripts


def fetch_javascript(urls: list[str]) -> list[tuple[str, str]]:
    print()
    print("=" * 80)
    print("STEP 3 - FETCH JAVASCRIPT")
    print("=" * 80)

    results: list[tuple[str, str]] = []

    for index, url in enumerate(urls, start=1):
        print()
        print(f"JS [{index:02d}]")
        print(f"URL: {url}")

        response = safe_request(url)

        if response is None:
            print("FAILED")
            continue

        print(f"status_code: {response.status_code}")
        print(
            "content_type: "
            f"{response.headers.get('Content-Type', '')}"
        )
        print(f"content_length: {len(response.content)}")

        if response.status_code != 200:
            print("SKIP: HTTP status is not 200")
            continue

        response.encoding = response.apparent_encoding or "utf-8"

        text = response.text

        if not text:
            print("SKIP: empty JavaScript")
            continue

        results.append((url, text))
        print("OK")

    print()
    print(f"usable_javascript_files: {len(results)}")

    return results


def discover_candidate_urls(
    html: str,
    javascript: list[tuple[str, str]],
) -> list[str]:
    print()
    print("=" * 80)
    print("STEP 4 - DISCOVER POSSIBLE DATA ENDPOINTS")
    print("=" * 80)

    candidates: list[str] = []

    def add_candidate(value: str, base_url: str):
        value = unescape(value).strip()
        value = value.strip("\"'`")

        if not value:
            return

        if value.startswith("//"):
            value = "https:" + value

        absolute = urljoin(base_url, value)

        parsed = urlparse(absolute)

        if parsed.scheme not in ("http", "https"):
            return

        if "tpex.org.tw" not in parsed.netloc.lower():
            return

        if absolute not in candidates:
            candidates.append(absolute)

    # Direct URLs inside HTML.
    html_url_patterns = [
        r"""https?://[^"'`\s<>]+""",
        r"""["'](/[^"'`\s<>]*(?:api|ajax|json|csv|historical|etf)[^"'`\s<>]*)["']""",
    ]

    for pattern in html_url_patterns:
        for match in re.finditer(
            pattern,
            html,
            flags=re.IGNORECASE,
        ):
            value = match.group(0)

            if value.startswith(("\"", "'")):
                value = value[1:-1]

            add_candidate(value, PAGE_URL)

    # URLs and endpoint-like strings inside JavaScript.
    for js_url, text in javascript:
        patterns = [
            r"""https?://[^"'`\s<>]+""",
            r"""["']([^"'`\s<>]*(?:api|ajax|json|csv|historical|history|etf)[^"'`\s<>]*)["']""",
            r"""url\s*:\s*["']([^"']+)["']""",
            r"""action\s*:\s*["']([^"']+)["']""",
            r"""href\s*:\s*["']([^"']+)["']""",
            r"""fetch\s*$begin:math:text$\\s\*\[\"\'\]\(\[\^\"\'\]\+\)\[\"\'\]\"\"\"\,
            r\"\"\"axios\\\.\(\?\:get\|post\)\\s\*\\\(\\s\*\[\"\'\]\(\[\^\"\'\]\+\)\[\"\'\]\"\"\"\,
            r\"\"\"\\\$\\\.\(\?\:get\|post\|ajax\)\\s\*\\\(\\s\*\[\"\'\]\(\[\^\"\'\]\+\)\[\"\'\]\"\"\"\,
        \]

        for pattern in patterns\:
            for match in re\.finditer\(
                pattern\,
                text\,
                flags\=re\.IGNORECASE\,
            \)\:
                value \= match\.group\(0\)

                if value\.startswith\(\(\"\\\"\"\, \"\'\"\)\)\:
                    value \= value\[1\:\-1\]

                if match\.lastindex\:
                    value \= match\.group\(match\.lastindex\)

                add\_candidate\(value\, js\_url\)

    \# Look for known TPEx historical API path fragments\.
    path\_fragments \= \[
        \"\/api\/\"\,
        \"\/api\"\,
        \"\/openapi\/\"\,
        \"\/web\/\"\,
        \"\/zh\-tw\/\"\,
        \"etf\"\,
        \"historical\"\,
        \"history\"\,
        \"daily\"\,
        \"download\"\,
        \"csv\"\,
        \"json\"\,
    \]

    filtered\: list\[str\] \= \[\]

    for url in candidates\:
        lower \= url\.lower\(\)

        if any\(fragment\.lower\(\) in lower for fragment in path\_fragments\)\:
            if url not in filtered\:
                filtered\.append\(url\)

    print\(f\"raw\_candidates\: \{len\(candidates\)\}\"\)
    print\(f\"filtered\_candidates\: \{len\(filtered\)\}\"\)

    for index\, url in enumerate\(filtered\, start\=1\)\:
        print\(f\"  \[\{index\:03d\}\] \{url\}\"\)

    return filtered


def extract\_request\_contexts\(
    javascript\: list\[tuple\[str\, str\]\]\,
\) \-\> list\[str\]\:
    print\(\)
    print\(\"\=\" \* 80\)
    print\(\"STEP 5 \- SEARCH JAVASCRIPT REQUEST CONTEXT\"\)
    print\(\"\=\" \* 80\)

    keywords \= \[
        \"ajax\"\,
        \"fetch\(\"\,
        \"\$\.get\"\,
        \"\$\.post\"\,
        \"\$\.ajax\"\,
        \"axios\"\,
        \"api\"\,
        \"json\"\,
        \"csv\"\,
        \"historical\"\,
        \"history\"\,
        \"etf\"\,
        \"download\"\,
        \"datatable\"\,
    \]

    contexts\: list\[str\] \= \[\]

    for js\_url\, text in javascript\:
        lower \= text\.lower\(\)

        for keyword in keywords\:
            start \= 0

            while True\:
                position \= lower\.find\(keyword\.lower\(\)\, start\)

                if position \< 0\:
                    break

                left \= max\(0\, position \- 250\)
                right \= min\(len\(text\)\, position \+ 500\)

                context \= text\[left\:right\]

                marker \= \(
                    f\"SOURCE\: \{js\_url\}\\n\"
                    f\"KEYWORD\: \{keyword\}\\n\"
                    f\"\{context\}\"
                \)

                contexts\.append\(marker\)

                start \= position \+ len\(keyword\)

                if len\(contexts\) \>\= 100\:
                    break

            if len\(contexts\) \>\= 100\:
                break

        if len\(contexts\) \>\= 100\:
            break

    print\(f\"request\_contexts\_found\: \{len\(contexts\)\}\"\)

    for index\, context in enumerate\(contexts\[\:30\]\, start\=1\)\:
        print\(\)
        print\(f\"\-\-\- REQUEST CONTEXT \{index\} \-\-\-\"\)
        print\(context\)

    return contexts


def parse\_json\_text\(text\: str\)\:
    cleaned \= text\.strip\(\)

    if not cleaned\:
        return None

    \# Normal JSON\.
    try\:
        return json\.loads\(cleaned\)
    except Exception\:
        pass

    \# JSONP\.
    jsonp\_match \= re\.match\(
        r\"\^\[A\-Za\-z\_\$\]\[A\-Za\-z0\-9\_\$\]\*\\s\*\\\(\(\.\*\)$end:math:text$\s*;?\s*$",
        cleaned,
        flags=re.DOTALL,
    )

    if jsonp_match:
        body = jsonp_match.group(1)

        try:
            return json.loads(body)
        except Exception:
            pass

    # Search for a JSON object or array embedded in text.
    object_match = re.search(
        r"(\{.*\}|$begin:math:display$\.\*$end:math:display$)",
        cleaned,
        flags=re.DOTALL,
    )

    if object_match:
        try:
            return json.loads(object_match.group(1))
        except Exception:
            pass

    return None


def flatten_rows(value, rows=None):
    if rows is None:
        rows = []

    if isinstance(value, list):
        if value and all(
            not isinstance(item, (dict, list))
            for item in value
        ):
            rows.append(value)
            return rows

        for item in value:
            flatten_rows(item, rows)

        return rows

    if isinstance(value, dict):
        for key, item in value.items():
            lower_key = str(key).lower()

            if lower_key in {
                "data",
                "aadata",
                "rows",
                "result",
                "results",
                "items",
                "records",
            }:
                flatten_rows(item, rows)
            else:
                flatten_rows(item, rows)

    return rows


def find_symbol_in_json(payload):
    rows = flatten_rows(payload)

    found = []

    for row in rows:
        if not isinstance(row, list):
            continue

        for value in row:
            if is_symbol(value):
                found.append(row)
                break

    return found


def find_symbol_in_text(text: str):
    if SYMBOL not in normalize(text):
        return []

    rows = []

    for line in text.splitlines():
        if SYMBOL in normalize(line):
            rows.append(line.strip())

    return rows


def parse_html_tables(text: str):
    """
    Lightweight HTML table parser.

    No external HTML parser dependency is required.
    """

    tables = re.findall(
        r"<table\b[^>]*>(.*?)</table>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    rows = []

    for table in tables:
        tr_list = re.findall(
            r"<tr\b[^>]*>(.*?)</tr>",
            table,
            flags=re.IGNORECASE | re.DOTALL,
        )

        for tr in tr_list:
            cells = re.findall(
                r"<t[dh]\b[^>]*>(.*?)</t[dh]>",
                tr,
                flags=re.IGNORECASE | re.DOTALL,
            )

            cleaned = []

            for cell in cells:
                cell = re.sub(
                    r"<[^>]+>",
                    " ",
                    cell,
                )
                cell = unescape(cell)
                cell = " ".join(cell.split())
                cleaned.append(cell)

            if cleaned:
                rows.append(cleaned)

    return rows


def parse_csv_text(text: str):
    rows = []

    try:
        reader = csv.reader(io.StringIO(text))

        for row in reader:
            if row:
                rows.append(row)

    except Exception:
        return []

    return rows


def find_symbol_in_rows(rows):
    found = []

    for row in rows:
        if not isinstance(row, list):
            continue

        for value in row:
            if is_symbol(value):
                found.append(row)
                break

    return found


def extract_numeric_fields(row):
    numeric = []

    for index, value in enumerate(row):
        text = normalize(value)

        if not text:
            continue

        if text in {
            "-",
            "--",
            "N/A",
            "NULL",
            "NA",
        }:
            continue

        cleaned = (
            text.replace(",", "")
            .replace(" ", "")
        )

        try:
            number = float(cleaned)
        except ValueError:
            continue

        numeric.append(
            (
                index,
                value,
                number,
            )
        )

    return numeric


def validate_found_row(row):
    print()
    print("=" * 80)
    print("FOUND 00838B")
    print("=" * 80)

    print("RAW ROW:")
    print(
        json.dumps(
            row,
            ensure_ascii=False,
            indent=2,
        )
    )

    print()
    print("ROW FIELDS:")

    for index, value in enumerate(row):
        print(
            f"  [{index:02d}] {value}"
        )

    numeric = extract_numeric_fields(row)

    print()
    print("NUMERIC FIELDS:")

    for index, value, number in numeric:
        print(
            f"  [{index:02d}] "
            f"value={value} "
            f"numeric={number}"
        )

    if not numeric:
        print()
        print("WARNING: no numeric fields detected")
        return False

    print()
    print("OK: 00838B row contains numeric data")

    return True


def test_candidate(
    url: str,
    test_date: date,
):
    """
    Test one discovered candidate endpoint.

    We intentionally send several common date parameter names
    because the page's JavaScript may use one of them.
    """

    print()
    print("=" * 80)
    print("TEST CANDIDATE ENDPOINT")
    print("=" * 80)

    print(f"URL: {url}")
    print(f"DATE: {test_date.isoformat()}")
    print(f"ROC DATE: {roc_date(test_date)}")

    date_variants = [
        {},
        {"d": roc_date(test_date)},
        {"date": roc_date(test_date)},
        {"date": test_date.isoformat()},
        {"startDate": roc_date(test_date)},
        {"queryDate": roc_date(test_date)},
        {"dataDate": roc_date(test_date)},
        {"tradeDate": roc_date(test_date)},
        {"date": test_date.strftime("%Y/%m/%d")},
        {"date": test_date.strftime("%Y-%m-%d")},
    ]

    seen_responses = set()

    for variant_index, params in enumerate(
        date_variants,
        start=1,
    ):
        print()
        print(
            f"PARAM VARIANT [{variant_index:02d}]: "
            f"{params}"
        )

        response = safe_request(
            url,
            params=params,
        )

        if response is None:
            continue

        content_hash = hash(response.content)

        if content_hash in seen_responses:
            print("DUPLICATE RESPONSE: skip")
            continue

        seen_responses.add(content_hash)

        print(
            f"status_code: "
            f"{response.status_code}"
        )
        print(
            "content_type: "
            f"{response.headers.get('Content-Type', '')}"
        )
        print(
            f"content_length: "
            f"{len(response.content)}"
        )

        if response.status_code != 200:
            continue

        response.encoding = (
            response.apparent_encoding
            or "utf-8"
        )

        text = response.text

        payload = parse_json_text(text)

        if payload is not None:
            print("FORMAT: JSON / JSONP")

            found = find_symbol_in_json(payload)

            print(
                f"00838B matches: "
                f"{len(found)}"
            )

            if found:
                for row in found:
                    validate_found_row(row)

                return True

            continue

        html_rows = parse_html_tables(text)

        if html_rows:
            print("FORMAT: HTML TABLE")

            found = find_symbol_in_rows(
                html_rows
            )

            print(
                f"00838B matches: "
                f"{len(found)}"
            )

            if found:
                for row in found:
                    validate_found_row(row)

                return True

        csv_rows = parse_csv_text(text)

        if csv_rows:
            found = find_symbol_in_rows(
                csv_rows
            )

            if found:
                print("FORMAT: CSV")

                for row in found:
                    validate_found_row(row)

                return True

        text_matches = find_symbol_in_text(text)

        if text_matches:
            print("FORMAT: TEXT")

            for line in text_matches[:10]:
                print(f"  {line}")

            print()
            print(
                "WARNING: symbol found in raw text "
                "but structured row parsing failed."
            )

            return True

    return False


def build_fallback_candidates():
    """
    Conservative official TPEx candidates.

    These are only diagnostics.
    No production code is changed.

    The important candidate is the new official ETF
    historical page itself; the script first attempts to
    discover its backend dynamically.
    """

    candidates = [
        PAGE_URL,
        LEGACY_PAGE_URL,
        "https://www.tpex.org.tw/",
        "https://www.tpex.org.tw/openapi/",
        "https://www.tpex.org.tw/openapi/swagger.json",
    ]

    return candidates


def main():
    print("=" * 80)
    print("00838B TPEx ETF OFFICIAL PRICE DIAGNOSTIC V4")
    print("=" * 80)

    print(f"TEST SYMBOL: {SYMBOL}")
    print("SOURCE: TPEx OFFICIAL")
    print("DATA TYPE: ETF HISTORICAL PRICE")
    print()
    print("Yahoo: NO")
    print("Universe: NO")
    print("Production pipeline: NO")
    print("fetch_prices.py: NOT MODIFIED")
    print()
    print("IMPORTANT:")
    print(
        "The ETF historical page is HTML. "
        "This script does not assume the page itself "
        "is the JSON data API."
    )

    html = fetch_page()

    if html is None:
        print()
        print("=" * 80)
        print("FINAL RESULT")
        print("=" * 80)
        print("FAILED: unable to fetch TPEx ETF page")
        return 1

    script_urls = extract_script_urls(html)

    inline_scripts = extract_inline_javascript(html)

    print()
    print(
        f"inline_javascript_blocks: "
        f"{len(inline_scripts)}"
    )

    javascript = fetch_javascript(script_urls)

    for index, inline in enumerate(
        inline_scripts,
        start=1,
    ):
        javascript.append(
            (
                f"{PAGE_URL}#inline-{index}",
                inline,
            )
        )

    candidate_urls = discover_candidate_urls(
        html,
        javascript,
    )

    extract_request_contexts(javascript)

    for fallback in build_fallback_candidates():
        if fallback not in candidate_urls:
            candidate_urls.append(fallback)

    print()
    print("=" * 80)
    print("STEP 6 - CANDIDATE ENDPOINT TEST")
    print("=" * 80)

    print(
        f"total_candidates_to_test: "
        f"{len(candidate_urls)}"
    )

    start_date = date(
        2026,
        8,
        28,
    )

    success_dates = []

    for offset in range(10):
        test_date = (
            start_date
            - timedelta(days=offset)
        )

        print()
        print("#" * 80)
        print(
            f"DATE ROUND: "
            f"{test_date.isoformat()}"
        )
        print("#" * 80)

        for candidate in candidate_urls:
            # Do not test obvious page URLs as if they were
            # APIs unless they contain a useful data hint.
            lower = candidate.lower()

            useful = any(
                token in lower
                for token in (
                    "api",
                    "ajax",
                    "json",
                    "csv",
                    "download",
                    "historical",
                    "history",
                    "etf",
                    "swagger",
                )
            )

            if not useful:
                continue

            try:
                ok = test_candidate(
                    candidate,
                    test_date,
                )
            except Exception as exc:
                print(
                    f"TEST ERROR: {exc}"
                )
                ok = False

            if ok:
                success_dates.append(
                    test_date.isoformat()
                )

                print()
                print(
                    "SUCCESS: candidate endpoint "
                    "returned 00838B"
                )

                # One confirmed date is sufficient
                # for this diagnostic.
                break

        if success_dates:
            break

    print()
    print("=" * 80)
    print("FINAL RESULT")
    print("=" * 80)

    print(
        f"HTTP / endpoint test dates: "
        f"10"
    )

    print(
        f"Found {SYMBOL}: "
        f"{len(success_dates)} dates"
    )

    if success_dates:
        print()
        print("SUCCESS")

        for item in success_dates:
            print(f"  {item}")

        print()
        print(
            f"Confirmed TPEx official ETF data "
            f"can return {SYMBOL}."
        )

        print()
        print(
            "NEXT STEP:"
        )
        print(
            "Inspect the confirmed endpoint and "
            "then modify production fetch_prices.py."
        )

        return 0

    print()
    print(
        f"FAILED: {SYMBOL} was not found."
    )

    print()
    print("IMPORTANT DIAGNOSTIC RESULT:")
    print(
        "The old etf_statistics.php URL is an HTML page, "
        "not a JSON API."
    )

    print(
        "The script therefore searched the official page "
        "and its JavaScript for backend data endpoints."
    )

    print()
    print(
        "Production fetch_prices.py was NOT modified."
    )

    return 1


if __name__ == "__main__":
    sys.exit(main())
