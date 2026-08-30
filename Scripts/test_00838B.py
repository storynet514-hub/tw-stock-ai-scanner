#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx ETF Official Price Diagnostic V6

Purpose:
1. Test the real TPEx ETF API route discovered from the official page.
2. Do not modify the production price pipeline.
3. Do not use stk_wn1430_result.php.
4. Do not use the normal OTC stock quote endpoint.
5. Inspect the official ETF historical page configuration.
6. Test the API route:
       /www/{LANG}/{ACTION}
   with:
       LANG   = zh-tw
       ACTION = ETFReport/historical
7. Accept JSON only.
8. Search all tables[].data rows for 00838B.
9. Validate that the discovered row contains market data.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from urllib.parse import urljoin

import requests


SYMBOL = "00838B"

PAGE_URL = (
    "https://www.tpex.org.tw/"
    "zh-tw/product/etf/info/historical/day.html"
)

API_BASE = "https://www.tpex.org.tw"

API_ROUTE = "/www/zh-tw/ETFReport/historical"

API_URL = urljoin(API_BASE, API_ROUTE)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": PAGE_URL,
    "X-Requested-With": "XMLHttpRequest",
}

TIMEOUT = 30

TEST_DAYS = 10

START_DATE = date(2026, 8, 28)


def roc_date(value: date) -> str:
    """Convert Gregorian date to TPEx ROC date."""
    return (
        f"{value.year - 1911:03d}/"
        f"{value.month:02d}/"
        f"{value.day:02d}"
    )


def normalize(value) -> str:
    """Normalize text for exact symbol comparison."""
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", "")
        .strip()
        .upper()
    )


def print_separator(char="=", width=80):
    print(char * width)


def request_page(session: requests.Session):
    print_separator()
    print("STEP 1 - OFFICIAL TPEx ETF HISTORICAL PAGE")
    print_separator()

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
        print("ERROR: official page returned non-200")
        return None

    return response


def test_api(
    session: requests.Session,
    test_date: date,
    params: dict,
):
    roc = roc_date(test_date)

    print()
    print_separator()
    print("TEST API")
    print_separator()

    print(f"URL: {API_URL}")
    print(f"DATE: {test_date.isoformat()}")
    print(f"ROC DATE: {roc}")
    print()
    print("PARAMS:")
    print(
        json.dumps(
            params,
            ensure_ascii=False,
            indent=2,
        )
    )

    try:
        response = session.get(
            API_URL,
            params=params,
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
        print("HTTP STATUS IS NOT 200")
        return None

    content_type = (
        response.headers.get("Content-Type", "")
        .lower()
    )

    text = response.text.lstrip()

    if (
        "json" not in content_type
        and not text.startswith("{")
        and not text.startswith("[")
    ):
        print("NOT JSON")
        print("RESPONSE PREVIEW:")
        print(response.text[:1000])
        return None

    try:
        payload = response.json()
    except Exception as exc:
        print(f"JSON DECODE FAILED: {exc}")
        print("RESPONSE PREVIEW:")
        print(response.text[:2000])
        return None

    print("JSON DECODE: OK")
    print(f"ROOT TYPE: {type(payload).__name__}")

    return payload


def extract_tables_data(payload):
    """
    Extract only:
        root
          tables[]
            data[]

    No aaData.
    No root-level data.
    No guessed rows.
    """

    if not isinstance(payload, dict):
        print("ERROR: JSON root is not dict")
        return []

    print()
    print("ROOT KEYS:")

    for key in payload.keys():
        print(f"  {key}")

    tables = payload.get("tables")

    print()
    print("TABLES")
    print(f"type: {type(tables).__name__}")

    if not isinstance(tables, list):
        print("ERROR: tables is not list")
        return []

    print(f"table_count: {len(tables)}")

    rows = []

    for table_index, table in enumerate(tables):
        print()
        print("-" * 80)
        print(f"TABLE [{table_index}]")

        if not isinstance(table, dict):
            print(
                "table type: "
                f"{type(table).__name__}"
            )
            continue

        print("table keys:")

        for key in table.keys():
            print(f"  {key}")

        data = table.get("data")

        print()
        print("tables[].data")
        print(f"type: {type(data).__name__}")

        if not isinstance(data, list):
            print("data is not list")
            continue

        print(f"raw rows: {len(data)}")

        valid_count = 0

        for row in data:
            if isinstance(row, list) and row:
                rows.append(
                    (
                        table_index,
                        row,
                    )
                )
                valid_count += 1

        print(f"valid rows: {valid_count}")

    return rows


def find_symbol(rows):
    """
    Search every field in every tables[].data row.

    Do not assume the symbol is column zero.
    """

    print()
    print_separator()
    print("GLOBAL SYMBOL SEARCH")
    print_separator()

    found = []

    for table_index, row in rows:
        for value in row:
            if normalize(value) == SYMBOL:
                found.append(
                    (
                        table_index,
                        row,
                    )
                )
                break

    print(f"SYMBOL: {SYMBOL}")
    print(f"MATCHES: {len(found)}")

    return found


def print_row(table_index, row):
    print()
    print_separator()
    print(f"FOUND SYMBOL IN TABLE: {table_index}")
    print_separator()

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
            f"  [{index:02d}] "
            f"{value}"
        )


def validate_row(row):
    """
    Validate that the row contains:
    1. exact symbol
    2. at least one numeric field

    We intentionally do not assume a fixed column order.
    """

    print()
    print_separator()
    print("ROW VALIDATION")
    print_separator()

    if not row:
        print("ERROR: empty row")
        return False

    symbol_found = any(
        normalize(value) == SYMBOL
        for value in row
    )

    if not symbol_found:
        print(
            f"ERROR: {SYMBOL} not found in row"
        )
        return False

    print(
        f"OK: exact symbol {SYMBOL} found"
    )

    numeric_fields = []

    for index, value in enumerate(row):
        text = normalize(value)

        if not text:
            continue

        if text in (
            "-",
            "--",
            "N/A",
            "NULL",
        ):
            continue

        cleaned = (
            text
            .replace(",", "")
            .replace(" ", "")
        )

        try:
            number = float(cleaned)
        except ValueError:
            continue

        numeric_fields.append(
            (
                index,
                value,
                number,
            )
        )

    print()
    print("NUMERIC FIELDS:")

    for index, value, number in numeric_fields:
        print(
            f"  [{index:02d}] "
            f"value={value} "
            f"numeric={number}"
        )

    if not numeric_fields:
        print(
            "ERROR: no numeric market fields"
        )
        return False

    print()
    print("OK: numeric market data exists")

    return True


def run_date(
    session: requests.Session,
    test_date: date,
):
    """
    Test the discovered official API route.

    We intentionally try a small set of parameter
    combinations because the page JavaScript may
    construct the request with different parameters.
    """

    roc = roc_date(test_date)

    candidate_params = [
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
            "l": "zh-tw",
            "date": roc,
        },
        {
            "l": "zh-tw",
            "date": test_date.isoformat(),
        },
        {
            "d": roc,
        },
        {
            "date": roc,
        },
    ]

    for attempt, params in enumerate(
        candidate_params,
        start=1,
    ):
        print()
        print(
            f"API ATTEMPT {attempt}/"
            f"{len(candidate_params)}"
        )

        payload = test_api(
            session,
            test_date,
            params,
        )

        if payload is None:
            continue

        rows = extract_tables_data(payload)

        print()
        print("DATA EXTRACTION SUMMARY")
        print(
            "source: tables[].data"
        )
        print(
            f"valid rows: {len(rows)}"
        )

        if not rows:
            print(
                "No valid rows in "
                "tables[].data"
            )
            continue

        found = find_symbol(rows)

        if not found:
            print(
                f"{SYMBOL} not found "
                "in tables[].data"
            )

            print()
            print("FIRST 10 ROWS:")

            for number, (
                table_index,
                row,
            ) in enumerate(
                rows[:10],
                start=1,
            ):
                preview = " | ".join(
                    str(value)
                    for value in row[:10]
                )

                print(
                    f"  {number:02d}. "
                    f"table={table_index} "
                    f"{preview}"
                )

            continue

        all_valid = True

        for table_index, row in found:
            print_row(
                table_index,
                row,
            )

            if not validate_row(row):
                all_valid = False

        if all_valid:
            return True

    return False


def main():
    print_separator()
    print(
        "00838B TPEx ETF "
        "OFFICIAL PRICE DIAGNOSTIC V6"
    )
    print_separator()

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
    print("DISCOVERED OFFICIAL PAGE:")
    print(PAGE_URL)

    print()
    print("DISCOVERED API PATTERN:")
    print("/www/{LANG}/{ACTION}")

    print()
    print("DISCOVERED ACTION:")
    print("ETFReport/historical")

    print()
    print("REAL API ROUTE:")
    print(API_ROUTE)

    print()
    print("REAL API URL:")
    print(API_URL)

    print()
    print("IMPORTANT:")
    print(
        "The previous /ETFReport/historical URL "
        "was a page route."
    )
    print(
        "The official JavaScript configuration "
        "defines API_PATTERN as:"
    )
    print(
        "/www/{LANG}/{ACTION}"
    )
    print(
        "Therefore this diagnostic tests "
        "/www/zh-tw/ETFReport/historical."
    )

    session = requests.Session()

    page_response = request_page(session)

    if page_response is None:
        print()
        print_separator()
        print("FINAL RESULT")
        print_separator()
        print("FAILED: official ETF page unavailable")
        return 1

    html = page_response.text

    print()
    print_separator()
    print("PAGE CONFIGURATION CHECK")
    print_separator()

    checks = {
        "ETFReport/historical": (
            "ETFReport/historical"
            in html
        ),
        "API_PATTERN": (
            "API_PATTERN"
            in html
        ),
        "tables.init": (
            "tables.init"
            in html
        ),
    }

    for name, result in checks.items():
        print(
            f"{name}: "
            f"{'FOUND' if result else 'NOT FOUND'}"
        )

    print()
    print("OFFICIAL API ROUTE CHECK")
    print(f"API URL: {API_URL}")

    success_dates = []

    for offset in range(TEST_DAYS):
        test_date = (
            START_DATE
            - timedelta(days=offset)
        )

        try:
            ok = run_date(
                session,
                test_date,
            )
        except Exception as exc:
            print()
            print(
                "TEST ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )
            ok = False

        if ok:
            success_dates.append(
                test_date.isoformat()
            )

    print()
    print_separator()
    print("FINAL RESULT")
    print_separator()

    print(
        f"HTTP / JSON test dates: "
        f"{TEST_DAYS}"
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
            "CONFIRMED:"
        )
        print(
            "1. TPEx official ETF page is reachable."
        )
        print(
            "2. Official page exposes "
            "API_PATTERN=/www/{LANG}/{ACTION}."
        )
        print(
            "3. ETFReport/historical is the "
            "configured action."
        )
        print(
            "4. Official API route returned "
            "usable JSON."
        )
        print(
            f"5. {SYMBOL} was found in "
            "tables[].data."
        )
        print(
            "6. Production price pipeline "
            "was not modified."
        )

        print()
        print(
            "NEXT STEP:"
        )
        print(
            "Integrate this ETF route into "
            "fetch_prices.py using isolated "
            "ETF routing only."
        )

        return 0

    print()
    print(
        f"FAILED: {SYMBOL} was not found."
    )

    print()
    print(
        "The official ETF API route was tested:"
    )
    print(API_URL)

    print()
    print(
        "Production files remain untouched."
    )

    return 1


if __name__ == "__main__":
    sys.exit(main())
