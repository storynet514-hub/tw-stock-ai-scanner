#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
analyze_stocks.py V2.0

============================================================
目的
============================================================

分析層：

1. 讀取 Data/universe.json
2. 讀取 Data/prices/manifest.json
3. 讀取 Data/prices/*.json
4. 讀取 Data/chip.json
5. 計算技術指標
6. 執行短期選股核心條件
7. 執行零股定投動態風控
8. 輸出 Data/analysis.json

============================================================
資料來源
============================================================

價格：
Data/prices/

籌碼：
Data/chip.json

股票清單：
Data/universe.json

============================================================
短期選股核心
============================================================

1. MACD 黃金交叉
2. KD 黃金交叉
3. RSI > 50
4. 成交量 > 5日均量 × 1.5
5. 股價站上20日線
   且20日線向上

============================================================
零股定投核心
============================================================

MA20 相對位置
60日高低位置
20MA 乖離
近期漲跌幅
主力籌碼
動態風控

============================================================
重要
============================================================

本程式：

- 不抓 CMoney API
- 不修改 chip.json
- 不修改 prices
- 不執行 fetch_chip.py
- 不執行 fetch_data.py

只負責分析。

輸出：

Data/analysis.json
"""

from __future__ import annotations

import json
import math
import sys
import time

from datetime import datetime
from pathlib import Path


# ============================================================
# 基本設定
# ============================================================

VERSION = "V2.0"

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
    "rsi_min": 50.0,

    # 成交量
    "volume_multiplier": 1.5,

    # MA20
    "require_price_above_ma20": True,
    "require_ma20_up": True,

    # KD
    "kd_low_preferred": 30.0,

    # 最少歷史
    "minimum_history": 60,
}


# ============================================================
# 零股定投設定
# ============================================================

DCA_CONFIG = {

    # MA20
    "ma20_aggressive": 0.97,
    "ma20_normal": 1.02,
    "ma20_pause": 1.05,

    # 60 日低點
    "low60_aggressive": 1.03,

    # 成交量
    "volume_aggressive": 1.5,
    "volume_normal": 1.0,
    "volume_observe": 0.5,

    # 最大虧損警戒
    "max_loss_pct": 20.0,

    # 再平衡
    "rebalance_warn": 30.0,
    "rebalance_high": 50.0,

    # 極端乖離
    "extreme_bias": -20.0,
}


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):

    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# JSON
# ============================================================

def load_json(path):

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

def safe_float(value):

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
# Universe
# ============================================================

def load_universe():

    data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 格式錯誤"
        )

    items = data.get(
        "items",
        []
    )

    if not isinstance(items, list):

        raise RuntimeError(
            "universe.json items 格式錯誤"
        )

    stocks = {}

    for item in items:

        if not isinstance(item, dict):
            continue

        symbol = (
            item.get("code")
            or item.get("symbol")
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
# Price file discovery
# ============================================================

def load_price_manifest():

    data = load_json(
        PRICES_MANIFEST
    )

    if not isinstance(data, dict):

        raise RuntimeError(
            "prices/manifest.json 格式錯誤"
        )

    return data


# ============================================================
# 尋找股票價格檔
# ============================================================

def find_price_file(
    symbol,
    manifest
):

    # --------------------------------------------------------
    # 常見 manifest 結構
    # --------------------------------------------------------

    candidates = []

    stocks = manifest.get(
        "stocks"
    )

    if isinstance(stocks, dict):

        item = stocks.get(
            symbol
        )

        if isinstance(item, str):

            candidates.append(
                item
            )

        elif isinstance(item, dict):

            for key in [
                "file",
                "path",
                "filename"
            ]:

                if item.get(key):
                    candidates.append(
                        item[key]
                    )

    # --------------------------------------------------------
    # 直接搜尋 prices 目錄
    # --------------------------------------------------------

    candidates.extend([
        f"{symbol}.json",
        f"{symbol}.TW.json",
        f"{symbol}.TWO.json",
    ])

    for candidate in candidates:

        candidate = str(
            candidate
        ).replace(
            "\\",
            "/"
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

    for path in PRICES_DIR.glob(
        "*.json"
    ):

        if path.name in {
            "manifest.json"
        }:
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
# 日期
# ============================================================

def parse_date(value):

    if value is None:
        return None

    text = str(value).strip()

    for fmt in [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
    ]:

        try:

            return datetime.strptime(
                text,
                fmt
            )

        except Exception:
            pass

    return None


# ============================================================
# Price parser
# ============================================================

def parse_price_history(
    data
):

    rows = []

    # --------------------------------------------------------
    # list
    # --------------------------------------------------------

    if isinstance(data, list):

        raw_rows = data

    # --------------------------------------------------------
    # dict
    # --------------------------------------------------------

    elif isinstance(data, dict):

        raw_rows = (
            data.get("prices")
            or data.get("data")
            or data.get("history")
            or data.get("rows")
            or []
        )

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

                    row = dict(item)

                    row.setdefault(
                        "date",
                        date
                    )

                    converted.append(
                        row
                    )

                else:

                    converted.append({
                        "date": date,
                        "close": item,
                    })

            raw_rows = converted

    else:

        raw_rows = []

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
        )

        dt = parse_date(
            date
        )

        if dt is None:
            continue

        close = (
            row.get("close")
            or row.get("Close")
            or row.get("price")
        )

        volume = (
            row.get("volume")
            or row.get("Volume")
        )

        close = safe_float(
            close
        )

        volume = safe_float(
            volume
        )

        if close is None:
            continue

        rows.append({

            "date": dt.strftime(
                "%Y-%m-%d"
            ),

            "close": close,

            "volume": volume,
        })

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
    )

    return rows


# ============================================================
# Load price history
# ============================================================

def load_prices(
    symbol,
    manifest
):

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
    values,
    period
):

    if len(values) < period:
        return None

    return sum(
        values[-period:]
    ) / period


def previous_moving_average(
    values,
    period
):

    if len(values) < period + 1:
        return None

    return sum(
        values[-period-1:-1]
    ) / period


# ============================================================
# EMA
# ============================================================

def ema_series(
    values,
    period
):

    if len(values) < period:
        return []

    multiplier = 2 / (
        period + 1
    )

    result = []

    ema = sum(
        values[:period]
    ) / period

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
    closes
):

    if len(closes) < 35:

        return {
            "macd": None,
            "signal": None,
            "histogram": None,
            "golden_cross": False,
        }

    ema12 = ema_series(
        closes,
        12
    )

    ema26 = ema_series(
        closes,
        26
    )

    if not ema12 or not ema26:

        return {
            "macd": None,
            "signal": None,
            "histogram": None,
            "golden_cross": False,
        }

    # 對齊 26EMA
    offset = (
        len(ema12)
        - len(ema26)
    )

    macd_series = []

    for i, ema26_value in enumerate(
        ema26
    ):

        ema12_value = ema12[
            i + offset
        ]

        macd_series.append(
            ema12_value
            - ema26_value
        )

    if len(macd_series) < 10:

        return {
            "macd": macd_series[-1]
            if macd_series else None,

            "signal": None,

            "histogram": None,

            "golden_cross": False,
        }

    signal_series = ema_series(
        macd_series,
        9
    )

    if not signal_series:

        return {
            "macd": macd_series[-1],
            "signal": None,
            "histogram": None,
            "golden_cross": False,
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
        if len(signal_series) >= 2
        else None
    )

    golden_cross = (

        previous_signal is not None

        and previous_macd
        <= previous_signal

        and current_macd
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
    }


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    closes,
    period=14
):

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
                0
            )

        else:

            gains.append(
                0
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
            avg_gain
            * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss
            * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return round(
        100
        - (
            100
            / (1 + rs)
        ),
        2
    )


# ============================================================
# KD
# ============================================================

def calculate_kd(
    rows,
    period=9
):

    if len(rows) < period:

        return {
            "k": None,
            "d": None,
            "golden_cross": False,
        }

    # 價格資料目前可能只有 close。
    # 若有 high / low，優先使用。
    highs = []
    lows = []
    closes = []

    for row in rows:

        close = safe_float(
            row.get("close")
        )

        high = safe_float(
            row.get("high")
        )

        low = safe_float(
            row.get("low")
        )

        if close is None:
            continue

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
                * 100
            )

        k = (
            k * 2
            + rsv
        ) / 3

        d = (
            d * 2
            + k
        ) / 3

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
    }


# ============================================================
# Price statistics
# ============================================================

def calculate_price_metrics(
    rows
):

    closes = [
        row["close"]
        for row in rows
        if row.get("close") is not None
    ]

    volumes = [
        row.get("volume")
        for row in rows
    ]

    volumes = [
        value
        for value in volumes
        if value is not None
    ]

    if not closes:

        return {}

    current = closes[-1]

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

    volume5 = None

    if len(volumes) >= 5:

        volume5 = (
            sum(
                volumes[-5:]
            ) / 5
        )

    volume20 = None

    if len(volumes) >= 20:

        volume20 = (
            sum(
                volumes[-20:]
            ) / 20
        )

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

    bias20 = None

    if ma20:

        bias20 = (
            current / ma20 - 1
        ) * 100

    change1 = None

    if len(closes) >= 2:

        change1 = (
            current
            / closes[-2]
            - 1
        ) * 100

    change5 = None

    if len(closes) >= 6:

        change5 = (
            current
            / closes[-6]
            - 1
        ) * 100

    change20 = None

    if len(closes) >= 21:

        change20 = (
            current
            / closes[-21]
            - 1
        ) * 100

    position60 = None

    if (
        high60 is not None
        and low60 is not None
        and high60 != low60
    ):

        position60 = (
            current
            - low60
        ) / (
            high60
            - low60
        ) * 100

    volume_ratio = None

    if (
        volume5 is not None
        and volume20 is not None
        and volume20 != 0
    ):

        volume_ratio = (
            volume5
            / volume20
        )

    return {

        "price":
            round(
                current,
                2
            ),

        "ma5":
            round(ma5, 2)
            if ma5 is not None
            else None,

        "ma20":
            round(ma20, 2)
            if ma20 is not None
            else None,

        "ma60":
            round(ma60, 2)
            if ma60 is not None
            else None,

        "ma20_up":
            (
                previous_ma20 is not None
                and ma20 is not None
                and ma20 > previous_ma20
            ),

        "bias20_pct":
            round(
                bias20,
                2
            )
            if bias20 is not None
            else None,

        "change1_pct":
            round(
                change1,
                2
            )
            if change1 is not None
            else None,

        "change5_pct":
            round(
                change5,
                2
            )
            if change5 is not None
            else None,

        "change20_pct":
            round(
                change20,
                2
            )
            if change20 is not None
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
                position60,
                2
            )
            if position60 is not None
            else None,

        "volume":
            volumes[-1]
            if volumes
            else None,

        "volume5":
            round(
                volume5,
                2
            )
            if volume5 is not None
            else None,

        "volume20":
            round(
                volume20,
                2
            )
            if volume20 is not None
            else None,

        "volume_ratio":
            round(
                volume_ratio,
                2
            )
            if volume_ratio is not None
            else None,
    }


# ============================================================
# 短期核心
# ============================================================

def evaluate_short_term(
    metrics,
    macd,
    kd,
    rsi,
    chip
):

    conditions = {}

    conditions[
        "macd_golden_cross"
    ] = bool(
        macd.get(
            "golden_cross"
        )
    )

    conditions[
        "kd_golden_cross"
    ] = bool(
        kd.get(
            "golden_cross"
        )
    )

    conditions[
        "rsi_above_50"
    ] = (
        rsi is not None
        and rsi > SHORT_TERM_CONFIG[
            "rsi_min"
        ]
    )

    volume = metrics.get(
        "volume"
    )

    volume5 = metrics.get(
        "volume5"
    )

    conditions[
        "volume_above_5ma_1_5x"
    ] = (
        volume is not None
        and volume5 is not None
        and volume5 > 0
        and volume
        >= volume5
        * SHORT_TERM_CONFIG[
            "volume_multiplier"
        ]
    )

    conditions[
        "price_above_ma20"
    ] = (
        metrics.get(
            "price"
        ) is not None
        and metrics.get(
            "ma20"
        ) is not None
        and metrics["price"]
        > metrics["ma20"]
    )

    conditions[
        "ma20_up"
    ] = bool(
        metrics.get(
            "ma20_up"
        )
    )

    # --------------------------------------------------------
    # 五大核心
    # --------------------------------------------------------

    five_core = (

        conditions[
            "macd_golden_cross"
        ]

        and conditions[
            "kd_golden_cross"
        ]

        and conditions[
            "rsi_above_50"
        ]

        and conditions[
            "volume_above_5ma_1_5x"
        ]

        and conditions[
            "price_above_ma20"
        ]

        and conditions[
            "ma20_up"
        ]
    )

    # --------------------------------------------------------
    # 額外籌碼資訊
    # --------------------------------------------------------

    main_force_1d = chip.get(
        "main_force_1d"
    )

    main_force_5d = chip.get(
        "main_force_5d"
    )

    main_force_10d = chip.get(
        "main_force_10d"
    )

    main_force_20d = chip.get(
        "main_force_20d"
    )

    return {

        "qualified":
            five_core,

        "score":
            sum(
                1
                for value in conditions.values()
                if value
            ),

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

        "chip":
            {

                "main_force_1d":
                    main_force_1d,

                "main_force_5d":
                    main_force_5d,

                "main_force_10d":
                    main_force_10d,

                "main_force_20d":
                    main_force_20d,
            },
    }


# ============================================================
# 零股定投
# ============================================================

def evaluate_dca(
    metrics,
    chip
):

    price = metrics.get(
        "price"
    )

    ma20 = metrics.get(
        "ma20"
    )

    low60 = metrics.get(
        "low60"
    )

    high60 = metrics.get(
        "high60"
    )

    bias20 = metrics.get(
        "bias20_pct"
    )

    position60 = metrics.get(
        "position60_pct"
    )

    volume_ratio = metrics.get(
        "volume_ratio"
    )

    main_force_5d = chip.get(
        "main_force_5d"
    )

    main_force_20d = chip.get(
        "main_force_20d"
    )

    # --------------------------------------------------------
    # MA20 ratio
    # --------------------------------------------------------

    ma20_ratio = None

    if (
        price is not None
        and ma20 is not None
        and ma20 != 0
    ):

        ma20_ratio = (
            price / ma20
        )

    # --------------------------------------------------------
    # 60 日低點距離
    # --------------------------------------------------------

    low60_ratio = None

    if (
        price is not None
        and low60 is not None
        and low60 != 0
    ):

        low60_ratio = (
            price / low60
        )

    # --------------------------------------------------------
    # 基本買進級別
    # --------------------------------------------------------

    if ma20_ratio is None:

        action = "資料不足"
        level = 0

    elif (
        ma20_ratio
        <= DCA_CONFIG[
            "ma20_aggressive"
        ]
        and (
            low60_ratio is not None
            and low60_ratio
            <= DCA_CONFIG[
                "low60_aggressive"
            ]
        )
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
    # 成交量調整
    # --------------------------------------------------------

    volume_signal = "normal"

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
            < DCA_CONFIG[
                "volume_observe"
            ]
        ):

            volume_signal = "量縮"

        elif (
            volume_ratio
            >= DCA_CONFIG[
                "volume_normal"
            ]
        ):

            volume_signal = "正常"

    # --------------------------------------------------------
    # 主力方向
    # --------------------------------------------------------

    chip_signal = "unknown"

    if main_force_20d is not None:

        if main_force_20d > 0:

            chip_signal = "主力20D偏多"

        elif main_force_20d < 0:

            chip_signal = "主力20D偏空"

        else:

            chip_signal = "主力20D中性"

    # --------------------------------------------------------
    # 再平衡 / 高檔風險
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
    # 高檔暫停
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
    # 回傳
    # --------------------------------------------------------

    return {

        "action":
            action,

        "level":
            level,

        "ma20_ratio":
            round(
                ma20_ratio,
                4
            )
            if ma20_ratio is not None
            else None,

        "low60_ratio":
            round(
                low60_ratio,
                4
            )
            if low60_ratio is not None
            else None,

        "volume_signal":
            volume_signal,

        "chip_signal":
            chip_signal,

        "rebalance_signal":
            rebalance_signal,

        "extreme_discount":
            extreme_discount,

        "main_force_5d":
            main_force_5d,

        "main_force_20d":
            main_force_20d,
    }


# ============================================================
# 分析單一股票
# ============================================================

def analyze_stock(
    stock,
    rows,
    chip
):

    symbol = stock[
        "symbol"
    ]

    name = stock[
        "name"
    ]

    if len(rows) < 2:

        return {

            "symbol": symbol,
            "name": name,
            "market":
                stock.get(
                    "market",
                    ""
                ),

            "status":
                "insufficient",

            "history_count":
                len(rows),

            "error":
                "價格歷史不足",

            "metrics": {},
            "short_term": {},
            "dca": {},
        }

    metrics = calculate_price_metrics(
        rows
    )

    closes = [
        row["close"]
        for row in rows
    ]

    macd = calculate_macd(
        closes
    )

    rsi = calculate_rsi(
        closes
    )

    kd = calculate_kd(
        rows
    )

    short_term = evaluate_short_term(
        metrics,
        macd,
        kd,
        rsi,
        chip
    )

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
            stock.get(
                "market",
                ""
            ),

        "status":
            "complete"
            if len(rows) >= 20
            else "partial",

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
            {

                "main_force_1d":
                    chip.get(
                        "main_force_1d"
                    ),

                "main_force_5d":
                    chip.get(
                        "main_force_5d"
                    ),

                "main_force_10d":
                    chip.get(
                        "main_force_10d"
                    ),

                "main_force_20d":
                    chip.get(
                        "main_force_20d"
                    ),
            },
    }


# ============================================================
# Main analysis
# ============================================================

def run_analysis():

    section(
        f"台股 AI 選股分析 V{VERSION}"
    )

    universe = load_universe()

    manifest = load_price_manifest()

    chip_data = load_json(
        CHIP_FILE
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

        results[symbol] = record

        status = record.get(
            "status"
        )

        if status == "complete":
            complete += 1

        elif status == "partial":
            partial += 1

        else:
            insufficient += 1

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
        # 只顯示進度
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
# Save
# ============================================================

def save_analysis(
    analysis
):

    section(
        "寫入 Data/analysis.json"
    )

    now = datetime.now()

    output = {

        "schema_version":
            VERSION,

        "generated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            {

                "universe":
                    "Data/universe.json",

                "prices":
                    "Data/prices/",

                "chip":
                    "Data/chip.json",
            },

        "short_term_definition":
            {

                "core_1":
                    "MACD 黃金交叉",

                "core_2":
                    "KD 黃金交叉",

                "core_3":
                    "RSI > 50",

                "core_4":
                    "成交量 > 5日均量 × 1.5",

                "core_5":
                    "股價站上20日線且20日線向上",
            },

        "dca_definition":
            DCA_CONFIG,

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

    temp_file = (
        OUTPUT_FILE.with_suffix(
            ".json.tmp"
        )
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
    # 寫入後驗證
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(
            f
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

    temp_file.replace(
        OUTPUT_FILE
    )

    log(
        f"✓ {OUTPUT_FILE}"
    )


# ============================================================
# Final summary
# ============================================================

def print_summary(
    analysis,
    elapsed
):

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

    log(
        ""
    )

    log(
        "短期五大核心符合："
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

    log(
        ""
    )

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

def main():

    start = time.time()

    log("")
    log("=" * 72)
    log(
        f"台股 AI 選股系統 "
        f"analyze_stocks.py V{VERSION}"
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
            f"V{VERSION} 執行失敗"
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