#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
backtest_winrate.py V1.0

============================================================
用途
============================================================

使用 Data/prices.json 的歷史價格資料，
回測目前台股 AI 選股系統核心技術條件：

1. MACD 黃金交叉
2. KD 黃金交叉
3. RSI > 50
4. 成交量 > 5日均量 × 1.5
5. 股價站上 MA20
6. MA20 向上

統計：

30 交易日
60 交易日
90 交易日

每一個條件分別統計：

- samples
- wins
- losses
- win_rate
- average_return
- median_return
- max_return
- min_return

另外統計：

- 6項全部成立
- 5項以上成立
- 4項以上成立
- 3項以上成立

輸出：

Data/backtest_stats.json

============================================================
重要
============================================================

本程式：

✓ 不修改 prices.json
✓ 不修改 universe.json
✓ 不修改 chip.json
✓ 不負責抓取價格
✓ 不負責抓取籌碼
✓ 不負責 UI
✓ 不負責交易下單

只負責：

prices.json
    ↓
歷史條件回測
    ↓
backtest_stats.json
"""

import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path


# ============================================================
# 基本設定
# ============================================================

VERSION = "V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

PRICES_FILE = DATA_DIR / "prices.json"

OUTPUT_FILE = DATA_DIR / "backtest_stats.json"

# 回測期間
HORIZONS = {
    "30d": 30,
    "60d": 60,
    "90d": 90,
}

# 最少樣本數
MIN_SAMPLES = 1

# 技術指標參數
MA5_PERIOD = 5
MA20_PERIOD = 20

RSI_PERIOD = 14

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

KD_PERIOD = 9
KD_SMOOTH_K = 3
KD_SMOOTH_D = 3

VOLUME_MULTIPLIER = 1.5


# ============================================================
# 輸出工具
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# JSON
# ============================================================

def load_prices():
    section("讀取 Data/prices.json")

    if not PRICES_FILE.exists():
        raise FileNotFoundError(
            f"找不到價格資料：{PRICES_FILE}"
        )

    with PRICES_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise RuntimeError(
            "prices.json 格式錯誤：頂層不是 object"
        )

    prices = data.get("prices")

    if not isinstance(prices, dict):
        raise RuntimeError(
            "prices.json 缺少 prices object"
        )

    log(
        f"prices.json 股票數量：{len(prices)}"
    )

    return data


# ============================================================
# 數值處理
# ============================================================

def to_float(value):
    if value is None:
        return None

    try:
        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except Exception:
        return None


def round_value(value, digits=4):
    if value is None:
        return None

    try:
        return round(float(value), digits)
    except Exception:
        return None


# ============================================================
# EMA
# ============================================================

def ema(values, period):
    result = [None] * len(values)

    if len(values) < period:
        return result

    valid_start = None

    for i, value in enumerate(values):
        if value is not None:
            valid_start = i
            break

    if valid_start is None:
        return result

    # 找到第一個連續 period 有效值
    for start in range(
        valid_start,
        len(values) - period + 1
    ):

        window = values[start:start + period]

        if all(
            value is not None
            for value in window
        ):
            initial = sum(window) / period

            result[start + period - 1] = initial

            multiplier = 2 / (period + 1)

            previous = initial

            for i in range(
                start + period,
                len(values)
            ):

                value = values[i]

                if value is None:
                    result[i] = None
                    continue

                previous = (
                    (value - previous)
                    * multiplier
                    + previous
                )

                result[i] = previous

            break

    return result


# ============================================================
# SMA
# ============================================================

def sma(values, period):
    result = [None] * len(values)

    if len(values) < period:
        return result

    for i in range(period - 1, len(values)):

        window = values[
            i - period + 1:
            i + 1
        ]

        if any(
            value is None
            for value in window
        ):
            continue

        result[i] = sum(window) / period

    return result


# ============================================================
# RSI
# ============================================================

def calculate_rsi(closes, period=14):
    result = [None] * len(closes)

    if len(closes) <= period:
        return result

    gains = [None] * len(closes)
    losses = [None] * len(closes)

    for i in range(1, len(closes)):

        current = closes[i]
        previous = closes[i - 1]

        if (
            current is None
            or previous is None
        ):
            continue

        change = current - previous

        gains[i] = max(change, 0)
        losses[i] = max(-change, 0)

    # 第一個 RSI
    gain_window = gains[1:period + 1]
    loss_window = losses[1:period + 1]

    if any(
        value is None
        for value in gain_window
    ):
        return result

    if any(
        value is None
        for value in loss_window
    ):
        return result

    average_gain = (
        sum(gain_window) / period
    )

    average_loss = (
        sum(loss_window) / period
    )

    if average_loss == 0:

        if average_gain > 0:
            result[period] = 100.0
        else:
            result[period] = 50.0

    else:

        rs = (
            average_gain /
            average_loss
        )

        result[period] = (
            100 -
            (100 / (1 + rs))
        )

    # Wilder smoothing
    for i in range(
        period + 1,
        len(closes)
    ):

        gain = gains[i]
        loss = losses[i]

        if gain is None or loss is None:
            continue

        average_gain = (
            (
                average_gain *
                (period - 1)
            )
            + gain
        ) / period

        average_loss = (
            (
                average_loss *
                (period - 1)
            )
            + loss
        ) / period

        if average_loss == 0:

            if average_gain > 0:
                result[i] = 100.0
            else:
                result[i] = 50.0

        else:

            rs = (
                average_gain /
                average_loss
            )

            result[i] = (
                100 -
                (100 / (1 + rs))
            )

    return result


# ============================================================
# MACD
# ============================================================

def calculate_macd(closes):
    fast = ema(
        closes,
        MACD_FAST
    )

    slow = ema(
        closes,
        MACD_SLOW
    )

    macd_line = [None] * len(closes)

    for i in range(len(closes)):

        if (
            fast[i] is not None
            and slow[i] is not None
        ):
            macd_line[i] = (
                fast[i] - slow[i]
            )

    signal = ema(
        macd_line,
        MACD_SIGNAL
    )

    histogram = [None] * len(closes)

    for i in range(len(closes)):

        if (
            macd_line[i] is not None
            and signal[i] is not None
        ):

            histogram[i] = (
                macd_line[i] -
                signal[i]
            )

    return (
        macd_line,
        signal,
        histogram
    )


# ============================================================
# KD
# ============================================================

def calculate_kd(
    highs,
    lows,
    closes,
    period=9,
    smooth_k=3,
    smooth_d=3
):

    raw_k = [None] * len(closes)

    for i in range(
        period - 1,
        len(closes)
    ):

        high_window = highs[
            i - period + 1:
            i + 1
        ]

        low_window = lows[
            i - period + 1:
            i + 1
        ]

        close = closes[i]

        if (
            close is None
            or any(
                value is None
                for value in high_window
            )
            or any(
                value is None
                for value in low_window
            )
        ):
            continue

        highest = max(high_window)
        lowest = min(low_window)

        if highest == lowest:
            raw_k[i] = 50.0
            continue

        raw_k[i] = (
            (
                close - lowest
            )
            /
            (
                highest - lowest
            )
        ) * 100

    k = sma(
        raw_k,
        smooth_k
    )

    d = sma(
        k,
        smooth_d
    )

    return k, d


# ============================================================
# 建立技術條件
# ============================================================

def build_indicators(rows):

    dates = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []

    for row in rows:

        dates.append(
            row.get("date")
        )

        opens.append(
            to_float(row.get("open"))
        )

        highs.append(
            to_float(row.get("high"))
        )

        lows.append(
            to_float(row.get("low"))
        )

        closes.append(
            to_float(row.get("close"))
        )

        volumes.append(
            to_float(row.get("volume"))
        )

    ma5 = sma(
        closes,
        MA5_PERIOD
    )

    ma20 = sma(
        closes,
        MA20_PERIOD
    )

    volume_ma5 = sma(
        volumes,
        MA5_PERIOD
    )

    rsi = calculate_rsi(
        closes,
        RSI_PERIOD
    )

    (
        macd_line,
        macd_signal,
        macd_histogram
    ) = calculate_macd(
        closes
    )

    k, d = calculate_kd(
        highs,
        lows,
        closes,
        KD_PERIOD,
        KD_SMOOTH_K,
        KD_SMOOTH_D
    )

    indicators = []

    for i in range(len(rows)):

        # ----------------------------------------------------
        # MACD 黃金交叉
        # ----------------------------------------------------

        macd_golden = False

        if i > 0:

            if (
                macd_line[i] is not None
                and macd_signal[i] is not None
                and macd_line[i - 1] is not None
                and macd_signal[i - 1] is not None
            ):

                macd_golden = (
                    macd_line[i] >
                    macd_signal[i]
                    and
                    macd_line[i - 1] <=
                    macd_signal[i - 1]
                )

        # ----------------------------------------------------
        # KD 黃金交叉
        # ----------------------------------------------------

        kd_golden = False

        if i > 0:

            if (
                k[i] is not None
                and d[i] is not None
                and k[i - 1] is not None
                and d[i - 1] is not None
            ):

                kd_golden = (
                    k[i] > d[i]
                    and
                    k[i - 1] <= d[i - 1]
                )

        # ----------------------------------------------------
        # RSI > 50
        # ----------------------------------------------------

        rsi_condition = (
            rsi[i] is not None
            and rsi[i] > 50
        )

        # ----------------------------------------------------
        # 成交量 > 5日均量 × 1.5
        # ----------------------------------------------------

        volume_condition = (
            volumes[i] is not None
            and volume_ma5[i] is not None
            and volumes[i] >
            volume_ma5[i] *
            VOLUME_MULTIPLIER
        )

        # ----------------------------------------------------
        # 股價站上 MA20
        # ----------------------------------------------------

        price_above_ma20 = (
            closes[i] is not None
            and ma20[i] is not None
            and closes[i] > ma20[i]
        )

        # ----------------------------------------------------
        # MA20 向上
        # ----------------------------------------------------

        ma20_up = False

        if i > 0:

            if (
                ma20[i] is not None
                and ma20[i - 1] is not None
            ):

                ma20_up = (
                    ma20[i] >
                    ma20[i - 1]
                )

        conditions = {
            "macd_golden_cross": macd_golden,
            "kd_golden_cross": kd_golden,
            "rsi_above_50": rsi_condition,
            "volume_above_5ma_1_5x": volume_condition,
            "price_above_ma20": price_above_ma20,
            "ma20_up": ma20_up,
        }

        score = sum(
            1
            for value in conditions.values()
            if value
        )

        indicators.append({
            "date": dates[i],
            "close": closes[i],
            "ma5": ma5[i],
            "ma20": ma20[i],
            "volume": volumes[i],
            "volume_ma5": volume_ma5[i],
            "rsi": rsi[i],
            "macd": macd_line[i],
            "macd_signal": macd_signal[i],
            "k": k[i],
            "d": d[i],
            "conditions": conditions,
            "score": score,
        })

    return indicators


# ============================================================
# 空白統計
# ============================================================

def empty_stat():

    return {
        "samples": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "average_return": None,
        "median_return": None,
        "max_return": None,
        "min_return": None,
    }


# ============================================================
# 完成統計
# ============================================================

def finalize_stat(returns):

    stat = empty_stat()

    if not returns:
        return stat

    wins = [
        value
        for value in returns
        if value > 0
    ]

    losses = [
        value
        for value in returns
        if value <= 0
    ]

    stat["samples"] = len(returns)
    stat["wins"] = len(wins)
    stat["losses"] = len(losses)

    stat["win_rate"] = round(
        len(wins)
        /
        len(returns)
        *
        100,
        2
    )

    stat["average_return"] = round(
        statistics.mean(returns),
        2
    )

    stat["median_return"] = round(
        statistics.median(returns),
        2
    )

    stat["max_return"] = round(
        max(returns),
        2
    )

    stat["min_return"] = round(
        min(returns),
        2
    )

    return stat


# ============================================================
# 回測單一股票
# ============================================================

def backtest_stock(symbol, record):

    rows = record.get("data", [])

    if not isinstance(rows, list):
        return None

    if len(rows) < 120:
        return None

    # --------------------------------------------------------
    # 按日期排序
    # --------------------------------------------------------

    rows = sorted(
        rows,
        key=lambda x: str(
            x.get("date", "")
        )
    )

    indicators = build_indicators(
        rows
    )

    result = {
        "symbol": symbol,
        "name": record.get(
            "name",
            ""
        ),
        "data_start": (
            rows[0].get("date")
            if rows
            else None
        ),
        "data_end": (
            rows[-1].get("date")
            if rows
            else None
        ),
        "horizons": {},
        "conditions": {},
        "combinations": {},
    }

    # --------------------------------------------------------
    # 每個 horizon
    # --------------------------------------------------------

    condition_names = [
        "macd_golden_cross",
        "kd_golden_cross",
        "rsi_above_50",
        "volume_above_5ma_1_5x",
        "price_above_ma20",
        "ma20_up",
    ]

    for horizon_name, days in HORIZONS.items():

        # ----------------------------------------------------
        # 單一條件
        # ----------------------------------------------------

        condition_returns = {
            name: []
            for name in condition_names
        }

        # ----------------------------------------------------
        # 組合條件
        # ----------------------------------------------------

        combination_returns = {
            "all_6": [],
            "at_least_5": [],
            "at_least_4": [],
            "at_least_3": [],
        }

        # ----------------------------------------------------
        # 逐日回測
        # ----------------------------------------------------

        max_entry_index = (
            len(indicators) - days
        )

        for i in range(
            max_entry_index
        ):

            current = indicators[i]

            entry_price = current.get(
                "close"
            )

            if entry_price is None:
                continue

            future = indicators[
                i + days
            ]

            exit_price = future.get(
                "close"
            )

            if exit_price is None:
                continue

            if entry_price <= 0:
                continue

            return_pct = (
                (
                    exit_price -
                    entry_price
                )
                /
                entry_price
            ) * 100

            conditions = current[
                "conditions"
            ]

            # ------------------------------------------------
            # 單一條件
            # ------------------------------------------------

            for condition_name in condition_names:

                if conditions.get(
                    condition_name
                ):

                    condition_returns[
                        condition_name
                    ].append(
                        return_pct
                    )

            # ------------------------------------------------
            # 組合條件
            # ------------------------------------------------

            score = current.get(
                "score",
                0
            )

            if score >= 6:
                combination_returns[
                    "all_6"
                ].append(return_pct)

            if score >= 5:
                combination_returns[
                    "at_least_5"
                ].append(return_pct)

            if score >= 4:
                combination_returns[
                    "at_least_4"
                ].append(return_pct)

            if score >= 3:
                combination_returns[
                    "at_least_3"
                ].append(return_pct)

        # ----------------------------------------------------
        # 完成統計
        # ----------------------------------------------------

        result["horizons"][
            horizon_name
        ] = {
            "days": days,
            "conditions": {
                name: finalize_stat(
                    values
                )
                for name, values
                in condition_returns.items()
            },
            "combination
