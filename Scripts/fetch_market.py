#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_market.py

MARKET ENVIRONMENT V2.1
============================================================

資料鏈
------------------------------------------------------------
TWSE 官方
    ├─ MI_INDEX
    ├─ MI_5MINS_HIST
    └─ T86

TPEx 官方
    └─ tpex_3insti_daily_trading

Data/prices/
    └─ fetch_prices.py V14.0 官方優先價格 shard

                ↓

Data/market.json

                ↓

Scripts/build_ui_data.py

                ↓

Data/ui_data.json

                ↓

index.html


核心原則
------------------------------------------------------------
1. Data/universe.json 是 Universe 唯一來源
2. 只使用 status == active
3. ETF / ETN / 權證 / REIT / 債券等非一般股票
   不納入市場 breadth
4. Data/prices/manifest.json 決定價格 shard
5. prices-v14.0 shard 必須全部合併
6. 同一股票跨 shard 不得互相覆蓋
7. breadth 以 latest_trading_date 為基準，
   使用 <= latest_trading_date 的最後一筆資料
8. 資料不足 = unavailable，不得當成 fail
9. unavailable 不計分
10. 有效條件 < 6 → 資料不足


市場核心條件
------------------------------------------------------------
1. TAIEX > MA20
2. MA20 上升
3. TAIEX RSI14 > 50
4. 上漲家數 / 下跌家數 >= 1
5. 站上 MA20 比例 >= 50%
6. 市場成交量 / 20 日均量 >= 1
7. 外資買賣超 > 0
8. 投信買賣超 > 0
9. 20 日新高 / 新低 >= 1
10. TAIEX ATR14% <= 3%

市場風向
------------------------------------------------------------
8~10  → 偏多
5~7   → 震盪
0~4   → 偏弱

注意：
------------------------------------------------------------
「20日新高 / 新低 >= 1」不是要求一定存在新低。
若新低 = 0 且有新高，視為 ratio = +inf，
條件通過。

同樣地：
上漲家數 > 0 且下跌家數 = 0
→ advance/decline ratio = +inf。
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"

OUTPUT_FILE = DATA_DIR / "market.json"

UNIVERSE_FILE = DATA_DIR / "universe.json"

PRICES_DIR = DATA_DIR / "prices"

MANIFEST_FILE = PRICES_DIR / "manifest.json"


# ============================================================
# VERSION
# ============================================================

SCHEMA_VERSION = "market-v2.1"

PRICE_SCHEMA_VERSION = "prices-v14.0"

TAIWAN_TZ = timezone(
    timedelta(hours=8)
)

REQUEST_TIMEOUT = 30

MIN_INDEX_HISTORY = 21

MIN_STOCK_HISTORY_FOR_MA20 = 20

MIN_VOLUME_HISTORY = 21


# ============================================================
# OFFICIAL DATA SOURCES
# ============================================================

TWSE_INDEX_URL = (
    "https://openapi.twse.com.tw/"
    "v1/exchangeReport/MI_INDEX"
)

TWSE_INDEX_HISTORY_URL = (
    "https://openapi.twse.com.tw/"
    "v1/indicesReport/MI_5MINS_HIST"
)

TWSE_T86_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/fund/T86"
)

TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/"
    "openapi/v1/"
    "tpex_3insti_daily_trading"
)


# ============================================================
# MARKET CONFIG
# ============================================================

CONFIG = {
    "ma_period": 20,
    "rsi_period": 14,
    "atr_period": 14,

    "volume_ma_period": 20,
    "new_high_low_period": 20,

    "advance_decline_min_ratio": 1.00,
    "breadth_min_pct": 0.50,
    "volume_ratio_min": 1.00,
    "new_high_low_min_ratio": 1.00,
    "atr_pct_max": 0.03,

    "score_bullish": 8,
    "score_sideways": 5,

    "minimum_valid_conditions": 6,
}


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; TW-Stock-AI-Scanner/2.1)"
    ),
    "Accept": (
        "application/json, "
        "text/plain, */*"
    ),
}


def log(message: str) -> None:
    print(
        message,
        flush=True,
    )


def request_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
) -> Any:

    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    try:
        return response.json()

    except Exception as exc:

        preview = (
            response.text[:500]
            .replace("\n", " ")
        )

        raise RuntimeError(
            f"非 JSON 回應：{url}; "
            f"{preview}"
        ) from exc


# ============================================================
# NUMBER
# ============================================================

def number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    if isinstance(
        value,
        bool,
    ):
        return None

    if isinstance(
        value,
        (int, float),
    ):

        result = float(value)

        return (
            result
            if math.isfinite(result)
            else None
        )

    text = str(
        value
    ).strip()

    if not text:
        return None

    # Taiwan official data may contain:
    # comma, %, spaces, "--", "－", etc.
    text = (
        text
        .replace(",", "")
        .replace("%", "")
        .replace(" ", "")
        .replace("　", "")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )

    if text in {
        "",
        "-",
        "--",
        "---",
        "N/A",
        "NA",
        "null",
        "None",
    }:
        return None

    try:

        result = float(text)

        if not math.isfinite(
            result
        ):
            return None

        return result

    except Exception:

        return None


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(
    value: Any,
) -> str:

    text = str(
        value or ""
    ).strip().upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TSE",
        ".OTC",
    ):

        if text.endswith(
            suffix
        ):

            text = text[
                :-len(suffix)
            ]

            break

    return text.strip()


# ============================================================
# DATE
# ============================================================

def parse_date(
    value: Any,
) -> Optional[date]:

    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):

        return value.date()

    if isinstance(
        value,
        date,
    ):

        return value

    text = str(
        value
    ).strip()

    if not text:
        return None

    # Remove time suffix when necessary.
    text = (
        text.split("T")[0]
        .split(" ")[0]
    )

    # ROC YYYYMMDD
    if (
        text.isdigit()
        and len(text) == 7
    ):

        try:

            return date(
                int(text[:3]) + 1911,
                int(text[3:5]),
                int(text[5:7]),
            )

        except ValueError:

            return None

    # ROC YYYY/MM/DD
    match = re.fullmatch(
        r"(\d{3})/(\d{1,2})/(\d{1,2})",
        text,
    )

    if match:

        try:

            return date(
                int(match.group(1))
                + 1911,
                int(match.group(2)),
                int(match.group(3)),
            )

        except ValueError:

            return None

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y.%m.%d",
    ):

        try:

            return datetime.strptime(
                text,
                fmt,
            ).date()

        except ValueError:

            pass

    return None


# ============================================================
# GENERIC DATA EXTRACTION
# ============================================================

def list_dict_rows(
    payload: Any,
) -> List[Dict[str, Any]]:

    if isinstance(
        payload,
        list,
    ):

        return [
            row
            for row in payload
            if isinstance(
                row,
                dict,
            )
        ]

    if isinstance(
        payload,
        dict,
    ):

        # Direct common wrappers.
        for key in (
            "data",
            "rows",
            "records",
            "result",
            "results",
            "items",
        ):

            value = payload.get(
                key
            )

            if isinstance(
                value,
                list,
            ):

                return [
                    row
                    for row in value
                    if isinstance(
                        row,
                        dict,
                    )
                ]

        # Some APIs return:
        # {"data": {"data": [...]}}
        for outer_key in (
            "data",
            "result",
            "results",
        ):

            outer = payload.get(
                outer_key
            )

            if isinstance(
                outer,
                dict,
            ):

                rows = list_dict_rows(
                    outer
                )

                if rows:
                    return rows

    return []


def table_rows(
    payload: Any,
) -> List[Dict[str, Any]]:

    if not isinstance(
        payload,
        dict,
    ):
        return []

    tables = payload.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):
        return []

    output = []

    for table in tables:

        if not isinstance(
            table,
            dict,
        ):
            continue

        fields = table.get(
            "fields"
        )

        data = table.get(
            "data"
        )

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

        for row in data:

            if not isinstance(
                row,
                list,
            ):
                continue

            mapped = {}

            for index, field in enumerate(
                fields
            ):

                mapped[
                    str(field).strip()
                ] = (
                    row[index]
                    if index < len(row)
                    else None
                )

            output.append(
                mapped
            )

    return output


def recursive_dict_rows(
    payload: Any,
) -> List[Dict[str, Any]]:

    """
    Find dictionary rows recursively.

    This is intentionally conservative:
    lists of dictionaries are treated as records,
    while nested metadata is ignored.
    """

    rows: List[
        Dict[str, Any]
    ] = []

    if isinstance(
        payload,
        list,
    ):

        if all(
            isinstance(
                item,
                dict,
            )
            for item in payload
        ):

            rows.extend(
                payload
            )

        else:

            for item in payload:

                rows.extend(
                    recursive_dict_rows(
                        item
                    )
                )

    elif isinstance(
        payload,
        dict,
    ):

        for value in payload.values():

            if isinstance(
                value,
                (dict, list),
            ):

                rows.extend(
                    recursive_dict_rows(
                        value
                    )
                )

    return rows


def get_value(
    row: Dict[str, Any],
    candidates: Iterable[str],
) -> Any:

    # Exact match first.
    for key in candidates:

        if key in row:

            return row.get(
                key
            )

    # Normalized match.
    normalized = {}

    for key in row:

        normalized[
            re.sub(
                r"\s+",
                "",
                str(key).strip().lower(),
            )
        ] = key

    for candidate in candidates:

        normalized_candidate = re.sub(
            r"\s+",
            "",
            str(candidate).strip().lower(),
        )

        original_key = normalized.get(
            normalized_candidate
        )

        if original_key is not None:

            return row.get(
                original_key
            )

    return None


# ============================================================
# 1. TAIEX CURRENT
# ============================================================

def fetch_index() -> Tuple[
    date,
    Dict[str, Any],
]:

    payload = request_json(
        TWSE_INDEX_URL
    )

    rows = list_dict_rows(
        payload
    )

    # Fallback for API wrapper changes.
    if not rows:

        rows = recursive_dict_rows(
            payload
        )

    for row in rows:

        index_name = str(
            get_value(
                row,
                (
                    "指數",
                    "Index",
                    "index_name",
                    "IndexName",
                ),
            )
            or ""
        ).strip()

        if (
            index_name
            and "發行量加權"
            not in index_name
            and index_name
            not in {
                "TAIEX",
                "TAIEX Index",
            }
        ):

            continue

        trading_date = parse_date(
            get_value(
                row,
                (
                    "日期",
                    "Date",
                    "date",
                ),
            )
        )

        close = number(
            get_value(
                row,
                (
                    "收盤指數",
                    "ClosingIndex",
                    "Close",
                    "close",
                ),
            )
        )

        change = number(
            get_value(
                row,
                (
                    "漲跌點數",
                    "Change",
                    "change",
                ),
            )
        )

        change_pct = number(
            get_value(
                row,
                (
                    "漲跌百分比",
                    "ChangePercent",
                    "change_pct",
                    "Change%",
                ),
            )
        )

        sign = str(
            get_value(
                row,
                (
                    "漲跌",
                    "Sign",
                    "sign",
                ),
            )
            or ""
        ).strip()

        if (
            sign == "-"
            and change is not None
        ):

            change = -abs(
                change
            )

        if (
            trading_date
            and close is not None
        ):

            return (
                trading_date,
                {
                    "name":
                        "加權指數",

                    "value":
                        round(
                            close,
                            2,
                        ),

                    "change":
                        (
                            round(
                                change,
                                2,
                            )
                            if change
                            is not None
                            else None
                        ),

                    "change_pct":
                        (
                            round(
                                change_pct,
                                2,
                            )
                            if change_pct
                            is not None
                            else None
                        ),
                },
            )

    raise RuntimeError(
        "TWSE MI_INDEX 找不到 "
        "發行量加權股價指數"
    )


# ============================================================
# 2. TAIEX HISTORY
# ============================================================

def parse_index_history_rows(
    payload: Any,
) -> List[
    Dict[str, Any]
]:

    rows = list_dict_rows(
        payload
    )

    if not rows:

        rows = table_rows(
            payload
        )

    if not rows:

        rows = recursive_dict_rows(
            payload
        )

    output = []

    for row in rows:

        trading_date = parse_date(
            get_value(
                row,
                (
                    "Date",
                    "date",
                    "日期",
                ),
            )
        )

        close = number(
            get_value(
                row,
                (
                    "ClosingIndex",
                    "closing_index",
                    "Close",
                    "close",
                    "收盤指數",
                ),
            )
        )

        high = number(
            get_value(
                row,
                (
                    "HighestIndex",
                    "highest_index",
                    "High",
                    "high",
                    "最高指數",
                ),
            )
        )

        low = number(
            get_value(
                row,
                (
                    "LowestIndex",
                    "lowest_index",
                    "Low",
                    "low",
                    "最低指數",
                ),
            )
        )

        if (
            trading_date
            and close is not None
        ):

            output.append(
                {
                    "date":
                        trading_date,

                    "close":
                        close,

                    "high":
                        high,

                    "low":
                        low,
                }
            )

    return output


def fetch_index_history() -> List[
    Dict[str, Any]
]:

    payload = request_json(
        TWSE_INDEX_HISTORY_URL
    )

    output = (
        parse_index_history_rows(
            payload
        )
    )

    # Deduplicate by date.
    by_date = {}

    for row in output:

        by_date[
            row["date"]
        ] = row

    result = list(
        by_date.values()
    )

    result.sort(
        key=lambda x: x["date"]
    )

    return result


# ============================================================
# 3. UNIVERSE
# ============================================================

def load_universe() -> Dict[
    str,
    Dict[str, Any],
]:

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "找不到 Data/universe.json"
        )

    data = json.loads(
        UNIVERSE_FILE.read_text(
            encoding="utf-8-sig"
        )
    )

    stocks = (
        data.get("stocks")
        if isinstance(
            data,
            dict,
        )
        else None
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.json stocks "
            "必須是 object"
        )

    output = {}

    for raw_symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):
            continue

        if item.get(
            "status"
        ) != "active":

            continue

        symbol = normalize_symbol(
            raw_symbol
        )

        if symbol:

            output[
                symbol
            ] = item

    if not output:

        raise RuntimeError(
            "universe.json 沒有 "
            "active instruments"
        )

    return output


# ============================================================
# 4. STOCK FILTER
# ============================================================

def is_stock(
    item: Dict[str, Any],
) -> bool:

    text = " ".join(
        str(
            item.get(
                key,
                "",
            )
        )
        for key in (
            "type",
            "instrument_type",
            "security_type",
            "category",
            "product_type",
            "market_type",
        )
    ).lower()

    excluded = (
        "etf",
        "基金",
        "bond",
        "債券",
        "etn",
        "權證",
        "warrant",
        "reit",
    )

    if any(
        token in text
        for token in excluded
    ):

        return False

    return True


# ============================================================
# 5. PRICE HISTORY PARSER
#
# fetch_prices.py V14.0
#
# {
#   "schema_version": "prices-v14.0",
#   "stocks": {
#       "1101": [
#           {
#               "date": "...",
#               "close": ...,
#               "volume": ...
#           }
#       ]
#   }
# }
# ============================================================

def parse_price_history(
    values: Any,
) -> List[
    Dict[str, Any]
]:

    if not isinstance(
        values,
        list,
    ):

        return []

    by_date: Dict[
        date,
        Dict[str, Any]
    ] = {}

    for row in values:

        if not isinstance(
            row,
            dict,
        ):
            continue

        trading_date = parse_date(
            get_value(
                row,
                (
                    "date",
                    "Date",
                    "trade_date",
                    "TradeDate",
                    "trading_date",
                    "TradingDate",
                    "日期",
                ),
            )
        )

        close = number(
            get_value(
                row,
                (
                    "close",
                    "Close",
                    "closing_price",
                    "ClosingPrice",
                    "收盤價",
                    "收盤",
                ),
            )
        )

        volume = number(
            get_value(
                row,
                (
                    "volume",
                    "Volume",
                    "成交量",
                    "成交股數",
                    "trading_volume",
                    "TradingVolume",
                ),
            )
        )

        if (
            trading_date is None
            or close is None
        ):
            continue

        by_date[
            trading_date
        ] = {
            "date":
                trading_date,

            "close":
                close,

            "volume":
                volume,
        }

    output = list(
        by_date.values()
    )

    output.sort(
        key=lambda x: x["date"]
    )

    return output


def merge_history(
    existing: List[
        Dict[str, Any]
    ],
    incoming: List[
        Dict[str, Any]
    ],
) -> List[
    Dict[str, Any]
]:

    by_date: Dict[
        date,
        Dict[str, Any]
    ] = {}

    for row in existing:
        by_date[
            row["date"]
        ] = row

    for row in incoming:

        current = by_date.get(
            row["date"]
        )

        if current is None:

            by_date[
                row["date"]
            ] = row

            continue

        # Prefer non-null values.
        if (
            current.get("close")
            is None
            and row.get("close")
            is not None
        ):

            current["close"] = (
                row["close"]
            )

        if (
            current.get("volume")
            is None
            and row.get("volume")
            is not None
        ):

            current["volume"] = (
                row["volume"]
            )

    output = list(
        by_date.values()
    )

    output.sort(
        key=lambda x: x["date"]
    )

    return output


def manifest_filenames(
    manifest: Dict[str, Any],
) -> List[str]:

    candidates = []

    files = manifest.get(
        "files"
    )

    if isinstance(
        files,
        list,
    ):

        for item in files:

            if isinstance(
                item,
                str,
            ):

                candidates.append(
                    item
                )

            elif isinstance(
                item,
                dict,
            ):

                value = (
                    item.get("file")
                    or item.get("path")
                    or item.get("filename")
                    or item.get("name")
                )

                if value:

                    candidates.append(
                        str(value)
                    )

    # Some manifest versions may use shards.
    shards = manifest.get(
        "shards"
    )

    if isinstance(
        shards,
        list,
    ):

        for item in shards:

            if isinstance(
                item,
                str,
            ):

                candidates.append(
                    item
                )

            elif isinstance(
                item,
                dict,
            ):

                value = (
                    item.get("file")
                    or item.get("path")
                    or item.get("filename")
                    or item.get("name")
                )

                if value:

                    candidates.append(
                        str(value)
                    )

    # Deduplicate while preserving order.
    result = []

    seen = set()

    for filename in candidates:

        clean = Path(
            filename
        ).name

        if (
            clean
            and clean not in seen
        ):

            seen.add(
                clean
            )

            result.append(
                clean
            )

    return result


def load_price_histories(
    universe: Dict[
        str,
        Dict[str, Any],
    ],
) -> Tuple[
    Dict[
        str,
        List[Dict[str, Any]],
    ],
    Dict[str, Any],
]:

    if not MANIFEST_FILE.exists():

        raise RuntimeError(
            "找不到 "
            "Data/prices/manifest.json"
        )

    manifest = json.loads(
        MANIFEST_FILE.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "prices/manifest.json "
            "必須是 object"
        )

    manifest_schema = str(
        manifest.get(
            "schema_version",
            "",
        )
    )

    # The actual contract is V14.0.
    # Do not silently consume another schema.
    if (
        manifest_schema
        and manifest_schema
        != PRICE_SCHEMA_VERSION
    ):

        raise RuntimeError(
            "manifest schema_version "
            f"錯誤："
            f"{manifest_schema}; "
            f"預期 {PRICE_SCHEMA_VERSION}"
        )

    filenames = (
        manifest_filenames(
            manifest
        )
    )

    if not filenames:

        filenames = [
            path.name
            for path in sorted(
                PRICES_DIR.glob(
                    "prices_*.json"
                )
            )
        ]

    if not filenames:

        raise RuntimeError(
            "manifest 沒有任何 "
            "price shard"
        )

    allowed_symbols = {
        symbol
        for symbol, item
        in universe.items()
        if is_stock(item)
    }

    merged: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

    loaded_files = []
    skipped_files = []
    stock_records = 0

    for filename in filenames:

        path = (
            PRICES_DIR
            / Path(filename).name
        )

        if not path.exists():

            skipped_files.append(
                filename
            )

            continue

        payload = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )

        if not isinstance(
            payload,
            dict,
        ):

            raise RuntimeError(
                f"{filename} "
                "不是 object"
            )

        schema = str(
            payload.get(
                "schema_version",
                "",
            )
        )

        if schema != PRICE_SCHEMA_VERSION:

            raise RuntimeError(
                f"{filename} "
                "schema_version 錯誤："
                f"{schema}; "
                f"預期 "
                f"{PRICE_SCHEMA_VERSION}"
            )

        stocks = payload.get(
            "stocks"
        )

        if not isinstance(
            stocks,
            dict,
        ):

            raise RuntimeError(
                f"{filename}.stocks "
                "不是 object"
            )

        file_records = 0

        for raw_symbol, values in (
            stocks.items()
        ):

            symbol = normalize_symbol(
                raw_symbol
            )

            if (
                not symbol
                or symbol
                not in allowed_symbols
            ):

                continue

            history = (
                parse_price_history(
                    values
                )
            )

            if not history:
                continue

            merged[symbol] = (
                merge_history(
                    merged.get(
                        symbol,
                        [],
                    ),
                    history,
                )
            )

            file_records += len(
                history
            )

        stock_records += file_records

        loaded_files.append(
            {
                "file":
                    filename,

                "symbols":
                    len(
                        stocks
                    ),

                "accepted_symbols":
                    sum(
                        1
                        for symbol
                        in stocks
                        if normalize_symbol(
                            symbol
                        )
                        in allowed_symbols
                    ),

                "records":
                    file_records,
            }
        )

    # Remove empty histories.
    merged = {
        symbol: history
        for symbol, history
        in merged.items()
        if history
    }

    metadata = {
        "manifest_schema_version":
            manifest_schema,

        "requested_files":
            len(filenames),

        "loaded_files":
            len(loaded_files),

        "missing_files":
            len(skipped_files),

        "missing_file_names":
            skipped_files,

        "loaded_file_details":
            loaded_files,

        "merged_symbols":
            len(merged),

        "accepted_stock_universe":
            len(allowed_symbols),

        "raw_records_accepted":
            stock_records,
    }

    return (
        merged,
        metadata,
    )


# ============================================================
# 6. AS-OF HISTORY
# ============================================================

def history_as_of(
    history: List[
        Dict[str, Any]
    ],
    latest_date: date,
) -> List[
    Dict[str, Any]
]:

    return [
        row
        for row in history
        if row["date"]
        <= latest_date
    ]


def latest_row_as_of(
    history: List[
        Dict[str, Any]
    ],
    latest_date: date,
) -> Optional[
    Dict[str, Any]
]:

    for row in reversed(history):

        if row["date"] <= latest_date:

            return row

    return None


# ============================================================
# 7. RSI
# ============================================================

def calculate_rsi(
    closes: List[float],
    period: int = 14,
) -> Optional[float]:

    if len(closes) < period + 1:

        return None

    changes = [
        current - previous
        for previous, current
        in zip(
            closes[
                -period - 1:
                -1
            ],
            closes[
                -period:
            ],
        )
    ]

    gains = [
        max(
            change,
            0.0,
        )
        for change in changes
    ]

    losses = [
        max(
            -change,
            0.0,
        )
        for change in changes
    ]

    average_gain = (
        sum(gains)
        / period
    )

    average_loss = (
        sum(losses)
        / period
    )

    if average_loss == 0:

        if average_gain == 0:
            return 50.0

        return 100.0

    rs = (
        average_gain
        / average_loss
    )

    return (
        100.0
        - (
            100.0
            / (1.0 + rs)
        )
    )


# ============================================================
# 8. INDEX METRICS
# ============================================================

def calculate_index_metrics(
    history: List[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    history = sorted(
        history,
        key=lambda x: x["date"],
    )

    closes = [
        row["close"]
        for row in history
        if row.get("close")
        is not None
    ]

    ma_period = CONFIG[
        "ma_period"
    ]

    rsi_period = CONFIG[
        "rsi_period"
    ]

    atr_period = CONFIG[
        "atr_period"
    ]

    ma20 = None
    previous_ma20 = None
    ma20_slope = None

    if len(closes) >= ma_period:

        ma20 = (
            sum(
                closes[-ma_period:]
            )
            / ma_period
        )

    if len(closes) >= ma_period + 1:

        previous_ma20 = (
            sum(
                closes[
                    -ma_period - 1:
                    -1
                ]
            )
            / ma_period
        )

    if (
        ma20 is not None
        and previous_ma20 is not None
    ):

        ma20_slope = (
            ma20
            - previous_ma20
        )

    rsi14 = calculate_rsi(
        closes,
        rsi_period,
    )

    true_ranges = []

    for index in range(
        1,
        len(history),
    ):

        current = history[
            index
        ]

        previous = history[
            index - 1
        ]

        high = current.get(
            "high"
        )

        low = current.get(
            "low"
        )

        previous_close = (
            previous.get("close")
        )

        if (
            high is None
            or low is None
            or previous_close is None
        ):

            continue

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

    atr14 = None

    if len(
        true_ranges
    ) >= atr_period:

        atr14 = (
            sum(
                true_ranges[
                    -atr_period:
                ]
            )
            / atr_period
        )

    atr14_pct = None

    if (
        atr14 is not None
        and closes
        and closes[-1] != 0
    ):

        atr14_pct = (
            atr14
            / closes[-1]
        )

    return {
        "ma20":
            ma20,

        "ma20_previous":
            previous_ma20,

        "ma20_slope":
            ma20_slope,

        "rsi14":
            rsi14,

        "atr14":
            atr14,

        "atr14_pct":
            atr14_pct,

        "history_count":
            len(history),
    }


# ============================================================
# 9. MARKET BREADTH
# ============================================================

def calculate_market_breadth(
    histories: Dict[
        str,
        List[Dict[str, Any]],
    ],
    latest_date: date,
) -> Dict[str, Any]:

    advancing = 0
    declining = 0
    unchanged = 0

    above_ma20 = 0
    ma20_valid = 0

    new_high_20d = 0
    new_low_20d = 0

    # Current market volume.
    current_volume = 0.0

    current_volume_symbols = 0

    # Daily market volume:
    # date -> total volume of all covered stocks.
    daily_volume_totals: Dict[
        date,
        float,
    ] = defaultdict(
        float
    )

    daily_volume_symbols: Dict[
        date,
        int,
    ] = defaultdict(
        int
    )

    coverage = 0

    for history in histories.values():

        as_of = history_as_of(
            history,
            latest_date,
        )

        if not as_of:
            continue

        current = as_of[-1]

        coverage += 1

        # ----------------------------------------------------
        # Advance / decline
        # ----------------------------------------------------

        if len(as_of) >= 2:

            previous = as_of[-2]

            current_close = number(
                current.get("close")
            )

            previous_close = number(
                previous.get("close")
            )

            if (
                current_close is not None
                and previous_close is not None
            ):

                if (
                    current_close
                    > previous_close
                ):

                    advancing += 1

                elif (
                    current_close
                    < previous_close
                ):

                    declining += 1

                else:

                    unchanged += 1

        # ----------------------------------------------------
        # MA20 / New high / New low
        # ----------------------------------------------------

        if len(as_of) >= 20:

            ma_window = as_of[
                -20:
            ]

            ma20 = (
                sum(
                    row["close"]
                    for row in ma_window
                    if row.get("close")
                    is not None
                )
                / 20
            )

            if (
                len(
                    [
                        row
                        for row in ma_window
                        if row.get("close")
                        is not None
                    ]
                )
                == 20
            ):

                ma20_valid += 1

                if (
                    current["close"]
                    > ma20
                ):

                    above_ma20 += 1

            # ------------------------------------------------
            # 20-day breakout:
            # compare current against PRIOR 20 sessions,
            # not a window that includes current itself.
            # ------------------------------------------------

            if len(as_of) >= 21:

                previous_20 = as_of[
                    -21:-1
                ]

                previous_closes = [
                    row["close"]
                    for row in previous_20
                    if row.get("close")
                    is not None
                ]

                if len(
                    previous_closes
                ) == 20:

                    previous_high = max(
                        previous_closes
                    )

                    previous_low = min(
                        previous_closes
                    )

                    if (
                        current["close"]
                        >= previous_high
                    ):

                        new_high_20d += 1

                    if (
                        current["close"]
                        <= previous_low
                    ):

                        new_low_20d += 1

        # ----------------------------------------------------
        # Volume
        # ----------------------------------------------------

        for row in as_of:

            row_volume = number(
                row.get("volume")
            )

            if row_volume is None:
                continue

            trading_date = row[
                "date"
            ]

            daily_volume_totals[
                trading_date
            ] += row_volume

            daily_volume_symbols[
                trading_date
            ] += 1

        current_row_volume = number(
            current.get("volume")
        )

        if (
            current_row_volume is not None
        ):

            current_volume += (
                current_row_volume
            )

            current_volume_symbols += 1

    # --------------------------------------------------------
    # Breadth ratios
    # --------------------------------------------------------

    if declining > 0:

        advance_decline_ratio = (
            advancing
            / declining
        )

    elif advancing > 0:

        advance_decline_ratio = (
            float("inf")
        )

    else:

        advance_decline_ratio = None

    above_ma20_pct = (
        above_ma20
        / ma20_valid
        if ma20_valid > 0
        else None
    )

    # --------------------------------------------------------
    # New high / new low
    # --------------------------------------------------------

    if new_low_20d > 0:

        new_high_low_ratio = (
            new_high_20d
            / new_low_20d
        )

    elif new_high_20d > 0:

        new_high_low_ratio = (
            float("inf")
        )

    else:

        new_high_low_ratio = None

    # --------------------------------------------------------
    # Volume ratio
    #
    # Use previous 20 market trading days.
    # Do not use a mixed-stock average.
    # --------------------------------------------------------

    historical_dates = sorted(
        trading_date
        for trading_date
        in daily_volume_totals
        if trading_date < latest_date
    )

    previous_dates = historical_dates[
        -CONFIG[
            "volume_ma_period"
        ]:
    ]

    average_20d_volume = None

    if len(
        previous_dates
    ) >= CONFIG[
        "volume_ma_period"
    ]:

        previous_totals = [
            daily_volume_totals[
                trading_date
            ]
            for trading_date
            in previous_dates
        ]

        average_20d_volume = (
            sum(
                previous_totals
            )
            / len(
                previous_totals
            )
        )

    volume_ratio = None

    if (
        average_20d_volume is not None
        and average_20d_volume > 0
        and current_volume > 0
    ):

        volume_ratio = (
            current_volume
            / average_20d_volume
        )

    # --------------------------------------------------------
    # Volume date coverage
    # --------------------------------------------------------

    current_volume_date_coverage = (
        daily_volume_symbols.get(
            latest_date,
            0,
        )
    )

    return {
        "advancing":
            advancing,

        "declining":
            declining,

        "unchanged":
            unchanged,

        "advance_decline_ratio":
            advance_decline_ratio,

        "above_ma20":
            above_ma20,

        "ma20_valid":
            ma20_valid,

        "above_ma20_pct":
            above_ma20_pct,

        "new_high_20d":
            new_high_20d,

        "new_low_20d":
            new_low_20d,

        "new_high_low_ratio":
            new_high_low_ratio,

        "volume":
            (
                current_volume
                if current_volume_symbols
                else None
            ),

        "volume_20d_average":
            average_20d_volume,

        "volume_ratio":
            volume_ratio,

        "coverage":
            coverage,

        "volume_valid_symbols":
            current_volume_symbols,

        "volume_date_coverage":
            current_volume_date_coverage,

        "historical_volume_days":
            len(previous_dates),
    }


# ============================================================
# 10. TWSE T86
# ============================================================

def fetch_twse_institutional(
    trading_date: date,
) -> Tuple[
    Optional[float],
    Optional[float],
    str,
]:

    params = {
        "response":
            "json",

        "date":
            trading_date.strftime(
                "%Y%m%d"
            ),

        "selectType":
            "ALLBUT0999",
    }

    try:

        payload = request_json(
            TWSE_T86_URL,
            params,
        )

    except Exception as exc:

        return (
            None,
            None,
            "TWSE T86 unavailable: "
            f"{exc}",
        )

    rows = table_rows(
        payload
    )

    if not rows:

        rows = list_dict_rows(
            payload
        )

    if not rows:

        rows = recursive_dict_rows(
            payload
        )

    foreign_net = 0.0
    trust_net = 0.0

    foreign_found = False
    trust_found = False

    for row in rows:

        # Exact official T86 fields.
        foreign_value = number(
            get_value(
                row,
                (
                    "外陸資買賣超股數"
                    "(不含外資自營商)",

                    "外陸資買賣超股數"
                    "(不含外資自營商)",

                    "Foreign Investors "
                    "including "
                    "Mainland Area Investors "
                    "(Foreign Dealers "
                    "excluded)-Difference",

                    "Foreign Investors "
                    "include Mainland "
                    "Area Investors "
                    "(Foreign Dealers "
                    "excluded)-Difference",
                ),
            )
        )

        # Some T86 variants may use a slightly
        # different label.
        if foreign_value is None:

            for key, value in row.items():

                key_text = str(
                    key
                )

                if (
                    "外陸資"
                    in key_text
                    and "買賣超"
                    in key_text
                    and "不含外資自營商"
                    in key_text
                ):

                    foreign_value = (
                        number(value)
                    )

                    break

        trust_value = number(
            get_value(
                row,
                (
                    "投信買賣超股數",

                    "Securities Investment "
                    "Trust Companies-Difference",

                    "SecuritiesInvestmentTrustCompanies"
                    "-Difference",
                ),
            )
        )

        if trust_value is None:

            for key, value in row.items():

                key_text = str(
                    key
                )

                if (
                    "投信"
                    in key_text
                    and "買賣超"
                    in key_text
                ):

                    trust_value = (
                        number(value)
                    )

                    break

        if foreign_value is not None:

            foreign_net += (
                foreign_value
            )

            foreign_found = True

        if trust_value is not None:

            trust_net += (
                trust_value
            )

            trust_found = True

    return (
        (
            foreign_net
            if foreign_found
            else None
        ),

        (
            trust_net
            if trust_found
            else None
        ),

        "TWSE T86",
    )


# ============================================================
# 11. TPEx INSTITUTIONAL
# ============================================================

def fetch_tpex_institutional() -> Tuple[
    Optional[float],
    Optional[float],
    str,
]:

    try:

        payload = request_json(
            TPEX_INSTITUTIONAL_URL
        )

    except Exception as exc:

        return (
            None,
            None,
            "TPEx "
            "tpex_3insti_daily_trading "
            f"unavailable: {exc}",
        )

    rows = list_dict_rows(
        payload
    )

    if not rows:

        rows = recursive_dict_rows(
            payload
        )

    foreign_total = 0.0
    trust_total = 0.0

    foreign_count = 0
    trust_count = 0

    foreign_candidates = (
        "Foreign Investors include "
        "Mainland Area Investors "
        "(Foreign Dealers excluded)"
        "-Difference",

        "Foreign Investors include "
        "Mainland Area Investors "
        "(Foreign Dealers excluded)"
        "Difference",

        "Foreign Investors include "
        "Mainland Area Investors "
        "(Foreign Dealers excluded)-Difference",
    )

    trust_candidates = (
        "SecuritiesInvestmentTrustCompanies"
        "-Difference",

        "Securities Investment Trust "
        "Companies-Difference",

        "SecuritiesInvestmentTrustCompanies"
        "Difference",
    )

    for row in rows:

        foreign = number(
            get_value(
                row,
                foreign_candidates,
            )
        )

        trust = number(
            get_value(
                row,
                trust_candidates,
            )
        )

        # Fuzzy fallback.
        if foreign is None:

            for key, value in row.items():

                key_text = str(
                    key
                )

                if (
                    "Foreign Investors"
                    in key_text
                    and "Difference"
                    in key_text
                ):

                    foreign = number(
                        value
                    )

                    break

        if trust is None:

            for key, value in row.items():

                key_text = str(
                    key
                )

                if (
                    "SecuritiesInvestmentTrust"
                    in key_text
                    and "Difference"
                    in key_text
                ):

                    trust = number(
                        value
                    )

                    break

        if foreign is not None:

            foreign_total += foreign
            foreign_count += 1

        if trust is not None:

            trust_total += trust
            trust_count += 1

    return (
        (
            foreign_total
            if foreign_count
            else None
        ),

        (
            trust_total
            if trust_count
            else None
        ),

        "TPEx "
        "tpex_3insti_daily_trading",
    )


# ============================================================
# 12. COMBINED INSTITUTIONAL
# ============================================================

def fetch_institutional(
    trading_date: date,
) -> Dict[str, Any]:

    (
        twse_foreign,
        twse_trust,
        twse_source,
    ) = fetch_twse_institutional(
        trading_date
    )

    (
        tpex_foreign,
        tpex_trust,
        tpex_source,
    ) = fetch_tpex_institutional()

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
        sum(
            foreign_parts
        )
        if foreign_parts
        else None
    )

    trust_net = (
        sum(
            trust_parts
        )
        if trust_parts
        else None
    )

    status = (
        "ok"
        if (
            twse_foreign is not None
            and twse_trust is not None
            and tpex_foreign is not None
            and tpex_trust is not None
        )
        else "partial/unavailable"
    )

    return {
        "foreign_net":
            foreign_net,

        "trust_net":
            trust_net,

        "twse_foreign_net":
            twse_foreign,

        "twse_trust_net":
            twse_trust,

        "tpex_foreign_net":
            tpex_foreign,

        "tpex_trust_net":
            tpex_trust,

        "status":
            status,

        "sources": [
            twse_source,
            tpex_source,
        ],
    }


# ============================================================
# 13. CONDITION
# ============================================================

def make_condition(
    name: str,
    value: Any,
    passed: Optional[bool],
    threshold: Any,
    unit: str = "",
) -> Dict[str, Any]:

    if passed is True:

        status = "pass"

    elif passed is False:

        status = "fail"

    else:

        status = "unavailable"

    return {
        "name":
            name,

        "value":
            value,

        "pass":
            passed,

        "threshold":
            threshold,

        "unit":
            unit,

        "status":
            status,
    }


# ============================================================
# 14. SENTIMENT
# ============================================================

def build_sentiment(
    conditions: List[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    valid = [
        condition
        for condition in conditions
        if condition.get("pass")
        is not None
    ]

    score = sum(
        1
        for condition in valid
        if condition.get("pass")
        is True
    )

    if len(valid) < CONFIG[
        "minimum_valid_conditions"
    ]:

        level = "資料不足"

        description = (
            "有效市場條件不足，"
            "停止放大風險"
        )

    elif score >= CONFIG[
        "score_bullish"
    ]:

        level = "偏多"

        description = (
            "市場氣氛偏強"
        )

    elif score >= CONFIG[
        "score_sideways"
    ]:

        level = "震盪"

        description = (
            "多空力量接近"
        )

    else:

        level = "偏弱"

        description = (
            "市場氣氛偏弱"
        )

    return {
        "level":
            level,

        "description":
            description,

        "score":
            score,

        "valid_conditions":
            len(valid),

        "total_conditions":
            len(conditions),
    }


# ============================================================
# 15. JSON CLEAN
# ============================================================

def clean_json(
    value: Any,
) -> Any:

    if isinstance(
        value,
        float,
    ):

        if not math.isfinite(
            value
        ):

            return None

        return value

    if isinstance(
        value,
        date,
    ):

        return value.isoformat()

    if isinstance(
        value,
        dict,
    ):

        return {
            key:
                clean_json(item)
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        list,
    ):

        return [
            clean_json(item)
            for item in value
        ]

    return value


# ============================================================
# 16. ATOMIC WRITE
# ============================================================

def atomic_write(
    data: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = (
        tempfile.mkstemp(
            prefix=".market.",
            suffix=".tmp",
            dir=DATA_DIR,
        )
    )

    temp_path = Path(
        temp_name
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                clean_json(data),
                file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )

            file.write(
                "\n"
            )

            file.flush()

            os.fsync(
                file.fileno()
            )

        os.replace(
            temp_path,
            OUTPUT_FILE,
        )

    finally:

        temp_path.unlink(
            missing_ok=True
        )


# ============================================================
# 17. VALIDATION
# ============================================================

def validate_market(
    data: Dict[str, Any],
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
        - set(data)
    )

    if missing:

        raise RuntimeError(
            "market.json 缺少欄位："
            f"{sorted(missing)}"
        )

    if data[
        "schema_version"
    ] != SCHEMA_VERSION:

        raise RuntimeError(
            "market.json "
            "schema_version 錯誤"
        )

    if data[
        "market_status"
    ] not in {
        "open",
        "closed",
    }:

        raise RuntimeError(
            "market_status 無效"
        )

    conditions = data[
        "conditions"
    ]

    if not isinstance(
        conditions,
        list,
    ):

        raise RuntimeError(
            "conditions 必須是 list"
        )

    if len(
        conditions
    ) != 10:

        raise RuntimeError(
            "市場核心條件必須正好 10 項"
        )

    expected_names = [
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

    actual_names = [
        item.get("name")
        for item in conditions
        if isinstance(
            item,
            dict,
        )
    ]

    if actual_names != expected_names:

        raise RuntimeError(
            "市場核心條件名稱或順序錯誤"
        )

    for item in conditions:

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                "condition 必須是 object"
            )

        if item.get(
            "status"
        ) not in {
            "pass",
            "fail",
            "unavailable",
        }:

            raise RuntimeError(
                "condition status 無效"
            )

        passed = item.get(
            "pass"
        )

        if passed not in {
            True,
            False,
            None,
        }:

            raise RuntimeError(
                "condition pass 必須是 "
                "true / false / null"
            )

    sentiment = data[
        "sentiment"
    ]

    if sentiment.get(
        "level"
    ) not in {
        "偏多",
        "震盪",
        "偏弱",
        "資料不足",
    }:

        raise RuntimeError(
            "市場風向 level 無效"
        )

    index_value = number(
        data[
            "index"
        ].get("value")
    )

    if index_value is None:

        raise RuntimeError(
            "TAIEX value 無效"
        )

    breadth = data[
        "breadth"
    ]

    coverage = number(
        breadth.get(
            "coverage"
        )
    )

    if (
        coverage is None
        or coverage < 0
    ):

        raise RuntimeError(
            "breadth coverage 無效"
        )

    institutional = data[
        "institutional"
    ]

    if institutional.get(
        "status"
    ) not in {
        "ok",
        "partial/unavailable",
    }:

        raise RuntimeError(
            "institutional status 無效"
        )


# ============================================================
# 18. MAIN
# ============================================================

def main() -> int:

    log("=" * 72)
    log("FETCH MARKET V2.1")
    log("=" * 72)

    now = datetime.now(
        TAIWAN_TZ
    )

    # --------------------------------------------------------
    # TAIEX CURRENT
    # --------------------------------------------------------

    latest_date, index = (
        fetch_index()
    )

    log(
        f"最新交易日："
        f"{latest_date}"
    )

    log(
        f"加權指數："
        f"{index['value']}"
    )

    # --------------------------------------------------------
    # TAIEX HISTORY
    # --------------------------------------------------------

    index_history = (
        fetch_index_history()
    )

    index_history = [
        row
        for row in index_history
        if row["date"]
        <= latest_date
    ]

    # Ensure current index exists.
    existing_index_dates = {
        row["date"]
        for row in index_history
    }

    if latest_date not in (
        existing_index_dates
    ):

        index_history.append(
            {
                "date":
                    latest_date,

                "close":
                    index["value"],

                "high":
                    index["value"],

                "low":
                    index["value"],
            }
        )

    index_history.sort(
        key=lambda x: x["date"]
    )

    log(
        "TAIEX 歷史交易日："
        f"{len(index_history)}"
    )

    index_metrics = (
        calculate_index_metrics(
            index_history
        )
    )

    # --------------------------------------------------------
    # UNIVERSE
    # --------------------------------------------------------

    universe = load_universe()

    stock_universe = {
        symbol:
            item
        for symbol, item
        in universe.items()
        if is_stock(item)
    }

    log(
        "Active Universe："
        f"{len(universe)}"
    )

    log(
        "一般股票 Universe："
        f"{len(stock_universe)}"
    )

    # --------------------------------------------------------
    # PRICE SHARDS
    # --------------------------------------------------------

    (
        histories,
        price_metadata,
    ) = load_price_histories(
        universe
    )

    log(
        "Price shard 載入："
        f"{price_metadata['loaded_files']}/"
        f"{price_metadata['requested_files']}"
    )

    log(
        "價格歷史合併股票："
        f"{price_metadata['merged_symbols']}"
    )

    # --------------------------------------------------------
    # BREADTH
    # --------------------------------------------------------

    breadth = (
        calculate_market_breadth(
            histories,
            latest_date,
        )
    )

    log(
        "市場 breadth coverage："
        f"{breadth['coverage']}"
    )

    log(
        "MA20 valid："
        f"{breadth['ma20_valid']}"
    )

    log(
        "Volume current coverage："
        f"{breadth['volume_valid_symbols']}"
    )

    # --------------------------------------------------------
    # INSTITUTIONAL
    # --------------------------------------------------------

    institutional = (
        fetch_institutional(
            latest_date
        )
    )

    log(
        "法人資料："
        f"{institutional['status']}"
    )

    # --------------------------------------------------------
    # 10 CORE CONDITIONS
    # --------------------------------------------------------

    close = index[
        "value"
    ]

    conditions = [

        make_condition(
            "TAIEX > MA20",

            index_metrics[
                "ma20"
            ],

            (
                close
                > index_metrics[
                    "ma20"
                ]
                if index_metrics[
                    "ma20"
                ] is not None
                else None
            ),

            "> MA20",
        ),

        make_condition(
            "MA20 上升",

            index_metrics[
                "ma20_slope"
            ],

            (
                index_metrics[
                    "ma20_slope"
                ] > 0
                if index_metrics[
                    "ma20_slope"
                ] is not None
                else None
            ),

            "> 0",
        ),

        make_condition(
            "TAIEX RSI14 > 50",

            index_metrics[
                "rsi14"
            ],

            (
                index_metrics[
                    "rsi14"
                ] > 50
                if index_metrics[
                    "rsi14"
                ] is not None
                else None
            ),

            50,
        ),

        make_condition(
            "上漲家數 / 下跌家數 >= 1",

            breadth[
                "advance_decline_ratio"
            ],

            (
                breadth[
                    "advance_decline_ratio"
                ] >= 1
                if breadth[
                    "advance_decline_ratio"
                ] is not None
                else None
            ),

            1,
        ),

        make_condition(
            "站上 MA20 比例 >= 50%",

            breadth[
                "above_ma20_pct"
            ],

            (
                breadth[
                    "above_ma20_pct"
                ] >= 0.50
                if breadth[
                    "above_ma20_pct"
                ] is not None
                else None
            ),

            0.50,
        ),

        make_condition(
            "市場成交量 / 20日均量 >= 1",

            breadth[
                "volume_ratio"
            ],

            (
                breadth[
                    "volume_ratio"
                ] >= 1
                if breadth[
                    "volume_ratio"
                ] is not None
                else None
            ),

            1,
        ),

        make_condition(
            "外資買賣超 > 0",

            institutional[
                "foreign_net"
            ],

            (
                institutional[
                    "foreign_net"
                ] > 0
                if institutional[
                    "foreign_net"
                ] is not None
                else None
            ),

            0,
        ),

        make_condition(
            "投信買賣超 > 0",

            institutional[
                "trust_net"
            ],

            (
                institutional[
                    "trust_net"
                ] > 0
                if institutional[
                    "trust_net"
                ] is not None
                else None
            ),

            0,
        ),

        make_condition(
            "20日新高 / 新低 >= 1",

            breadth[
                "new_high_low_ratio"
            ],

            (
                breadth[
                    "new_high_low_ratio"
                ] >= 1
                if breadth[
                    "new_high_low_ratio"
                ] is not None
                else None
            ),

            1,
        ),

        make_condition(
            "TAIEX ATR14% <= 3%",

            index_metrics[
                "atr14_pct"
            ],

            (
                index_metrics[
                    "atr14_pct"
                ] <= 0.03
                if index_metrics[
                    "atr14_pct"
                ] is not None
                else None
            ),

            0.03,
        ),
    ]

    market_sentiment = (
        build_sentiment(
            conditions
        )
    )

    # --------------------------------------------------------
    # MARKET STATUS
    # --------------------------------------------------------

    current_time = now.time()

    market_status = (
        "open"
        if (
            now.weekday() < 5
            and now.date()
            == latest_date
            and time(9, 0)
            <= current_time
            <= time(13, 30)
        )
        else "closed"
    )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    market_data = {

        "schema_version":
            SCHEMA_VERSION,

        "generated_at":
            now.isoformat(
                timespec="seconds"
            ),

        "market_status":
            market_status,

        "latest_trading_date":
            latest_date.isoformat(),

        "index":
            index,

        "trend":
            {
                "ma20":
                    index_metrics[
                        "ma20"
                    ],

                "ma20_previous":
                    index_metrics[
                        "ma20_previous"
                    ],

                "ma20_slope":
                    index_metrics[
                        "ma20_slope"
                    ],

                "rsi14":
                    index_metrics[
                        "rsi14"
                    ],

                "atr14":
                    index_metrics[
                        "atr14"
                    ],

                "atr14_pct":
                    index_metrics[
                        "atr14_pct"
                    ],

                "history_count":
                    index_metrics[
                        "history_count"
                    ],
            },

        "breadth":
            breadth,

        "volume":
            {
                "current":
                    breadth[
                        "volume"
                    ],

                "average_20d":
                    breadth[
                        "volume_20d_average"
                    ],

                "ratio":
                    breadth[
                        "volume_ratio"
                    ],
            },

        "institutional":
            institutional,

        "sentiment":
            market_sentiment,

        "conditions":
            conditions,

        "source":
            {
                "provider":
                    "TWSE + TPEx official",

                "index":
                    (
                        "TWSE MI_INDEX / "
                        "MI_5MINS_HIST"
                    ),

                "prices":
                    (
                        "Data/prices "
                        "prices-v14.0 "
                        "official-priority shards"
                    ),

                "institutional":
                    [
                        "TWSE T86",
                        (
                            "TPEx "
                            "tpex_3insti_daily_trading"
                        ),
                    ],
            },

        "coverage":
            {
                "active_universe":
                    len(universe),

                "stock_universe":
                    len(stock_universe),

                "price_history_symbols":
                    len(histories),

                "breadth_symbols":
                    breadth[
                        "coverage"
                    ],

                "price_coverage_pct":
                    (
                        len(histories)
                        / len(stock_universe)
                        if stock_universe
                        else None
                    ),

                "price_shards":
                    price_metadata,
            },

        "config":
            CONFIG,
    }

    # --------------------------------------------------------
    # WRITE
    # --------------------------------------------------------

    atomic_write(
        market_data
    )

    # --------------------------------------------------------
    # READ BACK
    # --------------------------------------------------------

    read_back = json.loads(
        OUTPUT_FILE.read_text(
            encoding="utf-8"
        )
    )

    validate_market(
        read_back
    )

    # --------------------------------------------------------
    # FINAL LOG
    # --------------------------------------------------------

    log("")
    log("=" * 72)
    log("FINAL MARKET VALIDATION")
    log("=" * 72)

    log(
        "市場條件："
        f"{market_sentiment['score']}/10"
        f" "
        f"valid="
        f"{market_sentiment['valid_conditions']}/10"
    )

    log(
        "市場風向："
        f"{market_sentiment['level']}"
    )

    log(
        "TAIEX MA20："
        f"{index_metrics['ma20']}"
    )

    log(
        "TAIEX RSI14："
        f"{index_metrics['rsi14']}"
    )

    log(
        "TAIEX ATR14%："
        f"{index_metrics['atr14_pct']}"
    )

    log(
        "上漲 / 下跌："
        f"{breadth['advancing']} / "
        f"{breadth['declining']}"
    )

    log(
        "站上 MA20："
        f"{breadth['above_ma20']}/"
        f"{breadth['ma20_valid']}"
    )

    log(
        "20日新高 / 新低："
        f"{breadth['new_high_20d']} / "
        f"{breadth['new_low_20d']}"
    )

    log(
        "成交量 ratio："
        f"{breadth['volume_ratio']}"
    )

    log(
        "外資買賣超："
        f"{institutional['foreign_net']}"
    )

    log(
        "投信買賣超："
        f"{institutional['trust_net']}"
    )

    log(
        "價格歷史覆蓋："
        f"{breadth['coverage']} 檔"
    )

    log(
        "價格覆蓋率："
        f"{market_data['coverage']['price_coverage_pct']}"
    )

    log(
        "✓ market.json validation PASS"
    )

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )