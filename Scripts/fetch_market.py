#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_market.py

MARKET ENVIRONMENT V2.1

資料鏈
------------------------------------------------------------
TWSE 官方
    ├─ MI_INDEX
    ├─ MI_5MINS_HIST
    └─ BFI82U

TPEx 官方
    └─ tpex_3insti_daily_trading

Data/prices/
    └─ fetch_prices.py 官方優先價格 shard

                ↓

Data/market.json

                ↓

Scripts/build_ui_data.py

                ↓

Data/ui_data.json

                ↓

index.html

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

資料不足：
    不得當成 FAIL = 0 分

市場風向：
    8~10  → 偏多
    5~7   → 震盪
    0~4   → 偏弱

有效條件 < 6：
    資料不足
"""

from __future__ import annotations

import json
import math
import os
import tempfile

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

TAIWAN_TZ = timezone(
    timedelta(hours=8)
)

REQUEST_TIMEOUT = 30


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

TWSE_INSTITUTIONAL_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/fund/BFI82U"
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
        flush=True
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
            response.text[:300]
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

    if isinstance(value, bool):
        return None

    try:

        result = float(
            str(value)
            .replace(",", "")
            .replace("%", "")
            .strip()
        )

        if not math.isfinite(result):
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

        if text.endswith(suffix):

            text = text[
                :-len(suffix)
            ]

            break

    return text


# ============================================================
# DATE
# ============================================================

def parse_date(
    value: Any,
) -> Optional[date]:

    text = str(
        value or ""
    ).strip()

    if not text:
        return None

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

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
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
# GENERIC ROW PARSER
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
            if isinstance(row, dict)
        ]

    if isinstance(
        payload,
        dict,
    ):

        for key in (
            "data",
            "rows",
            "records",
            "result",
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
                    if isinstance(row, dict)
                ]

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

    for row in rows:

        if (
            str(
                row.get("指數", "")
            ).strip()
            != "發行量加權股價指數"
        ):

            continue

        trading_date = parse_date(
            row.get("日期")
        )

        close = number(
            row.get("收盤指數")
        )

        change = number(
            row.get("漲跌點數")
        )

        change_pct = number(
            row.get("漲跌百分比")
        )

        sign = str(
            row.get("漲跌", "")
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
                    "name": "加權指數",
                    "value": round(
                        close,
                        2,
                    ),
                    "change": (
                        round(
                            change,
                            2,
                        )
                        if change is not None
                        else None
                    ),
                    "change_pct": (
                        round(
                            change_pct,
                            2,
                        )
                        if change_pct is not None
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

def fetch_index_history() -> List[
    Dict[str, Any]
]:

    payload = request_json(
        TWSE_INDEX_HISTORY_URL
    )

    rows = list_dict_rows(
        payload
    )

    output = []

    for row in rows:

        trading_date = parse_date(
            row.get("Date")
            or row.get("日期")
        )

        close = number(
            row.get("ClosingIndex")
            or row.get("收盤指數")
        )

        high = number(
            row.get("HighestIndex")
            or row.get("最高指數")
        )

        low = number(
            row.get("LowestIndex")
            or row.get("最低指數")
        )

        if (
            trading_date
            and close is not None
        ):

            output.append(
                {
                    "date": trading_date,
                    "close": close,
                    "high": high,
                    "low": low,
                }
            )

    output.sort(
        key=lambda x: x["date"]
    )

    return output


# ============================================================
# 3. UNIVERSE
# ============================================================

def load_universe() -> Dict[
    str,
    Dict[str, Any],
]:

    data = json.loads(
        UNIVERSE_FILE.read_text(
            encoding="utf-8-sig"
        )
    )

    stocks = (
        data.get("stocks")
        if isinstance(data, dict)
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

    return output


# ============================================================
# 4. STOCK FILTER
# ============================================================

def is_stock(
    item: Dict[str, Any],
) -> bool:

    text = " ".join(
        str(
            item.get(key, "")
        )
        for key in (
            "type",
            "instrument_type",
            "security_type",
            "category",
            "product_type",
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

    return not any(
        token in text
        for token in excluded
    )


# ============================================================
# 5. PRICE SHARD PARSER
#
# fetch_prices.py V14.0 actual contract:
#
# {
#   "schema_version": "prices-v14.0",
#   "stocks": {
#       "1101": [
#           {...},
#           {...}
#       ]
#   }
# }
# ============================================================

def parse_price_history(
    values: Any,
) -> List[Dict[str, Any]]:

    if not isinstance(
        values,
        list,
    ):

        return []

    output = []

    for row in values:

        if not isinstance(
            row,
            dict,
        ):

            continue

        trading_date = parse_date(
            row.get("date")
            or row.get("Date")
            or row.get("trade_date")
            or row.get("TradeDate")
        )

        close = number(
            row.get("close")
            or row.get("Close")
            or row.get("closing_price")
            or row.get("收盤價")
        )

        volume = number(
            row.get("volume")
            or row.get("Volume")
            or row.get("成交量")
            or row.get("成交股數")
        )

        if (
            trading_date
            and close is not None
        ):

            output.append(
                {
                    "date": trading_date,
                    "close": close,
                    "volume": volume,
                }
            )

    output.sort(
        key=lambda x: x["date"]
    )

    return output


def load_price_histories(
    universe: Dict[
        str,
        Dict[str, Any],
    ],
) -> Dict[
    str,
    List[Dict[str, Any]],
]:

    if not MANIFEST_FILE.exists():

        raise RuntimeError(
            "找不到 Data/prices/manifest.json"
        )

    manifest = json.loads(
        MANIFEST_FILE.read_text(
            encoding="utf-8"
        )
    )

    files = (
        manifest.get("files", [])
        if isinstance(manifest, dict)
        else []
    )

    shard_paths = []

    for item in files:

        if isinstance(
            item,
            str,
        ):

            filename = item

        elif isinstance(
            item,
            dict,
        ):

            filename = (
                item.get("file")
                or item.get("path")
                or item.get("filename")
                or item.get("name")
            )

        else:

            continue

        if not filename:
            continue

        path = (
            PRICES_DIR
            / Path(
                str(filename)
            ).name
        )

        if path.exists():
            shard_paths.append(
                path
            )

    if not shard_paths:

        shard_paths = sorted(
            PRICES_DIR.glob(
                "prices_*.json"
            )
        )

    allowed_symbols = {
        symbol
        for symbol, item
        in universe.items()
        if is_stock(item)
    }

    output = {}

    for path in shard_paths:

        payload = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )

        if (
            payload.get(
                "schema_version"
            )
            != "prices-v14.0"
        ):

            raise RuntimeError(
                f"{path.name} "
                "schema_version 錯誤"
            )

        stocks = payload.get(
            "stocks"
        )

        if not isinstance(
            stocks,
            dict,
        ):

            raise RuntimeError(
                f"{path.name}.stocks "
                "不是 object"
            )

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

            history = parse_price_history(
                values
            )

            if history:

                output[
                    symbol
                ] = history

    return output


# ============================================================
# 6. RSI
# ============================================================

def calculate_rsi(
    closes: List[float],
    period: int = 14,
) -> Optional[float]:

    if len(closes) < period + 1:

        return None

    changes = [
        b - a
        for a, b in zip(
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
            value,
            0.0,
        )
        for value in changes
    ]

    losses = [
        max(
            -value,
            0.0,
        )
        for value in changes
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
# 7. INDEX METRICS
# ============================================================

def calculate_index_metrics(
    history: List[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    closes = [
        row["close"]
        for row in history
    ]

    if len(closes) < 21:

        return {
            "ma20": None,
            "ma20_previous": None,
            "ma20_slope": None,
            "rsi14": None,
            "atr14_pct": None,
        }

    ma20 = (
        sum(
            closes[-20:]
        )
        / 20
    )

    previous_ma20 = (
        sum(
            closes[-21:-1]
        )
        / 20
    )

    rsi14 = calculate_rsi(
        closes,
        14,
    )

    true_ranges = []

    for current, previous in zip(
        history[1:],
        history[:-1],
    ):

        if (
            current["high"]
            is None
            or current["low"]
            is None
        ):

            continue

        true_range = max(
            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            ),
        )

        true_ranges.append(
            true_range
        )

    atr14 = (
        sum(
            true_ranges[-14:]
        )
        / 14
        if len(true_ranges) >= 14
        else None
    )

    atr14_pct = (
        atr14 / closes[-1]
        if (
            atr14 is not None
            and closes[-1] != 0
        )
        else None
    )

    return {
        "ma20": ma20,
        "ma20_previous": previous_ma20,
        "ma20_slope": (
            ma20 - previous_ma20
        ),
        "rsi14": rsi14,
        "atr14_pct": atr14_pct,
    }


# ============================================================
# 8. MARKET BREADTH
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

    current_volume = 0.0

    daily_volume_totals: Dict[
        date,
        float,
    ] = {}

    coverage = 0

    volume_valid_symbols = 0

    for history in histories.values():

        if not history:
            continue

        if history[-1]["date"] != latest_date:
            continue

        coverage += 1

        current = history[-1]

        previous = (
            history[-2]
            if len(history) >= 2
            else None
        )

        if previous:

            if (
                current["close"]
                > previous["close"]
            ):

                advancing += 1

            elif (
                current["close"]
                < previous["close"]
            ):

                declining += 1

            else:

                unchanged += 1

        window = history[-20:]

        if len(window) >= 20:

            ma20 = (
                sum(
                    row["close"]
                    for row in window
                )
                / 20
            )

            ma20_valid += 1

            if (
                current["close"]
                > ma20
            ):

                above_ma20 += 1

            highest = max(
                row["close"]
                for row in window
            )

            lowest = min(
                row["close"]
                for row in window
            )

            if (
                current["close"]
                >= highest
            ):

                new_high_20d += 1

            if (
                current["close"]
                <= lowest
            ):

                new_low_20d += 1

        volume_rows = [
            row
            for row in history
            if row.get("volume")
            is not None
        ]

        if len(volume_rows) >= 21:

            volume_valid_symbols += 1

            previous_20 = (
                volume_rows[-21:-1]
            )

            for row in previous_20:

                trading_date = row[
                    "date"
                ]

                daily_volume_totals[
                    trading_date
                ] = (
                    daily_volume_totals.get(
                        trading_date,
                        0.0,
                    )
                    + float(
                        row["volume"]
                    )
                )

            if (
                current.get("volume")
                is not None
            ):

                current_volume += float(
                    current["volume"]
                )

    above_ma20_pct = (
        above_ma20
        / ma20_valid
        if ma20_valid
        else None
    )

    advance_decline_ratio = (
        advancing / declining
        if declining > 0
        else None
    )

    new_high_low_ratio = (
        new_high_20d
        / new_low_20d
        if new_low_20d > 0
        else None
    )

    average_20d_volume = (
        sum(
            daily_volume_totals.values()
        )
        / len(
            daily_volume_totals
        )
        if len(
            daily_volume_totals
        ) >= 20
        else None
    )

    volume_ratio = (
        current_volume
        / average_20d_volume
        if (
            average_20d_volume
            and current_volume
        )
        else None
    )

    return {
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,

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
            current_volume
            if current_volume
            else None,

        "volume_20d_average":
            average_20d_volume,

        "volume_ratio":
            volume_ratio,

        "coverage":
            coverage,

        "volume_valid_symbols":
            volume_valid_symbols,
    }


# ============================================================
# 9. TWSE INSTITUTIONAL
# ============================================================

def fetch_twse_institutional(
    trading_date: date,
) -> Tuple[
    Optional[float],
    Optional[float],
    str,
]:

    try:

        payload = request_json(
            TWSE_INSTITUTIONAL_URL,
            {
                "response": "json",
                "dayDate":
                    trading_date.strftime(
                        "%Y%m%d"
                    ),
                "type": "day",
            },
        )

    except Exception as exc:

        return (
            None,
            None,
            f"TWSE BFI82U unavailable: "
            f"{exc}",
        )

    rows = table_rows(
        payload
    )

    foreign_net = None
    trust_net = None

    for row in rows:

        label = str(
            row.get("單位名稱")
            or row.get("法人名稱")
            or row.get("項目")
            or row.get("名稱")
            or ""
        ).strip()

        numeric_values = [
            number(value)
            for key, value
            in row.items()
            if key not in {
                "單位名稱",
                "法人名稱",
                "項目",
                "名稱",
            }
        ]

        numeric_values = [
            value
            for value in numeric_values
            if value is not None
        ]

        if not numeric_values:
            continue

        value = numeric_values[-1]

        if (
            "外資" in label
            and "投信" not in label
        ):

            foreign_net = value

        elif (
            label == "投信"
            or label.startswith("投信")
        ):

            trust_net = value

    return (
        foreign_net,
        trust_net,
        "TWSE BFI82U",
    )


# ============================================================
# 10. TPEX INSTITUTIONAL
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

    foreign_key = (
        "Foreign Investors include "
        "Mainland Area Investors "
        "(Foreign Dealers excluded)"
        "-Difference"
    )

    trust_key = (
        "SecuritiesInvestmentTrustCompanies"
        "-Difference"
    )

    foreign_total = 0.0
    trust_total = 0.0

    foreign_count = 0
    trust_count = 0

    for row in rows:

        foreign = number(
            row.get(
                foreign_key
            )
        )

        trust = number(
            row.get(
                trust_key
            )
        )

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
# 11. COMBINED INSTITUTIONAL
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

    foreign_net = (
        twse_foreign
        + tpex_foreign
        if (
            twse_foreign is not None
            and tpex_foreign is not None
        )
        else None
    )

    trust_net = (
        twse_trust
        + tpex_trust
        if (
            twse_trust is not None
            and tpex_trust is not None
        )
        else None
    )

    status = (
        "ok"
        if (
            foreign_net is not None
            and trust_net is not None
        )
        else "partial/unavailable"
    )

    return {
        "foreign_net": foreign_net,
        "trust_net": trust_net,

        "twse_foreign_net":
            twse_foreign,

        "twse_trust_net":
            twse_trust,

        "tpex_foreign_net":
            tpex_foreign,

        "tpex_trust_net":
            tpex_trust,

        "status": status,

        "sources": [
            twse_source,
            tpex_source,
        ],
    }


# ============================================================
# 12. CONDITION
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
        "name": name,
        "value": value,
        "pass": passed,
        "threshold": threshold,
        "unit": unit,
        "status": status,
    }


# ============================================================
# 13. SENTIMENT
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
        "level": level,
        "description": description,
        "score": score,
        "valid_conditions":
            len(valid),
        "total_conditions":
            len(conditions),
    }


# ============================================================
# 14. JSON CLEAN
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
        dict,
    ):

        return {
            key: clean_json(item)
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
# 15. ATOMIC WRITE
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

            file.write("\n")

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
# 16. VALIDATION
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

    if len(conditions) != 10:

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
        if isinstance(item, dict)
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


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    log("=" * 72)
    log("FETCH MARKET V2.1")
    log("=" * 72)

    now = datetime.now(
        TAIWAN_TZ
    )

    # --------------------------------------------------------
    # TAIEX
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

    index_history = [
        row
        for row in fetch_index_history()
        if row["date"]
        <= latest_date
    ]

    if (
        not index_history
        or index_history[-1]["date"]
        != latest_date
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

    index_metrics = (
        calculate_index_metrics(
            index_history
        )
    )

    # --------------------------------------------------------
    # STOCK PRICE HISTORY
    # --------------------------------------------------------

    universe = load_universe()

    histories = (
        load_price_histories(
            universe
        )
    )

    breadth = (
        calculate_market_breadth(
            histories,
            latest_date,
        )
    )

    # --------------------------------------------------------
    # INSTITUTIONAL
    # --------------------------------------------------------

    institutional = (
        fetch_institutional(
            latest_date
        )
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
            index_metrics["ma20"],
            (
                close
                > index_metrics["ma20"]
                if index_metrics["ma20"]
                is not None
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

                "atr14_pct":
                    index_metrics[
                        "atr14_pct"
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
                        "official-priority shards"
                    ),

                "institutional":
                    [
                        "TWSE BFI82U",
                        (
                            "TPEx "
                            "tpex_3insti_daily_trading"
                        ),
                    ],
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

    log(
        "市場條件："
        f"{market_sentiment['score']}/10"
        f" valid="
        f"{market_sentiment['valid_conditions']}"
    )

    log(
        "價格歷史覆蓋："
        f"{breadth['coverage']} 檔"
    )

    log(
        "市場風向："
        f"{market_sentiment['level']}"
    )

    log(
        "✓ market.json validation PASS"
    )

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )