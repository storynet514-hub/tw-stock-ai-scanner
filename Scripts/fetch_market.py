#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_market.py
============================================================

MARKET ENVIRONMENT V2.1

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 來源
2. 只處理 status == "active"
3. Data/prices/manifest.json 是價格 shard 唯一索引
4. 價格 coverage 必須從實際 shard 解析
5. 不假設 shard 固定為單一 JSON schema
6. 支援：
      - code -> rows
      - symbols -> code -> rows
      - stocks -> code -> history
      - data / rows / prices / records / items
      - row dict
      - compact row list
7. 價格資料只接受：
      code + date + close
8. 不使用 Yahoo / CMoney / 其他非官方資料補洞
9. TAIEX 使用 TWSE 官方 API
10. TWSE 三大法人使用官方 T86
11. TPEx 三大法人使用官方 OpenAPI
12. 官方資料不足時標記 unavailable
13. 不偽造、不推算法人資料
14. Data/market.json 採 atomic write
15. market.json 必須符合 market-v2.1
16. 最終 validation 必須通過
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = (
    DATA_DIR / "universe.json"
)

PRICES_DIR = (
    DATA_DIR / "prices"
)

MANIFEST_FILE = (
    PRICES_DIR / "manifest.json"
)

MARKET_FILE = (
    DATA_DIR / "market.json"
)


# ============================================================
# OFFICIAL API
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://openapi.twse.com.tw/"
    "v1/exchangeReport/MI_INDEX"
)

TWSE_T86_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/fund/T86"
)

TPEX_3INSTI_URL = (
    "https://www.tpex.org.tw/"
    "openapi/v1/"
    "tpex_3insti_daily_trading"
)


# ============================================================
# CONFIG
# ============================================================

HTTP_TIMEOUT = 30
HTTP_RETRIES = 3

TAIPEI_TZ = ZoneInfo(
    "Asia/Taipei"
)


# ============================================================
# FIELD ALIASES
# ============================================================

CODE_KEYS = (
    "code",
    "Code",
    "CODE",
    "stock_code",
    "stockCode",
    "symbol",
    "Symbol",
    "證券代號",
    "股票代號",
)

DATE_KEYS = (
    "date",
    "Date",
    "DATE",
    "日期",
    "交易日期",
    "交易日",
    "資料日期",
    "d",
)

CLOSE_KEYS = (
    "close",
    "Close",
    "CLOSE",
    "close_price",
    "closePrice",
    "收盤價",
    "收盤",
    "c",
)

OPEN_KEYS = (
    "open",
    "Open",
    "OPEN",
    "open_price",
    "openPrice",
    "開盤價",
    "o",
)

HIGH_KEYS = (
    "high",
    "High",
    "HIGH",
    "high_price",
    "highPrice",
    "最高價",
    "h",
)

LOW_KEYS = (
    "low",
    "Low",
    "LOW",
    "low_price",
    "lowPrice",
    "最低價",
    "l",
)

VOLUME_KEYS = (
    "volume",
    "Volume",
    "VOLUME",
    "成交量",
    "成交股數",
    "volume_shares",
    "v",
)


# ============================================================
# CONTAINER KEYS
# ============================================================

CONTAINER_KEYS = {
    "data",
    "rows",
    "row",
    "prices",
    "price",
    "records",
    "record",
    "items",
    "item",
    "results",
    "result",
    "list",
    "symbols",
    "symbol",
    "stocks",
    "stock",
    "history",
    "histories",
    "series",
    "quotes",
    "quote",
    "payload",
    "response",
    "content",
    "values",
}


# ============================================================
# LOG
# ============================================================

def log(
    message: str = "",
) -> None:
    print(
        message,
        flush=True,
    )


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_key(
    value: Any,
) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\r", "")
        .replace("\n", "")
        .replace("\t", "")
        .replace("\u3000", " ")
        .strip()
    )


def to_number(
    value: Any,
) -> float | None:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(
        value,
        (int, float),
    ):
        number = float(value)

    else:

        text = (
            str(value)
            .strip()
            .replace(",", "")
            .replace(" ", "")
            .replace("\u3000", "")
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
            "nan",
            "NaN",
        }:
            return None

        try:
            number = float(text)

        except ValueError:
            return None

    if not math.isfinite(number):
        return None

    return number


def normalize_code(
    value: Any,
) -> str | None:

    if value is None:
        return None

    text = normalize_key(
        value
    ).upper()

    if text.endswith(".TW"):
        text = text[:-3]

    elif text.endswith(".TWO"):
        text = text[:-4]

    if not re.fullmatch(
        r"[A-Z0-9]{4,6}",
        text,
    ):
        return None

    if (
        text.lower()
        in CONTAINER_KEYS
    ):
        return None

    return text


def normalize_date(
    value: Any,
) -> str | None:

    text = normalize_key(
        value
    )

    if not text:
        return None

    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    )

    for fmt in formats:

        try:

            return datetime.strptime(
                text,
                fmt,
            ).strftime(
                "%Y-%m-%d"
            )

        except ValueError:
            pass

    # 民國日期：
    # 1150903
    match = re.fullmatch(
        r"(\d{3})(\d{2})(\d{2})",
        text,
    )

    if match:

        try:

            year = (
                int(match.group(1))
                + 1911
            )

            month = int(
                match.group(2)
            )

            day = int(
                match.group(3)
            )

            return datetime(
                year,
                month,
                day,
            ).strftime(
                "%Y-%m-%d"
            )

        except ValueError:
            return None

    # embedded YYYY/MM/DD
    match = re.search(
        r"(20\d{2})[-/]"
        r"(\d{1,2})[-/]"
        r"(\d{1,2})",
        text,
    )

    if match:

        try:

            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            ).strftime(
                "%Y-%m-%d"
            )

        except ValueError:
            return None

    return None


def first_value(
    obj: dict[str, Any],
    keys: tuple[str, ...],
) -> Any:

    # exact
    for key in keys:

        if key in obj:
            return obj[key]

    # normalized exact
    normalized = {
        normalize_key(key).lower(): value
        for key, value in obj.items()
    }

    for key in keys:

        normalized_key = (
            normalize_key(key)
            .lower()
        )

        if normalized_key in normalized:
            return normalized[
                normalized_key
            ]

    return None


# ============================================================
# JSON
# ============================================================

def load_json(
    path: Path,
) -> Any:

    with path.open(
        "r",
        encoding="utf-8",
    ) as fh:

        return json.load(fh)


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> set[str]:

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "Missing universe file: "
            f"{UNIVERSE_FILE}"
        )

    payload = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        payload,
        dict,
    ):

        raise RuntimeError(
            "universe.json must be "
            "a JSON object"
        )

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.json: stocks "
            "must be dict"
        )

    universe: set[str] = set()

    for raw_code, info in stocks.items():

        if not isinstance(
            info,
            dict,
        ):
            continue

        status = str(
            info.get(
                "status",
                "",
            )
        ).strip().lower()

        if status != "active":
            continue

        stock_code = normalize_code(
            raw_code
        )

        if stock_code:
            universe.add(
                stock_code
            )

    if not universe:

        raise RuntimeError(
            "universe.json contains "
            "no active instruments"
        )

    return universe


# ============================================================
# MANIFEST
# ============================================================

def load_manifest_files() -> list[Path]:

    if not MANIFEST_FILE.exists():

        raise RuntimeError(
            "Missing price manifest: "
            f"{MANIFEST_FILE}"
        )

    payload = load_json(
        MANIFEST_FILE
    )

    candidates: Any = None

    if isinstance(
        payload,
        list,
    ):

        candidates = payload

    elif isinstance(
        payload,
        dict,
    ):

        for key in (
            "files",
            "shards",
            "parts",
            "prices",
        ):

            value = payload.get(
                key
            )

            if isinstance(
                value,
                list,
            ):

                candidates = value
                break

    names: list[str] = []

    if isinstance(
        candidates,
        list,
    ):

        for item in candidates:

            if isinstance(
                item,
                str,
            ):

                name = item.strip()

            elif isinstance(
                item,
                dict,
            ):

                name = str(
                    item.get("file")
                    or item.get(
                        "filename"
                    )
                    or item.get("path")
                    or item.get("name")
                    or item.get("shard")
                    or ""
                ).strip()

            else:
                continue

            if not name:
                continue

            path = Path(name)

            if path.name == "manifest.json":
                continue

            if path.is_absolute():
                continue

            if ".." in path.parts:
                continue

            names.append(
                path.name
            )

    # manifest 沒有 files 時才 discovery
    if not names:

        names = [
            path.name
            for path in sorted(
                PRICES_DIR.glob(
                    "*.json"
                )
            )
            if path.name
            != "manifest.json"
        ]

    names = list(
        dict.fromkeys(
            names
        )
    )

    if not names:

        raise RuntimeError(
            "No price shard files found"
        )

    return [
        PRICES_DIR / name
        for name in names
    ]


# ============================================================
# PRICE RECORD
# ============================================================

def make_price_record(
    obj: dict[str, Any],
    inherited_code: str | None = None,
) -> dict[str, Any] | None:

    own_code = normalize_code(
        first_value(
            obj,
            CODE_KEYS,
        )
    )

    stock_code = (
        own_code
        or inherited_code
    )

    trading_date = normalize_date(
        first_value(
            obj,
            DATE_KEYS,
        )
    )

    close = to_number(
        first_value(
            obj,
            CLOSE_KEYS,
        )
    )

    if stock_code is None:
        return None

    if trading_date is None:
        return None

    if close is None:
        return None

    if close <= 0:
        return None

    if close > 1_000_000_000:
        return None

    return {
        "code": stock_code,
        "date": trading_date,
        "close": close,
        "open": to_number(
            first_value(
                obj,
                OPEN_KEYS,
            )
        ),
        "high": to_number(
            first_value(
                obj,
                HIGH_KEYS,
            )
        ),
        "low": to_number(
            first_value(
                obj,
                LOW_KEYS,
            )
        ),
        "volume": to_number(
            first_value(
                obj,
                VOLUME_KEYS,
            )
        ),
    }


# ============================================================
# COMPACT LIST ROW
# ============================================================

def make_list_record(
    row: list[Any],
    inherited_code: str | None,
) -> dict[str, Any] | None:

    if not row:
        return None

    if inherited_code is None:
        return None

    trading_date = normalize_date(
        row[0]
    )

    if trading_date is None:
        return None

    values: list[float] = []

    for value in row[1:]:

        number = to_number(
            value
        )

        if number is not None:
            values.append(
                number
            )

    if not values:
        return None

    # [date, open, high, low, close, volume]
    if len(values) >= 5:

        open_price = values[-5]
        high_price = values[-4]
        low_price = values[-3]
        close = values[-2]
        volume = values[-1]

    # [date, close, volume]
    elif len(values) >= 2:

        open_price = None
        high_price = None
        low_price = None

        close = values[-2]
        volume = values[-1]

    # [date, close]
    else:

        open_price = None
        high_price = None
        low_price = None

        close = values[-1]
        volume = None

    if close <= 0:
        return None

    return {
        "code": inherited_code,
        "date": trading_date,
        "close": close,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "volume": volume,
    }


# ============================================================
# RECURSIVE SHARD WALKER
# ============================================================

def walk_price_records(
    value: Any,
    inherited_code: str | None = None,
    depth: int = 0,
) -> Iterator[dict[str, Any]]:

    if depth > 40:
        return

    # --------------------------------------------------------
    # dict
    # --------------------------------------------------------

    if isinstance(
        value,
        dict,
    ):

        own_code = normalize_code(
            first_value(
                value,
                CODE_KEYS,
            )
        )

        current_code = (
            own_code
            or inherited_code
        )

        direct_record = (
            make_price_record(
                value,
                current_code,
            )
        )

        if direct_record:
            yield direct_record

        for key, child in value.items():

            key_text = normalize_key(
                key
            )

            key_lower = (
                key_text.lower()
            )

            child_code = current_code

            # 只有真正像股票代號的 key
            # 才允許作為 inherited code
            if (
                key_lower
                not in CONTAINER_KEYS
            ):

                candidate = (
                    normalize_code(
                        key_text
                    )
                )

                if candidate:
                    child_code = candidate

            yield from walk_price_records(
                child,
                child_code,
                depth + 1,
            )

        return

    # --------------------------------------------------------
    # list
    # --------------------------------------------------------

    if isinstance(
        value,
        list,
    ):

        for item in value:

            if isinstance(
                item,
                list,
            ):

                record = (
                    make_list_record(
                        item,
                        inherited_code,
                    )
                )

                if record:
                    yield record

            else:

                yield from walk_price_records(
                    item,
                    inherited_code,
                    depth + 1,
                )


# ============================================================
# PRICE SHARDS
# ============================================================

def parse_price_shards(
    universe: set[str],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, int]],
]:

    by_code: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    shard_stats: dict[
        str,
        dict[str, int],
    ] = {}

    shard_files = (
        load_manifest_files()
    )

    log("")
    log("PRICE SHARDS")
    log("-" * 72)

    for shard_path in shard_files:

        stats = {
            "records_seen": 0,
            "valid_records": 0,
            "covered_symbols": 0,
        }

        if not shard_path.exists():

            log(
                f"WARNING: missing shard "
                f"{shard_path.name}"
            )

            shard_stats[
                shard_path.name
            ] = stats

            continue

        try:

            payload = load_json(
                shard_path
            )

        except Exception as exc:

            log(
                f"WARNING: invalid JSON "
                f"{shard_path.name}: "
                f"{exc}"
            )

            shard_stats[
                shard_path.name
            ] = stats

            continue

        shard_codes: set[str] = set()

        seen: set[
            tuple[str, str]
        ] = set()

        for record in walk_price_records(
            payload
        ):

            stats[
                "records_seen"
            ] += 1

            stock_code = record[
                "code"
            ]

            if stock_code not in universe:
                continue

            key = (
                stock_code,
                record["date"],
            )

            if key in seen:
                continue

            seen.add(key)

            by_code.setdefault(
                stock_code,
                [],
            ).append(
                record
            )

            shard_codes.add(
                stock_code
            )

            stats[
                "valid_records"
            ] += 1

        stats[
            "covered_symbols"
        ] = len(
            shard_codes
        )

        shard_stats[
            shard_path.name
        ] = stats

        log(
            f"{shard_path.name:<24}"
            f" records="
            f"{stats['valid_records']:<8}"
            f" symbols="
            f"{stats['covered_symbols']}"
        )

    # --------------------------------------------------------
    # sort + deduplicate
    # --------------------------------------------------------

    for stock_code, rows in (
        by_code.items()
    ):

        unique: dict[
            str,
            dict[str, Any],
        ] = {}

        for row in rows:

            unique[
                row["date"]
            ] = row

        by_code[
            stock_code
        ] = sorted(
            unique.values(),
            key=lambda item:
                item["date"],
        )

    return (
        by_code,
        shard_stats,
    )


# ============================================================
# INDICATORS
# ============================================================

def moving_average(
    values: list[float],
    period: int,
) -> float | None:

    if len(values) < period:
        return None

    return (
        sum(values[-period:])
        / period
    )


def rsi(
    values: list[float],
    period: int = 14,
) -> float | None:

    if len(values) <= period:
        return None

    gains = 0.0
    losses = 0.0

    start = (
        len(values)
        - period
        - 1
    )

    for index in range(
        start,
        len(values) - 1,
    ):

        delta = (
            values[index + 1]
            - values[index]
        )

        if delta > 0:
            gains += delta

        elif delta < 0:
            losses -= delta

    if losses == 0:

        if gains == 0:
            return 50.0

        return 100.0

    average_gain = (
        gains / period
    )

    average_loss = (
        losses / period
    )

    if average_loss == 0:
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


def volume_average(
    rows: list[dict[str, Any]],
    period: int,
) -> float | None:

    volumes = [
        row["volume"]
        for row in rows
        if row.get(
            "volume"
        ) is not None
    ]

    if len(volumes) < period:
        return None

    return (
        sum(volumes[-period:])
        / period
    )


# ============================================================
# OFFICIAL HTTP
# ============================================================

def request_json(
    url: str,
    params: dict[str, Any] | None = None,
) -> Any:

    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; "
            "TW-Stock-AI-Scanner/"
            "market-v2.1)"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
    }

    last_error: Exception | None = None

    for attempt in range(
        1,
        HTTP_RETRIES + 1,
    ):

        try:

            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            if (
                attempt
                < HTTP_RETRIES
            ):
                time.sleep(
                    attempt
                )

    raise RuntimeError(
        "Official API request failed: "
        f"{url}: {last_error}"
    )


# ============================================================
# TAIEX
# ============================================================

def fetch_taiex() -> dict[str, Any]:

    try:

        payload = request_json(
            TWSE_MI_INDEX_URL
        )

        if isinstance(
            payload,
            list,
        ):

            rows = payload

        elif isinstance(
            payload,
            dict,
        ):

            rows = payload.get(
                "data",
                [],
            )

        else:
            rows = []

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):
                continue

            name = str(
                row.get("指數")
                or row.get(
                    "指數名稱"
                )
                or row.get("name")
                or ""
            )

            if (
                "發行量加權股價指數"
                not in name
                and
                "TAIEX"
                not in name.upper()
            ):
                continue

            close = (
                to_number(
                    row.get(
                        "收盤指數"
                    )
                )
                or to_number(
                    row.get(
                        "收盤"
                    )
                )
                or to_number(
                    row.get(
                        "close"
                    )
                )
            )

            change = (
                to_number(
                    row.get(
                        "漲跌"
                    )
                )
                or to_number(
                    row.get(
                        "漲跌點數"
                    )
                )
                or to_number(
                    row.get(
                        "change"
                    )
                )
            )

            change_pct = (
                to_number(
                    row.get(
                        "漲跌幅"
                    )
                )
                or to_number(
                    row.get(
                        "change_pct"
                    )
                )
            )

            if close is None:
                continue

            return {
                "status": "ok",
                "name": (
                    name
                    or "TAIEX"
                ),
                "value": close,
                "change": (
                    change
                    if change is not None
                    else 0.0
                ),
                "change_pct": (
                    change_pct
                    if change_pct
                    is not None
                    else 0.0
                ),
            }

        return {
            "status": "unavailable",
            "name": "TAIEX",
            "value": None,
            "change": None,
            "change_pct": None,
        }

    except Exception as exc:

        return {
            "status": "unavailable",
            "name": "TAIEX",
            "value": None,
            "change": None,
            "change_pct": None,
            "error": str(exc),
        }


# ============================================================
# TWSE T86
# ============================================================

def fetch_twse_t86() -> dict[str, Any]:

    trading_date = (
        datetime.now(
            TAIPEI_TZ
        ).strftime(
            "%Y%m%d"
        )
    )

    try:

        payload = request_json(
            TWSE_T86_URL,
            params={
                "date": trading_date,
                "selectType": "ALL",
                "response": "json",
            },
        )

        if not isinstance(
            payload,
            dict,
        ):

            return {
                "status": "unavailable",
                "date": trading_date,
                "reason": (
                    "invalid response"
                ),
            }

        if payload.get(
            "stat"
        ) != "OK":

            return {
                "status": "unavailable",
                "date": trading_date,
                "reason": payload.get(
                    "stat",
                    "unknown",
                ),
            }

        fields = payload.get(
            "fields",
            [],
        )

        data = payload.get(
            "data",
            [],
        )

        if not isinstance(
            fields,
            list,
        ):
            fields = []

        if not isinstance(
            data,
            list,
        ):
            data = []

        return {
            "status": "ok",
            "date": trading_date,
            "fields": fields,
            "data": data,
            "count": len(data),
        }

    except Exception as exc:

        return {
            "status": "unavailable",
            "date": trading_date,
            "error": str(exc),
        }


# ============================================================
# TPEx 3 INSTITUTIONS
# ============================================================

def fetch_tpex_3insti() -> dict[str, Any]:

    try:

        payload = request_json(
            TPEX_3INSTI_URL
        )

        if isinstance(
            payload,
            list,
        ):

            rows = payload

        elif isinstance(
            payload,
            dict,
        ):

            rows = payload.get(
                "data",
                [],
            )

        else:
            rows = []

        if not isinstance(
            rows,
            list,
        ):
            rows = []

        return {
            "status": "ok",
            "count": len(rows),
            "data": rows,
        }

    except Exception as exc:

        return {
            "status": "unavailable",
            "count": 0,
            "data": [],
            "error": str(exc),
        }


# ============================================================
# STOCK RECORDS
# ============================================================

def build_stock_records(
    universe: set[str],
    prices: dict[
        str,
        list[dict[str, Any]]
    ],
) -> dict[
    str,
    dict[str, Any]
]:

    result: dict[
        str,
        dict[str, Any]
    ] = {}

    for stock_code in sorted(
        universe
    ):

        rows = prices.get(
            stock_code
        )

        if not rows:
            continue

        closes = [
            float(row["close"])
            for row in rows
            if row.get(
                "close"
            ) is not None
        ]

        if not closes:
            continue

        latest = rows[-1]

        previous_close = None

        if len(closes) >= 2:
            previous_close = (
                closes[-2]
            )

        change_pct = None

        if (
            previous_close
            is not None
            and previous_close != 0
        ):

            change_pct = (
                (
                    closes[-1]
                    / previous_close
                )
                - 1.0
            ) * 100.0

        result[stock_code] = {
            "date": latest["date"],
            "close": latest["close"],
            "open": latest.get(
                "open"
            ),
            "high": latest.get(
                "high"
            ),
            "low": latest.get(
                "low"
            ),
            "volume": latest.get(
                "volume"
            ),
            "history_rows": len(
                rows
            ),
            "ma5": moving_average(
                closes,
                5,
            ),
            "ma20": moving_average(
                closes,
                20,
            ),
            "rsi14": rsi(
                closes,
                14,
            ),
            "avg_volume5":
                volume_average(
                    rows,
                    5,
                ),
            "change_pct":
                change_pct,
        }

    return result


# ============================================================
# LATEST DATE
# ============================================================

def get_latest_trading_date(
    prices: dict[
        str,
        list[dict[str, Any]]
    ],
) -> str | None:

    dates = [
        rows[-1]["date"]
        for rows in prices.values()
        if rows
    ]

    if not dates:
        return None

    return max(dates)


# ============================================================
# BREADTH
# ============================================================

def build_breadth(
    stocks: dict[
        str,
        dict[str, Any]
    ],
    covered: int,
    active: int,
) -> dict[str, Any]:

    up = 0
    down = 0
    flat = 0
    above_ma20 = 0

    total_volume = 0.0

    for record in stocks.values():

        change_pct = record.get(
            "change_pct"
        )

        if change_pct is not None:

            if change_pct > 0:
                up += 1

            elif change_pct < 0:
                down += 1

            else:
                flat += 1

        ma20 = record.get(
            "ma20"
        )

        close = record.get(
            "close"
        )

        if (
            ma20 is not None
            and close is not None
            and close > ma20
        ):
            above_ma20 += 1

        volume = record.get(
            "volume"
        )

        if volume is not None:
            total_volume += float(
                volume
            )

    coverage_pct = 0.0

    if active:
        coverage_pct = (
            covered
            / active
            * 100.0
        )

    return {
        "up": up,
        "down": down,
        "flat": flat,
        "above_ma20":
            above_ma20,
        "covered":
            covered,
        "active":
            active,
        "coverage":
            f"{covered}/{active}",
        "coverage_pct":
            round(
                coverage_pct,
                2,
            ),
        "total_volume":
            total_volume,
    }


# ============================================================
# CONDITIONS
# ============================================================

CORE_CONDITION_NAMES = [
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


def unavailable_condition(
    name: str,
    value: Any = None,
) -> dict[str, Any]:

    return {
        "name": name,
        "status": "unavailable",
        "pass": None,
        "value": value,
    }


# ============================================================
# MARKET OBJECT
# ============================================================

def build_market(
    universe: set[str],
    prices: dict[
        str,
        list[dict[str, Any]]
    ],
    shard_stats: dict[
        str,
        dict[str, int]
    ],
) -> dict[str, Any]:

    stocks = build_stock_records(
        universe,
        prices,
    )

    covered = len(
        stocks
    )

    missing = sorted(
        universe
        - set(stocks.keys())
    )

    coverage_pct = (
        covered
        / len(universe)
        * 100.0
    )

    latest_date = (
        get_latest_trading_date(
            prices
        )
    )

    breadth = build_breadth(
        stocks,
        covered,
        len(universe),
    )

    taiex = fetch_taiex()

    twse_t86 = (
        fetch_twse_t86()
    )

    tpex_3insti = (
        fetch_tpex_3insti()
    )

    # --------------------------------------------------------
    # TAIEX
    #
    # 僅使用官方 MI_INDEX。
    # 不以股票價格代替 TAIEX 歷史資料。
    # --------------------------------------------------------

    taiex_value = taiex.get(
        "value"
    )

    taiex_change = taiex.get(
        "change"
    )

    taiex_change_pct = taiex.get(
        "change_pct"
    )

    # 目前價格 shard 是個股歷史資料，
    # 不能拿個股資料推導 TAIEX MA20 / RSI / ATR。
    taiex_ma20 = None
    taiex_rsi14 = None
    taiex_atr14_pct = None

    if taiex_value is not None:

        trend_status = (
            "bullish"
            if (
                taiex_change_pct
                is not None
                and taiex_change_pct > 0
            )
            else
            "bearish"
            if (
                taiex_change_pct
                is not None
                and taiex_change_pct < 0
            )
            else
            "flat"
        )

    else:

        trend_status = (
            "unavailable"
        )

    # --------------------------------------------------------
    # Breadth conditions
    # --------------------------------------------------------

    if breadth["down"] > 0:

        up_down_ratio = (
            breadth["up"]
            / breadth["down"]
        )

        up_down_condition = (
            up_down_ratio >= 1.0
        )

        up_down_status = (
            "pass"
            if up_down_condition
            else "fail"
        )

    elif breadth["up"] > 0:

        up_down_ratio = (
            999.0
        )

        up_down_condition = True

        up_down_status = "pass"

    else:

        up_down_ratio = None
        up_down_condition = None
        up_down_status = (
            "unavailable"
        )

    if covered > 0:

        above_ma20_ratio = (
            breadth["above_ma20"]
            / covered
        )

        above_ma20_condition = (
            above_ma20_ratio
            >= 0.50
        )

        above_ma20_status = (
            "pass"
            if above_ma20_condition
            else "fail"
        )

    else:

        above_ma20_ratio = None
        above_ma20_condition = None
        above_ma20_status = (
            "unavailable"
        )

    # --------------------------------------------------------
    # Conditions
    # --------------------------------------------------------

    conditions = [
        unavailable_condition(
            "TAIEX > MA20",
            taiex_ma20,
        ),

        unavailable_condition(
            "MA20 上升",
        ),

        unavailable_condition(
            "TAIEX RSI14 > 50",
            taiex_rsi14,
        ),

        {
            "name":
                "上漲家數 / 下跌家數 >= 1",
            "status":
                up_down_status,
            "pass":
                up_down_condition,
            "value":
                (
                    round(
                        up_down_ratio,
                        4,
                    )
                    if
                    up_down_ratio
                    is not None
                    else None
                ),
        },

        {
            "name":
                "站上 MA20 比例 >= 50%",
            "status":
                above_ma20_status,
            "pass":
                above_ma20_condition,
            "value":
                (
                    round(
                        above_ma20_ratio
                        * 100.0,
                        2,
                    )
                    if
                    above_ma20_ratio
                    is not None
                    else None
                ),
        },

        unavailable_condition(
            "市場成交量 / 20日均量 >= 1",
        ),

        unavailable_condition(
            "外資買賣超 > 0",
        ),

        unavailable_condition(
            "投信買賣超 > 0",
        ),

        unavailable_condition(
            "20日新高 / 新低 >= 1",
        ),

        unavailable_condition(
            "TAIEX ATR14% <= 3%",
            taiex_atr14_pct,
        ),
    ]

    # --------------------------------------------------------
    # Sentiment
    # --------------------------------------------------------

    valid_conditions = [
        item
        for item in conditions
        if item.get("pass")
        is not None
    ]

    passed_conditions = [
        item
        for item in valid_conditions
        if item.get("pass")
        is True
    ]

    valid_count = len(
        valid_conditions
    )

    score = len(
        passed_conditions
    )

    if valid_count == 0:

        sentiment_level = (
            "資料不足"
        )

    elif (
        score
        / valid_count
        >= 0.70
    ):

        sentiment_level = "偏多"

    elif (
        score
        / valid_count
        < 0.40
    ):

        sentiment_level = "偏弱"

    else:

        sentiment_level = "震盪"

    # --------------------------------------------------------
    # Trend
    # --------------------------------------------------------

    trend = {
        "status":
            trend_status,
        "direction":
            (
                "up"
                if (
                    taiex_change_pct
                    is not None
                    and taiex_change_pct
                    > 0
                )
                else
                "down"
                if (
                    taiex_change_pct
                    is not None
                    and taiex_change_pct
                    < 0
                )
                else
                "flat"
                if (
                    taiex_change_pct
                    is not None
                )
                else
                "unavailable"
            ),
        "ma20":
            taiex_ma20,
        "rsi14":
            taiex_rsi14,
        "atr14_pct":
            taiex_atr14_pct,
    }

    # --------------------------------------------------------
    # Volume
    #
    # 個股 shard 有成交量，
    # 但目前沒有獨立市場 20 日總量序列。
    # 不拿單日個股資料偽造市場 20 日均量。
    # --------------------------------------------------------

    volume = {
        "total":
            breadth[
                "total_volume"
            ],
        "avg20":
            None,
        "ratio20":
            None,
        "status":
            "unavailable",
    }

    # --------------------------------------------------------
    # Index
    # --------------------------------------------------------

    index = {
        "name":
            taiex.get(
                "name",
                "TAIEX",
            ),
        "value":
            (
                taiex_value
                if taiex_value
                is not None
                else 0.0
            ),
        "change":
            (
                taiex_change
                if taiex_change
                is not None
                else 0.0
            ),
        "change_pct":
            (
                taiex_change_pct
                if taiex_change_pct
                is not None
                else 0.0
            ),
    }

    # --------------------------------------------------------
    # Source
    # --------------------------------------------------------

    source = {
        "provider": [
            "TWSE",
            "TPEx",
        ],
        "prices":
            "Data/prices shards",
        "index":
            "TWSE MI_INDEX",
        "twse_institutional":
            "TWSE T86",
        "tpex_institutional":
            "TPEx OpenAPI",
    }

    # --------------------------------------------------------
    # Config
    # --------------------------------------------------------

    config = {
        "schema":
            "market-v2.1",
        "universe":
            "Data/universe.json",
        "price_manifest":
            "Data/prices/manifest.json",
        "official_only":
            True,
    }

    # --------------------------------------------------------
    # FINAL market-v2.1
    # --------------------------------------------------------

    return {
        "schema_version":
            "market-v2.1",

        "generated_at":
            datetime.now(
                TAIPEI_TZ
            ).isoformat(),

        "market_status":
            "closed",

        "latest_trading_date":
            latest_date,

        "index":
            index,

        "trend":
            trend,

        "breadth": {
            **breadth,
            "missing":
                missing,
        },

        "volume":
            volume,

        "institutional": {
            "twse":
                twse_t86,
            "tpex":
                tpex_3insti,
        },

        "sentiment": {
            "level":
                sentiment_level,
            "score":
                int(score),
            "valid_conditions":
                int(valid_count),
            "total_conditions":
                10,
        },

        "conditions":
            conditions,

        "source":
            source,

        "config":
            config,

        # ----------------------------------------------------
        # 保留既有資料區塊
        # 供其他 repo 程式使用
        # ----------------------------------------------------

        "universe": {
            "active_count":
                len(universe),
            "price_coverage":
                covered,
            "price_coverage_pct":
                round(
                    coverage_pct,
                    2,
                ),
            "missing_count":
                len(missing),
            "missing":
                missing,
        },

        "price_shards": {
            "count":
                len(shard_stats),
            "files":
                shard_stats,
        },

        "stocks":
            stocks,
    }


# ============================================================
# VALIDATION
# ============================================================

def validate_market(
    market: dict[str, Any],
    universe: set[str],
) -> None:

    if not isinstance(
        market,
        dict,
    ):

        raise RuntimeError(
            "market validation failed: "
            "market must be dict"
        )

    # --------------------------------------------------------
    # Root schema
    # --------------------------------------------------------

    required_root_fields = {
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
        required_root_fields
        - set(market.keys())
    )

    if missing:

        raise RuntimeError(
            "Missing market-v2.1 "
            "root fields: "
            f"{sorted(missing)}"
        )

    if (
        market.get(
            "schema_version"
        )
        != "market-v2.1"
    ):

        raise RuntimeError(
            "schema_version must be "
            "market-v2.1"
        )

    if market.get(
        "market_status"
    ) not in {
        "open",
        "closed",
    }:

        raise RuntimeError(
            "invalid market_status"
        )

    # --------------------------------------------------------
    # Index
    # --------------------------------------------------------

    index = market.get(
        "index"
    )

    if not isinstance(
        index,
        dict,
    ):

        raise RuntimeError(
            "index must be dict"
        )

    for key in (
        "name",
        "value",
        "change",
        "change_pct",
    ):

        if key not in index:

            raise RuntimeError(
                "Missing index field: "
                f"{key}"
            )

    value = to_number(
        index.get("value")
    )

    if (
        value is None
        or not math.isfinite(
            float(value)
        )
    ):

        raise RuntimeError(
            "index.value must be "
            "finite number"
        )

    # --------------------------------------------------------
    # Trend
    # --------------------------------------------------------

    trend = market.get(
        "trend"
    )

    if not isinstance(
        trend,
        dict,
    ):

        raise RuntimeError(
            "trend must be dict"
        )

    if trend.get(
        "status"
    ) not in {
        "bullish",
        "bearish",
        "flat",
        "unavailable",
    }:

        raise RuntimeError(
            "invalid trend.status"
        )

    # --------------------------------------------------------
    # Breadth
    # --------------------------------------------------------

    breadth = market.get(
        "breadth"
    )

    if not isinstance(
        breadth,
        dict,
    ):

        raise RuntimeError(
            "breadth must be dict"
        )

    if breadth.get(
        "active"
    ) != len(universe):

        raise RuntimeError(
            "breadth.active mismatch"
        )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    universe_info = market.get(
        "universe"
    )

    if not isinstance(
        universe_info,
        dict,
    ):

        raise RuntimeError(
            "universe missing"
        )

    if universe_info.get(
        "active_count"
    ) != len(universe):

        raise RuntimeError(
            "universe active_count "
            "mismatch"
        )

    coverage = (
        universe_info.get(
            "price_coverage"
        )
    )

    if not isinstance(
        coverage,
        int,
    ):

        raise RuntimeError(
            "invalid price coverage"
        )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    volume = market.get(
        "volume"
    )

    if not isinstance(
        volume,
        dict,
    ):

        raise RuntimeError(
            "volume must be dict"
        )

    # --------------------------------------------------------
    # Institutional
    # --------------------------------------------------------

    institutional = (
        market.get(
            "institutional"
        )
    )

    if not isinstance(
        institutional,
        dict,
    ):

        raise RuntimeError(
            "institutional must be "
            "dict"
        )

    if "twse" not in institutional:
        raise RuntimeError(
            "institutional.twse "
            "missing"
        )

    if "tpex" not in institutional:
        raise RuntimeError(
            "institutional.tpex "
            "missing"
        )

    # --------------------------------------------------------
    # Sentiment
    # --------------------------------------------------------

    sentiment = market.get(
        "sentiment"
    )

    if not isinstance(
        sentiment,
        dict,
    ):

        raise RuntimeError(
            "sentiment must be dict"
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
            "invalid sentiment level"
        )

    if not isinstance(
        sentiment.get(
            "score"
        ),
        int,
    ):

        raise RuntimeError(
            "sentiment.score must "
            "be int"
        )

    if not isinstance(
        sentiment.get(
            "valid_conditions"
        ),
        int,
    ):

        raise RuntimeError(
            "sentiment.valid_conditions "
            "must be int"
        )

    if (
        sentiment.get(
            "total_conditions"
        )
        != 10
    ):

        raise RuntimeError(
            "sentiment.total_conditions "
            "must equal 10"
        )

    # --------------------------------------------------------
    # Conditions
    # --------------------------------------------------------

    conditions = market.get(
        "conditions"
    )

    if not isinstance(
        conditions,
        list,
    ):

        raise RuntimeError(
            "conditions must be list"
        )

    if len(conditions) != 10:

        raise RuntimeError(
            "conditions must contain "
            "exactly 10 items"
        )

    names = [
        item.get("name")
        for item in conditions
        if isinstance(
            item,
            dict,
        )
    ]

    if (
        names
        != CORE_CONDITION_NAMES
    ):

        raise RuntimeError(
            "market core condition "
            "names/order mismatch"
        )

    for condition in conditions:

        if not isinstance(
            condition,
            dict,
        ):

            raise RuntimeError(
                "invalid condition item"
            )

        if condition.get(
            "status"
        ) not in {
            "pass",
            "fail",
            "unavailable",
        }:

            raise RuntimeError(
                "invalid condition status"
            )

        condition_pass = (
            condition.get("pass")
        )

        if (
            condition_pass
            is not None
            and not isinstance(
                condition_pass,
                bool,
            )
        ):

            raise RuntimeError(
                "condition.pass must "
                "be bool or null"
            )

    # --------------------------------------------------------
    # Source
    # --------------------------------------------------------

    source = market.get(
        "source"
    )

    if not isinstance(
        source,
        dict,
    ):

        raise RuntimeError(
            "source must be dict"
        )

    providers = source.get(
        "provider"
    )

    if not isinstance(
        providers,
        list,
    ):

        raise RuntimeError(
            "source.provider must "
            "be list"
        )

    if "TWSE" not in providers:
        raise RuntimeError(
            "source.provider missing "
            "TWSE"
        )

    if "TPEx" not in providers:
        raise RuntimeError(
            "source.provider missing "
            "TPEx"
        )

    # --------------------------------------------------------
    # Config
    # --------------------------------------------------------

    config = market.get(
        "config"
    )

    if not isinstance(
        config,
        dict,
    ):

        raise RuntimeError(
            "config must be dict"
        )

    if config.get(
        "schema"
    ) != "market-v2.1":

        raise RuntimeError(
            "config.schema mismatch"
        )

    if (
        config.get(
            "official_only"
        )
        is not True
    ):

        raise RuntimeError(
            "config.official_only "
            "must be true"
        )

    # --------------------------------------------------------
    # Stocks
    # --------------------------------------------------------

    stocks = market.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "stocks must be dict"
        )

    for stock_code, record in (
        stocks.items()
    ):

        if stock_code not in universe:

            raise RuntimeError(
                "market validation failed: "
                f"non-universe code="
                f"{stock_code}"
            )

        if not isinstance(
            record,
            dict,
        ):

            raise RuntimeError(
                "invalid stock record="
                f"{stock_code}"
            )

        close = to_number(
            record.get(
                "close"
            )
        )

        if (
            close is None
            or close <= 0
        ):

            raise RuntimeError(
                "invalid close="
                f"{stock_code}"
            )

        trading_date = (
            normalize_date(
                record.get(
                    "date"
                )
            )
        )

        if trading_date is None:

            raise RuntimeError(
                "invalid date="
                f"{stock_code}"
            )

    # --------------------------------------------------------
    # Entire JSON finite-number check
    # --------------------------------------------------------

    def validate_finite(
        value: Any,
    ) -> None:

        if isinstance(
            value,
            float,
        ):

            if not math.isfinite(
                value
            ):

                raise RuntimeError(
                    "non-finite number "
                    "in market.json"
                )

        elif isinstance(
            value,
            dict,
        ):

            for child in (
                value.values()
            ):

                validate_finite(
                    child
                )

        elif isinstance(
            value,
            list,
        ):

            for child in value:

                validate_finite(
                    child
                )

    validate_finite(
        market
    )


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write_json(
    path: Path,
    payload: Any,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temp_path.open(
        "w",
        encoding="utf-8",
    ) as fh:

        json.dump(
            payload,
            fh,
            ensure_ascii=False,
            allow_nan=False,
            separators=(
                ",",
                ":",
            ),
        )

        fh.write("\n")

    os.replace(
        temp_path,
        path,
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    log("=" * 72)
    log(
        "TW STOCK AI SCANNER - "
        "FETCH MARKET V2.1"
    )
    log("=" * 72)

    # --------------------------------------------------------
    # 1. Universe
    # --------------------------------------------------------

    universe = load_universe()

    log("")
    log(
        "ACTIVE UNIVERSE : "
        f"{len(universe)}"
    )

    # --------------------------------------------------------
    # 2. Price shards
    # --------------------------------------------------------

    prices, shard_stats = (
        parse_price_shards(
            universe
        )
    )

    covered = len(
        prices
    )

    missing = sorted(
        universe
        - set(prices.keys())
    )

    coverage_pct = (
        covered
        / len(universe)
        * 100.0
    )

    log("")
    log("=" * 72)
    log("PRICE COVERAGE")
    log("=" * 72)

    log(
        "ACTIVE           : "
        f"{len(universe)}"
    )

    log(
        "COVERED          : "
        f"{covered}"
    )

    log(
        "MISSING          : "
        f"{len(missing)}"
    )

    log(
        "COVERAGE         : "
        f"{coverage_pct:.2f}%"
    )

    if missing:

        log("")

        log(
            "MISSING SAMPLE   : "
            + ", ".join(
                missing[:50]
            )
        )

    # --------------------------------------------------------
    # 3. Build market-v2.1
    # --------------------------------------------------------

    market = build_market(
        universe,
        prices,
        shard_stats,
    )

    # --------------------------------------------------------
    # 4. Validate before write
    # --------------------------------------------------------

    validate_market(
        market,
        universe,
    )

    # --------------------------------------------------------
    # 5. Atomic write
    # --------------------------------------------------------

    atomic_write_json(
        MARKET_FILE,
        market,
    )

    # --------------------------------------------------------
    # 6. Read-back
    # --------------------------------------------------------

    written = load_json(
        MARKET_FILE
    )

    if not isinstance(
        written,
        dict,
    ):

        raise RuntimeError(
            "Final validation failed: "
            "market.json is not object"
        )

    # --------------------------------------------------------
    # 7. Final validation
    # --------------------------------------------------------

    validate_market(
        written,
        universe,
    )

    log("")
    log("=" * 72)
    log("FINAL VALIDATION")
    log("=" * 72)

    log(
        "Universe         PASS"
    )

    log(
        "Price shards     PASS"
    )

    log(
        "Price parser     PASS"
    )

    log(
        "Market build     PASS"
    )

    log(
        "JSON write       PASS"
    )

    log(
        "Read-back        PASS"
    )

    log(
        "Market V2.1      PASS"
    )

    log("")
    log(
        "ACTIVE           : "
        f"{len(universe)}"
    )

    log(
        "COVERED          : "
        f"{covered}"
    )

    log(
        "MISSING          : "
        f"{len(missing)}"
    )

    log(
        "COVERAGE         : "
        f"{coverage_pct:.2f}%"
    )

    log("")
    log(
        "OUTPUT           : "
        f"{MARKET_FILE}"
    )

    log("=" * 72)

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        raise SystemExit(
            main()
        )

    except KeyboardInterrupt:

        log(
            "Interrupted."
        )

        raise SystemExit(
            130
        )

    except Exception as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
            flush=True,
        )

        raise SystemExit(
            1
        )