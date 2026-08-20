#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/analyze_stocks.py
正式版 V3.0

============================================================
分析層責任
============================================================

本程式只負責「分析」。

讀取：

    Data/universe.json
    Data/prices/manifest.json
    Data/prices/*.json
    Data/chip.json

輸出：

    Data/analysis.json

============================================================
重要架構邊界
============================================================

本程式：

    不抓 CMoney API
    不執行 fetch_chip.py
    不執行 fetch_data.py
    不修改 chip.json
    不修改 prices
    不修改 universe.json

只使用既有資料進行分析。

============================================================
短期選股核心
============================================================

核心條件：

1. MACD 黃金交叉
2. KD 黃金交叉
3. RSI > 50
4. 今日成交量 ÷ 前5個交易日平均成交量 >= 1.5
5. 股價站上20日線
6. 20日線向上

其中：

第4項嚴格定義為：

    今日成交量
    ------------------------- >= 1.5
    前5個「交易日」平均成交量

注意：

前5日平均不包含今日。

============================================================
短期籌碼
============================================================

保留：

    主力1D
    主力5D
    主力10D
    主力20D

10D 不可移除。

10D 會實際參與籌碼方向判斷。

============================================================
零股定投核心
============================================================

1. MA20 相對位置
2. 60日高低位置
3. 20MA 乖離
4. 近期漲跌幅
5. 成交量狀態
6. 主力籌碼
7. 高檔再平衡
8. 極端負乖離
9. 最大虧損警戒

============================================================
"""

from __future__ import annotations

import json
import math
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 基本設定
# ============================================================

VERSION = "V3.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

PRICES_DIR = DATA_DIR / "prices"
PRICES_MANIFEST = PRICES_DIR / "manifest.json"

CHIP_FILE = DATA_DIR / "chip.json"

OUTPUT_FILE = DATA_DIR / "analysis.json"


# ============================================================
# 短期選股設定
# ============================================================

SHORT_TERM_CONFIG = {

    # RSI
    "rsi_period": 14,
    "rsi_min": 50.0,

    # 成交量
    #
    # 今日成交量 / 前5交易日平均成交量 >= 1.5
    #
    "volume_lookback": 5,
    "volume_multiplier": 1.5,

    # MA20
    "ma20_period": 20,

    # KD
    "kd_period": 9,

    # MACD
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    # 最少歷史
    "minimum_history": 60,
}


# ============================================================
# 零股定投設定
# ============================================================

DCA_CONFIG = {

    # --------------------------------------------------------
    # MA20 相對位置
    # --------------------------------------------------------

    "ma20_aggressive": 0.97,
    "ma20_normal": 1.02,
    "ma20_pause": 1.05,

    # --------------------------------------------------------
    # 60日低點
    # --------------------------------------------------------

    "low60_aggressive": 1.03,

    # --------------------------------------------------------
    # 成交量
    # --------------------------------------------------------

    "volume_aggressive": 1.5,
    "volume_normal": 1.0,
    "volume_observe": 0.5,

    # --------------------------------------------------------
    # 最大虧損警戒
    # --------------------------------------------------------

    "max_loss_pct": 20.0,

    # --------------------------------------------------------
    # 60日高檔再平衡
    # --------------------------------------------------------

    "rebalance_warn": 30.0,
    "rebalance_high": 50.0,

    # --------------------------------------------------------
    # 極端負乖離
    # --------------------------------------------------------

    "extreme_bias": -20.0,
}


# ============================================================
# Log
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:

    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# JSON
# ============================================================

def load_json(path: Path) -> Any:

    if not path.exists():

        raise RuntimeError(
            f"找不到檔案：{path}"
        )

    with path.open(
        "r",
        encoding="utf-8-sig"
    ) as f:

        return json.load(f)


# ============================================================
# Number
# ============================================================

def safe_float(value: Any) -> Optional[float]:

    if value is None:
        return None

    try:

        value = float(value)

        if not math.isfinite(value):
            return None

        return value

    except Exception:

        return None


# ============================================================
# Date
# ============================================================

def parse_date(value: Any) -> Optional[datetime]:

    if value is None:
        return None

    text = str(value).strip()

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
    ):

        try:

            return datetime.strptime(
                text,
                fmt
            )

        except Exception:
            pass

    return None


# ============================================================
# Universe
# ============================================================

def load_universe() -> Dict[str, Dict[str, Any]]:

    data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "universe.json 格式錯誤"
        )

    items = data.get(
        "items",
        []
    )

    if not isinstance(
        items,
        list
    ):

        raise RuntimeError(
            "universe.json items 格式錯誤"
        )

    stocks = {}

    for item in items:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = (
            item.get("code")
            or item.get("symbol")
            or item.get("ticker")
        )

        if symbol is None:
            continue

        symbol = str(
            symbol
        ).strip().upper()

        symbol = (
            symbol
            .replace(".TW", "")
            .replace(".TWO", "")
        )

        if not symbol:
            continue

        stocks[symbol] = {

            "symbol": symbol,

            "name": str(
                item.get(
                    "name",
                    ""
                )
            ).strip(),

            "market": str(
                item.get(
                    "market",
                    ""
                )
            ).strip(),
        }

    if not stocks:

        raise RuntimeError(
            "Universe 沒有有效股票"
        )

    log(
        f"Universe：{len(stocks)} 檔"
    )

    return stocks


# ============================================================
# Price Manifest
# ============================================================

def load_price_manifest() -> Dict[str, Any]:

    data = load_json(
        PRICES_MANIFEST
    )

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "prices/manifest.json 格式錯誤"
        )

    return data


# ============================================================
# Price File Discovery
# ============================================================

def find_price_file(
    symbol: str,
    manifest: Dict[str, Any]
) -> Optional[Path]:

    candidates: List[str] = []

    stocks = manifest.get(
        "stocks"
    )

    if isinstance(
        stocks,
        dict
    ):

        item = stocks.get(
            symbol
        )

        if isinstance(
            item,
            str
        ):

            candidates.append(
                item
            )

        elif isinstance(
            item,
            dict
        ):

            for key in (
                "file",
                "path",
                "filename",
            ):

                value = item.get(
                    key
                )

                if value:
                    candidates.append(
                        str(value)
                    )

    candidates.extend(
        [
            f"{symbol}.json",
            f"{symbol}.TW.json",
            f"{symbol}.TWO.json",
        ]
    )

    checked = set()

    for candidate in candidates:

        candidate = str(
            candidate
        ).replace(
            "\\",
            "/"
        )

        if candidate in checked:
            continue

        checked.add(
            candidate
        )

        path = PRICES_DIR / candidate

        if path.exists():
            return path

        path = DATA_DIR / candidate

        if path.exists():
            return path

    # --------------------------------------------------------
    # 最後掃描
    # --------------------------------------------------------

    if PRICES_DIR.exists():

        for path in PRICES_DIR.glob(
            "*.json"
        ):

            if path.name == "manifest.json":
                continue

            stem = path.stem.upper()

            if stem in {
                symbol,
                symbol + ".TW",
                symbol + ".TWO",
            }:

                return path

    return None


# ============================================================
# Price Parser
# ============================================================

def parse_price_history(
    data: Any
) -> List[Dict[str, Any]]:

    rows: List[Dict[str, Any]] = []

    # --------------------------------------------------------
    # List
    # --------------------------------------------------------

    if isinstance(
        data,
        list
    ):

        raw_rows = data

    # --------------------------------------------------------
    # Dict
    # --------------------------------------------------------

    elif isinstance(
        data,
        dict
    ):

        raw_rows = (
            data.get("prices")
            or data.get("data")
            or data.get("history")
            or data.get("rows")
            or []
        )

        # date -> record
        if isinstance(
            raw_rows,
            dict
        ):

            converted = []

            for date, item in raw_rows.items():

                if isinstance(
                    item,
                    dict
                ):

                    row = dict(
                        item
                    )

                    row.setdefault(
                        "date",
                        date
                    )

                    converted.append(
                        row
                    )

                else:

                    converted.append(
                        {
                            "date": date,
                            "close": item,
                        }
                    )

            raw_rows = converted

    else:

        raw_rows = []

    # --------------------------------------------------------
    # Parse
    # --------------------------------------------------------

    for row in raw_rows:

        if not isinstance(
            row,
            dict
        ):
            continue

        date = (
            row.get("date")
            or row.get("Date")
            or row.get("trade_date")
            or row.get("TradeDate")
        )

        dt = parse_date(
            date
        )

        if dt is None:
            continue

        close = (
            row.get("close")
            if row.get("close") is not None
            else row.get("Close")
        )

        if close is None:
            close = row.get(
                "price"
            )

        volume = (
            row.get("volume")
            if row.get("volume") is not None
            else row.get("Volume")
        )

        high = (
            row.get("high")
            if row.get("high") is not None
            else row.get("High")
        )

        low = (
            row.get("low")
            if row.get("low") is not None
            else row.get("Low")
        )

        close = safe_float(
            close
        )

        volume = safe_float(
            volume
        )

        high = safe_float(
            high
        )

        low = safe_float(
            low
        )

        if close is None:
            continue

        rows.append(
            {
                "date":
                    dt.strftime(
                        "%Y-%m-%d"
                    ),

                "close":
                    close,

                "volume":
                    volume,

                "high":
                    high,

                "low":
                    low,
            }
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for row in rows:

        unique[
            row["date"]
        ] = row

    rows = list(
        unique.values()
    )

    rows.sort(
        key=lambda x:
            parse_date(
                x["date"]
            )
            or datetime.min
    )

    return rows


# ============================================================
# Load Prices
# ============================================================

def load_prices(
    symbol: str,
    manifest: Dict[str, Any]
) -> List[Dict[str, Any]]:

    path = find_price_file(
        symbol,
        manifest
    )

    if path is None:
        return []

    try:

        data = load_json(
            path
        )

        return parse_price_history(
            data
        )

    except Exception as exc:

        log(
            f"   ⚠️ {symbol} 價格檔讀取失敗："
            f"{exc}"
        )

        return []


# ============================================================
# Moving Average
# ============================================================

def moving_average(
    values: List[float],
    period: int
) -> Optional[float]:

    if len(values) < period:
        return None

    return (
        sum(
            values[-period:]
        )
        / period
    )


def previous_moving_average(
    values: List[float],
    period: int
) -> Optional[float]:

    if len(values) < period + 1:
        return None

    return (
        sum(
            values[-period - 1:-1]
        )
        / period
    )


# ============================================================
# EMA
# ============================================================

def ema_series(
    values: List[float],
    period: int
) -> List[float]:

    if len(values) < period:
        return []

    multiplier = 2.0 / (
        period + 1
    )

    result = []

    ema = (
        sum(
            values[:period]
        )
        / period
    )

    result.append(
        ema
    )

    for value in values[period:]:

        ema = (
            value - ema
        ) * multiplier + ema

        result.append(
            ema
        )

    return result


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    closes: List[float]
) -> Dict[str, Any]:

    fast = SHORT_TERM_CONFIG[
        "macd_fast"
    ]

    slow = SHORT_TERM_CONFIG[
        "macd_slow"
    ]

    signal_period = SHORT_TERM_CONFIG[
        "macd_signal"
    ]

    if len(closes) < (
        slow + signal_period
    ):

        return {
            "macd": None,
            "signal": None,
            "histogram": None,
            "golden_cross": False,
            "status": "insufficient",
        }

    ema_fast = ema_series(
        closes,
        fast
    )

    ema_slow = ema_series(
        closes,
        slow
    )

    if not ema_fast or not ema_slow:

        return {
            "macd": None,
            "signal": None,
            "histogram": None,
            "golden_cross": False,
            "status": "insufficient",
        }

    offset = (
        len(ema_fast)
        - len(ema_slow)
    )

    macd_series = []

    for i, slow_value in enumerate(
        ema_slow
    ):

        fast_value = ema_fast[
            i + offset
        ]

        macd_series.append(
            fast_value
            - slow_value
        )

    signal_series = ema_series(
        macd_series,
        signal_period
    )

    if len(signal_series) < 2:

        return {
            "macd":
                round(
                    macd_series[-1],
                    4
                ),

            "signal": None,

            "histogram": None,

            "golden_cross": False,

            "status": "partial",
        }

    current_macd = (
        macd_series[-1]
    )

    previous_macd = (
        macd_series[-2]
    )

    current_signal = (
        signal_series[-1]
    )

    previous_signal = (
        signal_series[-2]
    )

    golden_cross = (
        previous_macd
        <= previous_signal

        and

        current_macd
        > current_signal
    )

    return {

        "macd":
            round(
                current_macd,
                4
            ),

        "signal":
            round(
                current_signal,
                4
            ),

        "histogram":
            round(
                current_macd
                - current_signal,
                4
            ),

        "golden_cross":
            golden_cross,

        "status":
            "complete",
    }


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    closes: List[float],
    period: int = 14
) -> Optional[float]:

    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(
        1,
        len(closes)
    ):

        change = (
            closes[i]
            - closes[i - 1]
        )

        if change > 0:

            gains.append(
                change
            )

            losses.append(
                0.0
            )

        else:

            gains.append(
                0.0
            )

            losses.append(
                abs(change)
            )

    avg_gain = (
        sum(
            gains[:period]
        )
        / period
    )

    avg_loss = (
        sum(
            losses[:period]
        )
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            avg_gain * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return round(
        100.0
        - (
            100.0
            / (1.0 + rs)
        ),
        2
    )


# ============================================================
# KD
# ============================================================

def calculate_kd(
    rows: List[Dict[str, Any]],
    period: int = 9
) -> Dict[str, Any]:

    if len(rows) < period:

        return {
            "k": None,
            "d": None,
            "golden_cross": False,
            "status": "insufficient",
        }

    highs = []
    lows = []
    closes = []

    for row in rows:

        close = safe_float(
            row.get("close")
        )

        if close is None:
            continue

        high = safe_float(
            row.get("high")
        )

        low = safe_float(
            row.get("low")
        )

        highs.append(
            high
            if high is not None
            else close
        )

        lows.append(
            low
            if low is not None
            else close
        )

        closes.append(
            close
        )

    if len(closes) < period:

        return {
            "k": None,
            "d": None,
            "golden_cross": False,
            "status": "insufficient",
        }

    k = 50.0
    d = 50.0

    k_values = []
    d_values = []

    for i in range(
        period - 1,
        len(closes)
    ):

        window_high = max(
            highs[
                i - period + 1:
                i + 1
            ]
        )

        window_low = min(
            lows[
                i - period + 1:
                i + 1
            ]
        )

        denominator = (
            window_high
            - window_low
        )

        if denominator == 0:

            rsv = 50.0

        else:

            rsv = (
                (
                    closes[i]
                    - window_low
                )
                / denominator
                * 100.0
            )

        k = (
            k * 2.0
            + rsv
        ) / 3.0

        d = (
            d * 2.0
            + k
        ) / 3.0

        k_values.append(
            k
        )

        d_values.append(
            d
        )

    golden_cross = False

    if len(k_values) >= 2:

        golden_cross = (
            k_values[-2]
            <= d_values[-2]

            and

            k_values[-1]
            > d_values[-1]
        )

    return {

        "k":
            round(
                k_values[-1],
                2
            ),

        "d":
            round(
                d_values[-1],
                2
            ),

        "golden_cross":
            golden_cross,

        "status":
            "complete",
    }


# ============================================================
# Price Metrics
# ============================================================

def calculate_price_metrics(
    rows: List[Dict[str, Any]]
) -> Dict[str, Any]:

    closes = [
        row["close"]
        for row in rows
        if row.get("close") is not None
    ]

    if not closes:

        return {}

    current_price = closes[-1]

    ma5 = moving_average(
        closes,
        5
    )

    ma20 = moving_average(
        closes,
        20
    )

    previous_ma20 = previous_moving_average(
        closes,
        20
    )

    ma60 = moving_average(
        closes,
        60
    )

    # --------------------------------------------------------
    # 成交量
    # --------------------------------------------------------

    raw_volumes = [
        row.get("volume")
        for row in rows
    ]

    volumes = [
        safe_float(v)
        for v in raw_volumes
    ]

    # 今日成交量
    today_volume = (
        volumes[-1]
        if volumes
        else None
    )

    # --------------------------------------------------------
    # 重要：
    #
    # 前5個交易日平均成交量
    #
    # 不包含今日。
    # --------------------------------------------------------

    previous_5_volume_avg = None

    if len(volumes) >= 6:

        previous_5 = [
            value
            for value in volumes[-6:-1]
            if value is not None
        ]

        if len(previous_5) == 5:

            previous_5_volume_avg = (
                sum(previous_5)
                / 5.0
            )

    volume_ratio_vs_previous_5 = None

    if (
        today_volume is not None
        and previous_5_volume_avg is not None
        and previous_5_volume_avg > 0
    ):

        volume_ratio_vs_previous_5 = (
            today_volume
            / previous_5_volume_avg
        )

    # --------------------------------------------------------
    # 5日均量
    #
    # 保留作資訊顯示。
    # 短期核心使用的是「前5日均量」。
    # --------------------------------------------------------

    volume5_including_today = None

    available_volumes = [
        value
        for value in volumes
        if value is not None
    ]

    if len(
        available_volumes
    ) >= 5:

        volume5_including_today = (
            sum(
                available_volumes[-5:]
            )
            / 5.0
        )

    volume20 = None

    if len(
        available_volumes
    ) >= 20:

        volume20 = (
            sum(
                available_volumes[-20:]
            )
            / 20.0
        )

    # --------------------------------------------------------
    # 60日高低
    # --------------------------------------------------------

    high60 = None
    low60 = None

    if len(closes) >= 60:

        high60 = max(
            closes[-60:]
        )

        low60 = min(
            closes[-60:]
        )

    elif closes:

        high60 = max(
            closes
        )

        low60 = min(
            closes
        )

    # --------------------------------------------------------
    # 20MA 乖離
    # --------------------------------------------------------

    bias20_pct = None

    if (
        ma20 is not None
        and ma20 != 0
    ):

        bias20_pct = (
            current_price
            / ma20
            - 1.0
        ) * 100.0

    # --------------------------------------------------------
    # 漲跌幅
    # --------------------------------------------------------

    change1_pct = None

    if len(closes) >= 2:

        change1_pct = (
            current_price
            / closes[-2]
            - 1.0
        ) * 100.0

    change5_pct = None

    if len(closes) >= 6:

        change5_pct = (
            current_price
            / closes[-6]
            - 1.0
        ) * 100.0

    change20_pct = None

    if len(closes) >= 21:

        change20_pct = (
            current_price
            / closes[-21]
            - 1.0
        ) * 100.0

    # --------------------------------------------------------
    # 60日位置
    # --------------------------------------------------------

    position60_pct = None

    if (
        high60 is not None
        and low60 is not None
        and high60 != low60
    ):

        position60_pct = (
            (
                current_price
                - low60
            )
            / (
                high60
                - low60
            )
            * 100.0
        )

    # --------------------------------------------------------
    # MA20 ratio
    # --------------------------------------------------------

    ma20_ratio = None

    if (
        ma20 is not None
        and ma20 != 0
    ):

        ma20_ratio = (
            current_price
            / ma20
        )

    # --------------------------------------------------------
    # Low60 ratio
    # --------------------------------------------------------

    low60_ratio = None

    if (
        low60 is not None
        and low60 != 0
    ):

        low60_ratio = (
            current_price
            / low60
        )

    # --------------------------------------------------------
    # Volume signal
    # --------------------------------------------------------

    volume_signal = "資料不足"

    if volume_ratio_vs_previous_5 is not None:

        ratio = (
            volume_ratio_vs_previous_5
        )

        if ratio >= 1.5:

            volume_signal = "放量"

        elif ratio >= 1.0:

            volume_signal = "正常"

        elif ratio >= 0.5:

            volume_signal = "量縮"

        else:

            volume_signal = "明顯量縮"

    return {

        "price":
            round(
                current_price,
                2
            ),

        "ma5":
            round(
                ma5,
                2
            )
            if ma5 is not None
            else None,

        "ma20":
            round(
                ma20,
                2
            )
            if ma20 is not None
            else None,

        "ma60":
            round(
                ma60,
                2
            )
            if ma60 is not None
            else None,

        "ma20_up":
            (
                previous_ma20 is not None
                and ma20 is not None
                and ma20 > previous_ma20
            ),

        "ma20_ratio":
            round(
                ma20_ratio,
                4
            )
            if ma20_ratio is not None
            else None,

        "bias20_pct":
            round(
                bias20_pct,
                2
            )
            if bias20_pct is not None
            else None,

        "change1_pct":
            round(
                change1_pct,
                2
            )
            if change1_pct is not None
            else None,

        "change5_pct":
            round(
                change5_pct,
                2
            )
            if change5_pct is not None
            else None,

        "change20_pct":
            round(
                change20_pct,
                2
            )
            if change20_pct is not None
            else None,

        "high60":
            round(
                high60,
                2
            )
            if high60 is not None
            else None,

        "low60":
            round(
                low60,
                2
            )
            if low60 is not None
            else None,

        "position60_pct":
            round(
                position60_pct,
                2
            )
            if position60_pct is not None
            else None,

        "low60_ratio":
            round(
                low60_ratio,
                4
            )
            if low60_ratio is not None
            else None,

        "volume":
            today_volume,

        "volume5_previous_avg":
            round(
                previous_5_volume_avg,
                2
            )
            if previous_5_volume_avg is not None
            else None,

        "volume5_including_today":
            round(
                volume5_including_today,
                2
            )
            if volume5_including_today is not None
            else None,

        "volume20":
            round(
                volume20,
                2
            )
            if volume20 is not None
            else None,

        "volume_ratio_vs_previous_5":
            round(
                volume_ratio_vs_previous_5,
                4
            )
            if volume_ratio_vs_previous_5 is not None
            else None,

        "volume_signal":
            volume_signal,
    }


# ============================================================
# Chip Helpers
# ============================================================

def chip_value(
    chip: Dict[str, Any],
    key: str
) -> Optional[float]:

    return safe_float(
        chip.get(key)
    )


def calculate_chip_analysis(
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    mf1 = chip_value(
        chip,
        "main_force_1d"
    )

    mf5 = chip_value(
        chip,
        "main_force_5d"
    )

    mf10 = chip_value(
        chip,
        "main_force_10d"
    )

    mf20 = chip_value(
        chip,
        "main_force_20d"
    )

    values = [
        value
        for value in (
            mf1,
            mf5,
            mf10,
            mf20,
        )
        if value is not None
    ]

    positive_count = sum(
        1
        for value in values
        if value > 0
    )

    negative_count = sum(
        1
        for value in values
        if value < 0
    )

    # --------------------------------------------------------
    # 10D 明確參與方向判斷
    # --------------------------------------------------------

    if len(values) == 0:

        direction = "資料不足"

    elif positive_count >= 3:

        direction = "偏多"

    elif negative_count >= 3:

        direction = "偏空"

    else:

        direction = "分歧"

    # --------------------------------------------------------
    # 中期籌碼：
    #
    # 5D / 10D / 20D
    # --------------------------------------------------------

    medium_values = [
        value
        for value in (
            mf5,
            mf10,
            mf20,
        )
        if value is not None
    ]

    medium_positive = sum(
        1
        for value in medium_values
        if value > 0
    )

    medium_negative = sum(
        1
        for value in medium_values
        if value < 0
    )

    if not medium_values:

        medium_direction = "資料不足"

    elif medium_positive >= 2:

        medium_direction = "中期偏多"

    elif medium_negative >= 2:

        medium_direction = "中期偏空"

    else:

        medium_direction = "中期分歧"

    return {

        "main_force_1d":
            mf1,

        "main_force_5d":
            mf5,

        "main_force_10d":
            mf10,

        "main_force_20d":
            mf20,

        "positive_count":
            positive_count,

        "negative_count":
            negative_count,

        "direction":
            direction,

        "medium_direction":
            medium_direction,

        "ten_day_used":
            True,
    }


# ============================================================
# Short Term Evaluation
# ============================================================

def evaluate_short_term(
    metrics: Dict[str, Any],
    macd: Dict[str, Any],
    kd: Dict[str, Any],
    rsi: Optional[float],
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    conditions = {}

    # --------------------------------------------------------
    # 1. MACD 黃金交叉
    # --------------------------------------------------------

    conditions[
        "macd_golden_cross"
    ] = bool(
        macd.get(
            "golden_cross",
            False
        )
    )

    # --------------------------------------------------------
    # 2. KD 黃金交叉
    # --------------------------------------------------------

    conditions[
        "kd_golden_cross"
    ] = bool(
        kd.get(
            "golden_cross",
            False
        )
    )

    # --------------------------------------------------------
    # 3. RSI > 50
    # --------------------------------------------------------

    conditions[
        "rsi_above_50"
    ] = (
        rsi is not None
        and rsi
        > SHORT_TERM_CONFIG[
            "rsi_min"
        ]
    )

    # --------------------------------------------------------
    # 4. 今日成交量 / 前5日平均 >= 1.5
    #
    # 這是新版成交量核心。
    # --------------------------------------------------------

    volume_ratio = metrics.get(
        "volume_ratio_vs_previous_5"
    )

    conditions[
        "volume_ratio_ge_1_5"
    ] = (
        volume_ratio is not None
        and volume_ratio
        >= SHORT_TERM_CONFIG[
            "volume_multiplier"
        ]
    )

    # --------------------------------------------------------
    # 5. 股價站上20日線
    # --------------------------------------------------------

    price = metrics.get(
        "price"
    )

    ma20 = metrics.get(
        "ma20"
    )

    conditions[
        "price_above_ma20"
    ] = (
        price is not None
        and ma20 is not None
        and price > ma20
    )

    # --------------------------------------------------------
    # 6. 20日線向上
    # --------------------------------------------------------

    conditions[
        "ma20_up"
    ] = bool(
        metrics.get(
            "ma20_up",
            False
        )
    )

    # --------------------------------------------------------
    # 六項全部通過
    # --------------------------------------------------------

    qualified = all(
        conditions.values()
    )

    score = sum(
        1
        for value in conditions.values()
        if value
    )

    # --------------------------------------------------------
    # 籌碼
    # --------------------------------------------------------

    chip_analysis = calculate_chip_analysis(
        chip
    )

    return {

        "qualified":
            qualified,

        "score":
            score,

        "core_total":
            len(conditions),

        "conditions":
            conditions,

        "rsi":
            rsi,

        "macd":
            macd,

        "kd":
            kd,

        "volume_rule":
            (
                "今日成交量 ÷ 前5個交易日"
                "平均成交量 >= 1.5"
            ),

        "volume_ratio":
            volume_ratio,

        "chip":
            chip_analysis,
    }


# ============================================================
# DCA Risk
# ============================================================

def calculate_loss_warning(
    current_price: Optional[float],
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    """
    最大虧損警戒。

    注意：

    本分析層沒有「實際持倉成本」資料時，
    不假設使用者成本。

    因此只有當 chip.json 或其他資料明確
    提供 cost / avg_cost 時才計算。

    不自行捏造成本。
    """

    cost = None

    for key in (
        "avg_cost",
        "cost",
        "average_cost",
    ):

        value = safe_float(
            chip.get(key)
        )

        if value is not None:
            cost = value
            break

    if (
        current_price is None
        or cost is None
        or cost <= 0
    ):

        return {

            "available":
                False,

            "cost":
                None,

            "loss_pct":
                None,

            "warning":
                False,

            "message":
                "無持倉成本資料",
        }

    loss_pct = (
        (
            current_price
            - cost
        )
        / cost
        * 100.0
    )

    warning = (
        loss_pct
        <= -DCA_CONFIG[
            "max_loss_pct"
        ]
    )

    return {

        "available":
            True,

        "cost":
            round(
                cost,
                2
            ),

        "loss_pct":
            round(
                loss_pct,
                2
            ),

        "warning":
            warning,

        "message":
            (
                "達到最大虧損警戒"
                if warning
                else "未達最大虧損警戒"
            ),
    }


# ============================================================
# DCA Evaluation
# ============================================================

def evaluate_dca(
    metrics: Dict[str, Any],
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    price = metrics.get(
        "price"
    )

    ma20_ratio = metrics.get(
        "ma20_ratio"
    )

    low60_ratio = metrics.get(
        "low60_ratio"
    )

    bias20 = metrics.get(
        "bias20_pct"
    )

    position60 = metrics.get(
        "position60_pct"
    )

    volume_ratio = metrics.get(
        "volume_ratio_vs_previous_5"
    )

    chip_analysis = calculate_chip_analysis(
        chip
    )

    # --------------------------------------------------------
    # 基本買進等級
    # --------------------------------------------------------

    if ma20_ratio is None:

        action = "資料不足"
        level = 0

    elif (
        ma20_ratio
        <= DCA_CONFIG[
            "ma20_aggressive"
        ]

        and

        low60_ratio is not None

        and

        low60_ratio
        <= DCA_CONFIG[
            "low60_aggressive"
        ]
    ):

        action = "積極買進"
        level = 3

    elif (
        ma20_ratio
        <= DCA_CONFIG[
            "ma20_normal"
        ]
    ):

        action = "正常買進"
        level = 2

    elif (
        ma20_ratio
        <= DCA_CONFIG[
            "ma20_pause"
        ]
    ):

        action = "觀察"
        level = 1

    else:

        action = "暫停加碼"
        level = 0

    # --------------------------------------------------------
    # 成交量
    #
    # DCA 不把 1.5 當作硬性買進條件。
    # 它是風險/動能訊號。
    # --------------------------------------------------------

    volume_signal = "資料不足"

    if volume_ratio is not None:

        if (
            volume_ratio
            >= DCA_CONFIG[
                "volume_aggressive"
            ]
        ):

            volume_signal = "放量"

        elif (
            volume_ratio
            >= DCA_CONFIG[
                "volume_normal"
            ]
        ):

            volume_signal = "正常"

        elif (
            volume_ratio
            >= DCA_CONFIG[
                "volume_observe"
            ]
        ):

            volume_signal = "量縮"

        else:

            volume_signal = "明顯量縮"

    # --------------------------------------------------------
    # 籌碼
    # --------------------------------------------------------

    chip_direction = chip_analysis.get(
        "direction"
    )

    medium_direction = chip_analysis.get(
        "medium_direction"
    )

    # --------------------------------------------------------
    # 高檔再平衡
    # --------------------------------------------------------

    rebalance_signal = "normal"

    if position60 is not None:

        if (
            position60
            >= DCA_CONFIG[
                "rebalance_high"
            ]
        ):

            rebalance_signal = "高檔"

        elif (
            position60
            >= DCA_CONFIG[
                "rebalance_warn"
            ]
        ):

            rebalance_signal = "偏高"

    # --------------------------------------------------------
    # 極端負乖離
    # --------------------------------------------------------

    extreme_discount = (
        bias20 is not None
        and bias20
        <= DCA_CONFIG[
            "extreme_bias"
        ]
    )

    if extreme_discount:

        if level > 0:
            level += 1

        action = "極端負乖離"

    # --------------------------------------------------------
    # 高檔降低加碼強度
    # --------------------------------------------------------

    if (
        rebalance_signal == "高檔"
        and level > 0
    ):

        action = "高檔觀察"

        level = max(
            0,
            level - 1
        )

    # --------------------------------------------------------
    # 搭配中期籌碼
    #
    # 不直接把籌碼當作硬性淘汰條件。
    # 避免把定投系統變成短線選股。
    # --------------------------------------------------------

    chip_adjustment = "neutral"

    if medium_direction == "中期偏多":

        chip_adjustment = "positive"

    elif medium_direction == "中期偏空":

        chip_adjustment = "negative"

    # --------------------------------------------------------
    # 搭配成交量
    # --------------------------------------------------------

    volume_adjustment = "neutral"

    if volume_signal == "放量":

        volume_adjustment = "positive"

    elif volume_signal in {
        "明顯量縮",
    }:

        volume_adjustment = "caution"

    # --------------------------------------------------------
    # 最大虧損
    # --------------------------------------------------------

    loss_warning = calculate_loss_warning(
        price,
        chip
    )

    # --------------------------------------------------------
    # 最終風控
    # --------------------------------------------------------

    risk_flags = []

    if rebalance_signal == "高檔":

        risk_flags.append(
            "60日高檔"
        )

    if volume_signal == "明顯量縮":

        risk_flags.append(
            "成交量明顯萎縮"
        )

    if chip_adjustment == "negative":

        risk_flags.append(
            "中期籌碼偏空"
        )

    if loss_warning.get(
        "warning"
    ):

        risk_flags.append(
            "最大虧損警戒"
        )

    return {

        "action":
            action,

        "level":
            level,

        "ma20_ratio":
            ma20_ratio,

        "low60_ratio":
            low60_ratio,

        "bias20_pct":
            bias20,

        "position60_pct":
            position60,

        "change1_pct":
            metrics.get(
                "change1_pct"
            ),

        "change5_pct":
            metrics.get(
                "change5_pct"
            ),

        "change20_pct":
            metrics.get(
                "change20_pct"
            ),

        "volume_ratio":
            volume_ratio,

        "volume_signal":
            volume_signal,

        "volume_adjustment":
            volume_adjustment,

        "chip_direction":
            chip_direction,

        "medium_chip_direction":
            medium_direction,

        "chip_adjustment":
            chip_adjustment,

        "rebalance_signal":
            rebalance_signal,

        "extreme_discount":
            extreme_discount,

        "loss_warning":
            loss_warning,

        "risk_flags":
            risk_flags,

        "main_force_1d":
            chip_analysis.get(
                "main_force_1d"
            ),

        "main_force_5d":
            chip_analysis.get(
                "main_force_5d"
            ),

        "main_force_10d":
            chip_analysis.get(
                "main_force_10d"
            ),

        "main_force_20d":
            chip_analysis.get(
                "main_force_20d"
            ),

        "ten_day_used":
            True,
    }


# ============================================================
# Analyze One Stock
# ============================================================

def analyze_stock(
    stock: Dict[str, Any],
    rows: List[Dict[str, Any]],
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    symbol = stock[
        "symbol"
    ]

    name = stock.get(
        "name",
        ""
    )

    market = stock.get(
        "market",
        ""
    )

    minimum_history = SHORT_TERM_CONFIG[
        "minimum_history"
    ]

    # --------------------------------------------------------
    # 歷史不足
    # --------------------------------------------------------

    if len(rows) < 2:

        return {

            "symbol":
                symbol,

            "name":
                name,

            "market":
                market,

            "status":
                "insufficient",

            "history_count":
                len(rows),

            "error":
                "價格歷史不足",

            "metrics":
                {},

            "short_term":
                {},

            "dca":
                {},

            "chip":
                calculate_chip_analysis(
                    chip
                ),
        }

    metrics = calculate_price_metrics(
        rows
    )

    closes = [
        row["close"]
        for row in rows
        if row.get("close") is not None
    ]

    macd = calculate_macd(
        closes
    )

    rsi = calculate_rsi(
        closes,
        SHORT_TERM_CONFIG[
            "rsi_period"
        ]
    )

    kd = calculate_kd(
        rows,
        SHORT_TERM_CONFIG[
            "kd_period"
        ]
    )

    # --------------------------------------------------------
    # 即使歷史不足60日，
    # DCA 仍可提供部分分析。
    #
    # 但短期選股不允許正式通過。
    # --------------------------------------------------------

    short_term = evaluate_short_term(
        metrics,
        macd,
        kd,
        rsi,
        chip
    )

    if len(rows) < minimum_history:

        short_term[
            "qualified"
        ] = False

        short_term[
            "history_requirement"
        ] = {

            "minimum":
                minimum_history,

            "actual":
                len(rows),

            "passed":
                False,
        }

    else:

        short_term[
            "history_requirement"
        ] = {

            "minimum":
                minimum_history,

            "actual":
                len(rows),

            "passed":
                True,
        }

    dca = evaluate_dca(
        metrics,
        chip
    )

    return {

        "symbol":
            symbol,

        "name":
            name,

        "market":
            market,

        "status":
            (
                "complete"
                if len(rows)
                >= minimum_history
                else "partial"
            ),

        "latest_date":
            rows[-1]["date"],

        "history_count":
            len(rows),

        "metrics":
            metrics,

        "short_term":
            short_term,

        "dca":
            dca,

        "chip":
            calculate_chip_analysis(
                chip
            ),
    }


# ============================================================
# Main Analysis
# ============================================================

def run_analysis() -> Dict[str, Any]:

    section(
        f"台股 AI 選股分析 V{VERSION}"
    )

    universe = load_universe()

    manifest = load_price_manifest()

    chip_data = load_json(
        CHIP_FILE
    )

    if not isinstance(
        chip_data,
        dict
    ):

        raise RuntimeError(
            "chip.json 格式錯誤"
        )

    chip_stocks = chip_data.get(
        "stocks",
        {}
    )

    if not isinstance(
        chip_stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 格式錯誤"
        )

    log(
        f"Chip 股票："
        f"{len(chip_stocks)}"
    )

    results = {}

    short_candidates = []

    dca_buy = []
    dca_observe = []
    dca_pause = []

    complete = 0
    partial = 0
    insufficient = 0

    total = len(
        universe
    )

    for index, (
        symbol,
        stock
    ) in enumerate(
        universe.items(),
        start=1
    ):

        price_rows = load_prices(
            symbol,
            manifest
        )

        chip = chip_stocks.get(
            symbol,
            {}
        )

        if not isinstance(
            chip,
            dict
        ):

            chip = {}

        record = analyze_stock(
            stock,
            price_rows,
            chip
        )

        results[
            symbol
        ] = record

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        status = record.get(
            "status"
        )

        if status == "complete":

            complete += 1

        elif status == "partial":

            partial += 1

        else:

            insufficient += 1

        # ----------------------------------------------------
        # Short term
        # ----------------------------------------------------

        if record.get(
            "short_term",
            {}
        ).get(
            "qualified",
            False
        ):

            short_candidates.append(
                symbol
            )

        # ----------------------------------------------------
        # DCA
        # ----------------------------------------------------

        dca_action = (
            record.get(
                "dca",
                {}
            ).get(
                "action"
            )
        )

        if dca_action in {
            "積極買進",
            "正常買進",
            "極端負乖離",
        }:

            dca_buy.append(
                symbol
            )

        elif dca_action in {
            "觀察",
            "高檔觀察",
        }:

            dca_observe.append(
                symbol
            )

        elif dca_action in {
            "暫停加碼",
        }:

            dca_pause.append(
                symbol
            )

        # ----------------------------------------------------
        # Progress
        # ----------------------------------------------------

        if (
            index == 1
            or index % 100 == 0
            or index == total
        ):

            log(
                f"進度："
                f"{index}/{total}"
            )

    return {

        "results":
            results,

        "statistics":
            {

                "universe":
                    total,

                "complete":
                    complete,

                "partial":
                    partial,

                "insufficient":
                    insufficient,

                "short_term_candidates":
                    len(
                        short_candidates
                    ),

                "dca_buy":
                    len(
                        dca_buy
                    ),

                "dca_observe":
                    len(
                        dca_observe
                    ),

                "dca_pause":
                    len(
                        dca_pause
                    ),
            },

        "short_term_candidates":
            short_candidates,

        "dca_buy":
            dca_buy,

        "dca_observe":
            dca_observe,

        "dca_pause":
            dca_pause,
    }


# ============================================================
# Save Analysis
# ============================================================

def save_analysis(
    analysis: Dict[str, Any]
) -> None:

    section(
        "寫入 Data/analysis.json"
    )

    output = {

        "schema_version":
            VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            {

                "universe":
                    "Data/universe.json",

                "prices":
                    "Data/prices/",

                "price_manifest":
                    "Data/prices/manifest.json",

                "chip":
                    "Data/chip.json",
            },

        "analysis_rules":
            {

                "short_term":
                    {

                        "macd":
                            "MACD 黃金交叉",

                        "kd":
                            "KD 黃金交叉",

                        "rsi":
                            "RSI > 50",

                        "volume":
                            (
                                "今日成交量 ÷ "
                                "前5個交易日平均成交量 "
                                ">= 1.5"
                            ),

                        "price_ma20":
                            "股價站上20日線",

                        "ma20_direction":
                            "20日線向上",

                        "minimum_history":
                            SHORT_TERM_CONFIG[
                                "minimum_history"
                            ],
                    },

                "chip":
                    {

                        "1d":
                            "保留並分析",

                        "5d":
                            "保留並分析",

                        "10d":
                            "保留並實際參與分析",

                        "20d":
                            "保留並分析",
                    },

                "dca":
                    DCA_CONFIG,
            },

        "statistics":
            analysis[
                "statistics"
            ],

        "short_term_candidates":
            analysis[
                "short_term_candidates"
            ],

        "dca_buy":
            analysis[
                "dca_buy"
            ],

        "dca_observe":
            analysis[
                "dca_observe"
            ],

        "dca_pause":
            analysis[
                "dca_pause"
            ],

        "stocks":
            analysis[
                "results"
            ],
    }

    # --------------------------------------------------------
    # Temporary file
    # --------------------------------------------------------

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 寫入後重新讀取驗證
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(
            f
        )

    if not isinstance(
        verify,
        dict
    ):

        raise RuntimeError(
            "analysis.json 寫入後格式錯誤"
        )

    if not isinstance(
        verify.get(
            "stocks"
        ),
        dict
    ):

        raise RuntimeError(
            "analysis.json stocks 格式錯誤"
        )

    if len(
        verify["stocks"]
    ) != len(
        analysis["results"]
    ):

        raise RuntimeError(
            "analysis.json 股票數量驗證失敗"
        )

    # --------------------------------------------------------
    # 檢查 schema
    # --------------------------------------------------------

    required_top_keys = {

        "schema_version",
        "generated_at",
        "source",
        "analysis_rules",
        "statistics",
        "short_term_candidates",
        "dca_buy",
        "dca_observe",
        "dca_pause",
        "stocks",
    }

    missing_keys = (
        required_top_keys
        - set(
            verify.keys()
        )
    )

    if missing_keys:

        raise RuntimeError(
            "analysis.json 缺少欄位："
            + ", ".join(
                sorted(
                    missing_keys
                )
            )
        )

    # --------------------------------------------------------
    # 正式替換
    # --------------------------------------------------------

    temp_file.replace(
        OUTPUT_FILE
    )

    log(
        f"✓ {OUTPUT_FILE}"
    )


# ============================================================
# Final Summary
# ============================================================

def print_summary(
    analysis: Dict[str, Any],
    elapsed: float
) -> None:

    stats = analysis[
        "statistics"
    ]

    section(
        "分析完成"
    )

    log(
        f"Universe："
        f"{stats['universe']}"
    )

    log(
        f"完整："
        f"{stats['complete']}"
    )

    log(
        f"部分："
        f"{stats['partial']}"
    )

    log(
        f"不足："
        f"{stats['insufficient']}"
    )

    log("")

    log(
        "短期六項核心全部符合："
        f"{stats['short_term_candidates']}"
    )

    log(
        "零股定投可買："
        f"{stats['dca_buy']}"
    )

    log(
        "零股定投觀察："
        f"{stats['dca_observe']}"
    )

    log(
        "零股定投暫停："
        f"{stats['dca_pause']}"
    )

    log("")

    log(
        "短期成交量規則："
    )

    log(
        "今日成交量 ÷ "
        "前5個交易日平均成交量 >= 1.5"
    )

    log("")

    log(
        "籌碼："
        "1D / 5D / 10D / 20D"
    )

    log(
        "10D：已實際參與籌碼方向分析"
    )

    log("")

    log(
        f"耗時："
        f"{elapsed:.1f} 秒"
    )

    log(
        f"輸出："
        f"{OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    start = time.time()

    log("")
    log("=" * 72)

    log(
        "台股 AI 選股系統 "
        f"analyze_stocks.py {VERSION}"
    )

    log("=" * 72)

    try:

        analysis = run_analysis()

        save_analysis(
            analysis
        )

        elapsed = (
            time.time()
            - start
        )

        print_summary(
            analysis,
            elapsed
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)

        log(
            f"❌ analyze_stocks.py "
            f"{VERSION} 執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )