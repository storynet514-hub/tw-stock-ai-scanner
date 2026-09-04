#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_market.py
============================================================

MARKET ENVIRONMENT V2.1

責任：
1. 讀取 Data/universe.json
2. 讀取 Data/prices/ 下 manifest-listed price shards
3. 建立市場 breadth / volume / new-high-new-low 統計
4. 抓取 TWSE TAIEX 現值
5. 抓取足夠 TAIEX 歷史資料計算 MA20 / RSI14 / ATR14
6. 抓取 TWSE T86 法人資料
7. 抓取 TPEx 三大法人資料
8. 建立 Data/market.json
9. 寫入後重新讀取並驗證

核心契約：
------------------------------------------------------------
- schema_version = market-v2.1
- market_status = open / closed
- conditions 固定 10 項且順序不可改
- 資料不足 = unavailable
- 不得以 0、False 或錯誤 fallback 掩蓋缺資料
- TAIEX 歷史少於 20 個交易日 -> FAIL
- TWSE T86 官方有資料但解析為 0 -> FAIL
- TAIEX change / change_pct 數學不一致 -> FAIL
- 不修改 Data/prices/
- 不修改 Data/universe.json
"""

from __future__ import annotations

import calendar
import json
import math
import re
import sys
import time

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

import requests


# ============================================================
# Paths
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

UNIVERSE_PATH = ROOT / "Data" / "universe.json"
PRICES_DIR = ROOT / "Data" / "prices"
PRICE_MANIFEST_PATH = PRICES_DIR / "manifest.json"
MARKET_PATH = ROOT / "Data" / "market.json"


# ============================================================
# Schema
# ============================================================

SCHEMA_VERSION = "market-v2.1"
PRICE_SCHEMA_VERSION = "prices-v14.0"

CONDITION_NAMES = [
    "TAIEX > MA20",
    "MA20 上升",
    "TAIEX RSI14 > 50",
    "上漲家數 / 下跌家數 >= 1",
    "站上 MA20 比例 >= 50%",
    "市場成交量 / 20日均量 >= 1",
    "外資買賣超 > 0",
    "投信買賣超 > 0",
    "20日新高 / 新低 >= 1",
    "TAIEX ATR14% <= 3%",
]


# ============================================================
# Official endpoints
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
)

TWSE_MI_5MINS_HIST_URL = (
    "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"
)

TWSE_MI_5MINS_HIST_RWD_URL = (
    "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
)

TWSE_T86_URL = (
    "https://www.twse.com.tw/rwd/zh/fund/T86"
)

TPEX_3INSTI_URL = (
    "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
)


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; tw-stock-ai-scanner/market-v2.1)"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
    }
)


def request_json(
    url: str,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 30,
    retries: int = 3,
) -> Any:
    """
    官方 JSON API。

    不做假資料 fallback。
    """

    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=timeout,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:
            last_error = exc

            if attempt < retries:
                print(
                    f"⚠️ API request failed "
                    f"{attempt}/{retries}: {url}"
                )
                time.sleep(attempt * 1.5)

    raise RuntimeError(
        f"Official API request failed: {url}"
    ) from last_error


# ============================================================
# Generic helpers
# ============================================================

def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    replacements = {
        "　": "",
        " ": "",
        "\t": "",
        "\r": "",
        "\n": "",
        ",": "",
        "，": "",
        "(": "",
        ")": "",
        "（": "",
        "）": "",
        "[": "",
        "]": "",
        "［": "",
        "］": "",
        "_": "",
        "-": "",
        "/": "",
        "／": "",
        ":": "",
        "：": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text.strip().lower()


def parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)

        if math.isfinite(number):
            return number

        return None

    text = str(value).strip()

    if not text:
        return None

    if text.lower() in {
        "-",
        "--",
        "---",
        "－",
        "—",
        "…",
        "...",
        "null",
        "none",
        "nan",
        "n/a",
        "na",
    }:
        return None

    negative = False

    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = (
        text
        .replace(",", "")
        .replace("，", "")
        .replace("%", "")
        .replace("％", "")
        .strip()
    )

    if not text:
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    if negative:
        number = -number

    if not math.isfinite(number):
        return None

    return number


def parse_date(value: Any) -> Optional[str]:
    """
    統一成 YYYY-MM-DD。

    支援：
    - YYYY-MM-DD
    - YYYY/MM/DD
    - YYYYMMDD
    - ROC YYYMMDD
    - ROC YYY/MM/DD
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace(".", "/")
        .replace("-", "/")
    )

    match = re.fullmatch(
        r"(\d{3,4})/(\d{1,2})/(\d{1,2})",
        text,
    )

    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        if year < 1911:
            year += 1911

        try:
            datetime(
                year,
                month,
                day,
            )
        except ValueError:
            return None

        return (
            f"{year:04d}-"
            f"{month:02d}-"
            f"{day:02d}"
        )

    digits = re.sub(
        r"\D",
        "",
        text,
    )

    if len(digits) == 8:
        year = int(digits[:4])
        month = int(digits[4:6])
        day = int(digits[6:8])

        if year < 1911:
            year += 1911

        try:
            datetime(
                year,
                month,
                day,
            )
        except ValueError:
            return None

        return (
            f"{year:04d}-"
            f"{month:02d}-"
            f"{day:02d}"
        )

    if len(digits) == 7:
        year = int(digits[:3]) + 1911
        month = int(digits[3:5])
        day = int(digits[5:7])

        try:
            datetime(
                year,
                month,
                day,
            )
        except ValueError:
            return None

        return (
            f"{year:04d}-"
            f"{month:02d}-"
            f"{day:02d}"
        )

    return None


def is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def clean_json(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None

        return value

    if isinstance(value, dict):
        return {
            key: clean_json(val)
            for key, val in value.items()
        }

    if isinstance(value, list):
        return [
            clean_json(item)
            for item in value
        ]

    return value


def first_value(
    row: dict[str, Any],
    keys: Iterable[str],
) -> Any:
    normalized = {
        normalize_text(key): value
        for key, value in row.items()
    }

    for key in keys:
        value = normalized.get(
            normalize_text(key)
        )

        if value is not None:
            return value

    return None


# ============================================================
# Universe
# ============================================================

def normalize_symbol(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    for suffix in (
        ".TW",
        ".TWO",
        ".TPEX",
    ):
        if text.endswith(suffix):
            text = text[:-len(suffix)]

    if not re.fullmatch(
        r"[0-9A-Z]{4,6}",
        text,
    ):
        return None

    return text


def is_excluded_instrument(
    item: dict[str, Any],
) -> bool:
    text_parts = []

    for key in (
        "type",
        "category",
        "security_type",
        "instrument_type",
        "market_type",
        "name",
    ):
        value = item.get(key)

        if value is not None:
            text_parts.append(str(value))

    text = normalize_text(
        " ".join(text_parts)
    )

    excluded = [
        "etf",
        "基金",
        "債券",
        "債",
        "etn",
        "權證",
        "認購",
        "認售",
        "reit",
        "受益證券",
        "存託憑證",
    ]

    return any(
        normalize_text(keyword) in text
        for keyword in excluded
    )


def load_universe() -> set[str]:
    if not UNIVERSE_PATH.exists():
        raise FileNotFoundError(
            f"Universe not found: {UNIVERSE_PATH}"
        )

    payload = json.loads(
        UNIVERSE_PATH.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(payload, dict):
        raise ValueError(
            "universe.json must be object"
        )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise ValueError(
            "universe.json stocks must be dict"
        )

    universe: set[str] = set()

    for key, item in stocks.items():
        if not isinstance(item, dict):
            continue

        if str(
            item.get("status", "")
        ).strip().lower() != "active":
            continue

        if is_excluded_instrument(item):
            continue

        symbol = normalize_symbol(
            item.get("symbol")
            or item.get("code")
            or item.get("stock_id")
            or key
        )

        if symbol:
            universe.add(symbol)

    if not universe:
        raise RuntimeError(
            "No active common stocks found"
        )

    print(
        "Active common-stock universe: "
        f"{len(universe)}"
    )

    return universe


# ============================================================
# Price shards
# ============================================================

DATE_KEYS = [
    "date",
    "Date",
    "trade_date",
    "TradeDate",
    "tradedate",
    "交易日期",
    "日期",
]

CLOSE_KEYS = [
    "close",
    "Close",
    "closing_price",
    "ClosingPrice",
    "closingprice",
    "收盤價",
    "收盤",
    "收盤價格",
    "成交價",
    "close_price",
]

VOLUME_KEYS = [
    "volume",
    "Volume",
    "成交量",
    "成交股數",
    "成交量(股)",
    "shares",
    "Shares",
    "total_volume",
    "TotalVolume",
]

SYMBOL_KEYS = [
    "symbol",
    "Symbol",
    "code",
    "Code",
    "stock_id",
    "stockId",
    "ticker",
    "Ticker",
    "證券代號",
    "股票代號",
]


def looks_like_price_row(
    value: Any,
) -> bool:
    if not isinstance(value, dict):
        return False

    date_value = first_value(
        value,
        DATE_KEYS,
    )

    close_value = first_value(
        value,
        CLOSE_KEYS,
    )

    return (
        parse_date(date_value) is not None
        and parse_number(close_value) is not None
    )


def extract_price_rows(
    payload: Any,
    inherited_symbol: Optional[str] = None,
) -> list[
    tuple[
        Optional[str],
        dict[str, Any],
    ]
]:
    """
    價格 shard 專用 parser。

    保留目前已驗證正常的 shard parsing。
    """

    result = []

    if isinstance(payload, list):
        for item in payload:
            result.extend(
                extract_price_rows(
                    item,
                    inherited_symbol,
                )
            )

        return result

    if not isinstance(payload, dict):
        return result

    if looks_like_price_row(payload):
        symbol = normalize_symbol(
            first_value(
                payload,
                SYMBOL_KEYS,
            )
        )

        result.append(
            (
                symbol or inherited_symbol,
                payload,
            )
        )

        return result

    wrappers = {
        "stocks",
        "data",
        "rows",
        "records",
        "result",
        "results",
        "items",
        "list",
        "prices",
        "history",
        "historical",
        "price_history",
        "dataRows",
    }

    for key, value in payload.items():
        if key in wrappers:
            result.extend(
                extract_price_rows(
                    value,
                    inherited_symbol,
                )
            )
            continue

        key_symbol = normalize_symbol(key)

        if key_symbol:
            result.extend(
                extract_price_rows(
                    value,
                    key_symbol,
                )
            )
            continue

        nested_symbol = None

        if isinstance(value, dict):
            nested_symbol = normalize_symbol(
                first_value(
                    value,
                    SYMBOL_KEYS,
                )
            )

        result.extend(
            extract_price_rows(
                value,
                nested_symbol
                or inherited_symbol,
            )
        )

    return result


def read_manifest() -> list[Path]:
    if not PRICE_MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"Price manifest not found: "
            f"{PRICE_MANIFEST_PATH}"
        )

    manifest = json.loads(
        PRICE_MANIFEST_PATH.read_text(
            encoding="utf-8"
        )
    )

    if isinstance(manifest, dict):
        entries = (
            manifest.get("files")
            or manifest.get("shards")
            or manifest.get("data")
        )
    else:
        entries = manifest

    if not isinstance(entries, list):
        raise ValueError(
            "prices manifest must contain "
            "a file list"
        )

    paths = []
    seen = set()

    for entry in entries:
        name = None

        if isinstance(entry, str):
            name = entry

        elif isinstance(entry, dict):
            name = (
                entry.get("file")
                or entry.get("path")
                or entry.get("filename")
                or entry.get("name")
            )

        if not name:
            continue

        path = Path(name)

        if not path.is_absolute():
            path = PRICES_DIR / path

        if not path.is_file():
            continue

        resolved = str(path.resolve())

        if resolved in seen:
            continue

        seen.add(resolved)
        paths.append(path)

    if not paths:
        raise RuntimeError(
            "No usable price shard files "
            "found from manifest"
        )

    return paths


def load_price_histories(
    universe: set[str],
) -> dict[str, list[dict[str, Any]]]:
    paths = read_manifest()

    merged: dict[
        str,
        dict[str, dict[str, Any]]
    ] = defaultdict(dict)

    raw_rows = 0
    valid_rows = 0
    valid_shards = 0
    malformed_shards = 0

    print(
        f"Price shards: {len(paths)} files"
    )

    for path in paths:
        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

        except Exception as exc:
            malformed_shards += 1

            raise RuntimeError(
                f"Cannot parse price shard: "
                f"{path}"
            ) from exc

        rows = extract_price_rows(
            payload
        )

        if rows:
            valid_shards += 1

        for raw_symbol, row in rows:
            raw_rows += 1

            symbol = normalize_symbol(
                raw_symbol
            )

            if symbol not in universe:
                continue

            trade_date = parse_date(
                first_value(
                    row,
                    DATE_KEYS,
                )
            )

            close = parse_number(
                first_value(
                    row,
                    CLOSE_KEYS,
                )
            )

            volume = parse_number(
                first_value(
                    row,
                    VOLUME_KEYS,
                )
            )

            if (
                trade_date is None
                or close is None
                or close <= 0
            ):
                continue

            normalized = {
                "date": trade_date,
                "close": close,
            }

            if (
                volume is not None
                and volume >= 0
            ):
                normalized["volume"] = volume

            existing = merged[
                symbol
            ].get(trade_date)

            if existing is None:
                merged[
                    symbol
                ][trade_date] = normalized

            elif (
                "volume" not in existing
                and "volume" in normalized
            ):
                merged[
                    symbol
                ][trade_date] = normalized

            valid_rows += 1

    histories = {
        symbol: sorted(
            values.values(),
            key=lambda row: row["date"],
        )
        for symbol, values in merged.items()
    }

    print(
        f"manifest shards: {len(paths)}"
    )

    print(
        f"valid shards: {valid_shards}"
    )

    print(
        f"malformed shards: {malformed_shards}"
    )

    print(
        f"raw price rows: {raw_rows}"
    )

    print(
        f"valid rows: {valid_rows}"
    )

    print(
        "stock coverage: "
        f"{len(histories)}/{len(universe)}"
    )

    if set(histories) != universe:
        missing = sorted(
            universe - set(histories)
        )

        raise RuntimeError(
            "Price history coverage mismatch: "
            f"{len(histories)}/{len(universe)}; "
            f"missing={missing[:20]}"
        )

    return histories


# ============================================================
# Generic table parser
# ============================================================

def extract_table_rows(
    payload: Any,
) -> list[dict[str, Any]]:
    """
    通用官方表格 parser。

    支援：
    1. fields + data
    2. fields + rows
    3. tables[].fields + tables[].data
    4. list[dict]
    """

    output: list[dict[str, Any]] = []

    def add_table(
        fields: Any,
        rows: Any,
    ) -> None:
        if not isinstance(
            fields,
            list,
        ):
            return

        if not isinstance(
            rows,
            list,
        ):
            return

        for raw_row in rows:
            if isinstance(
                raw_row,
                dict,
            ):
                output.append(
                    raw_row
                )
                continue

            if not isinstance(
                raw_row,
                list,
            ):
                continue

            row = {}

            for index, field in enumerate(
                fields
            ):
                if index >= len(raw_row):
                    break

                row[str(field)] = (
                    raw_row[index]
                )

            if row:
                output.append(row)

    def walk(
        value: Any,
    ) -> None:
        if isinstance(value, list):
            if value and all(
                isinstance(
                    item,
                    dict,
                )
                for item in value
            ):
                output.extend(value)
                return

            for item in value:
                walk(item)

            return

        if not isinstance(
            value,
            dict,
        ):
            return

        fields = (
            value.get("fields")
            or value.get("columns")
        )

        rows = (
            value.get("data")
            or value.get("rows")
            or value.get("records")
        )

        if fields is not None and rows is not None:
            add_table(
                fields,
                rows,
            )

        tables = value.get("tables")

        if isinstance(
            tables,
            list,
        ):
            for table in tables:
                if not isinstance(
                    table,
                    dict,
                ):
                    continue

                table_fields = (
                    table.get("fields")
                    or table.get("columns")
                )

                table_rows = (
                    table.get("data")
                    or table.get("rows")
                    or table.get("records")
                )

                add_table(
                    table_fields,
                    table_rows,
                )

        for key in (
            "result",
            "results",
            "data",
            "rows",
            "records",
        ):
            nested = value.get(key)

            if isinstance(
                nested,
                (dict, list),
            ):
                walk(nested)

    walk(payload)

    # 去除完全重複 row
    unique = []
    seen = set()

    for row in output:
        try:
            marker = json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            )
        except Exception:
            marker = repr(row)

        if marker in seen:
            continue

        seen.add(marker)
        unique.append(row)

    return unique


# ============================================================
# TAIEX current index
# ============================================================

def fetch_current_index() -> dict[str, Any]:
    payload = request_json(
        TWSE_MI_INDEX_URL,
        timeout=30,
        retries=3,
    )

    if not isinstance(
        payload,
        list,
    ):
        raise RuntimeError(
            "TWSE MI_INDEX unexpected response"
        )

    target = None

    for row in payload:
        if not isinstance(
            row,
            dict,
        ):
            continue

        index_name = str(
            row.get("指數", "")
        ).strip()

        normalized = normalize_text(
            index_name
        )

        if (
            "發行量加權股價指數"
            in index_name
            or "發行量加權"
            in index_name
            or normalized == "taiex"
        ):
            target = row
            break

    if target is None:
        raise RuntimeError(
            "TWSE MI_INDEX TAIEX row not found"
        )

    trade_date = parse_date(
        first_value(
            target,
            [
                "日期",
                "Date",
            ],
        )
    )

    value = parse_number(
        first_value(
            target,
            [
                "收盤指數",
                "ClosingIndex",
            ],
        )
    )

    change_points = parse_number(
        first_value(
            target,
            [
                "漲跌點數",
                "ChangePoints",
            ],
        )
    )

    change_pct = parse_number(
        first_value(
            target,
            [
                "漲跌百分比",
                "ChangePercent",
            ],
        )
    )

    change_sign = str(
        first_value(
            target,
            [
                "漲跌",
                "Change",
            ]
        )
        or ""
    ).strip()

    if change_points is not None:
        if change_sign == "-":
            change_points = -abs(
                change_points
            )

        elif change_sign == "+":
            change_points = abs(
                change_points
            )

    if value is None:
        raise RuntimeError(
            "TAIEX closing index invalid"
        )

    if (
        change_points is not None
        and change_pct is not None
    ):
        implied_pct = (
            change_points
            / (value - change_points)
            * 100
            if value != change_points
            else None
        )

        if implied_pct is not None:
            if abs(
                implied_pct - change_pct
            ) > 0.05:
                raise RuntimeError(
                    "MI_INDEX change consistency "
                    "failure: "
                    f"value={value}, "
                    f"change={change_points}, "
                    f"reported_pct={change_pct}, "
                    f"implied_pct="
                    f"{implied_pct:.4f}"
                )

    return {
        "name": "TAIEX",
        "value": value,
        "change": change_points,
        "change_pct": change_pct,
        "date": trade_date,
    }


# ============================================================
# TAIEX historical data
# ============================================================

def month_sequence(
    latest_date: str,
    months: int = 6,
) -> list[tuple[int, int]]:
    dt = datetime.strptime(
        latest_date,
        "%Y-%m-%d",
    )

    year = dt.year
    month = dt.month

    result = []

    for _ in range(months):
        result.append(
            (year, month)
        )

        month -= 1

        if month == 0:
            month = 12
            year -= 1

    return result


def parse_index_row(
    row: dict[str, Any],
) -> Optional[dict[str, Any]]:
    trade_date = parse_date(
        first_value(
            row,
            [
                "Date",
                "date",
                "日期",
                "交易日期",
            ],
        )
    )

    close = parse_number(
        first_value(
            row,
            [
                "ClosingIndex",
                "closing_index",
                "收盤指數",
                "收盤",
            ],
        )
    )

    if (
        trade_date is None
        or close is None
        or close <= 0
    ):
        return None

    return {
        "date": trade_date,
        "open": parse_number(
            first_value(
                row,
                [
                    "OpeningIndex",
                    "opening_index",
                    "開盤指數",
                    "開盤",
                ],
            )
        ),
        "high": parse_number(
            first_value(
                row,
                [
                    "HighestIndex",
                    "highest_index",
                    "最高指數",
                    "最高",
                ],
            )
        ),
        "low": parse_number(
            first_value(
                row,
                [
                    "LowestIndex",
                    "lowest_index",
                    "最低指數",
                    "最低",
                ],
            )
        ),
        "close": close,
    }


def extract_index_history_rows(
    payload: Any,
) -> list[dict[str, Any]]:
    rows = []

    def process(
        row: dict[str, Any],
    ) -> None:
        parsed = parse_index_row(
            row
        )

        if parsed:
            rows.append(parsed)

    def walk(
        value: Any,
    ) -> None:
        if isinstance(
            value,
            list,
        ):
            for item in value:
                if isinstance(
                    item,
                    dict,
                ):
                    process(item)
                else:
                    walk(item)

            return

        if not isinstance(
            value,
            dict,
        ):
            return

        fields = (
            value.get("fields")
            or value.get("columns")
        )

        data = (
            value.get("data")
            or value.get("rows")
        )

        if (
            isinstance(fields, list)
            and isinstance(data, list)
        ):
            for raw_row in data:
                if isinstance(
                    raw_row,
                    list,
                ):
                    process(
                        dict(
                            zip(
                                fields,
                                raw_row,
                            )
                        )
                    )

                elif isinstance(
                    raw_row,
                    dict,
                ):
                    process(raw_row)

        tables = value.get(
            "tables"
        )

        if isinstance(
            tables,
            list,
        ):
            for table in tables:
                if not isinstance(
                    table,
                    dict,
                ):
                    continue

                table_fields = (
                    table.get("fields")
                    or table.get("columns")
                )

                table_data = (
                    table.get("data")
                    or table.get("rows")
                )

                if (
                    isinstance(
                        table_fields,
                        list,
                    )
                    and isinstance(
                        table_data,
                        list,
                    )
                ):
                    for raw_row in table_data:
                        if isinstance(
                            raw_row,
                            list,
                        ):
                            process(
                                dict(
                                    zip(
                                        table_fields,
                                        raw_row,
                                    )
                                )
                            )

                        elif isinstance(
                            raw_row,
                            dict,
                        ):
                            process(raw_row)

        for key in (
            "result",
            "results",
            "data",
            "rows",
            "records",
            "items",
        ):
            nested = value.get(key)

            if isinstance(
                nested,
                (dict, list),
            ):
                walk(nested)

    walk(payload)

    unique = {}

    for row in rows:
        unique[
            row["date"]
        ] = row

    return sorted(
        unique.values(),
        key=lambda row: row["date"],
    )


def fetch_taiex_history(
    latest_date: str,
) -> list[dict[str, Any]]:
    """
    官方 TAIEX 歷史。

    策略：
    1. 先抓官方 RWD 月資料
    2. 再抓 OpenAPI
    3. 去重
    4. 只保留 <= latest trading date
    5. 至少 20 個交易日才允許進入計算
    """

    collected = {}

    # --------------------------------------------------------
    # 1. RWD monthly historical
    # --------------------------------------------------------

    for year, month in month_sequence(
        latest_date,
        months=6,
    ):
        date_arg = (
            f"{year:04d}"
            f"{month:02d}"
            "01"
        )

        try:
            payload = request_json(
                TWSE_MI_5MINS_HIST_RWD_URL,
                params={
                    "response": "json",
                    "date": date_arg,
                },
                timeout=30,
                retries=2,
            )

            rows = extract_index_history_rows(
                payload
            )

            for row in rows:
                if row["date"] <= latest_date:
                    collected[
                        row["date"]
                    ] = row

        except Exception as exc:
            print(
                "⚠️ TAIEX RWD history "
                f"{date_arg} failed: {exc}"
            )

    # --------------------------------------------------------
    # 2. OpenAPI
    # --------------------------------------------------------

    try:
        payload = request_json(
            TWSE_MI_5MINS_HIST_URL,
            timeout=30,
            retries=3,
        )

        rows = extract_index_history_rows(
            payload
        )

        for row in rows:
            if row["date"] <= latest_date:
                collected[
                    row["date"]
                ] = row

    except Exception as exc:
        print(
            "⚠️ TAIEX OpenAPI history failed: "
            f"{exc}"
        )

    history = sorted(
        collected.values(),
        key=lambda row: row["date"],
    )

    print(
        "TAIEX history: "
        f"{len(history)} trading days"
    )

    return history


# ============================================================
# TAIEX indicators
# ============================================================

def moving_average(
    values: list[float],
    period: int,
) -> Optional[float]:
    if len(values) < period:
        return None

    return (
        sum(values[-period:])
        / period
    )


def rsi_wilder(
    values: list[float],
    period: int = 14,
) -> Optional[float]:
    if len(values) < period + 1:
        return None

    deltas = [
        current - previous
        for previous, current
        in zip(
            values[:-1],
            values[1:],
        )
    ]

    gains = [
        max(delta, 0.0)
        for delta in deltas
    ]

    losses = [
        max(-delta, 0.0)
        for delta in deltas
    ]

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for index in range(
        period,
        len(deltas),
    ):
        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[index]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[index]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return (
        100.0
        - (
            100.0
            / (1.0 + rs)
        )
    )


def atr_percent(
    history: list[dict[str, Any]],
    period: int = 14,
) -> Optional[float]:
    """
    ATR14%。

    不使用 fake high / low。
    缺 OHLC 就 unavailable。
    """

    if len(history) < period + 1:
        return None

    true_ranges = []

    for previous, current in zip(
        history[:-1],
        history[1:],
    ):
        high = current.get("high")
        low = current.get("low")
        previous_close = previous.get(
            "close"
        )

        if (
            high is None
            or low is None
            or previous_close is None
        ):
            return None

        true_range = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            ),
        )

        true_ranges.append(
            true_range
        )

    if len(true_ranges) < period:
        return None

    atr = (
        sum(true_ranges[-period:])
        / period
    )

    current_close = history[-1][
        "close"
    ]

    if current_close <= 0:
        return None

    return (
        atr
        / current_close
        * 100
    )


# ============================================================
# Breadth / volume
# ============================================================

def market_stock_stats(
    histories: dict[
        str,
        list[dict[str, Any]]
    ],
    latest: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    coverage = 0
    stale = 0

    advance = 0
    decline = 0
    unchanged = 0

    above_ma20 = 0
    ma20_total = 0

    new_high = 0
    new_low = 0
    new_hilo_total = 0

    daily_volume = defaultdict(
        float
    )

    for history in histories.values():
        if not history:
            continue

        for row in history:
            volume = row.get(
                "volume"
            )

            if volume is not None:
                daily_volume[
                    row["date"]
                ] += volume

        if (
            history[-1]["date"]
            != latest
        ):
            stale += 1
            continue

        coverage += 1

        current = history[-1][
            "close"
        ]

        if len(history) >= 2:
            previous = history[-2][
                "close"
            ]

            if current > previous:
                advance += 1

            elif current < previous:
                decline += 1

            else:
                unchanged += 1

        closes = [
            row["close"]
            for row in history
        ]

        if len(closes) >= 20:
            ma20 = (
                sum(closes[-20:])
                / 20
            )

            ma20_total += 1

            if current > ma20:
                above_ma20 += 1

        if len(closes) >= 21:
            previous_20 = closes[
                -21:-1
            ]

            if current > max(
                previous_20
            ):
                new_high += 1

            if current < min(
                previous_20
            ):
                new_low += 1

            new_hilo_total += 1

    advance_decline_ratio = None

    if decline > 0:
        advance_decline_ratio = (
            advance
            / decline
        )

    above_ma20_pct = None

    if ma20_total > 0:
        above_ma20_pct = (
            above_ma20
            / ma20_total
            * 100
        )

    new_high_low_ratio = None

    if new_low > 0:
        new_high_low_ratio = (
            new_high
            / new_low
        )

    trading_dates = sorted(
        daily_volume.keys()
    )

    current_volume = daily_volume.get(
        latest,
        0.0,
    )

    previous_dates = [
        date
        for date in trading_dates
        if date < latest
    ][-20:]

    average_20d = None

    if previous_dates:
        average_20d = (
            sum(
                daily_volume[date]
                for date in previous_dates
            )
            / len(previous_dates)
        )

    volume_ratio = None

    if (
        average_20d is not None
        and average_20d > 0
    ):
        volume_ratio = (
            current_volume
            / average_20d
        )

    breadth = {
        "coverage": coverage,
        "stale_coverage": stale,
        "advance": advance,
        "decline": decline,
        "unchanged": unchanged,
        "advance_decline_ratio":
            advance_decline_ratio,
        "above_ma20": above_ma20,
        "ma20_total": ma20_total,
        "above_ma20_pct":
            above_ma20_pct,
        "new_high_20d": new_high,
        "new_low_20d": new_low,
        "new_high_low_ratio":
            new_high_low_ratio,
        "new_hilo_total":
            new_hilo_total,
    }

    volume = {
        "current": current_volume,
        "current_stock_count":
            coverage,
        "average_20d":
            average_20d,
        "ratio":
            volume_ratio,
    }

    return (
        breadth,
        volume,
    )


# ============================================================
# TWSE T86
# ============================================================

def find_field(
    fields: list[Any],
    aliases: list[str],
) -> Optional[int]:
    normalized_fields = [
        normalize_text(field)
        for field in fields
    ]

    normalized_aliases = [
        normalize_text(alias)
        for alias in aliases
    ]

    for alias in normalized_aliases:
        for index, field in enumerate(
            normalized_fields
        ):
            if field == alias:
                return index

    for alias in normalized_aliases:
        for index, field in enumerate(
            normalized_fields
        ):
            if alias in field:
                return index

    return None


def fetch_twse_t86(
    trading_date: str,
) -> dict[str, Any]:
    """
    TWSE T86。

    重要：
    不使用 price row parser。
    直接解析官方 fields + data。
    """

    date_arg = datetime.strptime(
        trading_date,
        "%Y-%m-%d",
    ).strftime("%Y%m%d")

    payload = request_json(
        TWSE_T86_URL,
        params={
            "date": date_arg,
            "selectType": "ALLBUT0999",
            "response": "json",
        },
        timeout=30,
        retries=3,
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "TWSE T86 unexpected response"
        )

    status = str(
        payload.get("stat", "")
    ).strip()

    if status != "OK":
        raise RuntimeError(
            "TWSE T86 unavailable: "
            f"{status or 'unknown status'}"
        )

    fields = payload.get(
        "fields"
    )

    data = payload.get(
        "data"
    )

    if not isinstance(
        fields,
        list,
    ):
        raise RuntimeError(
            "TWSE T86 fields missing"
        )

    if not isinstance(
        data,
        list,
    ):
        raise RuntimeError(
            "TWSE T86 data missing"
        )

    if not data:
        raise RuntimeError(
            "TWSE T86 returned no table rows"
        )

    code_index = find_field(
        fields,
        [
            "證券代號",
            "股票代號",
            "代號",
        ],
    )

    foreign_index = find_field(
        fields,
        [
            "外陸資買賣超股數(不含外資自營商)",
            "外陸資買賣超股數（不含外資自營商）",
        ],
    )

    trust_index = find_field(
        fields,
        [
            "投信買賣超股數",
            "投信買賣超",
        ],
    )

    if foreign_index is None:
        raise RuntimeError(
            "TWSE T86 missing foreign "
            "net field"
        )

    if trust_index is None:
        raise RuntimeError(
            "TWSE T86 missing investment "
            "trust field"
        )

    foreign = 0.0
    trust = 0.0
    valid_rows = 0

    for raw_row in data:
        if not isinstance(
            raw_row,
            list,
        ):
            continue

        max_index = max(
            foreign_index,
            trust_index,
        )

        if len(raw_row) <= max_index:
            continue

        code = None

        if code_index is not None:
            if code_index < len(
                raw_row
            ):
                code = str(
                    raw_row[code_index]
                ).strip()

        # 排除官方總計 / 非證券資料列
        if code:
            normalized_code = normalize_text(
                code
            )

            if normalized_code in {
                "合計",
                "總計",
                "total",
            }:
                continue

            if not re.fullmatch(
                r"[0-9A-Z]{4,6}",
                code.upper(),
            ):
                continue

        foreign_value = parse_number(
            raw_row[foreign_index]
        )

        trust_value = parse_number(
            raw_row[trust_index]
        )

        if foreign_value is None and trust_value is None:
            continue

        if foreign_value is not None:
            foreign += foreign_value

        if trust_value is not None:
            trust += trust_value

        valid_rows += 1

    if valid_rows == 0:
        raise RuntimeError(
            "TWSE T86 parsed zero "
            "valid rows"
        )

    return {
        "rows": valid_rows,
        "foreign": foreign,
        "trust": trust,
    }


# ============================================================
# TPEx institutional
# ============================================================

def fetch_tpex_institutional(
    trading_date: str,
) -> dict[str, Any]:
    payload = request_json(
        TPEX_3INSTI_URL,
        timeout=30,
        retries=3,
    )

    if not isinstance(
        payload,
        list,
    ):
        raise RuntimeError(
            "TPEx institutional "
            "unexpected response"
        )

    target_rows = []

    for row in payload:
        if not isinstance(
            row,
            dict,
        ):
            continue

        raw_date = first_value(
            row,
            [
                "date",
                "Date",
                "日期",
                "交易日期",
            ],
        )

        row_date = parse_date(
            raw_date
        )

        if row_date == trading_date:
            target_rows.append(
                row
            )

    if not target_rows:
        # API 可能本身只回傳最新交易日
        target_rows = [
            row
            for row in payload
            if isinstance(
                row,
                dict,
            )
        ]

    foreign = 0.0
    trust = 0.0
    valid_rows = 0

    for row in target_rows:
        foreign_value = parse_number(
            first_value(
                row,
                [
                    "Foreign_Investor_Net",
                    "ForeignInvestorNet",
                    "外資及陸資買賣超股數",
                    "外資買賣超",
                    "外資(不含自營商)買賣超",
                    "外資（不含自營商）買賣超",
                ],
            )
        )

        trust_value = parse_number(
            first_value(
                row,
                [
                    "Investment_Trust_Net",
                    "InvestmentTrustNet",
                    "投信買賣超股數",
                    "投信買賣超",
                ],
            )
        )

        if foreign_value is None and trust_value is None:
            continue

        if foreign_value is not None:
            foreign += foreign_value

        if trust_value is not None:
            trust += trust_value

        valid_rows += 1

    if valid_rows == 0:
        raise RuntimeError(
            "TPEx institutional parsed "
            "zero valid rows"
        )

    return {
        "rows": valid_rows,
        "foreign": foreign,
        "trust": trust,
    }


# ============================================================
# Conditions
# ============================================================

def make_condition(
    name: str,
    value: Optional[float],
    passed: Optional[bool],
) -> dict[str, Any]:
    if value is None:
        return {
            "name": name,
            "value": None,
            "pass": None,
            "status": "unavailable",
        }

    if passed is None:
        return {
            "name": name,
            "value": value,
            "pass": None,
            "status": "unavailable",
        }

    return {
        "name": name,
        "value": value,
        "pass": bool(passed),
        "status": (
            "pass"
            if passed
            else "fail"
        ),
    }


# ============================================================
# Market status
# ============================================================

def get_market_status() -> str:
    """
    台股正常交易時段：
    09:00 ~ 13:30。

    fetch_market 在盤後執行時為 closed。
    """

    now = datetime.now()

    if now.weekday() >= 5:
        return "closed"

    current_minutes = (
        now.hour * 60
        + now.minute
    )

    if (
        9 * 60
        <= current_minutes
        <= 13 * 60 + 30
    ):
        return "open"

    return "closed"


# ============================================================
# Validation
# ============================================================

def validate_no_nan(
    value: Any,
    path: str = "$",
) -> None:
    if isinstance(
        value,
        float,
    ):
        if not math.isfinite(value):
            raise RuntimeError(
                f"Invalid numeric value at {path}"
            )

        return

    if isinstance(
        value,
        dict,
    ):
        for key, child in value.items():
            validate_no_nan(
                child,
                f"{path}.{key}",
            )

        return

    if isinstance(
        value,
        list,
    ):
        for index, child in enumerate(
            value
        ):
            validate_no_nan(
                child,
                f"{path}[{index}]",
            )


def validate_market(
    data: dict[str, Any],
) -> None:
    required = {
        "schema_version",
        "generated_at",
        "market_status",
        "latest_trading_date",
        "index",
        "trend",
        "breadth",
        "volume",
        "institutional",
        "sentiment",
        "conditions",
        "source",
        "config",
    }

    missing = required - set(
        data.keys()
    )

    if missing:
        raise RuntimeError(
            "Missing root fields: "
            f"{sorted(missing)}"
        )

    if data["schema_version"] != (
        SCHEMA_VERSION
    ):
        raise RuntimeError(
            "schema_version mismatch"
        )

    if data["market_status"] not in {
        "open",
        "closed",
    }:
        raise RuntimeError(
            "market_status invalid"
        )

    index = data["index"]

    if not isinstance(
        index,
        dict,
    ):
        raise RuntimeError(
            "index must be object"
        )

    if not is_finite_number(
        index.get("value")
    ):
        raise RuntimeError(
            "TAIEX value invalid"
        )

    conditions = data[
        "conditions"
    ]

    if not isinstance(
        conditions,
        list,
    ):
        raise RuntimeError(
            "conditions must be list"
        )

    if len(conditions) != 10:
        raise RuntimeError(
            "condition count mismatch"
        )

    actual_names = [
        item.get("name")
        for item in conditions
    ]

    if actual_names != CONDITION_NAMES:
        raise RuntimeError(
            "condition names/order mismatch"
        )

    for index_number, condition in enumerate(
        conditions,
        start=1,
    ):
        status = condition.get(
            "status"
        )

        if status not in {
            "pass",
            "fail",
            "unavailable",
        }:
            raise RuntimeError(
                f"Condition {index_number} "
                "invalid status"
            )

        if status == "unavailable":
            if condition.get("pass") is not None:
                raise RuntimeError(
                    f"Condition {index_number} "
                    "unavailable but pass "
                    "is not None"
                )

        else:
            if not isinstance(
                condition.get("pass"),
                bool,
            ):
                raise RuntimeError(
                    f"Condition {index_number} "
                    "pass must be bool"
                )

            if not is_finite_number(
                condition.get("value")
            ):
                raise RuntimeError(
                    f"Condition {index_number} "
                    "value invalid"
                )

    sentiment = data[
        "sentiment"
    ]

    if sentiment.get(
        "total_conditions"
    ) != 10:
        raise RuntimeError(
            "sentiment total_conditions "
            "must be 10"
        )

    if sentiment.get(
        "level"
    ) not in {
        "偏多",
        "震盪",
        "偏弱",
        "資料不足",
    }:
        raise RuntimeError(
            "sentiment level invalid"
        )

    validate_no_nan(data)

    json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
    )


# ============================================================
# Main
# ============================================================

def main() -> None:
    print(
        "=" * 72
    )
    print(
        "FETCH MARKET V2.1"
    )
    print(
        "=" * 72
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    universe = load_universe()

    # --------------------------------------------------------
    # Price shards
    # --------------------------------------------------------

    histories = load_price_histories(
        universe
    )

    latest_trading_date = max(
        history[-1]["date"]
        for history in histories.values()
        if history
    )

    print(
        "latest trading date: "
        f"{latest_trading_date}"
    )

    # --------------------------------------------------------
    # Current TAIEX
    # --------------------------------------------------------

    index = fetch_current_index()

    if (
        index.get("date")
        and index["date"]
        != latest_trading_date
    ):
        print(
            "⚠️ TAIEX date differs from "
            "price latest date: "
            f"{index['date']} vs "
            f"{latest_trading_date}"
        )

    # --------------------------------------------------------
    # TAIEX history
    # --------------------------------------------------------

    taiex_history = fetch_taiex_history(
        latest_trading_date
    )

    # 絕對禁止 3 天資料繼續往下跑
    if len(taiex_history) < 20:
        raise RuntimeError(
            "TAIEX history insufficient: "
            f"{len(taiex_history)} trading days; "
            "need >=20"
        )

    closes = [
        row["close"]
        for row in taiex_history
    ]

    ma20 = moving_average(
        closes,
        20,
    )

    ma20_previous = None

    if len(closes) >= 21:
        ma20_previous = (
            sum(closes[-21:-1])
            / 20
        )

    rsi14 = rsi_wilder(
        closes,
        14,
    )

    atr14_pct = atr_percent(
        taiex_history,
        14,
    )

    # --------------------------------------------------------
    # Market breadth / volume
    # --------------------------------------------------------

    breadth, volume = (
        market_stock_stats(
            histories,
            latest_trading_date,
        )
    )

    # --------------------------------------------------------
    # TWSE T86
    # --------------------------------------------------------

    twse = None

    try:
        twse = fetch_twse_t86(
            latest_trading_date
        )

        print(
            "TWSE T86:"
        )

        print(
            f"  rows={twse['rows']}"
        )

        print(
            "  foreign="
            f"{twse['foreign']}"
        )

        print(
            "  trust="
            f"{twse['trust']}"
        )

    except Exception as exc:
        print(
            "TWSE T86 unavailable: "
            f"{exc}"
        )

    # --------------------------------------------------------
    # TPEx institutional
    # --------------------------------------------------------

    tpex = None

    try:
        tpex = (
            fetch_tpex_institutional(
                latest_trading_date
            )
        )

        print(
            "TPEx institutional:"
        )

        print(
            f"  rows={tpex['rows']}"
        )

        print(
            "  foreign="
            f"{tpex['foreign']}"
        )

        print(
            "  trust="
            f"{tpex['trust']}"
        )

    except Exception as exc:
        print(
            "TPEx institutional "
            f"unavailable: {exc}"
        )

    # --------------------------------------------------------
    # Institutional aggregation
    # --------------------------------------------------------

    foreign = None
    trust = None

    if twse is not None or tpex is not None:
        foreign = 0.0
        trust = 0.0

        if twse is not None:
            foreign += twse[
                "foreign"
            ]

            trust += twse[
                "trust"
            ]

        if tpex is not None:
            foreign += tpex[
                "foreign"
            ]

            trust += tpex[
                "trust"
            ]

    if twse is not None and tpex is not None:
        institutional_status = (
            "complete"
        )

    elif (
        twse is not None
        or tpex is not None
    ):
        institutional_status = (
            "partial"
        )

    else:
        institutional_status = (
            "unavailable"
        )

    # --------------------------------------------------------
    # Conditions
    # --------------------------------------------------------

    condition_1_value = None
    condition_1_pass = None

    if ma20 is not None:
        condition_1_value = (
            index["value"]
            / ma20
        )

        condition_1_pass = (
            index["value"]
            > ma20
        )

    condition_2_value = None
    condition_2_pass = None

    if (
        ma20 is not None
        and ma20_previous is not None
    ):
        condition_2_value = (
            ma20
            - ma20_previous
        )

        condition_2_pass = (
            ma20
            > ma20_previous
        )

    condition_3_value = rsi14
    condition_3_pass = (
        rsi14 > 50
        if rsi14 is not None
        else None
    )

    condition_4_value = (
        breadth[
            "advance_decline_ratio"
        ]
    )

    condition_4_pass = (
        condition_4_value >= 1
        if condition_4_value
        is not None
        else None
    )

    condition_5_value = (
        breadth[
            "above_ma20_pct"
        ]
    )

    condition_5_pass = (
        condition_5_value >= 50
        if condition_5_value
        is not None
        else None
    )

    condition_6_value = (
        volume["ratio"]
    )

    condition_6_pass = (
        condition_6_value >= 1
        if condition_6_value
        is not None
        else None
    )

    condition_7_value = foreign

    condition_7_pass = (
        foreign > 0
        if foreign is not None
        else None
    )

    condition_8_value = trust

    condition_8_pass = (
        trust > 0
        if trust is not None
        else None
    )

    condition_9_value = (
        breadth[
            "new_high_low_ratio"
        ]
    )

    condition_9_pass = (
        condition_9_value >= 1
        if condition_9_value
        is not None
        else None
    )

    condition_10_value = (
        atr14_pct
    )

    condition_10_pass = (
        atr14_pct <= 3
        if atr14_pct is not None
        else None
    )

    conditions = [
        make_condition(
            CONDITION_NAMES[0],
            condition_1_value,
            condition_1_pass,
        ),
        make_condition(
            CONDITION_NAMES[1],
            condition_2_value,
            condition_2_pass,
        ),
        make_condition(
            CONDITION_NAMES[2],
            condition_3_value,
            condition_3_pass,
        ),
        make_condition(
            CONDITION_NAMES[3],
            condition_4_value,
            condition_4_pass,
        ),
        make_condition(
            CONDITION_NAMES[4],
            condition_5_value,
            condition_5_pass,
        ),
        make_condition(
            CONDITION_NAMES[5],
            condition_6_value,
            condition_6_pass,
        ),
        make_condition(
            CONDITION_NAMES[6],
            condition_7_value,
            condition_7_pass,
        ),
        make_condition(
            CONDITION_NAMES[7],
            condition_8_value,
            condition_8_pass,
        ),
        make_condition(
            CONDITION_NAMES[8],
            condition_9_value,
            condition_9_pass,
        ),
        make_condition(
            CONDITION_NAMES[9],
            condition_10_value,
            condition_10_pass,
        ),
    ]

    # --------------------------------------------------------
    # Sentiment
    # --------------------------------------------------------

    valid_conditions = sum(
        condition["status"]
        != "unavailable"
        for condition in conditions
    )

    score = sum(
        condition["status"]
        == "pass"
        for condition in conditions
    )

    if valid_conditions < 6:
        sentiment_level = (
            "資料不足"
        )

    elif score >= 8:
        sentiment_level = (
            "偏多"
        )

    elif score >= 5:
        sentiment_level = (
            "震盪"
        )

    else:
        sentiment_level = (
            "偏弱"
        )

    # --------------------------------------------------------
    # Market status
    # --------------------------------------------------------

    market_status = (
        get_market_status()
    )

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    market = {
        "schema_version":
            SCHEMA_VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),

        "market_status":
            market_status,

        "latest_trading_date":
            latest_trading_date,

        "index": {
            "name":
                "TAIEX",

            "value":
                index["value"],

            "change":
                index["change"],

            "change_pct":
                index["change_pct"],
        },

        "trend": {
            "ma20":
                ma20,

            "ma20_previous":
                ma20_previous,

            "ma20_slope":
                (
                    ma20
                    - ma20_previous
                    if (
                        ma20 is not None
                        and ma20_previous
                        is not None
                    )
                    else None
                ),

            "rsi14":
                rsi14,

            "atr14_pct":
                atr14_pct,

            "history_days":
                len(taiex_history),
        },

        "breadth":
            breadth,

        "volume":
            volume,

        "institutional": {
            "foreign_net":
                foreign,

            "trust_net":
                trust,

            "twse_foreign_net":
                (
                    twse["foreign"]
                    if twse is not None
                    else None
                ),

            "tpex_foreign_net":
                (
                    tpex["foreign"]
                    if tpex is not None
                    else None
                ),

            "twse_trust_net":
                (
                    twse["trust"]
                    if twse is not None
                    else None
                ),

            "tpex_trust_net":
                (
                    tpex["trust"]
                    if tpex is not None
                    else None
                ),

            "foreign_status":
                institutional_status,

            "trust_status":
                institutional_status,

            "status":
                institutional_status,
        },

        "sentiment": {
            "level":
                sentiment_level,

            "score":
                score,

            "valid_conditions":
                valid_conditions,

            "total_conditions":
                10,
        },

        "conditions":
            conditions,

        "source": {
            "provider": [
                "TWSE",
                "TPEx",
            ],

            "twse": {
                "index":
                    TWSE_MI_INDEX_URL,

                "index_history":
                    TWSE_MI_5MINS_HIST_URL,

                "index_history_rwd":
                    TWSE_MI_5MINS_HIST_RWD_URL,

                "institutional":
                    TWSE_T86_URL,
            },

            "tpex": {
                "institutional":
                    TPEX_3INSTI_URL,
            },

            "price": {
                "local":
                    "Data/prices",

                "manifest":
                    "Data/prices/manifest.json",

                "schema":
                    PRICE_SCHEMA_VERSION,
            },
        },

        "config": {
            "condition_count":
                10,

            "rsi_period":
                14,

            "ma_period":
                20,

            "atr_period":
                14,

            "new_high_low_period":
                20,

            "volume_average_period":
                20,

            "sentiment": {
                "bullish_min_score":
                    8,

                "neutral_min_score":
                    5,

                "weak_max_score":
                    4,

                "minimum_valid_conditions":
                    6,
            },

            "price_coverage": {
                "universe_count":
                    len(universe),

                "history_count":
                    len(histories),

                "latest_date_exact":
                    breadth[
                        "coverage"
                    ],

                "stale":
                    breadth[
                        "stale_coverage"
                    ],
            },
        },
    }

    market = clean_json(
        market
    )

    # --------------------------------------------------------
    # Final validation
    # --------------------------------------------------------

    validate_market(
        market
    )

    # --------------------------------------------------------
    # Atomic write
    # --------------------------------------------------------

    MARKET_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = MARKET_PATH.with_suffix(
        ".tmp"
    )

    temp_path.write_text(
        json.dumps(
            market,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    temp_path.replace(
        MARKET_PATH
    )

    # --------------------------------------------------------
    # Read-back validation
    # --------------------------------------------------------

    written = json.loads(
        MARKET_PATH.read_text(
            encoding="utf-8"
        )
    )

    validate_market(
        written
    )

    # --------------------------------------------------------
    # Final log
    # --------------------------------------------------------

    print(
        "=" * 72
    )

    print(
        "VALIDATE MARKET.JSON V2.1 PASS"
    )

    print(
        f"Active common-stock universe: "
        f"{len(universe)}"
    )

    print(
        f"Price coverage: "
        f"{breadth['coverage']}/"
        f"{len(universe)}"
    )

    print(
        f"Stale coverage: "
        f"{breadth['stale_coverage']}"
    )

    print(
        f"TAIEX history: "
        f"{len(taiex_history)} "
        f"trading days"
    )

    print(
        f"TAIEX: "
        f"{index['value']}"
    )

    print(
        f"Change: "
        f"{index['change']}"
    )

    print(
        f"Change %: "
        f"{index['change_pct']}"
    )

    print(
        f"Market status: "
        f"{market_status}"
    )

    print(
        f"MA20: "
        f"{ma20}"
    )

    print(
        f"RSI14: "
        f"{rsi14}"
    )

    print(
        f"ATR14%: "
        f"{atr14_pct}"
    )

    print(
        f"Sentiment: "
        f"{sentiment_level}"
    )

    print(
        f"Score: "
        f"{score}/10"
    )

    print(
        f"Valid conditions: "
        f"{valid_conditions}/10"
    )

    print(
        f"market-v2.1 validation PASS"
    )

    print(
        f"Wrote: {MARKET_PATH}"
    )


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print(
            "Interrupted",
            file=sys.stderr,
        )
        sys.exit(130)

    except Exception as exc:
        print(
            "FETCH MARKET V2.1 FAIL: "
            f"{exc}",
            file=sys.stderr,
        )
        sys.exit(1)