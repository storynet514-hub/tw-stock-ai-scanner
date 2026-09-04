#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Taiwan stock market environment builder - market-v2.1.
"""

from __future__ import annotations

import calendar
import json
import math
import os
import re
import tempfile
import time as time_module
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"
OUTPUT_FILE = DATA_DIR / "market.json"
UNIVERSE_FILE = DATA_DIR / "universe.json"
PRICES_DIR = DATA_DIR / "prices"
MANIFEST_FILE = PRICES_DIR / "manifest.json"


SCHEMA_VERSION = "market-v2.1"
PRICE_SCHEMA_VERSION = "prices-v14.0"

TAIWAN_TZ = timezone(timedelta(hours=8))

REQUEST_TIMEOUT = 30
RETRY_COUNT = 3


TWSE_INDEX_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
)

TWSE_INDEX_HISTORY_URL = (
    "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"
)

TWSE_T86_URL = (
    "https://www.twse.com.tw/rwd/zh/fund/T86"
)

TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_3insti_daily_trading"
)


CONFIG = {
    "ma_period": 20,
    "rsi_period": 14,
    "atr_period": 14,
    "volume_ma_period": 20,
    "new_high_low_period": 20,
    "advance_decline_min_ratio": 1.0,
    "breadth_min_pct": 0.50,
    "volume_ratio_min": 1.0,
    "new_high_low_min_ratio": 1.0,
    "atr_pct_max": 0.03,
    "score_bullish": 8,
    "score_sideways": 5,
    "minimum_valid_conditions": 6,
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; TW-Stock-AI-Scanner/2.1)"
    ),
    "Accept": (
        "application/json,text/plain,*/*"
    ),
}


def log(message: str) -> None:
    print(message, flush=True)


def request_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    last_error: Optional[Exception] = None

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:
            last_error = exc

            if attempt < RETRY_COUNT:
                time_module.sleep(attempt)

    raise RuntimeError(
        f"官方 API 讀取失敗: {url}: {last_error}"
    ) from last_error


def number(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        result = float(value)

        if math.isfinite(result):
            return result

        return None

    text = str(value).strip()

    if not text:
        return None

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

        if math.isfinite(result):
            return result

    except ValueError:
        pass

    return None


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TSE",
        ".OTC",
    ):
        if text.endswith(suffix):
            text = text[:-len(suffix)]

    return text


def parse_date(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = re.sub(r"[^0-9]", "", text)

    # ROC YYYYMMDD -> Gregorian YYYYMMDD
    if len(text) == 7:
        text = str(int(text[:3]) + 1911) + text[3:]

    if len(text) != 8:
        return None

    try:
        parsed = datetime.strptime(
            text,
            "%Y%m%d",
        ).date()

        return parsed.isoformat()

    except ValueError:
        return None


def normalize_label(value: Any) -> str:
    return re.sub(
        r"[\s\u3000()（）:：_\-]+",
        "",
        str(value or ""),
    ).lower()


def clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): clean_json(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            clean_json(v)
            for v in value
        ]

    if isinstance(value, float):
        if not math.isfinite(value):
            return None

    return value


def load_json_file(path: Path) -> Any:
    with path.open(
        "r",
        encoding="utf-8",
    ) as fh:
        return json.load(fh)


# ============================================================
# UNIVERSE
# ============================================================

def is_stock(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False

    status = str(
        item.get("status", "")
    ).strip().lower()

    if status != "active":
        return False

    kind = " ".join(
        str(item.get(key, ""))
        for key in (
            "type",
            "category",
            "security_type",
            "instrument_type",
            "name",
        )
    )

    kind_lower = kind.lower()

    excluded = (
        "etf",
        "etn",
        "reit",
        "warrant",
        "權證",
        "債券",
        "基金",
        "受益憑證",
        "槓桿",
        "反向",
    )

    if any(
        token in kind_lower
        for token in excluded
    ):
        return False

    return True


def load_universe() -> Dict[str, Dict[str, Any]]:
    data = load_json_file(
        UNIVERSE_FILE
    )

    raw = (
        data.get("stocks")
        if isinstance(data, dict)
        else None
    )

    if not isinstance(raw, dict):
        raise RuntimeError(
            "Data/universe.json 的 stocks 必須是 dict"
        )

    universe: Dict[str, Dict[str, Any]] = {}

    for key, item in raw.items():
        if not isinstance(item, dict):
            continue

        if not is_stock(item):
            continue

        symbol = normalize_symbol(
            item.get("symbol")
            or item.get("code")
            or key
        )

        if symbol:
            universe[symbol] = item

    if not universe:
        raise RuntimeError(
            "Universe 沒有可用的 active 一般股票"
        )

    return universe


# ============================================================
# PRICE SHARD PARSER
# ============================================================

DATE_KEYS = (
    "date",
    "Date",
    "trade_date",
    "TradeDate",
    "tradedate",
    "交易日期",
    "日期",
)

CLOSE_KEYS = (
    "close",
    "Close",
    "closing_price",
    "ClosingPrice",
    "closingprice",
    "收盤價",
    "收盤",
)

VOLUME_KEYS = (
    "volume",
    "Volume",
    "成交量",
    "成交股數",
    "TradingVolume",
    "trading_volume",
)

SYMBOL_KEYS = (
    "symbol",
    "Symbol",
    "code",
    "Code",
    "stock_id",
    "stockId",
    "ticker",
    "Ticker",
    "證券代號",
)


def first_value(
    row: Dict[str, Any],
    keys: Iterable[str],
) -> Any:
    normalized = {
        normalize_label(key): value
        for key, value in row.items()
    }

    for key in keys:
        normalized_key = normalize_label(key)

        if normalized_key in normalized:
            return normalized[normalized_key]

    return None


def row_has_date_close(
    row: Dict[str, Any],
) -> bool:
    return (
        parse_date(
            first_value(
                row,
                DATE_KEYS,
            )
        )
        is not None
        and
        number(
            first_value(
                row,
                CLOSE_KEYS,
            )
        )
        is not None
    )


def extract_rows(
    value: Any,
) -> List[Dict[str, Any]]:
    """
    Recursively locate price rows.

    Supports:
        list[dict]
        {"data": [...]}
        {"rows": [...]}
        {"records": [...]}
        {"result": [...]}
        {"prices": [...]}
        {"history": [...]}
        {"payload": [...]}
        nested wrappers
    """

    found: List[Dict[str, Any]] = []
    seen: set[int] = set()

    def walk(obj: Any) -> None:
        if id(obj) in seen:
            return

        if isinstance(
            obj,
            (dict, list),
        ):
            seen.add(id(obj))

        if isinstance(obj, dict):
            if row_has_date_close(obj):
                found.append(obj)
                return

            preferred_keys = (
                "data",
                "rows",
                "records",
                "result",
                "items",
                "prices",
                "history",
                "list",
                "values",
                "payload",
                "content",
            )

            for key in preferred_keys:
                if key in obj:
                    walk(obj[key])

            for key, child in obj.items():
                if key in preferred_keys:
                    continue

                if isinstance(
                    child,
                    (dict, list),
                ):
                    walk(child)

        elif isinstance(obj, list):
            for child in obj:
                walk(child)

    walk(value)

    return found


def parse_history(
    value: Any,
    symbol_hint: str = "",
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}

    for row in extract_rows(value):
        symbol = normalize_symbol(
            first_value(
                row,
                SYMBOL_KEYS,
            )
            or symbol_hint
        )

        if not symbol:
            continue

        trade_date = parse_date(
            first_value(
                row,
                DATE_KEYS,
            )
        )

        close = number(
            first_value(
                row,
                CLOSE_KEYS,
            )
        )

        volume = number(
            first_value(
                row,
                VOLUME_KEYS,
            )
        )

        if not trade_date or close is None:
            continue

        result[trade_date] = {
            "date": trade_date,
            "close": close,
            "volume": volume,
        }

    return result


def looks_like_symbol(
    value: Any,
) -> bool:
    text = normalize_symbol(value)

    return bool(
        re.fullmatch(
            r"[0-9A-Z]{4,6}",
            text,
        )
    )


def extract_symbol_histories(
    payload: Any,
) -> Dict[
    str,
    Dict[str, Dict[str, Any]],
]:
    output: Dict[
        str,
        Dict[str, Dict[str, Any]],
    ] = defaultdict(dict)

    def merge(
        symbol: str,
        rows: Dict[str, Dict[str, Any]],
    ) -> None:
        symbol = normalize_symbol(symbol)

        if not symbol:
            return

        for trade_date, row in rows.items():
            output[symbol][trade_date] = row

    def walk(
        obj: Any,
        hint: str = "",
    ) -> None:
        if isinstance(obj, list):
            if obj and all(
                isinstance(
                    item,
                    dict,
                )
                for item in obj
            ):
                rows = parse_history(
                    obj,
                    hint,
                )

                if rows and hint:
                    merge(
                        hint,
                        rows,
                    )

                elif rows:
                    for row in obj:
                        symbol = normalize_symbol(
                            first_value(
                                row,
                                SYMBOL_KEYS,
                            )
                        )

                        trade_date = parse_date(
                            first_value(
                                row,
                                DATE_KEYS,
                            )
                        )

                        close = number(
                            first_value(
                                row,
                                CLOSE_KEYS,
                            )
                        )

                        volume = number(
                            first_value(
                                row,
                                VOLUME_KEYS,
                            )
                        )

                        if (
                            symbol
                            and trade_date
                            and close is not None
                        ):
                            merge(
                                symbol,
                                {
                                    trade_date: {
                                        "date": trade_date,
                                        "close": close,
                                        "volume": volume,
                                    }
                                },
                            )

                return

            for child in obj:
                walk(
                    child,
                    hint,
                )

            return

        if not isinstance(obj, dict):
            return

        schema = (
            obj.get("schema_version")
            or obj.get("schema")
        )

        if (
            schema is not None
            and str(schema)
            != PRICE_SCHEMA_VERSION
        ):
            raise RuntimeError(
                "價格 shard schema_version 錯誤: "
                f"{schema}"
            )

        if (
            "stocks" in obj
            and isinstance(
                obj["stocks"],
                (dict, list),
            )
        ):
            walk(
                obj["stocks"],
                hint,
            )

        for key, child in obj.items():
            if key in {
                "schema_version",
                "schema",
                "generated_at",
                "source",
                "stocks",
            }:
                continue

            if (
                looks_like_symbol(key)
                and isinstance(
                    child,
                    (dict, list),
                )
            ):
                rows = parse_history(
                    child,
                    key,
                )

                if rows:
                    merge(
                        key,
                        rows,
                    )
                    continue

            if isinstance(
                child,
                (dict, list),
            ):
                child_symbol = hint

                for symbol_key in SYMBOL_KEYS:
                    if symbol_key in obj:
                        child_symbol = normalize_symbol(
                            obj[symbol_key]
                        )
                        break

                walk(
                    child,
                    child_symbol,
                )

        rows = parse_history(
            obj,
            hint,
        )

        if rows and hint:
            merge(
                hint,
                rows,
            )

    walk(payload)

    return output


def manifest_files() -> List[Path]:
    if not MANIFEST_FILE.exists():
        raise RuntimeError(
            "缺少 Data/prices/manifest.json"
        )

    manifest = load_json_file(
        MANIFEST_FILE
    )

    if isinstance(manifest, dict):
        entries = (
            manifest.get("files")
            or manifest.get("shards")
            or manifest.get("data")
            or []
        )
    else:
        entries = manifest

    if not isinstance(
        entries,
        list,
    ):
        raise RuntimeError(
            "manifest.json 的 shard 清單格式錯誤"
        )

    paths: List[Path] = []

    for entry in entries:
        if isinstance(entry, str):
            name = entry

        elif isinstance(entry, dict):
            name = (
                entry.get("file")
                or entry.get("path")
                or entry.get("filename")
                or entry.get("name")
            )

        else:
            name = None

        if not name:
            continue

        path = PRICES_DIR / str(name)

        if path.exists() and path.is_file():
            paths.append(path)

        else:
            log(
                "WARNING: manifest shard 不存在: "
                f"{path}"
            )

    if not paths:
        fallback = sorted(
            PRICES_DIR.glob(
                "prices_*.json"
            )
        )

        if fallback:
            log(
                "WARNING: manifest 無可用 shard，"
                "啟用 prices_*.json defensive fallback"
            )

            paths = fallback

    if not paths:
        raise RuntimeError(
            "manifest 沒有任何可讀價格 shard"
        )

    return list(
        dict.fromkeys(paths)
    )


def load_price_histories(
    universe: Dict[str, Dict[str, Any]],
) -> Dict[
    str,
    List[Dict[str, Any]],
]:
    merged: Dict[
        str,
        Dict[str, Dict[str, Any]],
    ] = defaultdict(dict)

    paths = manifest_files()

    parsed = 0

    for path in paths:
        try:
            payload = load_json_file(
                path
            )

            shard = extract_symbol_histories(
                payload
            )

            for symbol, rows in shard.items():
                if symbol not in universe:
                    continue

                merged[symbol].update(
                    rows
                )

            parsed += 1

        except Exception as exc:
            raise RuntimeError(
                f"價格 shard 解析失敗: {path}: {exc}"
            ) from exc

    histories = {
        symbol: sorted(
            rows.values(),
            key=lambda item: item["date"],
        )
        for symbol, rows in merged.items()
        if rows
    }

    log(
        "PRICE SHARDS: "
        f"manifest={len(paths)}, "
        f"parsed={parsed}, "
        f"merged_symbols={len(histories)}, "
        f"universe_stocks={len(universe)}"
    )

    if len(histories) < 1000:
        raise RuntimeError(
            "價格 coverage 異常過低: "
            f"{len(histories)} 檔；"
            "拒絕產生 market.json"
        )

    return histories


# ============================================================
# TAIEX
# ============================================================

def month_starts(
    count: int = 3,
) -> List[Tuple[int, int]]:
    now = datetime.now(
        TAIWAN_TZ
    ).date()

    year = now.year
    month = now.month

    result: List[Tuple[int, int]] = []

    for _ in range(count):
        result.append(
            (year, month)
        )

        month -= 1

        if month == 0:
            year -= 1
            month = 12

    return result


def fetch_index_history() -> List[Dict[str, Any]]:
    by_date: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for year, month in month_starts(3):
        params = {
            "date": (
                f"{year}{month:02d}01"
            ),
            "response": "json",
        }

        try:
            payload = request_json(
                TWSE_INDEX_HISTORY_URL,
                params,
            )

        except Exception as exc:
            log(
                "WARNING: TAIEX history "
                f"{year}-{month:02d} 失敗: {exc}"
            )
            continue

        rows = extract_rows(
            payload
        )

        # MI_5MINS_HIST can also return
        # a plain list of daily records.
        if not rows and isinstance(
            payload,
            list,
        ):
            rows = [
                row
                for row in payload
                if isinstance(
                    row,
                    dict,
                )
            ]

        for row in rows:
            trade_date = parse_date(
                first_value(
                    row,
                    (
                        "Date",
                        "日期",
                        "date",
                    ),
                )
            )

            close = number(
                first_value(
                    row,
                    (
                        "ClosingIndex",
                        "收盤指數",
                        "close",
                        "Close",
                    ),
                )
            )

            high = number(
                first_value(
                    row,
                    (
                        "HighestIndex",
                        "最高指數",
                        "high",
                        "High",
                    ),
                )
            )

            low = number(
                first_value(
                    row,
                    (
                        "LowestIndex",
                        "最低指數",
                        "low",
                        "Low",
                    ),
                )
            )

            opening = number(
                first_value(
                    row,
                    (
                        "OpeningIndex",
                        "開盤指數",
                        "open",
                        "Open",
                    ),
                )
            )

            if (
                trade_date
                and close is not None
            ):
                by_date[trade_date] = {
                    "date": trade_date,
                    "open": opening,
                    "high": high,
                    "low": low,
                    "close": close,
                }

    return sorted(
        by_date.values(),
        key=lambda item: item["date"],
    )


def fetch_current_index() -> Tuple[
    Optional[str],
    Optional[float],
]:
    payload = request_json(
        TWSE_INDEX_URL
    )

    if isinstance(
        payload,
        list,
    ):
        raw = payload

    elif isinstance(
        payload,
        dict,
    ):
        raw = (
            payload.get("data")
            or payload.get("rows")
            or []
        )

    else:
        raw = []

    candidates: List[
        Tuple[str, float]
    ] = []

    for row in raw:
        if not isinstance(
            row,
            dict,
        ):
            continue

        trade_date = parse_date(
            row.get("日期")
            or row.get("Date")
        )

        close = number(
            row.get("收盤指數")
            or row.get("ClosingIndex")
        )

        name = str(
            row.get("指數")
            or row.get("Index")
            or ""
        )

        if (
            trade_date
            and close is not None
            and (
                "發行量加權" in name
                or "TAIEX" in name.upper()
                or "加權" in name
            )
        ):
            candidates.append(
                (
                    trade_date,
                    close,
                )
            )

    # Defensive fallback:
    # if the index name cannot be matched,
    # accept any valid index row.
    if not candidates:
        for row in raw:
            if not isinstance(
                row,
                dict,
            ):
                continue

            trade_date = parse_date(
                row.get("日期")
                or row.get("Date")
            )

            close = number(
                row.get("收盤指數")
                or row.get("ClosingIndex")
            )

            if (
                trade_date
                and close is not None
            ):
                candidates.append(
                    (
                        trade_date,
                        close,
                    )
                )

    if not candidates:
        return None, None

    return max(
        candidates,
        key=lambda item: item[0],
    )


def rsi_wilder(
    closes: List[float],
    period: int = 14,
) -> Optional[float]:
    if len(closes) < period + 1:
        return None

    changes = [
        b - a
        for a, b in zip(
            closes[:-1],
            closes[1:],
        )
    ]

    if len(changes) < period:
        return None

    gains = [
        max(change, 0.0)
        for change in changes
    ]

    losses = [
        max(-change, 0.0)
        for change in changes
    ]

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(changes),
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
        if avg_gain > 0:
            return 100.0

        return 50.0

    rs = avg_gain / avg_loss

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def index_metrics(
    history: List[Dict[str, Any]],
    latest_date: Optional[str],
    current_close: Optional[float],
) -> Dict[str, Any]:
    rows = [
        row
        for row in history
        if row.get("date")
        and (
            latest_date is None
            or row["date"] <= latest_date
        )
    ]

    if (
        current_close is not None
        and latest_date
    ):
        if (
            not rows
            or rows[-1]["date"]
            < latest_date
        ):
            rows.append(
                {
                    "date": latest_date,
                    "open": current_close,
                    "high": current_close,
                    "low": current_close,
                    "close": current_close,
                }
            )

        elif rows[-1]["date"] == latest_date:
            rows[-1] = {
                **rows[-1],
                "close": current_close,
            }

    rows = sorted(
        {
            row["date"]: row
            for row in rows
        }.values(),
        key=lambda item: item["date"],
    )

    closes = [
        row["close"]
        for row in rows
        if row.get("close") is not None
    ]

    ma20 = (
        sum(closes[-20:]) / 20
        if len(closes) >= 20
        else None
    )

    previous_ma20 = (
        sum(closes[-21:-1]) / 20
        if len(closes) >= 21
        else None
    )

    slope = (
        ma20 - previous_ma20
        if (
            ma20 is not None
            and previous_ma20 is not None
        )
        else None
    )

    rsi = rsi_wilder(
        closes,
        14,
    )

    atr_pct = None

    atr_rows = rows[-15:]

    if (
        len(atr_rows) >= 15
        and all(
            row.get("high") is not None
            and row.get("low") is not None
            for row in atr_rows
        )
    ):
        true_ranges: List[float] = []

        for previous, current in zip(
            atr_rows[:-1],
            atr_rows[1:],
        ):
            high = current["high"]
            low = current["low"]
            previous_close = previous["close"]

            true_ranges.append(
                max(
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
            )

        if (
            len(true_ranges) == 14
            and closes
            and closes[-1] > 0
        ):
            atr = (
                sum(true_ranges)
                / 14
            )

            atr_pct = (
                atr / closes[-1]
            )

    return {
        "close": (
            current_close
            if current_close is not None
            else (
                closes[-1]
                if closes
                else None
            )
        ),
        "ma20": ma20,
        "ma20_slope": slope,
        "rsi14": rsi,
        "atr14_pct": atr_pct,
        "history_days": len(rows),
    }


# ============================================================
# MARKET BREADTH
# ============================================================

def latest_row_before(
    rows: List[Dict[str, Any]],
    target: str,
) -> Optional[
    Tuple[int, Dict[str, Any]]
]:
    candidate = None

    for index, row in enumerate(rows):
        if row["date"] <= target:
            candidate = (
                index,
                row,
            )
        else:
            break

    return candidate


def calculate_market_breadth(
    histories: Dict[
        str,
        List[Dict[str, Any]],
    ],
    latest_date: str,
) -> Dict[str, Any]:
    exact_current = 0
    stale = 0

    up = 0
    down = 0
    unchanged = 0

    above_ma20 = 0
    ma20_valid = 0

    new_high = 0
    new_low = 0
    new_hl_valid = 0

    daily_volume: Dict[
        str,
        float,
    ] = defaultdict(float)

    current_volume = 0.0
    current_volume_count = 0

    for rows in histories.values():
        selected = latest_row_before(
            rows,
            latest_date,
        )

        if selected is None:
            continue

        index, row = selected

        if row["date"] != latest_date:
            stale += 1
            continue

        exact_current += 1

        close = number(
            row.get("close")
        )

        volume = number(
            row.get("volume")
        )

        if volume is not None:
            current_volume += volume
            current_volume_count += 1

        # ----------------------------
        # Advance / decline
        # ----------------------------

        if index > 0 and close is not None:
            previous_close = number(
                rows[index - 1].get(
                    "close"
                )
            )

            if previous_close is not None:
                if close > previous_close:
                    up += 1

                elif close < previous_close:
                    down += 1

                else:
                    unchanged += 1

        # ----------------------------
        # MA20 breadth
        # ----------------------------

        if (
            index >= 19
            and close is not None
        ):
            ma20_closes = [
                number(
                    row_item.get(
                        "close"
                    )
                )
                for row_item
                in rows[
                    index - 19:
                    index + 1
                ]
            ]

            if all(
                value is not None
                for value in ma20_closes
            ):
                ma20 = (
                    sum(ma20_closes)
                    / 20
                )

                ma20_valid += 1

                if close > ma20:
                    above_ma20 += 1

        # ----------------------------
        # 20-day new high / low
        #
        # IMPORTANT:
        # Compare today's close against
        # the PREVIOUS 20 trading days.
        # Do not include today.
        # ----------------------------

        if (
            index >= 20
            and close is not None
        ):
            previous_20 = [
                number(
                    row_item.get(
                        "close"
                    )
                )
                for row_item
                in rows[
                    index - 20:
                    index
                ]
            ]

            if all(
                value is not None
                for value in previous_20
            ):
                new_hl_valid += 1

                if close >= max(
                    previous_20
                ):
                    new_high += 1

                if close <= min(
                    previous_20
                ):
                    new_low += 1

    # ----------------------------
    # Historical market volume
    # ----------------------------

    for rows in histories.values():
        for row in rows:
            trade_date = row.get(
                "date"
            )

            if not trade_date:
                continue

            if trade_date >= latest_date:
                continue

            volume = number(
                row.get("volume")
            )

            if volume is not None:
                daily_volume[
                    trade_date
                ] += volume

    previous_dates = sorted(
        daily_volume.keys()
    )[-20:]

    average_volume = (
        sum(
            daily_volume[
                trade_date
            ]
            for trade_date
            in previous_dates
        ) / 20
        if len(previous_dates) == 20
        else None
    )

    volume_ratio = (
        current_volume
        / average_volume
        if (
            average_volume
            and current_volume_count
        )
        else None
    )

    # ----------------------------
    # A/D ratio
    # ----------------------------

    ad_ratio = None

    if (
        down == 0
        and up > 0
    ):
        ad_status = "infinite"

    elif down > 0:
        ad_ratio = up / down
        ad_status = "finite"

    elif up == 0 and down == 0:
        ad_status = "unavailable"

    else:
        ad_status = "unavailable"

    # ----------------------------
    # MA20 breadth ratio
    # ----------------------------

    breadth_ratio = (
        above_ma20 / ma20_valid
        if ma20_valid
        else None
    )

    # ----------------------------
    # New high / low
    # ----------------------------

    nhl_ratio = None

    if (
        new_low == 0
        and new_high > 0
    ):
        nhl_status = "infinite"

    elif new_low > 0:
        nhl_ratio = (
            new_high
            / new_low
        )
        nhl_status = "finite"

    elif (
        new_high == 0
        and new_low == 0
        and new_hl_valid
    ):
        nhl_ratio = 0.0
        nhl_status = "finite_zero"

    else:
        nhl_status = "unavailable"

    return {
        "coverage": {
            "current_date": latest_date,
            "exact_current": exact_current,
            "stale_or_missing": (
                len(histories)
                - exact_current
            ),
            "stale_exact_latest": stale,
        },

        "advance_decline": {
            "up": up,
            "down": down,
            "unchanged": unchanged,
            "ratio": ad_ratio,
            "ratio_status": ad_status,
        },

        "ma20_breadth": {
            "above": above_ma20,
            "valid": ma20_valid,
            "ratio": breadth_ratio,
        },

        "volume": {
            "current": (
                current_volume
                if current_volume_count
                else None
            ),
            "current_stocks": (
                current_volume_count
            ),
            "previous_20_day_average": (
                average_volume
            ),
            "ratio": volume_ratio,
            "valid_days": len(
                previous_dates
            ),
        },

        "new_high_low": {
            "new_high": new_high,
            "new_low": new_low,
            "valid": new_hl_valid,
            "ratio": nhl_ratio,
            "ratio_status": nhl_status,
        },
    }


# ============================================================
# INSTITUTIONAL
# ============================================================

def table_rows(
    payload: Any,
) -> List[Dict[str, Any]]:
    """
    Convert:
        tables[].fields + tables[].data

    into:
        list[dict]
    """

    result: List[
        Dict[str, Any]
    ] = []

    if (
        isinstance(payload, dict)
        and isinstance(
            payload.get("tables"),
            list,
        )
    ):
        for table in payload[
            "tables"
        ]:
            if not isinstance(
                table,
                dict,
            ):
                continue

            fields = (
                table.get("fields")
                or table.get("columns")
            )

            data = (
                table.get("data")
                or table.get("rows")
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

            for values in data:
                if isinstance(
                    values,
                    list,
                ):
                    result.append(
                        dict(
                            zip(
                                fields,
                                values,
                            )
                        )
                    )

                elif isinstance(
                    values,
                    dict,
                ):
                    result.append(
                        values
                    )

    if result:
        return result

    return extract_rows(
        payload
    )


def find_field(
    row: Dict[str, Any],
    exact: Iterable[str],
    contains: Iterable[str] = (),
) -> Optional[float]:
    normalized = {
        normalize_label(key): value
        for key, value in row.items()
    }

    for candidate in exact:
        key = normalize_label(
            candidate
        )

        if key in normalized:
            return number(
                normalized[key]
            )

    contains_tokens = [
        normalize_label(token)
        for token in contains
    ]

    for key, value in normalized.items():
        if all(
            token in key
            for token in contains_tokens
        ):
            result = number(value)

            if result is not None:
                return result

    return None


def fetch_twse_institutional(
    target_date: str,
) -> Dict[str, Any]:
    ymd = target_date.replace(
        "-",
        "",
    )

    payload = request_json(
        TWSE_T86_URL,
        {
            "date": ymd,
            "selectType": "ALLBUT0999",
            "response": "json",
        },
    )

    rows = table_rows(
        payload
    )

    foreign = 0.0
    foreign_dealer = 0.0
    trust = 0.0

    foreign_count = 0
    trust_count = 0

    for row in rows:
        foreign_value = find_field(
            row,
            [
                "外陸資買賣超股數(不含外資自營商)",
                "外陸資買賣超股數",
            ],
            (
                "外陸資買賣超股數",
            ),
        )

        foreign_dealer_value = find_field(
            row,
            [
                "外資自營商買賣超股數",
            ],
            (
                "外資自營商買賣超股數",
            ),
        )

        trust_value = find_field(
            row,
            [
                "投信買賣超股數",
            ],
            (
                "投信買賣超股數",
            ),
        )

        if foreign_value is not None:
            foreign += foreign_value
            foreign_count += 1

        if foreign_dealer_value is not None:
            foreign_dealer += (
                foreign_dealer_value
            )

        if trust_value is not None:
            trust += trust_value
            trust_count += 1

    return {
        "foreign_net": (
            foreign
            if foreign_count
            else None
        ),
        "foreign_dealer_net": (
            foreign_dealer
            if rows
            else None
        ),
        "trust_net": (
            trust
            if trust_count
            else None
        ),
        "rows": len(rows),
        "source": "TWSE T86",
    }


def fetch_tpex_institutional(
    target_date: str,
) -> Dict[str, Any]:
    payload = request_json(
        TPEX_INSTITUTIONAL_URL
    )

    rows = table_rows(
        payload
    )

    target_slash = (
        target_date.replace(
            "-",
            "/",
        )
    )

    target_compact = (
        target_date.replace(
            "-",
            "",
        )
    )

    filtered: List[
        Dict[str, Any]
    ] = []

    for row in rows:
        values = " ".join(
            str(value)
            for value in row.values()
        )

        if (
            target_slash in values
            or target_date in values
            or target_compact in values
        ):
            filtered.append(
                row
            )

    # Some TPEx API responses are already
    # date-scoped. Do not discard them.
    if not filtered:
        filtered = rows

    foreign = 0.0
    trust = 0.0

    foreign_count = 0
    trust_count = 0

    for row in filtered:
        foreign_value = find_field(
            row,
            [],
            (
                "foreigninvestorsinclude",
                "difference",
            ),
        )

        if foreign_value is None:
            foreign_value = find_field(
                row,
                [],
                (
                    "外資",
                    "買賣超",
                ),
            )

        trust_value = find_field(
            row,
            [],
            (
                "securitiesinvestmenttrustcompanies",
                "difference",
            ),
        )

        if trust_value is None:
            trust_value = find_field(
                row,
                [],
                (
                    "投信",
                    "買賣超",
                ),
            )

        if foreign_value is not None:
            foreign += foreign_value
            foreign_count += 1

        if trust_value is not None:
            trust += trust_value
            trust_count += 1

    return {
        "foreign_net": (
            foreign
            if foreign_count
            else None
        ),
        "trust_net": (
            trust
            if trust_count
            else None
        ),
        "rows": len(filtered),
        "source": (
            "TPEx "
            "tpex_3insti_daily_trading"
        ),
    }


def fetch_institutional(
    target_date: str,
) -> Dict[str, Any]:
    try:
        twse = fetch_twse_institutional(
            target_date
        )

    except Exception as exc:
        log(
            "WARNING: TWSE T86 unavailable: "
            f"{exc}"
        )

        twse = {
            "foreign_net": None,
            "foreign_dealer_net": None,
            "trust_net": None,
            "rows": 0,
            "source": "TWSE T86",
        }

    try:
        tpex = fetch_tpex_institutional(
            target_date
        )

    except Exception as exc:
        log(
            "WARNING: TPEx institutional "
            f"unavailable: {exc}"
        )

        tpex = {
            "foreign_net": None,
            "trust_net": None,
            "rows": 0,
            "source": (
                "TPEx "
                "tpex_3insti_daily_trading"
            ),
        }

    def combine(
        first: Optional[float],
        second: Optional[float],
    ) -> Tuple[
        Optional[float],
        str,
    ]:
        values = [
            value
            for value in (
                first,
                second,
            )
            if value is not None
        ]

        if len(values) == 2:
            return (
                sum(values),
                "complete",
            )

        if len(values) == 1:
            return (
                values[0],
                "partial",
            )

        return (
            None,
            "unavailable",
        )

    foreign_net, foreign_status = combine(
        twse.get("foreign_net"),
        tpex.get("foreign_net"),
    )

    trust_net, trust_status = combine(
        twse.get("trust_net"),
        tpex.get("trust_net"),
    )

    return {
        "foreign_net": foreign_net,
        "foreign_status": foreign_status,
        "trust_net": trust_net,
        "trust_status": trust_status,

        "twse": twse,
        "tpex": tpex,

        "sources": [
            "TWSE T86",
            "TPEx tpex_3insti_daily_trading",
        ],
    }


# ============================================================
# CONDITIONS
# ============================================================

def condition(
    name: str,
    value: Any,
    passed: Optional[bool],
    reason: str,
    status: str = "available",
) -> Dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "pass": passed,
        "status": status,
        "reason": reason,
    }


def build_conditions(
    index: Dict[str, Any],
    breadth: Dict[str, Any],
    institutional: Dict[str, Any],
) -> List[Dict[str, Any]]:
    conditions: List[
        Dict[str, Any]
    ] = []

    close = index.get(
        "close"
    )

    ma20 = index.get(
        "ma20"
    )

    slope = index.get(
        "ma20_slope"
    )

    rsi = index.get(
        "rsi14"
    )

    atr = index.get(
        "atr14_pct"
    )

    # 1
    ratio = (
        close / ma20
        if (
            close is not None
            and ma20 is not None
            and ma20 != 0
        )
        else None
    )

    passed = (
        close > ma20
        if (
            close is not None
            and ma20 is not None
        )
        else None
    )

    conditions.append(
        condition(
            "TAIEX > MA20",
            ratio,
            passed,
            (
                "TAIEX 收盤指數高於 MA20"
                if passed is True
                else (
                    "未高於 MA20"
                    if passed is False
                    else "資料不足"
                )
            ),
            (
                "available"
                if passed is not None
                else "unavailable"
            ),
        )
    )

    # 2
    passed = (
        slope > 0
        if slope is not None
        else None
    )

    conditions.append(
        condition(
            "MA20 上升",
            slope,
            passed,
            (
                "MA20 高於前一日 MA20"
                if passed is True
                else (
                    "MA20 未上升"
                    if passed is False
                    else "資料不足"
                )
            ),
            (
                "available"
                if passed is not None
                else "unavailable"
            ),
        )
    )

    # 3
    passed = (
        rsi > 50
        if rsi is not None
        else None
    )

    conditions.append(
        condition(
            "TAIEX RSI14 > 50",
            rsi,
            passed,
            (
                "RSI14 高於 50"
                if passed is True
                else (
                    "RSI14 未高於 50"
                    if passed is False
                    else "資料不足"
                )
            ),
            (
                "available"
                if passed is not None
                else "unavailable"
            ),
        )
    )

    # 4
    ad = breadth[
        "advance_decline"
    ]

    if ad["ratio_status"] == "infinite":
        ad_pass = True

    elif ad["ratio"] is not None:
        ad_pass = (
            ad["ratio"]
            >= CONFIG[
                "advance_decline_min_ratio"
            ]
        )

    else:
        ad_pass = None

    conditions.append(
        condition(
            "上漲家數 / 下跌家數 >= 1",
            ad["ratio"],
            ad_pass,
            (
                "上漲家數/下跌家數達標"
                if ad_pass is True
                else (
                    "上漲家數/下跌家數未達標"
                    if ad_pass is False
                    else "資料不足"
                )
            ),
            (
                "available"
                if ad_pass is not None
                else "unavailable"
            ),
        )
    )

    # 5
    breadth_ratio = breadth[
        "ma20_breadth"
    ]["ratio"]

    breadth_pass = (
        breadth_ratio
        >= CONFIG[
            "breadth_min_pct"
        ]
        if breadth_ratio is not None
        else None
    )

    conditions.append(
        condition(
            "站上 MA20 比例 >= 50%",
            breadth_ratio,
            breadth_pass,
            (
                "站上 MA20 比例達標"
                if breadth_pass is True
                else (
                    "站上 MA20 比例未達標"
                    if breadth_pass is False
                    else "資料不足"
                )
            ),
            (
                "available"
                if breadth_pass is not None
                else "unavailable"
            ),
        )
    )

    # 6
    volume_ratio = breadth[
        "volume"
    ]["ratio"]

    volume_pass = (
        volume_ratio
        >= CONFIG[
            "volume_ratio_min"
        ]
        if volume_ratio is not None
        else None
    )

    conditions.append(
        condition(
            "市場成交量 / 20日均量 >= 1",
            volume_ratio,
            volume_pass,
            (
                "成交量達 20 日均量"
                if volume_pass is True
                else (
                    "成交量未達 20 日均量"
                    if volume_pass is False
                    else "資料不足"
                )
            ),
            (
                "available"
                if volume_pass is not None
                else "unavailable"
            ),
        )
    )

    # 7
    foreign_net = institutional.get(
        "foreign_net"
    )

    foreign_pass = (
        foreign_net > 0
        if foreign_net is not None
        else None
    )

    conditions.append(
        condition(
            "外資買賣超 > 0",
            foreign_net,
            foreign_pass,
            (
                "外資買超"
                if foreign_pass is True
                else (
                    "外資未買超"
                    if foreign_pass is False
                    else "資料不足"
                )
            ),
            institutional.get(
                "foreign_status",
                "unavailable",
            ),
        )
    )

    # 8
    trust_net = institutional.get(
        "trust_net"
    )

    trust_pass = (
        trust_net > 0
        if trust_net is not None
        else None
    )

    conditions.append(
        condition(
            "投信買賣超 > 0",
            trust_net,
            trust_pass,
            (
                "投信買超"
                if trust_pass is True
                else (
                    "投信未買超"
                    if trust_pass is False
                    else "資料不足"
                )
            ),
            institutional.get(
                "trust_status",
                "unavailable",
            ),
        )
    )

    # 9
    nhl = breadth[
        "new_high_low"
    ]

    if nhl["ratio_status"] == "infinite":
        nhl_pass = True

    elif nhl["ratio"] is not None:
        nhl_pass = (
            nhl["ratio"]
            >= CONFIG[
                "new_high_low_min_ratio"
            ]
        )

    else:
        nhl_pass = None

    conditions.append(
        condition(
            "20日新高 / 新低 >= 1",
            nhl["ratio"],
            nhl_pass,
            (
                "新高/新低達標"
                if nhl_pass is True
                else (
                    "新高/新低未達標"
                    if nhl_pass is False
                    else "資料不足"
                )
            ),
            (
                "available"
                if nhl_pass is not None
                else "unavailable"
            ),
        )
    )

    # 10
    atr_pass = (
        atr
        <= CONFIG[
            "atr_pct_max"
        ]
        if atr is not None
        else None
    )

    conditions.append(
        condition(
            "TAIEX ATR14% <= 3%",
            atr,
            atr_pass,
            (
                "ATR14% 未超過 3%"
                if atr_pass is True
                else (
                    "ATR14% 超過 3%"
                    if atr_pass is False
                    else "資料不足"
                )
            ),
            (
                "available"
                if atr_pass is not None
                else "unavailable"
            ),
        )
    )

    return conditions


def market_sentiment(
    conditions: List[Dict[str, Any]],
) -> Tuple[
    str,
    int,
    int,
]:
    valid = [
        item
        for item in conditions
        if (
            item.get("status")
            != "unavailable"
            and item.get("pass")
            is not None
        )
    ]

    score = sum(
        1
        for item in valid
        if item.get("pass") is True
    )

    valid_count = len(valid)

    if (
        valid_count
        < CONFIG[
            "minimum_valid_conditions"
        ]
    ):
        return (
            "資料不足",
            score,
            valid_count,
        )

    if (
        score
        >= CONFIG[
            "score_bullish"
        ]
    ):
        return (
            "偏多",
            score,
            valid_count,
        )

    if (
        score
        >= CONFIG[
            "score_sideways"
        ]
    ):
        return (
            "震盪",
            score,
            valid_count,
        )

    return (
        "偏弱",
        score,
        valid_count,
    )


def market_status(
    now: Optional[datetime] = None,
) -> str:
    now = (
        now
        or datetime.now(
            TAIWAN_TZ
        )
    )

    if now.weekday() >= 5:
        return "closed"

    if (
        time(9, 0)
        <= now.time()
        <= time(13, 30)
    ):
        return "open"

    return "closed"


# ============================================================
# OUTPUT
# ============================================================

def atomic_write(
    path: Path,
    payload: Dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                clean_json(payload),
                fh,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )

            fh.write("\n")
            fh.flush()
            os.fsync(
                fh.fileno()
            )

        os.replace(
            temp_name,
            path,
        )

    finally:
        if os.path.exists(
            temp_name
        ):
            os.unlink(
                temp_name
            )


def validate_market(
    payload: Dict[str, Any],
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
        - set(payload)
    )

    if missing:
        raise RuntimeError(
            "market.json 缺少欄位: "
            f"{sorted(missing)}"
        )

    if (
        payload[
            "schema_version"
        ]
        != SCHEMA_VERSION
    ):
        raise RuntimeError(
            "schema_version 錯誤"
        )

    if payload[
        "market_status"
    ] not in {
        "open",
        "closed",
    }:
        raise RuntimeError(
            "market_status 錯誤"
        )

    expected_conditions = [
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

    actual_conditions = [
        item.get("name")
        for item
        in payload["conditions"]
    ]

    if (
        actual_conditions
        != expected_conditions
    ):
        raise RuntimeError(
            "conditions 順序/名稱錯誤: "
            f"{actual_conditions}"
        )

    if len(
        payload["conditions"]
    ) != 10:
        raise RuntimeError(
            "conditions 必須正好 10 個"
        )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
    )

    if (
        "Infinity" in serialized
        or "NaN" in serialized
    ):
        raise RuntimeError(
            "JSON 不得包含 "
            "Infinity / NaN"
        )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    log(
        "========================================"
    )

    log(
        "FETCH MARKET V2.1"
    )

    log(
        "========================================"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    universe = load_universe()

    log(
        "UNIVERSE: "
        f"{len(universe)} "
        "active common stocks"
    )

    # --------------------------------------------------------
    # Price shards
    # --------------------------------------------------------

    histories = load_price_histories(
        universe
    )

    # --------------------------------------------------------
    # Current TAIEX
    # --------------------------------------------------------

    current_date, current_close = (
        fetch_current_index()
    )

    # --------------------------------------------------------
    # Historical TAIEX
    # --------------------------------------------------------

    index_history = (
        fetch_index_history()
    )

    history_latest = (
        index_history[-1]["date"]
        if index_history
        else None
    )

    candidates = [
        value
        for value in (
            current_date,
            history_latest,
        )
        if value is not None
    ]

    if not candidates:
        raise RuntimeError(
            "無法取得 TAIEX "
            "latest trading date"
        )

    latest_date = max(
        candidates
    )

    if current_date != latest_date:
        current_close = None

        if (
            index_history
            and index_history[-1]["date"]
            == latest_date
        ):
            current_close = (
                index_history[-1]["close"]
            )

    # --------------------------------------------------------
    # Index metrics
    # --------------------------------------------------------

    index = index_metrics(
        index_history,
        latest_date,
        current_close,
    )

    # --------------------------------------------------------
    # Breadth
    # --------------------------------------------------------

    breadth = calculate_market_breadth(
        histories,
        latest_date,
    )

    # --------------------------------------------------------
    # Institutional
    # --------------------------------------------------------

    institutional = fetch_institutional(
        latest_date
    )

    # --------------------------------------------------------
    # Conditions
    # --------------------------------------------------------

    conditions = build_conditions(
        index,
        breadth,
        institutional,
    )

    sentiment, score, valid = (
        market_sentiment(
            conditions
        )
    )

    generated_at = (
        datetime.now(
            TAIWAN_TZ
        ).isoformat(
            timespec="seconds"
        )
    )

    # --------------------------------------------------------
    # market-v2.1
    # --------------------------------------------------------

    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,

        "generated_at": generated_at,

        "market_status": market_status(),

        "latest_trading_date": latest_date,

        "index": {
            "name": "TAIEX",
            "close": index["close"],
            "ma20": index["ma20"],
            "rsi14": index["rsi14"],
            "atr14_pct": index[
                "atr14_pct"
            ],
            "history_days": index[
                "history_days"
            ],
        },

        "trend": {
            "ma20": index["ma20"],
            "ma20_slope": index[
                "ma20_slope"
            ],
        },

        "breadth": {
            "coverage": breadth[
                "coverage"
            ],
            "advance_decline": breadth[
                "advance_decline"
            ],
            "ma20_breadth": breadth[
                "ma20_breadth"
            ],
            "new_high_low": breadth[
                "new_high_low"
            ],
        },

        "volume": breadth[
            "volume"
        ],

        "institutional": institutional,

        "sentiment": {
            "label": sentiment,
            "score": score,
            "valid_conditions": valid,
            "total_conditions": 10,
        },

        "conditions": conditions,

        "source": {
            "index": "TWSE MI_INDEX",
            "index_history": (
                "TWSE MI_5MINS_HIST"
            ),
            "institutional": [
                "TWSE T86",
                "TPEx "
                "tpex_3insti_daily_trading",
            ],
            "prices": (
                "Data/prices/manifest.json "
                "+ prices-v14.0 shards"
            ),
        },

        "config": CONFIG,
    }

    # --------------------------------------------------------
    # Validation BEFORE write
    # --------------------------------------------------------

    validate_market(
        payload
    )

    # --------------------------------------------------------
    # Atomic write
    # --------------------------------------------------------

    atomic_write(
        OUTPUT_FILE,
        payload,
    )

    # --------------------------------------------------------
    # Read-back validation
    # --------------------------------------------------------

    read_back = load_json_file(
        OUTPUT_FILE
    )

    validate_market(
        read_back
    )

    # --------------------------------------------------------
    # Final diagnostics
    # --------------------------------------------------------

    coverage = len(
        histories
    )

    universe_count = len(
        universe
    )

    log(
        "PRICE COVERAGE: "
        f"{coverage}/{universe_count}"
    )

    log(
        f"LATEST: {latest_date}"
    )

    log(
        "TAIEX: "
        f"{index['close']} / "
        f"MA20={index['ma20']} / "
        f"RSI14={index['rsi14']} / "
        f"ATR14%={index['atr14_pct']}"
    )

    log(
        "SENTIMENT: "
        f"{sentiment} "
        f"score={score}/10 "
        f"valid={valid}/10"
    )

    log(
        f"OUTPUT: {OUTPUT_FILE}"
    )

    log(
        "READ-BACK VALIDATION: PASS"
    )


if __name__ == "__main__":
    main()