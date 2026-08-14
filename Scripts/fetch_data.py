#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
台股 AI 選股・零股定投・動態風控
fetch_data.py V6
============================================================

正式版 V6

主要修正：
1. RSI 強制限制 0～100
2. 標準 Wilder RSI(14)
3. 防止 NaN / Infinity 汙染 JSON
4. 修正 MA5 殘留測試寫法
5. 修正 ETF 判斷
6. 完整股票＋ETF資料池
7. AI Top 25 僅為排名，不限制資料庫
8. MACD 12/26/9
9. KD 9/3/3
10. MA5 / MA20 / MA60
11. 成交量 / 5MA
12. DCA 四段式
13. 動態風控
14. JSON 最終輸出驗證

執行：

python Scripts/fetch_data.py

輸出：

Data/prices.json
============================================================
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("ERROR: requests 未安裝")
    sys.exit(1)


# ============================================================
# 基本設定
# ============================================================

VERSION = "V6"

BASE_DIR = Path(__file__).resolve().parent.parent

OUTPUT_DIR = BASE_DIR / "Data"

OUTPUT_FILE = OUTPUT_DIR / "prices.json"

REQUEST_TIMEOUT = 20

# AI 排名數量
AI_TOP_N = 25

# Yahoo Finance 歷史資料
PERIOD = "18mo"

# 請求間隔
REQUEST_DELAY = 0.08

# RSI
RSI_PERIOD = 14

# KD
KD_PERIOD = 9
KD_SMOOTH_K = 3
KD_SMOOTH_D = 3

# MACD
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Volume
VOLUME_MA_PERIOD = 5

# MA
MA_SHORT = 5
MA_MEDIUM = 20
MA_LONG = 60


# ============================================================
# API
# ============================================================

YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

TWSE_STOCK_API = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
)

TWSE_FUND_API = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"
)

TPEX_STOCK_API = (
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
)

TPEX_FUND_API = (
    "https://www.tpex.org.tw/openapi/v1/tpex_etf"
)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    )
}


# ============================================================
# 基本工具
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> Optional[float]:

    try:

        if value is None:
            return None

        if isinstance(value, str):

            value = (
                value
                .replace(",", "")
                .replace("%", "")
                .strip()
            )

            if value in (
                "",
                "-",
                "--",
                "N/A",
                "NA",
                "null",
                "None"
            ):
                return None

        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except Exception:

        return None


def clamp(
    value: Optional[float],
    minimum: float,
    maximum: float
) -> Optional[float]:

    if value is None:
        return None

    return max(
        minimum,
        min(
            maximum,
            value
        )
    )


def round_number(
    value: Optional[float],
    digits: int = 2
) -> Optional[float]:

    if value is None:
        return None

    value = safe_float(value)

    if value is None:
        return None

    return round(
        value,
        digits
    )


def safe_divide(
    a: Optional[float],
    b: Optional[float]
) -> Optional[float]:

    if a is None or b is None:
        return None

    if b == 0:
        return None

    return safe_float(
        a / b
    )


def clean_code(
    value: Any
) -> str:

    if value is None:
        return ""

    text = str(value).strip()

    if text.endswith(".0"):
        text = text[:-2]

    return text


def normalize_name(
    value: Any
) -> str:

    if value is None:
        return ""

    return str(value).strip()


def is_numeric_code(
    code: str
) -> bool:

    return bool(
        re.fullmatch(
            r"\d{4,6}",
            code
        )
    )


# ============================================================
# HTTP
# ============================================================

def get_json(
    url: str,
    timeout: int = REQUEST_TIMEOUT
) -> Any:

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# 欄位搜尋
# ============================================================

def find_value(
    row: Dict[str, Any],
    candidates: List[str]
) -> Any:

    # 精確
    for key in candidates:

        if key in row:
            return row[key]

    # 忽略大小寫
    lowered = {
        str(k).lower(): v
        for k, v in row.items()
    }

    for key in candidates:

        if key.lower() in lowered:

            return lowered[
                key.lower()
            ]

    # 模糊
    for row_key, value in row.items():

        row_key_text = str(
            row_key
        ).lower()

        for candidate in candidates:

            if candidate.lower() in row_key_text:

                return value

    return None


# ============================================================
# ETF 判斷
# ============================================================

def detect_etf(
    code: str,
    name: str,
    raw: Optional[Dict[str, Any]] = None
) -> bool:

    raw = raw or {}

    text = (
        name
        + " "
        + " ".join(
            str(v)
            for v in raw.values()
            if v is not None
        )
    ).upper()

    keywords = [
        "ETF",
        "指數股票型基金",
        "指數型基金",
        "槓桿型",
        "反向型"
    ]

    for keyword in keywords:

        if keyword.upper() in text:

            return True

    # 台股 ETF 多數為 00 開頭
    if code.startswith("00"):

        return True

    return False


# ============================================================
# 標的過濾
# ============================================================

def is_valid_security(
    code: str,
    name: str,
    is_etf: bool
) -> bool:

    if not code:
        return False

    if not is_numeric_code(code):
        return False

    # 權證
    if code.startswith("7"):
        return False

    bad_keywords = [
        "認購權證",
        "認售權證",
        "牛證",
        "熊證",
        "可展延",
        "附認股權",
        "公司債",
        "轉換公司債",
        "海外存託憑證"
    ]

    for keyword in bad_keywords:

        if keyword in name:

            return False

    return True


# ============================================================
# TWSE 股票
# ============================================================

def fetch_twse_stocks() -> List[Dict[str, Any]]:

    print("📡 取得 TWSE 上市標的...")

    result = []

    try:

        data = get_json(
            TWSE_STOCK_API
        )

        if not isinstance(data, list):

            return result

        for row in data:

            if not isinstance(row, dict):
                continue

            code = clean_code(
                find_value(
                    row,
                    [
                        "公司代號",
                        "證券代號",
                        "股票代號",
                        "代號",
                        "Code"
                    ]
                )
            )

            name = normalize_name(
                find_value(
                    row,
                    [
                        "公司簡稱",
                        "證券名稱",
                        "股票名稱",
                        "名稱",
                        "Name"
                    ]
                )
            )

            if not code or not name:
                continue

            etf = detect_etf(
                code,
                name,
                row
            )

            if not is_valid_security(
                code,
                name,
                etf
            ):
                continue

            result.append(
                {
                    "id": code,
                    "name": name,
                    "market": "TWSE",
                    "type": (
                        "ETF"
                        if etf
                        else "STOCK"
                    )
                }
            )

    except Exception as exc:

        print(
            f"⚠️ TWSE 上市清單取得失敗：{exc}"
        )

    print(
        f"   TWSE：{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE ETF
# ============================================================

def fetch_twse_funds() -> List[Dict[str, Any]]:

    print("📡 取得 TWSE ETF 清單...")

    result = []

    try:

        data = get_json(
            TWSE_FUND_API
        )

        if not isinstance(data, list):

            return result

        for row in data:

            if not isinstance(row, dict):
                continue

            code = clean_code(
                find_value(
                    row,
                    [
                        "基金代號",
                        "證券代號",
                        "股票代號",
                        "代號",
                        "Code"
                    ]
                )
            )

            name = normalize_name(
                find_value(
                    row,
                    [
                        "基金名稱",
                        "證券名稱",
                        "名稱",
                        "Name"
                    ]
                )
            )

            if not code or not name:
                continue

            if not is_numeric_code(code):
                continue

            result.append(
                {
                    "id": code,
                    "name": name,
                    "market": "TWSE",
                    "type": "ETF"
                }
            )

    except Exception as exc:

        print(
            f"⚠️ TWSE ETF 清單取得失敗：{exc}"
        )

    print(
        f"   TWSE ETF：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx 股票
# ============================================================

def fetch_tpex_stocks() -> List[Dict[str, Any]]:

    print("📡 取得 TPEx 上櫃標的...")

    result = []

    try:

        data = get_json(
            TPEX_STOCK_API
        )

        if not isinstance(data, list):

            return result

        for row in data:

            if not isinstance(row, dict):
                continue

            code = clean_code(
                find_value(
                    row,
                    [
                        "公司代號",
                        "證券代號",
                        "股票代號",
                        "代號",
                        "SecuritiesCompanyCode"
                    ]
                )
            )

            name = normalize_name(
                find_value(
                    row,
                    [
                        "公司簡稱",
                        "證券名稱",
                        "股票名稱",
                        "名稱",
                        "公司名稱"
                    ]
                )
            )

            if not code or not name:
                continue

            etf = detect_etf(
                code,
                name,
                row
            )

            if not is_valid_security(
                code,
                name,
                etf
            ):
                continue

            result.append(
                {
                    "id": code,
                    "name": name,
                    "market": "TPEx",
                    "type": (
                        "ETF"
                        if etf
                        else "STOCK"
                    )
                }
            )

    except Exception as exc:

        print(
            f"⚠️ TPEx 清單取得失敗：{exc}"
        )

    print(
        f"   TPEx：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx ETF
# ============================================================

def fetch_tpex_funds() -> List[Dict[str, Any]]:

    print("📡 取得 TPEx ETF 清單...")

    result = []

    try:

        data = get_json(
            TPEX_FUND_API
        )

        if not isinstance(data, list):

            return result

        for row in data:

            if not isinstance(row, dict):
                continue

            code = clean_code(
                find_value(
                    row,
                    [
                        "證券代號",
                        "基金代號",
                        "代號",
                        "Code"
                    ]
                )
            )

            name = normalize_name(
                find_value(
                    row,
                    [
                        "證券名稱",
                        "基金名稱",
                        "名稱",
                        "Name"
                    ]
                )
            )

            if not code or not name:
                continue

            if not is_numeric_code(code):
                continue

            result.append(
                {
                    "id": code,
                    "name": name,
                    "market": "TPEx",
                    "type": "ETF"
                }
            )

    except Exception as exc:

        print(
            f"⚠️ TPEx ETF 清單取得失敗：{exc}"
        )

    print(
        f"   TPEx ETF：{len(result)} 檔"
    )

    return result


# ============================================================
# Universe
# ============================================================

def build_universe() -> List[Dict[str, Any]]:

    all_items = {}

    sources = [
        fetch_twse_stocks(),
        fetch_twse_funds(),
        fetch_tpex_stocks(),
        fetch_tpex_funds()
    ]

    for source in sources:

        for item in source:

            code = item["id"]

            if code not in all_items:

                all_items[code] = item

            else:

                # ETF 優先
                if item["type"] == "ETF":

                    all_items[code]["type"] = "ETF"

                # 保留較完整名稱
                if len(
                    item["name"]
                ) > len(
                    all_items[code]["name"]
                ):

                    all_items[code][
                        "name"
                    ] = item["name"]

    result = list(
        all_items.values()
    )

    result.sort(
        key=lambda x: x["id"]
    )

    stock_count = sum(
        1
        for x in result
        if x["type"] == "STOCK"
    )

    etf_count = sum(
        1
        for x in result
        if x["type"] == "ETF"
    )

    print("")
    print(
        f"📊 完整標的池：{len(result)} 檔"
    )

    print(
        f"   個股：{stock_count}"
    )

    print(
        f"   ETF：{etf_count}"
    )

    return result


# ============================================================
# Yahoo Symbol
# ============================================================

def yahoo_symbol(
    code: str,
    market: str
) -> str:

    if market == "TPEx":

        return f"{code}.TWO"

    return f"{code}.TW"


# ============================================================
# Yahoo History
# ============================================================

def fetch_history(
    code: str,
    market: str
) -> Optional[Dict[str, List[Any]]]:

    symbol = yahoo_symbol(
        code,
        market
    )

    url = YAHOO_CHART_URL.format(
        symbol=quote(symbol)
    )

    params = {
        "range": PERIOD,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true"
    }

    try:

        response = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        chart = data.get(
            "chart",
            {}
        )

        results = chart.get(
            "result"
        )

        if not results:

            return None

        result = results[0]

        timestamps = (
            result.get("timestamp")
            or []
        )

        indicators = result.get(
            "indicators",
            {}
        )

        quote_data = (
            indicators
            .get(
                "quote",
                [{}]
            )[0]
        )

        closes = (
            quote_data.get(
                "close",
                []
            )
        )

        opens = (
            quote_data.get(
                "open",
                []
            )
        )

        highs = (
            quote_data.get(
                "high",
                []
            )
        )

        lows = (
            quote_data.get(
                "low",
                []
            )
        )

        volumes = (
            quote_data.get(
                "volume",
                []
            )
        )

        clean = {
            "timestamp": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": []
        }

        for i, timestamp in enumerate(
            timestamps
        ):

            if i >= len(closes):
                continue

            close = safe_float(
                closes[i]
            )

            if close is None:
                continue

            clean[
                "timestamp"
            ].append(
                timestamp
            )

            clean[
                "open"
            ].append(
                safe_float(
                    opens[i]
                )
                if i < len(opens)
                else None
            )

            clean[
                "high"
            ].append(
                safe_float(
                    highs[i]
                )
                if i < len(highs)
                else None
            )

            clean[
                "low"
            ].append(
                safe_float(
                    lows[i]
                )
                if i < len(lows)
                else None
            )

            clean[
                "close"
            ].append(
                close
            )

            clean[
                "volume"
            ].append(
                safe_float(
                    volumes[i]
                )
                if i < len(volumes)
                else 0
            )

        if len(
            clean["close"]
        ) < 60:

            return None

        return clean

    except Exception as exc:

        print(
            f"   ⚠️ {symbol} 行情失敗：{exc}"
        )

        return None


# ============================================================
# EMA
# ============================================================

def ema(
    values: List[float],
    period: int
) -> List[Optional[float]]:

    result = [
        None
    ] * len(values)

    if len(values) < period:

        return result

    first = sum(
        values[:period]
    ) / period

    result[
        period - 1
    ] = first

    multiplier = (
        2 /
        (period + 1)
    )

    previous = first

    for i in range(
        period,
        len(values)
    ):

        current = (
            (
                values[i] -
                previous
            )
            * multiplier
            + previous
        )

        result[i] = current

        previous = current

    return result


# ============================================================
# SMA
# ============================================================

def sma(
    values: List[float],
    period: int
) -> List[Optional[float]]:

    result = [
        None
    ] * len(values)

    if len(values) < period:

        return result

    window_sum = sum(
        values[:period]
    )

    result[
        period - 1
    ] = (
        window_sum /
        period
    )

    for i in range(
        period,
        len(values)
    ):

        window_sum += values[i]

        window_sum -= values[
            i - period
        ]

        result[i] = (
            window_sum /
            period
        )

    return result


# ============================================================
# RSI Wilder
# ============================================================

def calculate_rsi(
    closes: List[float],
    period: int = RSI_PERIOD
) -> List[Optional[float]]:

    """
    標準 Wilder RSI

    輸出絕對限制：
    0 <= RSI <= 100

    防止：
    RSI = 560
    RSI = -100
    RSI = NaN
    RSI = Infinity
    """

    result = [
        None
    ] * len(closes)

    if len(closes) <= period:

        return result

    gains = []
    losses = []

    for i in range(
        1,
        len(closes)
    ):

        delta = (
            closes[i] -
            closes[i - 1]
        )

        if delta > 0:

            gains.append(delta)

            losses.append(0.0)

        else:

            gains.append(0.0)

            losses.append(
                abs(delta)
            )

    avg_gain = (
        sum(
            gains[:period]
        ) /
        period
    )

    avg_loss = (
        sum(
            losses[:period]
        ) /
        period
    )

    def make_rsi(
        gain: float,
        loss: float
    ) -> float:

        gain = max(
            0.0,
            gain
        )

        loss = max(
            0.0,
            loss
        )

        if loss == 0:

            if gain == 0:

                return 50.0

            return 100.0

        rs = gain / loss

        value = (
            100.0 -
            (
                100.0 /
                (
                    1.0 + rs
                )
            )
        )

        # 最終防線
        value = max(
            0.0,
            min(
                100.0,
                value
            )
        )

        return value

    result[
        period
    ] = make_rsi(
        avg_gain,
        avg_loss
    )

    for i in range(
        period + 1,
        len(closes)
    ):

        gain = gains[
            i - 1
        ]

        loss = losses[
            i - 1
        ]

        avg_gain = (
            (
                avg_gain *
                (period - 1)
            )
            + gain
        ) / period

        avg_loss = (
            (
                avg_loss *
                (period - 1)
            )
            + loss
        ) / period

        result[i] = make_rsi(
            avg_gain,
            avg_loss
        )

    return result


# ============================================================
# KD
# ============================================================

def calculate_kd(
    highs: List[Optional[float]],
    lows: List[Optional[float]],
    closes: List[float],
    period: int = KD_PERIOD
) -> Tuple[
    List[Optional[float]],
    List[Optional[float]]
]:

    k_values = [
        None
    ] * len(closes)

    d_values = [
        None
    ] * len(closes)

    k = 50.0

    d = 50.0

    for i in range(
        period - 1,
        len(closes)
    ):

        high_window = [
            x
            for x in highs[
                i - period + 1:
                i + 1
            ]
            if x is not None
        ]

        low_window = [
            x
            for x in lows[
                i - period + 1:
                i + 1
            ]
            if x is not None
        ]

        if not high_window:
            continue

        if not low_window:
            continue

        highest = max(
            high_window
        )

        lowest = min(
            low_window
        )

        if highest == lowest:

            rsv = 50.0

        else:

            rsv = (
                (
                    closes[i] -
                    lowest
                )
                /
                (
                    highest -
                    lowest
                )
            ) * 100.0

        rsv = max(
            0.0,
            min(
                100.0,
                rsv
            )
        )

        k = (
            (
                2.0 / 3.0
            )
            * k
            +
            (
                1.0 / 3.0
            )
            * rsv
        )

        d = (
            (
                2.0 / 3.0
            )
            * d
            +
            (
                1.0 / 3.0
            )
            * k
        )

        k_values[i] = max(
            0.0,
            min(
                100.0,
                k
            )
        )

        d_values[i] = max(
            0.0,
            min(
                100.0,
                d
            )
        )

    return (
        k_values,
        d_values
    )


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    closes: List[float]
) -> Tuple[
    List[Optional[float]],
    List[Optional[float]],
    List[Optional[float]]
]:

    ema12 = ema(
        closes,
        MACD_FAST
    )

    ema26 = ema(
        closes,
        MACD_SLOW
    )

    macd_line = [
        None
    ] * len(closes)

    for i in range(
        len(closes)
    ):

        if (
            ema12[i] is not None
            and
            ema26[i] is not None
        ):

            macd_line[i] = (
                ema12[i] -
                ema26[i]
            )

    valid_macd = [
        x
        for x in macd_line
        if x is not None
    ]

    signal_valid = ema(
        valid_macd,
        MACD_SIGNAL
    )

    signal_line = [
        None
    ] * len(closes)

    valid_index = 0

    for i in range(
        len(closes)
    ):

        if macd_line[i] is None:
            continue

        if valid_index < len(
            signal_valid
        ):

            signal_line[i] = (
                signal_valid[
                    valid_index
                ]
            )

        valid_index += 1

    histogram = [
        None
    ] * len(closes)

    for i in range(
        len(closes)
    ):

        if (
            macd_line[i] is not None
            and
            signal_line[i] is not None
        ):

            histogram[i] = (
                macd_line[i]
                -
                signal_line[i]
            )

    return (
        macd_line,
        signal_line,
        histogram
    )


# ============================================================
# Technical Indicators
# ============================================================

def calculate_indicators(
    history: Dict[str, List[Any]]
) -> Dict[str, Any]:

    closes = history["close"]

    highs = history["high"]

    lows = history["low"]

    volumes = history["volume"]

    ma5 = sma(
        closes,
        MA_SHORT
    )

    ma20 = sma(
        closes,
        MA_MEDIUM
    )

    ma60 = sma(
        closes,
        MA_LONG
    )

    rsi = calculate_rsi(
        closes,
        RSI_PERIOD
    )

    k_values, d_values = calculate_kd(
        highs,
        lows,
        closes
    )

    macd_line, signal_line, macd_hist = (
        calculate_macd(
            closes
        )
    )

    clean_volumes = [
        v if v is not None else 0
        for v in volumes
    ]

    volume_ma5 = sma(
        clean_volumes,
        VOLUME_MA_PERIOD
    )

    i = len(
        closes
    ) - 1

    previous_i = i - 1

    close = closes[i]

    previous_close = (
        closes[previous_i]
        if previous_i >= 0
        else None
    )

    current_ma5 = ma5[i]

    current_ma20 = ma20[i]

    current_ma60 = ma60[i]

    previous_ma20 = (
        ma20[previous_i]
        if previous_i >= 0
        else None
    )

    current_rsi = rsi[i]

    current_k = k_values[i]

    current_d = d_values[i]

    previous_k = (
        k_values[previous_i]
        if previous_i >= 0
        else None
    )

    previous_d = (
        d_values[previous_i]
        if previous_i >= 0
        else None
    )

    current_macd = macd_line[i]

    current_signal = signal_line[i]

    current_hist = macd_hist[i]

    previous_macd = (
        macd_line[previous_i]
        if previous_i >= 0
        else None
    )

    previous_signal = (
        signal_line[previous_i]
        if previous_i >= 0
        else None
    )

    current_volume = (
        volumes[i]
        if volumes[i] is not None
        else 0
    )

    current_volume_ma5 = (
        volume_ma5[i]
        if i < len(volume_ma5)
        else None
    )

    volume_ratio = safe_divide(
        current_volume,
        current_volume_ma5
    )

    change = (
        close -
        previous_close
        if previous_close is not None
        else None
    )

    change_percent = (
        safe_divide(
            change,
            previous_close
        ) * 100.0
        if (
            change is not None
            and
            previous_close is not None
            and
            previous_close != 0
        )
        else None
    )

    # --------------------------------------------------------
    # MACD 黃金交叉
    # --------------------------------------------------------

    macd_golden_cross = (
        previous_macd is not None
        and
        previous_signal is not None
        and
        current_macd is not None
        and
        current_signal is not None
        and
        previous_macd <= previous_signal
        and
        current_macd > current_signal
    )

    # --------------------------------------------------------
    # KD 黃金交叉
    # --------------------------------------------------------

    kd_golden_cross = (
        previous_k is not None
        and
        previous_d is not None
        and
        current_k is not None
        and
        current_d is not None
        and
        previous_k <= previous_d
        and
        current_k > current_d
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    current_rsi = clamp(
        current_rsi,
        0.0,
        100.0
    )

    rsi_above_50 = (
        current_rsi is not None
        and
        current_rsi > 50.0
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    volume_over_1_5x = (
        volume_ratio is not None
        and
        volume_ratio >= 1.5
    )

    # --------------------------------------------------------
    # MA20
    # --------------------------------------------------------

    above_ma20 = (
        current_ma20 is not None
        and
        close > current_ma20
    )

    ma20_up = (
        current_ma20 is not None
        and
        previous_ma20 is not None
        and
        current_ma20 > previous_ma20
    )

    # --------------------------------------------------------
    # 核心訊號
    # --------------------------------------------------------

    short_term_core = all([
        macd_golden_cross,
        kd_golden_cross,
        rsi_above_50,
        volume_over_1_5x,
        above_ma20,
        ma20_up
    ])

    return {
        "ma5": current_ma5,
        "ma20": current_ma20,
        "ma60": current_ma60,

        "rsi": current_rsi,

        "k": current_k,
        "d": current_d,

        "macd": current_macd,
        "macd_signal": current_signal,
        "macd_hist": current_hist,

        "volume_ratio": volume_ratio,

        "change": change,
        "change_percent": change_percent,

        "macd_golden_cross":
            macd_golden_cross,

        "kd_golden_cross":
            kd_golden_cross,

        "rsi_above_50":
            rsi_above_50,

        "volume_over_1_5x":
            volume_over_1_5x,

        "above_ma20":
            above_ma20,

        "ma20_up":
            ma20_up,

        "short_term_core":
            short_term_core
    }


# ============================================================
# AI Score
# ============================================================

def calculate_score(
    indicators: Dict[str, Any]
) -> int:

    score = 0

    # MACD
    if indicators[
        "macd_golden_cross"
    ]:

        score += 25

    # KD
    if indicators[
        "kd_golden_cross"
    ]:

        score += 20

    # RSI
    rsi = indicators[
        "rsi"
    ]

    if rsi is not None:

        rsi = clamp(
            rsi,
            0.0,
            100.0
        )

        if rsi > 70:

            # 避免超買反而被過度獎勵
            score += 10

        elif rsi > 60:

            score += 18

        elif rsi > 50:

            score += 15

    # Volume
    volume_ratio = indicators[
        "volume_ratio"
    ]

    if volume_ratio is not None:

        if volume_ratio >= 2.0:

            score += 20

        elif volume_ratio >= 1.5:

            score += 15

        elif volume_ratio >= 1.2:

            score += 8

    # MA20
    if indicators[
        "above_ma20"
    ]:

        score += 10

    if indicators[
        "ma20_up"
    ]:

        score += 10

    return max(
        0,
        min(
            100,
            int(score)
        )
    )


# ============================================================
# Signal
# ============================================================

def get_signal(
    score: int,
    core: bool
) -> str:

    if core:

        return "核心買進"

    if score >= 75:

        return "強勢"

    if score >= 60:

        return "偏多"

    if score >= 40:

        return "觀察"

    return "弱勢"


# ============================================================
# DCA
# ============================================================

def calculate_dca(
    price: Optional[float],
    ma20: Optional[float]
) -> Dict[str, Any]:

    if price is None:

        return {
            "buy_1": None,
            "buy_2": None,
            "buy_3": None,
            "buy_4": None,
            "action": "無價格資料"
        }

    base = (
        ma20
        if ma20 is not None
        else price
    )

    buy_1 = base

    buy_2 = base * 0.97

    buy_3 = base * 0.94

    buy_4 = base * 0.90

    if price <= buy_4:

        action = "第四批區域"

    elif price <= buy_3:

        action = "第三批區域"

    elif price <= buy_2:

        action = "第二批區域"

    elif price <= buy_1:

        action = "第一批區域"

    else:

        action = "等待回檔"

    return {
        "buy_1": round_number(
            buy_1
        ),
        "buy_2": round_number(
            buy_2
        ),
        "buy_3": round_number(
            buy_3
        ),
        "buy_4": round_number(
            buy_4
        ),
        "action": action
    }


# ============================================================
# Risk Control
# ============================================================

def calculate_risk(
    price: Optional[float],
    ma20: Optional[float],
    score: int
) -> Dict[str, Any]:

    if price is None:

        return {
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "risk_level": "未知"
        }

    # 基本停損
    stop_loss = price * 0.93

    # MA20 技術停損
    if ma20 is not None:

        technical_stop = (
            ma20 * 0.96
        )

        stop_loss = min(
            stop_loss,
            technical_stop
        )

    take_profit_1 = (
        price * 1.08
    )

    take_profit_2 = (
        price * 1.15
    )

    if score >= 75:

        risk_level = "低～中"

    elif score >= 50:

        risk_level = "中"

    else:

        risk_level = "中～高"

    return {
        "stop_loss":
            round_number(
                stop_loss
            ),

        "take_profit_1":
            round_number(
                take_profit_1
            ),

        "take_profit_2":
            round_number(
                take_profit_2
            ),

        "risk_level":
            risk_level
    }


# ============================================================
# JSON 數值清洗
# ============================================================

def sanitize_number(
    value: Any,
    digits: int = 2
) -> Optional[float]:

    number = safe_float(
        value
    )

    if number is None:
        return None

    return round(
        number,
        digits
    )


def sanitize_rsi(
    value: Any
) -> Optional[float]:

    number = safe_float(
        value
    )

    if number is None:
        return None

    # RSI 絕對限制 0～100
    number = max(
        0.0,
        min(
            100.0,
            number
        )
    )

    return round(
        number,
        2
    )


# ============================================================
# 單一標的分析
# ============================================================

def analyze_security(
    item: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    code = item["id"]

    name = item["name"]

    market = item["market"]

    security_type = item["type"]

    history = fetch_history(
        code,
        market
    )

    if history is None:

        return None

    closes = history["close"]

    if len(closes) < 60:

        return None

    indicators = calculate_indicators(
        history
    )

    score = calculate_score(
        indicators
    )

    core = bool(
        indicators[
            "short_term_core"
        ]
    )

    signal = get_signal(
        score,
        core
    )

    price = safe_float(
        closes[-1]
    )

    if price is None:

        return None

    previous_close = (
        safe_float(
            closes[-2]
        )
        if len(closes) >= 2
        else None
    )

    dca = calculate_dca(
        price,
        indicators["ma20"]
    )

    risk = calculate_risk(
        price,
        indicators["ma20"],
        score
    )

    rsi_value = sanitize_rsi(
        indicators["rsi"]
    )

    # ========================================================
    # 最終資料
    # ========================================================

    result = {

        "id": code,

        "symbol": code,

        "name": name,

        "market": market,

        "type": security_type,

        "price": {

            "close":
                sanitize_number(
                    price
                ),

            "previous_close":
                sanitize_number(
                    previous_close
                ),

            "change":
                sanitize_number(
                    indicators[
                        "change"
                    ]
                ),

            "change_percent":
                sanitize_number(
                    indicators[
                        "change_percent"
                    ]
                )
        },

        "technical": {

            "ma5":
                sanitize_number(
                    indicators["ma5"]
                ),

            "ma20":
                sanitize_number(
                    indicators["ma20"]
                ),

            "ma60":
                sanitize_number(
                    indicators["ma60"]
                ),

            # RSI 強制 0～100
            "rsi":
                rsi_value,

            "k":
                sanitize_number(
                    indicators["k"]
                ),

            "d":
                sanitize_number(
                    indicators["d"]
                ),

            "macd":
                sanitize_number(
                    indicators["macd"]
                ),

            "macd_signal":
                sanitize_number(
                    indicators[
                        "macd_signal"
                    ]
                ),

            "macd_hist":
                sanitize_number(
                    indicators[
                        "macd_hist"
                    ]
                ),

            "volume_ratio":
                sanitize_number(
                    indicators[
                        "volume_ratio"
                    ],
                    2
                )
        },

        "conditions": {

            "macd_golden_cross":
                bool(
                    indicators[
                        "macd_golden_cross"
                    ]
                ),

            "kd_golden_cross":
                bool(
                    indicators[
                        "kd_golden_cross"
                    ]
                ),

            "rsi_above_50":
                bool(
                    indicators[
                        "rsi_above_50"
                    ]
                ),

            "volume_over_1_5x":
                bool(
                    indicators[
                        "volume_over_1_5x"
                    ]
                ),

            "above_ma20":
                bool(
                    indicators[
                        "above_ma20"
                    ]
                ),

            "ma20_up":
                bool(
                    indicators[
                        "ma20_up"
                    ]
                ),

            "short_term_core":
                core
        },

        "short_term": {

            "score":
                score,

            "signal":
                signal
        },

        "dca":
            dca,

        "risk_control":
            risk,

        "data": {

            "history_days":
                len(closes),

            "source":
                "Yahoo Finance",

            "updated_at":
                now_iso()
        }
    }

    return result


# ============================================================
# Ranking
# ============================================================

def build_rankings(
    stocks: List[Dict[str, Any]]
) -> Dict[str, List[str]]:

    valid = [
        x
        for x in stocks
        if x.get(
            "price",
            {}
        ).get(
            "close"
        ) is not None
    ]

    # ========================================================
    # AI Top 25
    # ========================================================

    short_term = sorted(
        valid,
        key=lambda x: (
            x.get(
                "short_term",
                {}
            ).get(
                "score",
                0
            ),

            x.get(
                "technical",
                {}
            ).get(
                "volume_ratio",
                0
            ) or 0
        ),
        reverse=True
    )

    short_term_ids = [
        x["id"]
        for x in short_term[
            :AI_TOP_N
        ]
    ]

    # ========================================================
    # Core
    # ========================================================

    core = [
        x
        for x in valid
        if x.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )
    ]

    core.sort(
        key=lambda x:
            x.get(
                "short_term",
                {}
            ).get(
                "score",
                0
            ),
        reverse=True
    )

    core_ids = [
        x["id"]
        for x in core
    ]

    # ========================================================
    # DCA
    # ========================================================

    dca = sorted(
        valid,
        key=lambda x: (
            x.get(
                "short_term",
                {}
            ).get(
                "score",
                0
            ),

            x.get(
                "conditions",
                {}
            ).get(
                "above_ma20",
                False
            )
        ),
        reverse=True
    )

    dca_ids = [
        x["id"]
        for x in dca[
            :AI_TOP_N
        ]
    ]

    return {

        "short_term":
            short_term_ids,

        "core":
            core_ids,

        "dca":
            dca_ids
    }


# ============================================================
# Statistics
# ============================================================

def build_statistics(
    stocks: List[Dict[str, Any]]
) -> Dict[str, Any]:

    total = len(
        stocks
    )

    stocks_count = sum(
        1
        for x in stocks
        if x.get(
            "type"
        ) == "STOCK"
    )

    etf_count = sum(
        1
        for x in stocks
        if x.get(
            "type"
        ) == "ETF"
    )

    core_count = sum(
        1
        for x in stocks
        if x.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )
    )

    macd_count = sum(
        1
        for x in stocks
        if x.get(
            "conditions",
            {}
        ).get(
            "macd_golden_cross",
            False
        )
    )

    kd_count = sum(
        1
        for x in stocks
        if x.get(
            "conditions",
            {}
        ).get(
            "kd_golden_cross",
            False
        )
    )

    rsi_count = sum(
        1
        for x in stocks
        if x.get(
            "conditions",
            {}
        ).get(
            "rsi_above_50",
            False
        )
    )

    volume_count = sum(
        1
        for x in stocks
        if x.get(
            "conditions",
            {}
        ).get(
            "volume_over_1_5x",
            False
        )
    )

    ma20_count = sum(
        1
        for x in stocks
        if x.get(
            "conditions",
            {}
        ).get(
            "above_ma20",
            False
        )
    )

    return {

        "total_stocks":
            total,

        "stocks":
            stocks_count,

        "etfs":
            etf_count,

        "core_stocks":
            core_count,

        "macd_golden":
            macd_count,

        "kd_golden":
            kd_count,

        "rsi_above_50":
            rsi_count,

        "volume_over_1_5x":
            volume_count,

        "above_ma20":
            ma20_count,

        "ai_top_n":
            AI_TOP_N
    }


# ============================================================
# 最終 JSON 驗證
# ============================================================

def validate_output(
    stocks: List[Dict[str, Any]]
) -> None:

    print("")
    print("🔍 執行最終資料驗證...")

    errors = []

    for stock in stocks:

        code = stock.get(
            "id",
            "UNKNOWN"
        )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        rsi = (
            stock
            .get(
                "technical",
                {}
            )
            .get(
                "rsi"
            )
        )

        if rsi is not None:

            if not (
                0.0 <= rsi <= 100.0
            ):

                errors.append(
                    f"{code}: RSI={rsi}"
                )

        # ----------------------------------------------------
        # Price
        # ----------------------------------------------------

        price = (
            stock
            .get(
                "price",
                {}
            )
            .get(
                "close"
            )
        )

        if price is not None:

            if price <= 0:

                errors.append(
                    f"{code}: price={price}"
                )

        # ----------------------------------------------------
        # K / D
        # ----------------------------------------------------

        for key in [
            "k",
            "d"
        ]:

            value = (
                stock
                .get(
                    "technical",
                    {}
                )
                .get(
                    key
                )
            )

            if value is not None:

                if not (
                    0 <= value <= 100
                ):

                    errors.append(
                        f"{code}: "
                        f"{key}={value}"
                    )

    if errors:

        print(
            "❌ 資料驗證失敗："
        )

        for error in errors[:20]:

            print(
                f"   {error}"
            )

        raise ValueError(
            "JSON 技術指標驗證失敗"
        )

    print(
        "✅ RSI / KD / 價格資料驗證通過"
    )


# ============================================================
# 主程式
# ============================================================

def main():

    print("")
    print("=" * 70)

    print(
        f"🚀 台股 AI 選股系統 {VERSION}"
    )

    print(
        "完整股票＋ETF＋AI Top 25＋DCA＋動態風控"
    )

    print("=" * 70)

    print("")

    start_time = time.time()

    # ========================================================
    # Universe
    # ========================================================

    universe = build_universe()

    if not universe:

        print(
            "❌ 無法取得任何股票清單"
        )

        sys.exit(1)

    # ========================================================
    # Analyze
    # ========================================================

    analyzed = []

    total = len(
        universe
    )

    print("")

    print(
        f"🔎 開始分析 {total} 檔標的"
    )

    print(
        "   ⚠️ 25 檔只代表 AI Top 25"
    )

    print(
        "   ⚠️ 不代表資料庫只有 25 檔"
    )

    print("")

    success = 0

    failed = 0

    for index, item in enumerate(
        universe,
        start=1
    ):

        code = item["id"]

        name = item["name"]

        print(
            f"[{index}/{total}] "
            f"{code} {name}"
        )

        result = analyze_security(
            item
        )

        if result:

            analyzed.append(
                result
            )

            success += 1

        else:

            failed += 1

        time.sleep(
            REQUEST_DELAY
        )

    # ========================================================
    # Empty
    # ========================================================

    if not analyzed:

        print("")
        print(
            "❌ 沒有任何有效行情資料"
        )

        sys.exit(1)

    # ========================================================
    # Validate
    # ========================================================

    validate_output(
        analyzed
    )

    # ========================================================
    # Rankings
    # ========================================================

    rankings = build_rankings(
        analyzed
    )

    statistics_data = (
        build_statistics(
            analyzed
        )
    )

    # ========================================================
    # Output
    # ========================================================

    output = {

        "version":
            VERSION,

        "generated_at":
            now_iso(),

        "source": {

            "price":
                "Yahoo Finance",

            "twse":
                TWSE_STOCK_API,

            "tpex":
                TPEX_STOCK_API
        },

        "config": {

            "ai_top_n":
                AI_TOP_N,

            "universe_limit":
                None,

            "period":
                PERIOD,

            "rsi_period":
                RSI_PERIOD,

            "rsi_range":
                "0-100",

            "kd_period":
                KD_PERIOD,

            "kd_smoothing":
                "3/3",

            "macd":
                "12/26/9",

            "volume_ma":
                VOLUME_MA_PERIOD,

            "volume_rule":
                "5MA × 1.5",

            "ma_rule":
                "MA5 / MA20 / MA60"
        },

        "statistics":
            statistics_data,

        "rankings":
            rankings,

        "stocks":
            analyzed
    }

    # ========================================================
    # Output Directory
    # ========================================================

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = (
        OUTPUT_FILE.with_suffix(
            ".tmp"
        )
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        )

    os.replace(
        temp_file,
        OUTPUT_FILE
    )

    # ========================================================
    # Done
    # ========================================================

    elapsed = (
        time.time()
        -
        start_time
    )

    print("")
    print("=" * 70)

    print(
        f"✅ {VERSION} 資料更新完成"
    )

    print("=" * 70)

    print(
        f"📊 完整資料池："
        f"{statistics_data['total_stocks']} 檔"
    )

    print(
        f"📈 個股："
        f"{statistics_data['stocks']} 檔"
    )

    print(
        f"💰 ETF："
        f"{statistics_data['etfs']} 檔"
    )

    print(
        f"🚀 核心訊號："
        f"{statistics_data['core_stocks']} 檔"
    )

    print(
        f"📡 MACD 黃金交叉："
        f"{statistics_data['macd_golden']} 檔"
    )

    print(
        f"📈 KD 黃金交叉："
        f"{statistics_data['kd_golden']} 檔"
    )

    print(
        f"📊 RSI > 50："
        f"{statistics_data['rsi_above_50']} 檔"
    )

    print(
        f"🔊 成交量 > 1.5×："
        f"{statistics_data['volume_over_1_5x']} 檔"
    )

    print(
        f"📈 MA20 上方："
        f"{statistics_data['above_ma20']} 檔"
    )

    print(
        f"🏆 AI Top {AI_TOP_N}："
        f"{len(rankings['short_term'])} 檔"
    )

    print(
        f"✅ 成功分析：{success}"
    )

    print(
        f"⚠️ 無有效資料：{failed}"
    )

    print(
        f"⏱️ 執行時間：{elapsed:.1f} 秒"
    )

    print(
        f"📁 輸出：{OUTPUT_FILE}"
    )

    print("=" * 70)
    print("")


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    main()
