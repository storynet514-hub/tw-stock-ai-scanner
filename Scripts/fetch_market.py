#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_market.py
============================================================

Market V2.1

責任：
1. 讀取 Data/universe.json
2. 讀取 Data/prices/ 下所有 manifest-listed price shards
3. 建立市場 breadth / volume / new-high-new-low 統計
4. 抓取 TWSE TAIEX
5. 抓取足夠 TAIEX 歷史資料計算 MA20 / RSI14 / ATR14
6. 抓取 TWSE T86 法人資料
7. 抓取 TPEx 三大法人資料
8. 建立 Data/market.json
9. 寫入後重新讀取並驗證

重要契約：
- schema_version = market-v2.1
- market_status = open / closed
- conditions 必須固定 10 項且順序不可改
- 資料不足 = unavailable，不得偽造 fail
- 不修改 Data/prices/
- 不修改 Data/universe.json
"""

from __future__ import annotations

import calendar
import json
import math
import os
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
        "Accept": "application/json,text/plain,*/*",
    }
)


def request_json(
    url: str,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 30,
    retries: int = 3,
) -> Any:
    """
    官方 API JSON request。

    不在這裡偽造資料。
    API 失敗經 retries 後直接拋出例外。
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
                sleep_seconds = attempt * 1.5
                print(
                    f"⚠️ API request failed "
                    f"{attempt}/{retries}: {url}"
                )
                time.sleep(sleep_seconds)

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
        "［": "",
        "］": "",
        "[": "",
        "]": "",
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

    if text in {
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
    }:
        return None

    negative = False

    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = (
        text.replace(",", "")
        .replace("，", "")
        .replace("%", "")
        .replace("％", "")
        .strip()
    )

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

    text = text.replace(".", "/").replace("-", "/")

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
            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )
        except Exception:
            return None

    digits = re.sub(r"\D", "", text)

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

            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )

        except ValueError:
            return None

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

            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )

        except ValueError:
            return None

    return None


def is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def clean_json(value: Any) -> Any:
    """
    JSON 輸出前清理 NaN / Infinity。
    """

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


# ============================================================
# Universe
# ============================================================

def is_excluded_instrument(item: dict[str, Any]) -> bool:
    """
    排除 ETF / 基金 / 債券 / ETN / 權證 / REIT 等。

    Universe 本身已經負責主要分類，
    這裡再做 defensive filtering。
    """

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

    text = normalize_text(" ".join(text_parts))

    excluded_keywords = [
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

    for keyword in excluded_keywords:
        if normalize_text(keyword) in text:
            return True

    return False


def is_stock(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False

    status = str(
        item.get("status", "")
    ).strip().lower()

    if status != "active":
        return False

    if is_excluded_instrument(item):
        return False

    return True


def normalize_symbol(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = text.upper()

    # 去除常見市場後綴
    for suffix in (
        ".TW",
        ".TWO",
        ".TPEX",
    ):
        if text.endswith(suffix):
            text = text[: -len(suffix)]

    # 台股代號主要為 4~6 碼英數
    if not re.fullmatch(
        r"[0-9A-Z]{4,6}",
        text,
    ):
        return None

    return text


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

    for key, raw_item in stocks.items():
        if not isinstance(raw_item, dict):
            continue

        if not is_stock(raw_item):
            continue

        symbol = normalize_symbol(
            raw_item.get("symbol")
            or raw_item.get("code")
            or raw_item.get("stock_id")
            or key
        )

        if symbol:
            universe.add(symbol)

    if not universe:
        raise RuntimeError(
            "No active common stocks found in universe.json"
        )

    print(
        f"✓ Active common-stock universe: "
        f"{len(universe)}"
    )

    return universe


# ============================================================
# Price shard parsing
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
) -> list[tuple[Optional[str], dict[str, Any]]]:
    """
    遞迴解析 price shard。

    支援：
    - {"stocks": {...}}
    - {"2330": [...]}
    - {"data": {"rows": [...]}}
    - {"result": {"prices": [...]}}
    - 單一股票 object
    - symbol/code/stock_id/ticker
    """

    results: list[
        tuple[Optional[str], dict[str, Any]]
    ] = []

    if isinstance(payload, list):
        for item in payload:
            results.extend(
                extract_price_rows(
                    item,
                    inherited_symbol,
                )
            )

        return results

    if not isinstance(payload, dict):
        return results

    # --------------------------------------------------------
    # 直接 row
    # --------------------------------------------------------

    if looks_like_price_row(payload):
        symbol = normalize_symbol(
            first_value(
                payload,
                SYMBOL_KEYS,
            )
        ) or inherited_symbol

        results.append(
            (
                symbol,
                payload,
            )
        )

        return results

    # --------------------------------------------------------
    # 優先處理 wrapper keys
    # --------------------------------------------------------

    wrapper_keys = [
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
    ]

    for key in wrapper_keys:
        if key not in payload:
            continue

        child = payload[key]

        results.extend(
            extract_price_rows(
                child,
                inherited_symbol,
            )
        )

    # --------------------------------------------------------
    # symbol map / nested objects
    # --------------------------------------------------------

    for key, value in payload.items():
        if key in wrapper_keys:
            continue

        symbol_from_key = normalize_symbol(key)

        if symbol_from_key:
            results.extend(
                extract_price_rows(
                    value,
                    symbol_from_key,
                )
            )
            continue

        # object 本身可能含 symbol
        nested_symbol = None

        if isinstance(value, dict):
            nested_symbol = normalize_symbol(
                first_value(
                    value,
                    SYMBOL_KEYS,
                )
            )

        results.extend(
            extract_price_rows(
                value,
                nested_symbol or inherited_symbol,
            )
        )

    return results


def normalize_price_row(
    symbol: Optional[str],
    row: dict[str, Any],
) -> Optional[dict[str, Any]]:
    if not symbol:
        return None

    date_value = first_value(
        row,
        DATE_KEYS,
    )

    close_value = first_value(
        row,
        CLOSE_KEYS,
    )

    volume_value = first_value(
        row,
        VOLUME_KEYS,
    )

    date = parse_date(date_value)
    close = parse_number(close_value)
    volume = parse_number(volume_value)

    if date is None:
        return None

    if close is None:
        return None

    if close <= 0:
        return None

    normalized: dict[str, Any] = {
        "date": date,
        "close": close,
    }

    if volume is not None and volume >= 0:
        normalized["volume"] = volume

    return normalized


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

    entries: Any

    if isinstance(manifest, dict):
        entries = (
            manifest.get("files")
            or manifest.get("shards")
            or manifest.get("data")
        )

        if entries is None:
            entries = []

    elif isinstance(manifest, list):
        entries = manifest

    else:
        raise ValueError(
            "prices manifest must be object or list"
        )

    paths: list[Path] = []

    for entry in entries:
        raw_name: Optional[str] = None

        if isinstance(entry, str):
            raw_name = entry

        elif isinstance(entry, dict):
            raw_name = (
                entry.get("file")
                or entry.get("path")
                or entry.get("filename")
                or entry.get("name")
            )

        if not raw_name:
            continue

        path = Path(raw_name)

        if not path.is_absolute():
            path = PRICES_DIR / path

        if path.exists() and path.is_file():
            paths.append(path)

    # 防止 manifest 中重複
    unique_paths: list[Path] = []
    seen: set[str] = set()

    for path in paths:
        key = str(path.resolve())

        if key in seen:
            continue

        seen.add(key)
        unique_paths.append(path)

    if not unique_paths:
        # Defensive fallback。
        # 不是用來掩蓋 manifest 問題，
        # 只是防止 manifest 結構異常時整個 market 流程無資料。
        fallback = sorted(
            PRICES_DIR.glob("prices_*.json")
        )

        if fallback:
            print(
                "⚠️ Manifest contains no usable files; "
                "using prices_*.json fallback"
            )

            unique_paths = fallback

    if not unique_paths:
        raise RuntimeError(
            "No usable price shard files found"
        )

    return unique_paths


def load_price_histories(
    universe: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """
    讀取所有 manifest-listed shards。

    關鍵：
    - 不覆蓋同股票歷史
    - 多 shard 合併
    - 日期去重
    - 只保留 universe 內股票
    """

    shard_paths = read_manifest()

    print(
        f"PRICE SHARDS: {len(shard_paths)} files"
    )

    merged: dict[
        str,
        dict[str, dict[str, Any]],
    ] = defaultdict(dict)

    valid_shards = 0
    malformed_shards = 0
    raw_rows = 0
    valid_rows = 0

    for index, path in enumerate(
        shard_paths,
        start=1,
    ):
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

        # ----------------------------------------------------
        # schema version
        # ----------------------------------------------------

        if isinstance(payload, dict):
            shard_schema = payload.get(
                "schema_version"
            )

            if (
                shard_schema is not None
                and shard_schema != PRICE_SCHEMA_VERSION
            ):
                print(
                    f"⚠️ Skip incompatible shard: "
                    f"{path.name} "
                    f"schema={shard_schema}"
                )
                continue

        rows = extract_price_rows(payload)

        if rows:
            valid_shards += 1

        for raw_symbol, raw_row in rows:
            raw_rows += 1

            symbol = normalize_symbol(
                raw_symbol
            )

            if not symbol:
                continue

            if symbol not in universe:
                continue

            normalized = normalize_price_row(
                symbol,
                raw_row,
            )

            if normalized is None:
                continue

            valid_rows += 1

            date = normalized["date"]

            existing = merged[symbol].get(
                date
            )

            # 同日期多來源時：
            # 保留有 volume 的版本；
            # 否則保留後者。
            if existing is None:
                merged[symbol][date] = normalized

            else:
                existing_has_volume = (
                    "volume" in existing
                    and existing["volume"] is not None
                )

                new_has_volume = (
                    "volume" in normalized
                    and normalized["volume"] is not None
                )

                if new_has_volume and not existing_has_volume:
                    merged[symbol][date] = normalized

                elif new_has_volume == existing_has_volume:
                    merged[symbol][date] = normalized

        if index % 25 == 0:
            print(
                f"  parsed {index}/"
                f"{len(shard_paths)} shards..."
            )

    output: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    for symbol, date_map in merged.items():
        history = sorted(
            date_map.values(),
            key=lambda row: row["date"],
        )

        if history:
            output[symbol] = history

    coverage = len(output)

    print(
        "PRICE SHARD RESULT:"
    )
    print(
        f"  manifest shards : {len(shard_paths)}"
    )
    print(
        f"  valid shards    : {valid_shards}"
    )
    print(
        f"  malformed shards: {malformed_shards}"
    )
    print(
        f"  raw price rows  : {raw_rows}"
    )
    print(
        f"  valid rows      : {valid_rows}"
    )
    print(
        f"  stock coverage  : {coverage}/"
        f"{len(universe)}"
    )

    if coverage < 1000:
        raise RuntimeError(
            "Price coverage critically low: "
            f"{coverage}/{len(universe)}"
        )

    return output


# ============================================================
# TAIEX
# ============================================================

def find_first_dict_with_key(
    payload: Any,
    candidate_keys: set[str],
) -> Optional[dict[str, Any]]:
    if isinstance(payload, dict):
        normalized_keys = {
            normalize_text(key)
            for key in payload.keys()
        }

        normalized_candidates = {
            normalize_text(key)
            for key in candidate_keys
        }

        if normalized_keys & normalized_candidates:
            return payload

        for value in payload.values():
            result = find_first_dict_with_key(
                value,
                candidate_keys,
            )

            if result is not None:
                return result

    elif isinstance(payload, list):
        for item in payload:
            result = find_first_dict_with_key(
                item,
                candidate_keys,
            )

            if result is not None:
                return result

    return None


def extract_index_row(
    payload: Any,
) -> Optional[dict[str, Any]]:
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue

            if (
                first_value(
                    row,
                    [
                        "指數",
                        "收盤指數",
                        "ClosingIndex",
                    ],
                )
                is not None
            ):
                return row

        return None

    if isinstance(payload, dict):
        candidate = find_first_dict_with_key(
            payload,
            {
                "指數",
                "收盤指數",
                "ClosingIndex",
            },
        )

        if candidate:
            return candidate

    return None


def fetch_taiex_current() -> dict[str, Any]:
    payload = request_json(
        TWSE_MI_INDEX_URL
    )

    row = extract_index_row(payload)

    if row is None:
        raise RuntimeError(
            "TWSE MI_INDEX returned no TAIEX row"
        )

    date = parse_date(
        first_value(
            row,
            [
                "日期",
                "Date",
            ],
        )
    )

    value = parse_number(
        first_value(
            row,
            [
                "收盤指數",
                "指數",
                "ClosingIndex",
                "close",
            ],
        )
    )

    change = parse_number(
        first_value(
            row,
            [
                "漲跌點數",
                "漲跌",
                "Change",
                "change",
            ],
        )
    )

    change_pct = parse_number(
        first_value(
            row,
            [
                "漲跌百分比",
                "漲跌%",
                "ChangePercent",
                "change_pct",
            ],
        )
    )

    if value is None:
        raise RuntimeError(
            "TWSE MI_INDEX has no valid TAIEX value"
        )

    if date is None:
        raise RuntimeError(
            "TWSE MI_INDEX has no valid date"
        )

    return {
        "date": date,
        "value": value,
        "change": change,
        "change_pct": change_pct,
    }


def month_starts(
    end_date: datetime,
    count: int = 4,
) -> list[str]:
    months: list[str] = []

    year = end_date.year
    month = end_date.month

    for _ in range(count):
        months.append(
            f"{year:04d}{month:02d}01"
        )

        month -= 1

        if month == 0:
            month = 12
            year -= 1

    return months


def extract_index_history_rows(
    payload: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue

            date = parse_date(
                first_value(
                    item,
                    [
                        "Date",
                        "日期",
                        "date",
                    ],
                )
            )

            close = parse_number(
                first_value(
                    item,
                    [
                        "ClosingIndex",
                        "收盤指數",
                        "close",
                    ],
                )
            )

            high = parse_number(
                first_value(
                    item,
                    [
                        "HighestIndex",
                        "最高指數",
                        "high",
                    ],
                )
            )

            low = parse_number(
                first_value(
                    item,
                    [
                        "LowestIndex",
                        "最低指數",
                        "low",
                    ],
                )
            )

            if date is None or close is None:
                continue

            rows.append(
                {
                    "date": date,
                    "close": close,
                    "high": (
                        high
                        if high is not None
                        else close
                    ),
                    "low": (
                        low
                        if low is not None
                        else close
                    ),
                }
            )

        return rows

    if isinstance(payload, dict):
        wrapper_keys = [
            "data",
            "rows",
            "records",
            "result",
            "results",
            "items",
        ]

        for key in wrapper_keys:
            if key in payload:
                rows.extend(
                    extract_index_history_rows(
                        payload[key]
                    )
                )

    return rows


def fetch_taiex_history(
    latest_date: str,
) -> list[dict[str, Any]]:
    latest_dt = datetime.strptime(
        latest_date,
        "%Y-%m-%d",
    )

    all_rows: dict[
        str,
        dict[str, Any],
    ] = {}

    requested_months = month_starts(
        latest_dt,
        count=4,
    )

    for month_start in requested_months:
        try:
            payload = request_json(
                TWSE_MI_5MINS_HIST_URL,
                params={
                    "date": month_start,
                },
            )

        except Exception as exc:
            print(
                f"⚠️ TAIEX history month failed "
                f"{month_start}: {exc}"
            )
            continue

        rows = extract_index_history_rows(
            payload
        )

        for row in rows:
            all_rows[row["date"]] = row

    history = sorted(
        all_rows.values(),
        key=lambda row: row["date"],
    )

    history = [
        row
        for row in history
        if row["date"] <= latest_date
    ]

    print(
        f"TAIEX history: "
        f"{len(history)} trading days"
    )

    return history


# ============================================================
# Technical calculations
# ============================================================

def calculate_rsi(
    closes: list[float],
    period: int = 14,
) -> Optional[float]:
    if len(closes) < period + 1:
        return None

    changes = [
        closes[i] - closes[i - 1]
        for i in range(1, len(closes))
    ]

    gains = [
        max(change, 0.0)
        for change in changes
    ]

    losses = [
        max(-change, 0.0)
        for change in changes
    ]

    if len(gains) < period:
        return None

    avg_gain = sum(
        gains[:period]
    ) / period

    avg_loss = sum(
        losses[:period]
    ) / period

    for i in range(
        period,
        len(gains),
    ):
        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        if avg_gain == 0:
            return 50.0

        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def calculate_atr_pct(
    history: list[dict[str, Any]],
    period: int = 14,
) -> Optional[float]:
    if len(history) < period + 1:
        return None

    true_ranges: list[float] = []

    for i in range(
        1,
        len(history),
    ):
        current = history[i]
        previous = history[i - 1]

        high = current["high"]
        low = current["low"]
        previous_close = previous["close"]

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None

    atr = sum(
        true_ranges[-period:]
    ) / period

    latest_close = history[-1]["close"]

    if latest_close <= 0:
        return None

    return atr / latest_close * 100.0


def calculate_index_metrics(
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    if not history:
        return {
            "ma20": None,
            "ma20_previous": None,
            "ma20_slope": None,
            "rsi14": None,
            "atr14_pct": None,
        }

    closes = [
        row["close"]
        for row in history
    ]

    ma20: Optional[float] = None
    ma20_previous: Optional[float] = None
    ma20_slope: Optional[float] = None

    if len(closes) >= 20:
        ma20 = sum(
            closes[-20:]
        ) / 20

    if len(closes) >= 21:
        ma20_previous = sum(
            closes[-21:-1]
        ) / 20

        if (
            ma20 is not None
            and ma20_previous is not None
        ):
            ma20_slope = (
                ma20
                - ma20_previous
            )

    rsi14 = calculate_rsi(
        closes,
        period=14,
    )

    atr14_pct = calculate_atr_pct(
        history,
        period=14,
    )

    return {
        "ma20": ma20,
        "ma20_previous": ma20_previous,
        "ma20_slope": ma20_slope,
        "rsi14": rsi14,
        "atr14_pct": atr14_pct,
    }


# ============================================================
# Market breadth
# ============================================================

def latest_row_on_or_before(
    history: list[dict[str, Any]],
    target_date: str,
) -> Optional[dict[str, Any]]:
    candidate = None

    for row in history:
        row_date = row.get("date")

        if not row_date:
            continue

        if row_date > target_date:
            break

        candidate = row

    return candidate


def calculate_market_breadth(
    histories: dict[str, list[dict[str, Any]]],
    latest_date: str,
) -> dict[str, Any]:
    advance = 0
    decline = 0
    unchanged = 0

    above_ma20 = 0
    ma20_total = 0

    new_high_20d = 0
    new_low_20d = 0
    new_hilo_total = 0

    exact_coverage = 0
    stale_coverage = 0

    current_volume = 0.0
    current_volume_count = 0

    daily_volume: dict[
        str,
        float,
    ] = defaultdict(float)

    daily_volume_count: dict[
        str,
        int,
    ] = defaultdict(int)

    for symbol, history in histories.items():
        if not history:
            continue

        current = latest_row_on_or_before(
            history,
            latest_date,
        )

        if current is None:
            continue

        current_date = current["date"]

        if current_date == latest_date:
            exact_coverage += 1
        else:
            stale_coverage += 1

        # ----------------------------------------------------
        # Breadth only uses exact latest date.
        # ----------------------------------------------------

        if current_date != latest_date:
            continue

        current_close = current["close"]

        # ----------------------------------------------------
        # Up / down
        # ----------------------------------------------------

        previous = None

        for row in reversed(history):
            if row["date"] < latest_date:
                previous = row
                break

        if previous is not None:
            previous_close = previous["close"]

            if current_close > previous_close:
                advance += 1

            elif current_close < previous_close:
                decline += 1

            else:
                unchanged += 1

        # ----------------------------------------------------
        # MA20
        # ----------------------------------------------------

        prior_rows = [
            row
            for row in history
            if row["date"] <= latest_date
        ]

        if len(prior_rows) >= 20:
            last20 = prior_rows[-20:]

            ma20 = sum(
                row["close"]
                for row in last20
            ) / 20

            ma20_total += 1

            if current_close >= ma20:
                above_ma20 += 1

        # ----------------------------------------------------
        # 20-day new high / new low
        #
        # IMPORTANT:
        # current price compares against PREVIOUS 20
        # trading days, not a window containing itself.
        # ----------------------------------------------------

        if len(prior_rows) >= 21:
            previous20 = prior_rows[-21:-1]

            previous_closes = [
                row["close"]
                for row in previous20
            ]

            if previous_closes:
                previous_max = max(
                    previous_closes
                )

                previous_min = min(
                    previous_closes
                )

                new_hilo_total += 1

                if current_close >= previous_max:
                    new_high_20d += 1

                if current_close <= previous_min:
                    new_low_20d += 1

        # ----------------------------------------------------
        # Current volume
        # ----------------------------------------------------

        volume = current.get("volume")

        if (
            volume is not None
            and volume >= 0
        ):
            current_volume += volume
            current_volume_count += 1

        # ----------------------------------------------------
        # Historical daily market volume
        #
        # Aggregate ALL available stocks by date.
        # ----------------------------------------------------

        for row in history:
            row_date = row["date"]

            if row_date >= latest_date:
                continue

            volume_value = row.get(
                "volume"
            )

            if (
                volume_value is None
                or volume_value < 0
            ):
                continue

            daily_volume[row_date] += volume_value
            daily_volume_count[row_date] += 1

    # --------------------------------------------------------
    # New high / new low ratio
    # --------------------------------------------------------

    if new_hilo_total == 0:
        high_low_ratio = None

    elif new_low_20d == 0:
        if new_high_20d > 0:
            # No mathematical finite ratio.
            # Condition evaluator handles this case directly.
            high_low_ratio = None
        else:
            high_low_ratio = None

    else:
        high_low_ratio = (
            new_high_20d
            / new_low_20d
        )

    # --------------------------------------------------------
    # Volume ratio
    # --------------------------------------------------------

    previous_dates = sorted(
        [
            date
            for date in daily_volume.keys()
            if date < latest_date
        ]
    )[-20:]

    volume_20d_average: Optional[float] = None
    volume_ratio: Optional[float] = None

    if (
        len(previous_dates) >= 20
        and current_volume_count > 0
    ):
        totals = [
            daily_volume[date]
            for date in previous_dates
            if daily_volume_count.get(
                date,
                0,
            ) > 0
        ]

        if len(totals) >= 20:
            volume_20d_average = (
                sum(totals[-20:]) / 20
            )

            if volume_20d_average > 0:
                volume_ratio = (
                    current_volume
                    / volume_20d_average
                )

    coverage = exact_coverage

    if decline > 0:
        advance_decline_ratio = (
            advance / decline
        )
    elif advance > 0:
        advance_decline_ratio = None
    else:
        advance_decline_ratio = None

    if ma20_total > 0:
        above_ma20_pct = (
            above_ma20
            / ma20_total
            * 100.0
        )
    else:
        above_ma20_pct = None

    return {
        "coverage": coverage,
        "stale_coverage": stale_coverage,
        "advance": advance,
        "decline": decline,
        "unchanged": unchanged,
        "advance_decline_ratio": (
            advance_decline_ratio
        ),
        "above_ma20": above_ma20,
        "ma20_total": ma20_total,
        "above_ma20_pct": above_ma20_pct,
        "new_high_20d": new_high_20d,
        "new_low_20d": new_low_20d,
        "new_high_low_ratio": (
            high_low_ratio
        ),
        "new_hilo_total": new_hilo_total,
        "current_volume": current_volume,
        "current_volume_count": (
            current_volume_count
        ),
        "volume_20d_average": (
            volume_20d_average
        ),
        "volume_ratio": volume_ratio,
    }


# ============================================================
# Institutional - TWSE T86
# ============================================================

def table_rows_from_t86(
    payload: Any,
) -> list[dict[str, Any]]:
    """
    TWSE T86：
        tables[].fields
        tables[].data
    """

    rows: list[dict[str, Any]] = []

    if not isinstance(payload, dict):
        return rows

    tables = payload.get("tables")

    if not isinstance(tables, list):
        return rows

    for table in tables:
        if not isinstance(table, dict):
            continue

        fields = table.get("fields")
        data = table.get("data")

        if not isinstance(
            fields,
            list,
        ):
            continue

        if not isinstance(
            data,
            list,
        ):
            continue

        normalized_fields = [
            str(field).strip()
            for field in fields
        ]

        for raw_row in data:
            if not isinstance(
                raw_row,
                list,
            ):
                continue

            row: dict[str, Any] = {}

            for index, field in enumerate(
                normalized_fields
            ):
                if index >= len(raw_row):
                    break

                row[field] = raw_row[index]

            if row:
                rows.append(row)

    return rows


def field_value_by_contains(
    row: dict[str, Any],
    required_tokens: list[str],
    forbidden_tokens: Optional[
        list[str]
    ] = None,
) -> Optional[float]:
    forbidden_tokens = (
        forbidden_tokens or []
    )

    for key, value in row.items():
        normalized_key = normalize_text(key)

        if not all(
            normalize_text(token)
            in normalized_key
            for token in required_tokens
        ):
            continue

        if any(
            normalize_text(token)
            in normalized_key
            for token in forbidden_tokens
        ):
            continue

        number = parse_number(value)

        if number is not None:
            return number

    return None


def fetch_twse_institutional(
    latest_date: str,
) -> dict[str, Any]:
    date_text = latest_date.replace(
        "-",
        "",
    )

    payload = request_json(
        TWSE_T86_URL,
        params={
            "date": date_text,
            "selectType": "ALLBUT0999",
            "response": "json",
        },
    )

    rows = table_rows_from_t86(
        payload
    )

    if not rows:
        raise RuntimeError(
            "TWSE T86 returned no table rows"
        )

    foreign_main = 0.0
    foreign_main_count = 0

    foreign_dealer = 0.0
    foreign_dealer_count = 0

    trust = 0.0
    trust_count = 0

    for row in rows:
        foreign_main_value = (
            field_value_by_contains(
                row,
                [
                    "外陸資買賣超股數",
                ],
            )
        )

        # T86 field explicitly says excluding
        # foreign proprietary dealer.
        if foreign_main_value is not None:
            foreign_main += foreign_main_value
            foreign_main_count += 1

        dealer_value = (
            field_value_by_contains(
                row,
                [
                    "外資自營商買賣超股數",
                ],
            )
        )

        if dealer_value is not None:
            foreign_dealer += dealer_value
            foreign_dealer_count += 1

        trust_value = (
            field_value_by_contains(
                row,
                [
                    "投信買賣超股數",
                ],
            )
        )

        if trust_value is not None:
            trust += trust_value
            trust_count += 1

    if foreign_main_count == 0:
        foreign_main_value = None
    else:
        foreign_main_value = foreign_main

    if foreign_dealer_count == 0:
        foreign_dealer_value = None
    else:
        foreign_dealer_value = foreign_dealer

    if trust_count == 0:
        trust_value = None
    else:
        trust_value = trust

    print(
        "TWSE T86:"
    )
    print(
        f"  foreign(ex-dealer): "
        f"{foreign_main_value}"
    )
    print(
        f"  foreign dealer: "
        f"{foreign_dealer_value}"
    )
    print(
        f"  investment trust: "
        f"{trust_value}"
    )

    return {
        "foreign_net": foreign_main_value,
        "foreign_dealer_net": (
            foreign_dealer_value
        ),
        "trust_net": trust_value,
        "rows": len(rows),
        "status": "complete",
    }


# ============================================================
# Institutional - TPEx
# ============================================================

def recursive_dict_rows(
    payload: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                rows.append(item)
            else:
                rows.extend(
                    recursive_dict_rows(item)
                )

        return rows

    if isinstance(payload, dict):
        # If this object itself looks like a row,
        # retain it.
        if any(
            isinstance(value, (str, int, float))
            or value is None
            for value in payload.values()
        ):
            rows.append(payload)

        for value in payload.values():
            if isinstance(
                value,
                (dict, list),
            ):
                rows.extend(
                    recursive_dict_rows(value)
                )

    return rows


def find_tpex_field_value(
    row: dict[str, Any],
    kind: str,
) -> Optional[float]:
    for key, value in row.items():
        normalized_key = normalize_text(key)

        if kind == "foreign":
            # Preserve the existing TPEx semantic:
            # foreign investors excluding foreign dealers.
            if (
                (
                    "foreigninvestorsinclude"
                    in normalized_key
                    or "外資" in normalized_key
                )
                and (
                    "difference"
                    in normalized_key
                    or "買賣超"
                    in normalized_key
                )
                and (
                    "dealer"
                    not in normalized_key
                    and "自營商"
                    not in normalized_key
                )
            ):
                number = parse_number(value)

                if number is not None:
                    return number

        elif kind == "trust":
            if (
                (
                    "securitiesinvestmenttrust"
                    in normalized_key
                    or "投信"
                    in normalized_key
                )
                and (
                    "difference"
                    in normalized_key
                    or "買賣超"
                    in normalized_key
                )
            ):
                number = parse_number(value)

                if number is not None:
                    return number

    return None


def fetch_tpex_institutional(
    latest_date: str,
) -> dict[str, Any]:
    date_text = latest_date.replace(
        "-",
        "/",
    )

    payload = request_json(
        TPEX_3INSTI_URL
    )

    rows = recursive_dict_rows(
        payload
    )

    # TPEx endpoint may return current date data.
    # Filter only when a recognizable date field exists.
    filtered_rows: list[
        dict[str, Any]
    ] = []

    for row in rows:
        row_date = parse_date(
            first_value(
                row,
                [
                    "Date",
                    "date",
                    "資料日期",
                    "日期",
                ],
            )
        )

        if row_date is None:
            filtered_rows.append(row)
            continue

        if row_date == latest_date:
            filtered_rows.append(row)

    if filtered_rows:
        rows = filtered_rows

    foreign_total = 0.0
    foreign_count = 0

    trust_total = 0.0
    trust_count = 0

    for row in rows:
        foreign_value = (
            find_tpex_field_value(
                row,
                "foreign",
            )
        )

        if foreign_value is not None:
            foreign_total += foreign_value
            foreign_count += 1

        trust_value = (
            find_tpex_field_value(
                row,
                "trust",
            )
        )

        if trust_value is not None:
            trust_total += trust_value
            trust_count += 1

    foreign_net = (
        foreign_total
        if foreign_count > 0
        else None
    )

    trust_net = (
        trust_total
        if trust_count > 0
        else None
    )

    print(
        "TPEx institutional:"
    )
    print(
        f"  rows: {len(rows)}"
    )
    print(
        f"  foreign(ex-dealer): "
        f"{foreign_net}"
    )
    print(
        f"  investment trust: "
        f"{trust_net}"
    )

    return {
        "foreign_net": foreign_net,
        "trust_net": trust_net,
        "rows": len(rows),
        "status": (
            "complete"
            if rows
            else "unavailable"
        ),
    }


def combine_institutional(
    twse: Optional[dict[str, Any]],
    tpex: Optional[dict[str, Any]],
) -> dict[str, Any]:
    twse_foreign = (
        twse.get("foreign_net")
        if twse
        else None
    )

    tpex_foreign = (
        tpex.get("foreign_net")
        if tpex
        else None
    )

    twse_trust = (
        twse.get("trust_net")
        if twse
        else None
    )

    tpex_trust = (
        tpex.get("trust_net")
        if tpex
        else None
    )

    foreign_parts = [
        value
        for value in (
            twse_foreign,
            tpex_foreign,
        )
        if value is not None
    ]

    trust_parts = [
        value
        for value in (
            twse_trust,
            tpex_trust,
        )
        if value is not None
    ]

    foreign_net = (
        sum(foreign_parts)
        if foreign_parts
        else None
    )

    trust_net = (
        sum(trust_parts)
        if trust_parts
        else None
    )

    if len(foreign_parts) == 2:
        foreign_status = "complete"
    elif len(foreign_parts) == 1:
        foreign_status = "partial"
    else:
        foreign_status = "unavailable"

    if len(trust_parts) == 2:
        trust_status = "complete"
    elif len(trust_parts) == 1:
        trust_status = "partial"
    else:
        trust_status = "unavailable"

    overall_status = "complete"

    if (
        foreign_status == "unavailable"
        or trust_status == "unavailable"
    ):
        overall_status = "partial"

    if (
        foreign_status == "unavailable"
        and trust_status == "unavailable"
    ):
        overall_status = "unavailable"

    return {
        "foreign_net": foreign_net,
        "trust_net": trust_net,
        "twse_foreign_net": twse_foreign,
        "tpex_foreign_net": tpex_foreign,
        "twse_trust_net": twse_trust,
        "tpex_trust_net": tpex_trust,
        "foreign_status": foreign_status,
        "trust_status": trust_status,
        "status": overall_status,
    }


# ============================================================
# Conditions
# ============================================================

def condition(
    name: str,
    value: Any,
    passed: Optional[bool],
) -> dict[str, Any]:
    if passed is None:
        status = "unavailable"

    elif passed:
        status = "pass"

    else:
        status = "fail"

    return {
        "name": name,
        "value": value,
        "pass": passed,
        "status": status,
    }


def build_conditions(
    index: dict[str, Any],
    trend: dict[str, Any],
    breadth: dict[str, Any],
    volume: dict[str, Any],
    institutional: dict[str, Any],
) -> list[dict[str, Any]]:
    values: list[
        dict[str, Any]
    ] = []

    # --------------------------------------------------------
    # 1. TAIEX > MA20
    # --------------------------------------------------------

    taiex_value = index.get("value")
    ma20 = trend.get("ma20")

    if (
        taiex_value is None
        or ma20 is None
    ):
        values.append(
            condition(
                CONDITION_NAMES[0],
                None,
                None,
            )
        )
    else:
        values.append(
            condition(
                CONDITION_NAMES[0],
                taiex_value - ma20,
                taiex_value > ma20,
            )
        )

    # --------------------------------------------------------
    # 2. MA20 上升
    # --------------------------------------------------------

    ma20_slope = trend.get(
        "ma20_slope"
    )

    if ma20_slope is None:
        values.append(
            condition(
                CONDITION_NAMES[1],
                None,
                None,
            )
        )
    else:
        values.append(
            condition(
                CONDITION_NAMES[1],
                ma20_slope,
                ma20_slope > 0,
            )
        )

    # --------------------------------------------------------
    # 3. RSI14 > 50
    # --------------------------------------------------------

    rsi14 = trend.get("rsi14")

    if rsi14 is None:
        values.append(
            condition(
                CONDITION_NAMES[2],
                None,
                None,
            )
        )
    else:
        values.append(
            condition(
                CONDITION_NAMES[2],
                rsi14,
                rsi14 > 50,
            )
        )

    # --------------------------------------------------------
    # 4. Advance / decline >= 1
    # --------------------------------------------------------

    ad_ratio = breadth.get(
        "advance_decline_ratio"
    )

    advance = breadth.get(
        "advance"
    )

    decline = breadth.get(
        "decline"
    )

    if (
        ad_ratio is None
        and not (
            isinstance(advance, int)
            and isinstance(decline, int)
            and advance > 0
            and decline == 0
        )
    ):
        values.append(
            condition(
                CONDITION_NAMES[3],
                None,
                None,
            )
        )

    elif decline == 0 and advance > 0:
        values.append(
            condition(
                CONDITION_NAMES[3],
                None,
                True,
            )
        )

    else:
        values.append(
            condition(
                CONDITION_NAMES[3],
                ad_ratio,
                ad_ratio >= 1,
            )
        )

    # --------------------------------------------------------
    # 5. Above MA20 >= 50%
    # --------------------------------------------------------

    above_ma20_pct = breadth.get(
        "above_ma20_pct"
    )

    if above_ma20_pct is None:
        values.append(
            condition(
                CONDITION_NAMES[4],
                None,
                None,
            )
        )
    else:
        values.append(
            condition(
                CONDITION_NAMES[4],
                above_ma20_pct,
                above_ma20_pct >= 50,
            )
        )

    # --------------------------------------------------------
    # 6. Market volume / 20D avg >= 1
    # --------------------------------------------------------

    volume_ratio = volume.get(
        "ratio"
    )

    if volume_ratio is None:
        values.append(
            condition(
                CONDITION_NAMES[5],
                None,
                None,
            )
        )
    else:
        values.append(
            condition(
                CONDITION_NAMES[5],
                volume_ratio,
                volume_ratio >= 1,
            )
        )

    # --------------------------------------------------------
    # 7. Foreign net > 0
    # --------------------------------------------------------

    foreign_net = institutional.get(
        "foreign_net"
    )

    if foreign_net is None:
        values.append(
            condition(
                CONDITION_NAMES[6],
                None,
                None,
            )
        )
    else:
        values.append(
            condition(
                CONDITION_NAMES[6],
                foreign_net,
                foreign_net > 0,
            )
        )

    # --------------------------------------------------------
    # 8. Trust net > 0
    # --------------------------------------------------------

    trust_net = institutional.get(
        "trust_net"
    )

    if trust_net is None:
        values.append(
            condition(
                CONDITION_NAMES[7],
                None,
                None,
            )
        )
    else:
        values.append(
            condition(
                CONDITION_NAMES[7],
                trust_net,
                trust_net > 0,
            )
        )

    # --------------------------------------------------------
    # 9. 20D new high / new low >= 1
    # --------------------------------------------------------

    new_high = breadth.get(
        "new_high_20d"
    )

    new_low = breadth.get(
        "new_low_20d"
    )

    new_hilo_total = breadth.get(
        "new_hilo_total"
    )

    new_high_low_ratio = breadth.get(
        "new_high_low_ratio"
    )

    if (
        not isinstance(
            new_hilo_total,
            int,
        )
        or new_hilo_total <= 0
    ):
        values.append(
            condition(
                CONDITION_NAMES[8],
                None,
                None,
            )
        )

    elif new_low == 0 and new_high > 0:
        values.append(
            condition(
                CONDITION_NAMES[8],
                None,
                True,
            )
        )

    elif new_low == 0:
        values.append(
            condition(
                CONDITION_NAMES[8],
                None,
                None,
            )
        )

    elif new_high_low_ratio is None:
        values.append(
            condition(
                CONDITION_NAMES[8],
                None,
                None,
            )
        )

    else:
        values.append(
            condition(
                CONDITION_NAMES[8],
                new_high_low_ratio,
                new_high_low_ratio >= 1,
            )
        )

    # --------------------------------------------------------
    # 10. ATR14% <= 3%
    # --------------------------------------------------------

    atr14_pct = trend.get(
        "atr14_pct"
    )

    if atr14_pct is None:
        values.append(
            condition(
                CONDITION_NAMES[9],
                None,
                None,
            )
        )
    else:
        values.append(
            condition(
                CONDITION_NAMES[9],
                atr14_pct,
                atr14_pct <= 3,
            )
        )

    if len(values) != 10:
        raise RuntimeError(
            "Condition count is not 10"
        )

    actual_names = [
        item["name"]
        for item in values
    ]

    if actual_names != CONDITION_NAMES:
        raise RuntimeError(
            "Condition name/order contract violated"
        )

    return values


# ============================================================
# Sentiment
# ============================================================

def calculate_sentiment(
    conditions: list[dict[str, Any]],
) -> dict[str, Any]:
    valid = [
        item
        for item in conditions
        if item.get("status")
        in {
            "pass",
            "fail",
        }
    ]

    valid_conditions = len(valid)

    score = sum(
        1
        for item in valid
        if item.get("status") == "pass"
    )

    if valid_conditions < 6:
        level = "資料不足"

    elif score >= 8:
        level = "偏多"

    elif score >= 5:
        level = "震盪"

    else:
        level = "偏弱"

    return {
        "level": level,
        "score": score,
        "valid_conditions": valid_conditions,
        "total_conditions": 10,
    }


# ============================================================
# Market status
# ============================================================

def market_status_now() -> str:
    """
    台灣現貨市場：
    週一至週五 09:00–13:30。
    """

    now = datetime.now()

    if now.weekday() >= 5:
        return "closed"

    minutes = (
        now.hour * 60
        + now.minute
    )

    if (
        9 * 60
        <= minutes
        <= 13 * 60 + 30
    ):
        return "open"

    return "closed"


# ============================================================
# Validation
# ============================================================

def validate_market_payload(
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

    missing = (
        required
        - set(data.keys())
    )

    if missing:
        raise RuntimeError(
            "Missing market root fields: "
            f"{sorted(missing)}"
        )

    if data["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError(
            "Invalid market schema_version"
        )

    if data["market_status"] not in {
        "open",
        "closed",
    }:
        raise RuntimeError(
            "Invalid market_status"
        )

    index = data["index"]

    if not isinstance(index, dict):
        raise RuntimeError(
            "index must be object"
        )

    for key in (
        "name",
        "value",
        "change",
        "change_pct",
    ):
        if key not in index:
            raise RuntimeError(
                f"Missing index field: {key}"
            )

    if not is_finite_number(
        index["value"]
    ):
        raise RuntimeError(
            "index.value must be finite number"
        )

    conditions = data["conditions"]

    if not isinstance(
        conditions,
        list,
    ):
        raise RuntimeError(
            "conditions must be list"
        )

    if len(conditions) != 10:
        raise RuntimeError(
            "conditions must contain exactly 10 items"
        )

    actual_names = [
        item.get("name")
        for item in conditions
    ]

    if actual_names != CONDITION_NAMES:
        raise RuntimeError(
            "Condition names/order mismatch"
        )

    for item in conditions:
        if item.get("status") not in {
            "pass",
            "fail",
            "unavailable",
        }:
            raise RuntimeError(
                "Invalid condition status"
            )

        passed = item.get("pass")

        if passed is not None and not isinstance(
            passed,
            bool,
        ):
            raise RuntimeError(
                "condition.pass must be bool or null"
            )

    sentiment = data["sentiment"]

    if sentiment.get("level") not in {
        "偏多",
        "震盪",
        "偏弱",
        "資料不足",
    }:
        raise RuntimeError(
            "Invalid sentiment level"
        )

    if not isinstance(
        sentiment.get("score"),
        int,
    ):
        raise RuntimeError(
            "sentiment.score must be int"
        )

    if not isinstance(
        sentiment.get("valid_conditions"),
        int,
    ):
        raise RuntimeError(
            "sentiment.valid_conditions must be int"
        )

    if sentiment.get(
        "total_conditions"
    ) != 10:
        raise RuntimeError(
            "sentiment.total_conditions must be 10"
        )

    source = data["source"]

    if not isinstance(
        source,
        dict,
    ):
        raise RuntimeError(
            "source must be object"
        )

    provider = source.get(
        "provider"
    )

    if not isinstance(
        provider,
        list,
    ):
        raise RuntimeError(
            "source.provider must be list"
        )

    if "TWSE" not in provider:
        raise RuntimeError(
            "TWSE missing from source.provider"
        )

    if "TPEx" not in provider:
        raise RuntimeError(
            "TPEx missing from source.provider"
        )


def validate_no_nonfinite(
    value: Any,
    path: str = "root",
) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeError(
                f"Non-finite number at {path}"
            )

    elif isinstance(value, dict):
        for key, child in value.items():
            validate_no_nonfinite(
                child,
                f"{path}.{key}",
            )

    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_no_nonfinite(
                child,
                f"{path}[{index}]",
            )


# ============================================================
# Atomic write
# ============================================================

def atomic_write_json(
    path: Path,
    data: dict[str, Any],
) -> None:
    cleaned = clean_json(data)

    validate_no_nonfinite(
        cleaned
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            cleaned,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    os.replace(
        temporary,
        path,
    )


# ============================================================
# Main
# ============================================================

def main() -> int:
    print("")
    print("=" * 72)
    print("FETCH MARKET V2.1")
    print("=" * 72)

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

    # --------------------------------------------------------
    # Current TAIEX
    # --------------------------------------------------------

    current_index = fetch_taiex_current()

    latest_date = current_index[
        "date"
    ]

    print(
        f"✓ Latest trading date: "
        f"{latest_date}"
    )

    # --------------------------------------------------------
    # TAIEX history
    # --------------------------------------------------------

    taiex_history = fetch_taiex_history(
        latest_date
    )

    index_metrics = (
        calculate_index_metrics(
            taiex_history
        )
    )

    # --------------------------------------------------------
    # Market breadth
    # --------------------------------------------------------

    breadth = calculate_market_breadth(
        histories,
        latest_date,
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    volume = {
        "current": breadth[
            "current_volume"
        ],
        "current_stock_count": breadth[
            "current_volume_count"
        ],
        "average_20d": breadth[
            "volume_20d_average"
        ],
        "ratio": breadth[
            "volume_ratio"
        ],
    }

    # --------------------------------------------------------
    # Institutional
    # --------------------------------------------------------

    twse_institutional: Optional[
        dict[str, Any]
    ] = None

    tpex_institutional: Optional[
        dict[str, Any]
    ] = None

    try:
        twse_institutional = (
            fetch_twse_institutional(
                latest_date
            )
        )

    except Exception as exc:
        print(
            f"⚠️ TWSE T86 unavailable: "
            f"{exc}"
        )

    try:
        tpex_institutional = (
            fetch_tpex_institutional(
                latest_date
            )
        )

    except Exception as exc:
        print(
            f"⚠️ TPEx institutional "
            f"unavailable: {exc}"
        )

    institutional = combine_institutional(
        twse_institutional,
        tpex_institutional,
    )

    # --------------------------------------------------------
    # Index object
    #
    # IMPORTANT:
    # V2.1 requires:
    # name / value / change / change_pct
    # --------------------------------------------------------

    index = {
        "name": "TAIEX",
        "value": current_index[
            "value"
        ],
        "change": current_index[
            "change"
        ],
        "change_pct": current_index[
            "change_pct"
        ],
    }

    # --------------------------------------------------------
    # Trend
    # --------------------------------------------------------

    trend = {
        "ma20": index_metrics[
            "ma20"
        ],
        "ma20_previous": index_metrics[
            "ma20_previous"
        ],
        "ma20_slope": index_metrics[
            "ma20_slope"
        ],
        "rsi14": index_metrics[
            "rsi14"
        ],
        "atr14_pct": index_metrics[
            "atr14_pct"
        ],
        "history_days": len(
            taiex_history
        ),
    }

    # --------------------------------------------------------
    # Conditions
    # --------------------------------------------------------

    conditions = build_conditions(
        index=index,
        trend=trend,
        breadth=breadth,
        volume=volume,
        institutional=institutional,
    )

    # --------------------------------------------------------
    # Sentiment
    # --------------------------------------------------------

    sentiment = calculate_sentiment(
        conditions
    )

    # --------------------------------------------------------
    # Source
    # --------------------------------------------------------

    source = {
        "provider": [
            "TWSE",
            "TPEx",
        ],
        "twse": {
            "index": TWSE_MI_INDEX_URL,
            "index_history": (
                TWSE_MI_5MINS_HIST_URL
            ),
            "institutional": TWSE_T86_URL,
        },
        "tpex": {
            "institutional": TPEX_3INSTI_URL,
        },
        "price": {
            "local": str(
                PRICES_DIR.relative_to(ROOT)
            ),
            "manifest": str(
                PRICE_MANIFEST_PATH.relative_to(
                    ROOT
                )
            ),
            "schema": PRICE_SCHEMA_VERSION,
        },
    }

    # --------------------------------------------------------
    # Config
    # --------------------------------------------------------

    config = {
        "condition_count": 10,
        "rsi_period": 14,
        "ma_period": 20,
        "atr_period": 14,
        "new_high_low_period": 20,
        "volume_average_period": 20,
        "sentiment": {
            "bullish_min_score": 8,
            "neutral_min_score": 5,
            "weak_max_score": 4,
            "minimum_valid_conditions": 6,
        },
        "price_coverage": {
            "universe_count": len(
                universe
            ),
            "history_count": len(
                histories
            ),
            "latest_date_exact": breadth[
                "coverage"
            ],
            "stale": breadth[
                "stale_coverage"
            ],
        },
    }

    # --------------------------------------------------------
    # Final payload
    # --------------------------------------------------------

    market = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "market_status": market_status_now(),
        "latest_trading_date": latest_date,
        "index": index,
        "trend": trend,
        "breadth": {
            "coverage": breadth[
                "coverage"
            ],
            "stale_coverage": breadth[
                "stale_coverage"
            ],
            "advance": breadth[
                "advance"
            ],
            "decline": breadth[
                "decline"
            ],
            "unchanged": breadth[
                "unchanged"
            ],
            "advance_decline_ratio": (
                breadth[
                    "advance_decline_ratio"
                ]
            ),
            "above_ma20": breadth[
                "above_ma20"
            ],
            "ma20_total": breadth[
                "ma20_total"
            ],
            "above_ma20_pct": breadth[
                "above_ma20_pct"
            ],
            "new_high_20d": breadth[
                "new_high_20d"
            ],
            "new_low_20d": breadth[
                "new_low_20d"
            ],
            "new_high_low_ratio": (
                breadth[
                    "new_high_low_ratio"
                ]
            ),
            "new_hilo_total": breadth[
                "new_hilo_total"
            ],
        },
        "volume": volume,
        "institutional": institutional,
        "sentiment": sentiment,
        "conditions": conditions,
        "source": source,
        "config": config,
    }

    # --------------------------------------------------------
    # Validate BEFORE write
    # --------------------------------------------------------

    market = clean_json(
        market
    )

    validate_market_payload(
        market
    )

    validate_no_nonfinite(
        market
    )

    # --------------------------------------------------------
    # Atomic write
    # --------------------------------------------------------

    atomic_write_json(
        MARKET_PATH,
        market,
    )

    # --------------------------------------------------------
    # Read-back validation
    # --------------------------------------------------------

    if not MARKET_PATH.exists():
        raise RuntimeError(
            "market.json was not written"
        )

    written = json.loads(
        MARKET_PATH.read_text(
            encoding="utf-8"
        )
    )

    validate_market_payload(
        written
    )

    validate_no_nonfinite(
        written
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print("")
    print("=" * 72)
    print("MARKET V2.1 RESULT")
    print("=" * 72)

    print(
        f"Schema           : "
        f"{written['schema_version']}"
    )

    print(
        f"Market status    : "
        f"{written['market_status']}"
    )

    print(
        f"Trading date     : "
        f"{written['latest_trading_date']}"
    )

    print(
        f"TAIEX            : "
        f"{written['index']['value']}"
    )

    print(
        f"Price coverage   : "
        f"{written['breadth']['coverage']}/"
        f"{len(universe)}"
    )

    print(
        f"Stale coverage   : "
        f"{written['breadth']['stale_coverage']}"
    )

    print(
        f"Sentiment        : "
        f"{written['sentiment']['level']}"
    )

    print(
        f"Score            : "
        f"{written['sentiment']['score']}/10"
    )

    print(
        f"Valid conditions : "
        f"{written['sentiment']['valid_conditions']}/10"
    )

    print("")

    for index, item in enumerate(
        written["conditions"],
        start=1,
    ):
        print(
            f"{index:02d}. "
            f"{item['name']} "
            f"→ {item['status']} "
            f"value={item['value']}"
        )

    print("")
    print(
        f"✓ Wrote: {MARKET_PATH}"
    )

    print(
        "✓ market-v2.1 validation PASS"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(
            main()
        )

    except KeyboardInterrupt:
        print(
            "\n❌ Interrupted",
            file=sys.stderr,
        )
        raise SystemExit(130)

    except Exception as exc:
        print(
            "",
            file=sys.stderr,
        )
        print(
            "=" * 72,
            file=sys.stderr,
        )
        print(
            "FETCH MARKET V2.1 FAILED",
            file=sys.stderr,
        )
        print(
            "=" * 72,
            file=sys.stderr,
        )
        print(
            f"❌ {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)