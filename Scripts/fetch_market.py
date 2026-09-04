#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_market.py
============================================================

MARKET ENVIRONMENT V3.2

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
15. 最終 validation 必須通過
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


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
PRICES_DIR = DATA_DIR / "prices"
MANIFEST_FILE = PRICES_DIR / "manifest.json"
MARKET_FILE = DATA_DIR / "market.json"


# ============================================================
# OFFICIAL API
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
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

HTTP_TIMEOUT = 30
HTTP_RETRIES = 3


# ============================================================
# KEY ALIASES
# ============================================================

CODE_KEYS = (
    "code",
    "Code",
    "CODE",
    "stock_code",
    "stockCode",
    "stockCode",
    "symbol",
    "Symbol",
    "證券代號",
    "證券碼",
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
    "收盤價",
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
#
# 非股票代號的容器 key。
# 非常重要：
# rows / data / symbols / stocks 不得被誤判成股票代號。
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

def log(message: str = "") -> None:
    print(message, flush=True)


# ============================================================
# NUMBER
# ============================================================

def to_number(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if math.isfinite(number):
            return number

        return None

    text = str(value).strip()

    if not text:
        return None

    text = text.replace(",", "")
    text = text.replace(" ", "")
    text = text.replace("\u3000", "")

    if text in {
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
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


# ============================================================
# KEY NORMALIZATION
# ============================================================

def normalize_key(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\ufeff", "")
    text = text.replace("\r", "")
    text = text.replace("\n", "")
    text = text.replace("\t", "")
    text = text.replace("\u3000", " ")

    return text.strip()


# ============================================================
# CODE
# ============================================================

def normalize_code(value: Any) -> str | None:
    if value is None:
        return None

    text = normalize_key(value).upper()

    if not text:
        return None

    if text.endswith(".TWO"):
        text = text[:-4]

    elif text.endswith(".TW"):
        text = text[:-3]

    text = text.strip()

    # 台股股票 / ETF / 特殊 6 碼商品
    if not re.fullmatch(r"[A-Z0-9]{4,6}", text):
        return None

    # 防止容器名稱被解析成股票
    if text.lower() in CONTAINER_KEYS:
        return None

    return text


# ============================================================
# DATE
# ============================================================

def normalize_date(value: Any) -> str | None:
    if value is None:
        return None

    text = normalize_key(value)

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
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # ISO datetime
    if "T" in text:
        candidate = text.split("T", 1)[0]

        parsed = normalize_date(candidate)

        if parsed:
            return parsed

    # ROC YYYYMMDD
    match = re.fullmatch(
        r"(\d{3})(\d{2})(\d{2})",
        text,
    )

    if match:
        try:
            year = int(match.group(1)) + 1911
            month = int(match.group(2))
            day = int(match.group(3))

            dt = datetime(year, month, day)

            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # embedded ISO date
    match = re.search(
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})",
        text,
    )

    if match:
        try:
            dt = datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )

            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


# ============================================================
# DICT LOOKUP
# ============================================================

def first_value(
    obj: dict[str, Any],
    keys: tuple[str, ...],
) -> Any:
    if not isinstance(obj, dict):
        return None

    # Exact
    for key in keys:
        if key in obj:
            return obj[key]

    # Case-insensitive / normalized
    normalized_map: dict[str, Any] = {}

    for actual_key, value in obj.items():
        normalized_map[
            normalize_key(actual_key).lower()
        ] = value

    for key in keys:
        normalized = normalize_key(key).lower()

        if normalized in normalized_map:
            return normalized_map[normalized]

    return None


# ============================================================
# JSON LOAD
# ============================================================

def load_json(path: Path) -> Any:
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
            f"Missing universe file: {UNIVERSE_FILE}"
        )

    payload = load_json(UNIVERSE_FILE)

    if not isinstance(payload, dict):
        raise RuntimeError(
            "universe.json must be a JSON object"
        )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "universe.json: stocks must be a dict"
        )

    universe: set[str] = set()

    for raw_code, info in stocks.items():

        if not isinstance(info, dict):
            continue

        status = str(
            info.get("status", "")
        ).strip().lower()

        if status != "active":
            continue

        normalized = normalize_code(raw_code)

        if normalized:
            universe.add(normalized)

    if not universe:
        raise RuntimeError(
            "universe.json contains no active instruments"
        )

    return universe


# ============================================================
# MANIFEST
# ============================================================

def load_manifest_files() -> list[Path]:
    if not MANIFEST_FILE.exists():
        raise RuntimeError(
            f"Missing price manifest: {MANIFEST_FILE}"
        )

    payload = load_json(MANIFEST_FILE)

    candidates: Any = None

    if isinstance(payload, list):
        candidates = payload

    elif isinstance(payload, dict):

        for key in (
            "files",
            "shards",
            "parts",
            "prices",
        ):
            value = payload.get(key)

            if isinstance(value, list):
                candidates = value
                break

    names: list[str] = []

    if isinstance(candidates, list):

        for item in candidates:

            if isinstance(item, str):
                name = item.strip()

            elif isinstance(item, dict):
                name = str(
                    item.get("file")
                    or item.get("filename")
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

            names.append(path.name)

    # manifest 正常情況應該已有 files。
    # 若沒有，才做同目錄明確 JSON shard discovery。
    if not names:

        names = [
            p.name
            for p in sorted(
                PRICES_DIR.glob("*.json")
            )
            if p.name != "manifest.json"
        ]

    names = list(dict.fromkeys(names))

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
        first_value(obj, CODE_KEYS)
    )

    stock_code = own_code or inherited_code

    trading_date = normalize_date(
        first_value(obj, DATE_KEYS)
    )

    close = to_number(
        first_value(obj, CLOSE_KEYS)
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
            first_value(obj, OPEN_KEYS)
        ),
        "high": to_number(
            first_value(obj, HIGH_KEYS)
        ),
        "low": to_number(
            first_value(obj, LOW_KEYS)
        ),
        "volume": to_number(
            first_value(obj, VOLUME_KEYS)
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

    trading_date = normalize_date(row[0])

    if trading_date is None:
        return None

    values: list[float] = []

    for value in row[1:]:
        number = to_number(value)

        if number is not None:
            values.append(number)

    if not values:
        return None

    # 常見 OHLCV：
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

    if isinstance(value, dict):

        own_code = normalize_code(
            first_value(value, CODE_KEYS)
        )

        current_code = own_code or inherited_code

        direct_record = make_price_record(
            value,
            current_code,
        )

        if direct_record:
            yield direct_record

        for key, child in value.items():

            key_text = normalize_key(key)

            key_lower = key_text.lower()

            child_code = current_code

            # 僅當 key 明確像股票代號時才繼承。
            # rows / data / symbols 等容器不得成為 code。
            if key_lower not in CONTAINER_KEYS:

                candidate = normalize_code(key_text)

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

    if isinstance(value, list):

        for item in value:

            if isinstance(item, list):

                record = make_list_record(
                    item,
                    inherited_code,
                )

                if record:
                    yield record

            else:

                yield from walk_price_records(
                    item,
                    inherited_code,
                    depth + 1,
                )

        return


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

    shard_files = load_manifest_files()

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
            payload = load_json(shard_path)

        except Exception as exc:

            log(
                f"WARNING: invalid JSON "
                f"{shard_path.name}: {exc}"
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

            stats["records_seen"] += 1

            stock_code = record["code"]

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
            ).append(record)

            shard_codes.add(stock_code)

            stats["valid_records"] += 1

        stats["covered_symbols"] = len(
            shard_codes
        )

        shard_stats[
            shard_path.name
        ] = stats

        log(
            f"{shard_path.name:<24}"
            f" records={stats['valid_records']:<8}"
            f" symbols={stats['covered_symbols']}"
        )

    # --------------------------------------------------------
    # sort + deduplicate across shards
    # --------------------------------------------------------

    for stock_code, rows in by_code.items():

        unique: dict[
            str,
            dict[str, Any],
        ] = {}

        for row in rows:
            unique[row["date"]] = row

        by_code[stock_code] = sorted(
            unique.values(),
            key=lambda x: x["date"],
        )

    return by_code, shard_stats


# ============================================================
# INDICATORS
# ============================================================

def moving_average(
    values: list[float],
    period: int,
) -> float | None:

    if len(values) < period:
        return None

    subset = values[-period:]

    return sum(subset) / period


def rsi(
    values: list[float],
    period: int = 14,
) -> float | None:

    if len(values) <= period:
        return None

    gains = 0.0
    losses = 0.0

    start = len(values) - period - 1

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
        return 100.0

    average_gain = gains / period
    average_loss = losses / period

    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def volume_average(
    rows: list[dict[str, Any]],
    period: int,
) -> float | None:

    volumes = [
        row["volume"]
        for row in rows
        if row.get("volume") is not None
    ]

    if len(volumes) < period:
        return None

    return sum(
        volumes[-period:]
    ) / period


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
            "(compatible; TW-Stock-AI-Scanner/3.2)"
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

            if attempt < HTTP_RETRIES:
                time.sleep(attempt)

    raise RuntimeError(
        f"Official API request failed: "
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

        if isinstance(payload, list):
            rows = payload

        elif isinstance(payload, dict):
            rows = payload.get(
                "data",
                [],
            )

        else:
            rows = []

        for row in rows:

            if not isinstance(row, dict):
                continue

            name = str(
                row.get("指數")
                or row.get("指數名稱")
                or row.get("name")
                or ""
            )

            if (
                "發行量加權股價指數"
                not in name
                and "TAIEX"
                not in name.upper()
            ):
                continue

            close = (
                to_number(
                    row.get("收盤指數")
                )
                or to_number(
                    row.get("收盤")
                )
                or to_number(
                    row.get("close")
                )
            )

            if close is None:
                continue

            return {
                "status": "ok",
                "name": name,
                "close": close,
            }

        return {
            "status": "unavailable",
            "reason": "TAIEX row not found",
        }

    except Exception as exc:

        return {
            "status": "unavailable",
            "error": str(exc),
        }


# ============================================================
# TWSE T86
# ============================================================

def fetch_twse_t86() -> dict[str, Any]:

    trading_date = (
        datetime.now().strftime("%Y%m%d")
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
                "reason": "invalid response",
            }

        if payload.get("stat") != "OK":

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

        if not isinstance(fields, list):
            fields = []

        if not isinstance(data, list):
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

        if isinstance(payload, list):
            rows = payload

        elif isinstance(payload, dict):
            rows = payload.get(
                "data",
                [],
            )

        else:
            rows = []

        if not isinstance(rows, list):
            rows = []

        return {
            "status": "ok",
            "count": len(rows),
            "data": rows,
        }

    except Exception as exc:

        return {
            "status": "unavailable",
            "error": str(exc),
        }


# ============================================================
# STOCK MARKET RECORDS
# ============================================================

def build_stock_records(
    universe: set[str],
    prices: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:

    result: dict[
        str,
        dict[str, Any],
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
            if row.get("close") is not None
        ]

        if not closes:
            continue

        latest = rows[-1]

        result[stock_code] = {
            "date": latest["date"],
            "close": latest["close"],
            "open": latest.get("open"),
            "high": latest.get("high"),
            "low": latest.get("low"),
            "volume": latest.get("volume"),
            "history_rows": len(rows),
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
            "avg_volume5": volume_average(
                rows,
                5,
            ),
        }

    return result


# ============================================================
# MARKET OBJECT
# ============================================================

def build_market(
    universe: set[str],
    prices: dict[str, list[dict[str, Any]]],
    shard_stats: dict[str, dict[str, int]],
) -> dict[str, Any]:

    stock_records = build_stock_records(
        universe,
        prices,
    )

    covered = set(
        stock_records.keys()
    )

    missing = sorted(
        universe - covered
    )

    coverage_pct = (
        len(covered)
        / len(universe)
        * 100.0
    )

    return {
        "schema_version": "market-v3.2",

        "generated_at": (
            datetime.now()
            .astimezone()
            .isoformat()
        ),

        "universe": {
            "active_count": len(
                universe
            ),
            "price_coverage": len(
                covered
            ),
            "price_coverage_pct": round(
                coverage_pct,
                2,
            ),
            "missing_count": len(
                missing
            ),
            "missing": missing,
        },

        "price_shards": {
            "count": len(
                shard_stats
            ),
            "files": shard_stats,
        },

        "taiex": fetch_taiex(),

        "institutions": {
            "twse_t86": fetch_twse_t86(),
            "tpex_3insti": fetch_tpex_3insti(),
        },

        "stocks": stock_records,
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

    universe_info = market.get(
        "universe"
    )

    if not isinstance(
        universe_info,
        dict,
    ):
        raise RuntimeError(
            "market validation failed: "
            "universe missing"
        )

    active_count = universe_info.get(
        "active_count"
    )

    if active_count != len(
        universe
    ):
        raise RuntimeError(
            "market validation failed: "
            f"active_count={active_count}, "
            f"expected={len(universe)}"
        )

    coverage = universe_info.get(
        "price_coverage"
    )

    if not isinstance(
        coverage,
        int,
    ):
        raise RuntimeError(
            "market validation failed: "
            "invalid price coverage"
        )

    stocks = market.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "market validation failed: "
            "stocks must be dict"
        )

    for stock_code, record in stocks.items():

        if stock_code not in universe:
            raise RuntimeError(
                "market validation failed: "
                f"non-universe code={stock_code}"
            )

        if not isinstance(
            record,
            dict,
        ):
            raise RuntimeError(
                "market validation failed: "
                f"invalid record={stock_code}"
            )

        close = to_number(
            record.get("close")
        )

        if close is None or close <= 0:
            raise RuntimeError(
                "market validation failed: "
                f"invalid close={stock_code}"
            )

        trading_date = normalize_date(
            record.get("date")
        )

        if trading_date is None:
            raise RuntimeError(
                "market validation failed: "
                f"invalid date={stock_code}"
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
    log("TW STOCK AI SCANNER - FETCH MARKET V3.2")
    log("=" * 72)

    # --------------------------------------------------------
    # 1. Universe
    # --------------------------------------------------------

    universe = load_universe()

    log("")
    log(
        f"ACTIVE UNIVERSE : "
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

    coverage_pct = (
        covered
        / len(universe)
        * 100.0
    )

    missing = sorted(
        universe
        - set(prices.keys())
    )

    log("")
    log("=" * 72)
    log("PRICE COVERAGE")
    log("=" * 72)

    log(
        f"ACTIVE           : "
        f"{len(universe)}"
    )

    log(
        f"COVERED          : "
        f"{covered}"
    )

    log(
        f"MISSING          : "
        f"{len(missing)}"
    )

    log(
        f"COVERAGE         : "
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
    # 3. Build market
    # --------------------------------------------------------

    market = build_market(
        universe,
        prices,
        shard_stats,
    )

    # --------------------------------------------------------
    # 4. Validation
    # --------------------------------------------------------

    validate_market(
        market,
        universe,
    )

    # --------------------------------------------------------
    # 5. Write
    # --------------------------------------------------------

    atomic_write_json(
        MARKET_FILE,
        market,
    )

    # --------------------------------------------------------
    # 6. Final validation after write
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

    validate_market(
        written,
        universe,
    )

    log("")
    log("=" * 72)
    log("FINAL VALIDATION")
    log("=" * 72)

    log("Universe         PASS")
    log("Price shards     PASS")
    log("Price parser     PASS")
    log("Market build     PASS")
    log("JSON write       PASS")
    log("Read-back        PASS")
    log("Final validation PASS")

    log("")
    log(
        f"OUTPUT           : "
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

        raise SystemExit(130)

    except Exception as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
            flush=True,
        )

        raise SystemExit(1)