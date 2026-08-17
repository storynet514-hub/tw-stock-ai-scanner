#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
backtest_winrate.py V2.0

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

回測期間：

30 交易日
60 交易日
90 交易日

統計內容：

單一條件：
- samples
- wins
- losses
- win_rate
- average_return
- median_return
- max_return
- min_return

組合條件：
- 6項全部成立
- 5項以上成立
- 4項以上成立
- 3項以上成立

另外統計：

- 每個條件的歷史訊號數
- 每個條件的勝率
- 每個條件平均報酬
- 各組合條件勝率
- 各組合條件平均報酬

輸出：

Data/backtest_stats.json

============================================================
本程式責任
============================================================

✓ 讀取 prices.json
✓ 計算技術指標
✓ 執行 30/60/90 日回測
✓ 統計勝率
✓ 統計報酬
✓ 建立 backtest_stats.json

============================================================
本程式不負責
============================================================

❌ 不抓價格
❌ 不抓籌碼
❌ 不修改 prices.json
❌ 不修改 universe.json
❌ 不修改 chip.json
❌ 不建立 UI
❌ 不下單

============================================================
資料流程
============================================================

Data/prices.json
        ↓
backtest_winrate.py
        ↓
技術指標
        ↓
30 / 60 / 90 日回測
        ↓
Data/backtest_stats.json
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

VERSION = "V2.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

PRICES_FILE = DATA_DIR / "prices.json"

OUTPUT_FILE = DATA_DIR / "backtest_stats.json"


# ============================================================
# 回測設定
# ============================================================

HORIZONS = {
    "30d": 30,
    "60d": 60,
    "90d": 90,
}


# ============================================================
# 技術指標設定
# ============================================================

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
# 最低歷史資料需求
# ============================================================

MIN_HISTORY_ROWS = 150


# ============================================================
# 條件名稱
# ============================================================

CONDITION_NAMES = [
    "macd_golden_cross",
    "kd_golden_cross",
    "rsi_above_50",
    "volume_above_5ma_1_5x",
    "price_above_ma20",
    "ma20_up",
]


# ============================================================
# 組合名稱
# ============================================================

COMBINATION_NAMES = [
    "all_6",
    "at_least_5",
    "at_least_4",
    "at_least_3",
]


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
# 讀取 prices.json
# ============================================================

def load_prices():

    section("讀取 Data/prices.json")

    if not PRICES_FILE.exists():

        raise FileNotFoundError(
            f"找不到價格資料：{PRICES_FILE}"
        )

    try:

        with PRICES_FILE.open(
            "r",
            encoding="utf-8-sig"
        ) as f:

            data = json.load(f)

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            f"prices.json JSON 格式錯誤：{exc}"
        ) from exc

    if not isinstance(data, dict):

        raise RuntimeError(
            "prices.json 頂層格式錯誤"
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
# SMA
# ============================================================

def sma(values, period):

    result = [None] * len(values)

    if len(values) < period:
        return result

    for i in range(
        period - 1,
        len(values)
    ):

        window = values[
            i - period + 1:
            i + 1
        ]

        if any(
            value is None
            for value in window
        ):
            continue

        result[i] = (
            sum(window) / period
        )

    return result


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    result = [None] * len(values)

    if len(values) < period:
        return result

    # 尋找第一個完整有效 window
    start = None

    for i in range(
        0,
        len(values) - period + 1
    ):

        window = values[
            i:i + period
        ]

        if all(
            value is not None
            for value in window
        ):

            start = i
            break

    if start is None:
        return result

    initial = (
        sum(
            values[
                start:start + period
            ]
        )
        /
        period
    )

    index = start + period - 1

    result[index] = initial

    multiplier = 2 / (period + 1)

    previous = initial

    for i in range(
        index + 1,
        len(values)
    ):

        value = values[i]

        if value is None:

            result[i] = None
            continue

        previous = (
            (
                value - previous
            )
            * multiplier
            +
            previous
        )

        result[i] = previous

    return result


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    closes,
    period=14
):

    result = [None] * len(closes)

    if len(closes) <= period:
        return result

    gains = [None] * len(closes)
    losses = [None] * len(closes)

    for i in range(
        1,
        len(closes)
    ):

        current = closes[i]
        previous = closes[i - 1]

        if (
            current is None
            or previous is None
        ):
            continue

        change = current - previous

        gains[i] = max(
            change,
            0
        )

        losses[i] = max(
            -change,
            0
        )

    first_gain_window = gains[
        1:period + 1
    ]

    first_loss_window = losses[
        1:period + 1
    ]

    if any(
        value is None
        for value in first_gain_window
    ):
        return result

    if any(
        value is None
        for value in first_loss_window
    ):
        return result

    average_gain = (
        sum(first_gain_window)
        /
        period
    )

    average_loss = (
        sum(first_loss_window)
        /
        period
    )

    if average_loss == 0:

        if average_gain > 0:
            result[period] = 100.0
        else:
            result[period] = 50.0

    else:

        rs = (
            average_gain
            /
            average_loss
        )

        result[period] = (
            100
            -
            (
                100
                /
                (1 + rs)
            )
        )

    # Wilder smoothing
    for i in range(
        period + 1,
        len(closes)
    ):

        gain = gains[i]
        loss = losses[i]

        if (
            gain is None
            or loss is None
        ):
            continue

        average_gain = (
            (
                average_gain
                *
                (period - 1)
            )
            +
            gain
        ) / period

        average_loss = (
            (
                average_loss
                *
                (period - 1)
            )
            +
            loss
        ) / period

        if average_loss == 0:

            if average_gain > 0:
                result[i] = 100.0
            else:
                result[i] = 50.0

        else:

            rs = (
                average_gain
                /
                average_loss
            )

            result[i] = (
                100
                -
                (
                    100
                    /
                    (1 + rs)
                )
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

    macd_line = [
        None
    ] * len(closes)

    for i in range(
        len(closes)
    ):

        if (
            fast[i] is not None
            and slow[i] is not None
        ):

            macd_line[i] = (
                fast[i]
                -
                slow[i]
            )

    signal = ema(
        macd_line,
        MACD_SIGNAL
    )

    histogram = [
        None
    ] * len(closes)

    for i in range(
        len(closes)
    ):

        if (
            macd_line[i]
            is not None
            and
            signal[i]
            is not None
        ):

            histogram[i] = (
                macd_line[i]
                -
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

    raw_k = [
        None
    ] * len(closes)

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

        if close is None:
            continue

        if any(
            value is None
            for value in high_window
        ):
            continue

        if any(
            value is None
            for value in low_window
        ):
            continue

        highest = max(
            high_window
        )

        lowest = min(
            low_window
        )

        if highest == lowest:

            raw_k[i] = 50.0

            continue

        raw_k[i] = (
            (
                close
                -
                lowest
            )
            /
            (
                highest
                -
                lowest
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
# 建立技術指標
# ============================================================

def build_indicators(rows):

    dates = []
    highs = []
    lows = []
    closes = []
    volumes = []

    for row in rows:

        dates.append(
            row.get("date")
        )

        highs.append(
            to_float(
                row.get("high")
            )
        )

        lows.append(
            to_float(
                row.get("low")
            )
        )

        closes.append(
            to_float(
                row.get("close")
            )
        )

        volumes.append(
            to_float(
                row.get("volume")
            )
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

    for i in range(
        len(rows)
    ):

        # ====================================================
        # MACD 黃金交叉
        # ====================================================

        macd_golden = False

        if i > 0:

            if (
                macd_line[i]
                is not None
                and
                macd_signal[i]
                is not None
                and
                macd_line[i - 1]
                is not None
                and
                macd_signal[i - 1]
                is not None
            ):

                macd_golden = (
                    macd_line[i]
                    >
                    macd_signal[i]
                    and
                    macd_line[i - 1]
                    <=
                    macd_signal[i - 1]
                )

        # ====================================================
        # KD 黃金交叉
        # ====================================================

        kd_golden = False

        if i > 0:

            if (
                k[i] is not None
                and
                d[i] is not None
                and
                k[i - 1] is not None
                and
                d[i - 1] is not None
            ):

                kd_golden = (
                    k[i]
                    >
                    d[i]
                    and
                    k[i - 1]
                    <=
                    d[i - 1]
                )

        # ====================================================
        # RSI > 50
        # ====================================================

        rsi_condition = (
            rsi[i] is not None
            and
            rsi[i] > 50
        )

        # ====================================================
        # 成交量 > 5日均量 × 1.5
        # ====================================================

        volume_condition = (
            volumes[i] is not None
            and
            volume_ma5[i] is not None
            and
            volumes[i]
            >
            volume_ma5[i]
            *
            VOLUME_MULTIPLIER
        )

        # ====================================================
        # 股價站上 MA20
        # ====================================================

        price_above_ma20 = (
            closes[i] is not None
            and
            ma20[i] is not None
            and
            closes[i] > ma20[i]
        )

        # ====================================================
        # MA20 向上
        # ====================================================

        ma20_up = False

        if i > 0:

            if (
                ma20[i] is not None
                and
                ma20[i - 1] is not None
            ):

                ma20_up = (
                    ma20[i]
                    >
                    ma20[i - 1]
                )

        conditions = {

            "macd_golden_cross":
                macd_golden,

            "kd_golden_cross":
                kd_golden,

            "rsi_above_50":
                rsi_condition,

            "volume_above_5ma_1_5x":
                volume_condition,

            "price_above_ma20":
                price_above_ma20,

            "ma20_up":
                ma20_up,
        }

        score = sum(
            1
            for value
            in conditions.values()
            if value
        )

        indicators.append({

            "date":
                dates[i],

            "close":
                closes[i],

            "ma5":
                ma5[i],

            "ma20":
                ma20[i],

            "volume":
                volumes[i],

            "volume_ma5":
                volume_ma5[i],

            "rsi":
                rsi[i],

            "macd":
                macd_line[i],

            "macd_signal":
                macd_signal[i],

            "macd_histogram":
                macd_histogram[i],

            "k":
                k[i],

            "d":
                d[i],

            "conditions":
                conditions,

            "score":
                score,
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
# 統計完成
# ============================================================

def finalize_stat(
    returns
):

    stat = empty_stat()

    if not returns:
        return stat

    clean_returns = [
        value
        for value in returns
        if value is not None
        and math.isfinite(value)
    ]

    if not clean_returns:
        return stat

    wins = [
        value
        for value in clean_returns
        if value > 0
    ]

    losses = [
        value
        for value in clean_returns
        if value <= 0
    ]

    stat["samples"] = (
        len(clean_returns)
    )

    stat["wins"] = (
        len(wins)
    )

    stat["losses"] = (
        len(losses)
    )

    stat["win_rate"] = round(
        len(wins)
        /
        len(clean_returns)
        *
        100,
        2
    )

    stat["average_return"] = round(
        statistics.mean(
            clean_returns
        ),
        2
    )

    stat["median_return"] = round(
        statistics.median(
            clean_returns
        ),
        2
    )

    stat["max_return"] = round(
        max(clean_returns),
        2
    )

    stat["min_return"] = round(
        min(clean_returns),
        2
    )

    return stat


# ============================================================
# 回測單一股票
# ============================================================

def backtest_stock(
    symbol,
    record
):

    rows = record.get(
        "data",
        []
    )

    if not isinstance(
        rows,
        list
    ):
        return None

    if len(rows) < MIN_HISTORY_ROWS:
        return None

    # --------------------------------------------------------
    # 清理有效資料
    # --------------------------------------------------------

    valid_rows = []

    for row in rows:

        if not isinstance(
            row,
            dict
        ):
            continue

        date = row.get(
            "date"
        )

        close = to_float(
            row.get("close")
        )

        if not date:
            continue

        if close is None:
            continue

        if close <= 0:
            continue

        valid_rows.append(
            row
        )

    if len(valid_rows) < MIN_HISTORY_ROWS:
        return None

    # --------------------------------------------------------
    # 日期排序
    # --------------------------------------------------------

    valid_rows.sort(
        key=lambda x:
        str(x.get("date", ""))
    )

    indicators = build_indicators(
        valid_rows
    )

    result = {

        "symbol":
            symbol,

        "name":
            record.get(
                "name",
                ""
            ),

        "data_start":
            valid_rows[0].get(
                "date"
            ),

        "data_end":
            valid_rows[-1].get(
                "date"
            ),

        "history_days":
            len(valid_rows),

        "horizons":
            {},

    }

    # ========================================================
    # 逐一回測 30 / 60 / 90 日
    # ========================================================

    for horizon_name, days in HORIZONS.items():

        condition_returns = {

            name: []

            for name
            in CONDITION_NAMES
        }

        combination_returns = {

            name: []

            for name
            in COMBINATION_NAMES
        }

        # ----------------------------------------------------
        # 額外統計
        # ----------------------------------------------------

        all_returns = []

        signal_dates = {

            name: []

            for name
            in CONDITION_NAMES
        }

        combination_signal_dates = {

            name: []

            for name
            in COMBINATION_NAMES
        }

        # ----------------------------------------------------
        # 避免未來資料洩漏
        #
        # i = 訊號日
        # i + days = 未來出場日
        # ----------------------------------------------------

        max_entry_index = (
            len(indicators)
            -
            days
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
                    exit_price
                    -
                    entry_price
                )
                /
                entry_price

            ) * 100

            if not math.isfinite(
                return_pct
            ):
                continue

            all_returns.append(
                return_pct
            )

            conditions = current[
                "conditions"
            ]

            # =================================================
            # 單一條件
            # =================================================

            for condition_name in CONDITION_NAMES:

                if conditions.get(
                    condition_name
                ):

                    condition_returns[
                        condition_name
                    ].append(
                        return_pct
                    )

                    signal_dates[
                        condition_name
                    ].append(
                        current.get(
                            "date"
                        )
                    )

            # =================================================
            # 組合條件
            # =================================================

            score = current.get(
                "score",
                0
            )

            if score >= 6:

                combination_returns[
                    "all_6"
                ].append(
                    return_pct
                )

                combination_signal_dates[
                    "all_6"
                ].append(
                    current.get(
                        "date"
                    )
                )

            if score >= 5:

                combination_returns[
                    "at_least_5"
                ].append(
                    return_pct
                )

                combination_signal_dates[
                    "at_least_5"
                ].append(
                    current.get(
                        "date"
                    )
                )

            if score >= 4:

                combination_returns[
                    "at_least_4"
                ].append(
                    return_pct
                )

                combination_signal_dates[
                    "at_least_4"
                ].append(
                    current.get(
                        "date"
                    )
                )

            if score >= 3:

                combination_returns[
                    "at_least_3"
                ].append(
                    return_pct
                )

                combination_signal_dates[
                    "at_least_3"
                ].append(
                    current.get(
                        "date"
                    )
                )

        # ====================================================
        # 統計
        # ====================================================

        horizon_result = {

            "days":
                days,

            "overall":
                finalize_stat(
                    all_returns
                ),

            "conditions": {},

            "combinations": {},
        }

        # ----------------------------------------------------
        # 單一條件
        # ----------------------------------------------------

        for name in CONDITION_NAMES:

            stat = finalize_stat(
                condition_returns[name]
            )

            stat["signal_dates"] = (
                signal_dates[name]
            )

            horizon_result[
                "conditions"
            ][name] = stat

        # ----------------------------------------------------
        # 組合條件
        # ----------------------------------------------------

        for name in COMBINATION_NAMES:

            stat = finalize_stat(
                combination_returns[name]
            )

            stat["signal_dates"] = (
                combination_signal_dates[name]
            )

            horizon_result[
                "combinations"
            ][name] = stat

        result[
            "horizons"
        ][horizon_name] = (
            horizon_result
        )

    return result


# ============================================================
# 建立全市場統計
# ============================================================

def aggregate_results(
    stock_results
):

    section(
        "建立全市場統計"
    )

    market_result = {

        "30d": {
            "overall": [],
            "conditions": {
                name: []
                for name
                in CONDITION_NAMES
            },
            "combinations": {
                name: []
                for name
                in COMBINATION_NAMES
            },
        },

        "60d": {
            "overall": [],
            "conditions": {
                name: []
                for name
                in CONDITION_NAMES
            },
            "combinations": {
                name: []
                for name
                in COMBINATION_NAMES
            },
        },

        "90d": {
            "overall": [],
            "conditions": {
                name: []
                for name
                in CONDITION_NAMES
            },
            "combinations": {
                name: []
                for name
                in COMBINATION_NAMES
            },
        },
    }

    for stock in stock_results.values():

        horizons = stock.get(
            "horizons",
            {}
        )

        for horizon_name in HORIZONS:

            horizon = horizons.get(
                horizon_name,
                {}
            )

            # ------------------------------------------------
            # Overall
            # ------------------------------------------------

            overall = horizon.get(
                "overall",
                {}
            )

            # 這裡不能直接使用平均報酬，
            # 必須重新累積樣本。
            #
            # 因為原本 finalize_stat
            # 已經只保留統計數值。
            #
            # 因此全市場層級只統計
            # 股票層級的平均值。
            # ------------------------------------------------

            # ------------------------------------------------
            # Conditions
            # ------------------------------------------------

            for name in CONDITION_NAMES:

                stat = horizon.get(
                    "conditions",
                    {}
                ).get(
                    name,
                    {}
                )

                samples = stat.get(
                    "samples",
                    0
                )

                wins = stat.get(
                    "wins",
                    0
                )

                average_return = stat.get(
                    "average_return"
                )

                if samples:

                    market_result[
                        horizon_name
                    ][
                        "conditions"
                    ][name].append({

                        "samples":
                            samples,

                        "wins":
                            wins,

                        "losses":
                            stat.get(
                                "losses",
                                0
                            ),

                        "average_return":
                            average_return,

                        "median_return":
                            stat.get(
                                "median_return"
                            ),

                        "max_return":
                            stat.get(
                                "max_return"
                            ),

                        "min_return":
                            stat.get(
                                "min_return"
                            ),
                    })

            # ------------------------------------------------
            # Combinations
            # ------------------------------------------------

            for name in COMBINATION_NAMES:

                stat = horizon.get(
                    "combinations",
                    {}
                ).get(
                    name,
                    {}
                )

                samples = stat.get(
                    "samples",
                    0
                )

                if samples:

                    market_result[
                        horizon_name
                    ][
                        "combinations"
                    ][name].append({

                        "samples":
                            samples,

                        "wins":
                            stat.get(
                                "wins",
                                0
                            ),

                        "losses":
                            stat.get(
                                "losses",
                                0
                            ),

                        "average_return":
                            stat.get(
                                "average_return"
                            ),

                        "median_return":
                            stat.get(
                                "median_return"
                            ),

                        "max_return":
                            stat.get(
                                "max_return"
                            ),

                        "min_return":
                            stat.get(
                                "min_return"
                            ),
                    })

    # ========================================================
    # 完成市場統計
    # ========================================================

    output = {}

    for horizon_name in HORIZONS:

        horizon_output = {

            "days":
                HORIZONS[horizon_name],

            "conditions":
                {},

            "combinations":
                {},
        }

        # ----------------------------------------------------
        # Conditions
        # ----------------------------------------------------

        for name in CONDITION_NAMES:

            entries = (
                market_result[
                    horizon_name
                ][
                    "conditions"
                ][name]
            )

            total_samples = sum(
                item["samples"]
                for item in entries
            )

            total_wins = sum(
                item["wins"]
                for item in entries
            )

            total_losses = sum(
                item["losses"]
                for item in entries
            )

            weighted_return = None

            if total_samples:

                weighted_return = sum(

                    item["average_return"]
                    *
                    item["samples"]

                    for item in entries

                    if item[
                        "average_return"
                    ] is not None

                ) / total_samples

            horizon_output[
                "conditions"
            ][name] = {

                "samples":
                    total_samples,

                "wins":
                    total_wins,

                "losses":
                    total_losses,

                "win_rate":
                    round(
                        total_wins
                        /
                        total_samples
                        *
                        100,
                        2
                    )
                    if total_samples
                    else None,

                "weighted_average_return":
                    round(
                        weighted_return,
                        2
                    )
                    if weighted_return
                    is not None
                    else None,
            }

        # ----------------------------------------------------
        # Combinations
        # ----------------------------------------------------

        for name in COMBINATION_NAMES:

            entries = (
                market_result[
                    horizon_name
                ][
                    "combinations"
                ][name]
            )

            total_samples = sum(
                item["samples"]
                for item in entries
            )

            total_wins = sum(
                item["wins"]
                for item in entries
            )

            total_losses = sum(
                item["losses"]
                for item in entries
            )

            weighted_return = None

            if total_samples:

                weighted_return = sum(

                    item[
                        "average_return"
                    ]
                    *
                    item[
                        "samples"
                    ]

                    for item in entries

                    if item[
                        "average_return"
                    ] is not None

                ) / total_samples

            horizon_output[
                "combinations"
            ][name] = {

                "samples":
                    total_samples,

                "wins":
                    total_wins,

                "losses":
                    total_losses,

                "win_rate":
                    round(
                        total_wins
                        /
                        total_samples
                        *
                        100,
                        2
                    )
                    if total_samples
                    else None,

                "weighted_average_return":
                    round(
                        weighted_return,
                        2
                    )
                    if weighted_return
                    is not None
                    else None,
            }

        output[
            horizon_name
        ] = horizon_output

    return output


# ============================================================
# 建立輸出資料
# ============================================================

def build_output(
    prices_data,
    stock_results,
    market_statistics
):

    return {

        "schema_version":
            "1.0",

        "generator":
            "backtest_winrate.py",

        "generator_version":
            VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            "Data/prices.json",

        "source_generated_at":
            prices_data.get(
                "generated_at"
            ),

        "method": {

            "forward_horizons":
                [30, 60, 90],

            "return_definition":
                "future_close / entry_close - 1",

            "win_definition":
                "future_return > 0",

            "entry":
                "signal_day_close",

            "exit":
                "N_trading_days_later_close",

            "lookahead_bias":
                False,
        },

        "technical_conditions": {

            "macd_golden_cross":
                "MACD line crosses above signal line",

            "kd_golden_cross":
                "K crosses above D",

            "rsi_above_50":
                "RSI > 50",

            "volume_above_5ma_1_5x":
                "Volume > MA5 volume × 1.5",

            "price_above_ma20":
                "Close > MA20",

            "ma20_up":
                "MA20[t] > MA20[t-1]",
        },

        "universe": {

            "source_count":
                len(
                    prices_data.get(
                        "prices",
                        {}
                    )
                ),

            "backtested_count":
                len(
                    stock_results
                ),
        },

        "market_statistics":
            market_statistics,

        "stocks":
            stock_results,
    }


# ============================================================
# 儲存 JSON
# ============================================================

def save_output(
    output
):

    section(
        "寫入 Data/backtest_stats.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

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
            indent=2,
            allow_nan=False
        )

    temp_file.replace(
        OUTPUT_FILE
    )

    size = OUTPUT_FILE.stat().st_size

    log(
        "✓ backtest_stats.json 建立成功"
    )

    log(
        f"檔案大小："
        f"{size / 1024:.2f} KB"
    )


# ============================================================
# 顯示摘要
# ============================================================

def print_summary(
    market_statistics
):

    section(
        "30 / 60 / 90 日回測摘要"
    )

    for horizon_name in [
        "30d",
        "60d",
        "90d"
    ]:

        horizon = market_statistics[
            horizon_name
        ]

        log("")
        log(
            f"【{horizon_name}】"
        )

        log(
            "條件                 "
            "Samples   Wins   Win Rate"
        )

        log(
            "-" * 64
        )

        for name in CONDITION_NAMES:

            stat = horizon[
                "conditions"
            ][name]

            log(

                f"{name:<26}"

                f"{stat['samples']:>8}"

                f"{stat['wins']:>7}"

                f"{str(stat['win_rate']) + '%':>11}"
                if stat["win_rate"]
                is not None

                else

                f"{name:<26}"

                f"{stat['samples']:>8}"

                f"{stat['wins']:>7}"

                f"{'N/A':>11}"

            )

        log("")

        for name in COMBINATION_NAMES:

            stat = horizon[
                "combinations"
            ][name]

            log(

                f"{name:<26}"

                f"{stat['samples']:>8}"

                f"{stat['wins']:>7}"

                f"{str(stat['win_rate']) + '%':>11}"
                if stat["win_rate"]
                is not None

                else

                f"{name:<26}"

                f"{stat['samples']:>8}"

                f"{stat['wins']:>7}"

                f"{'N/A':>11}"

            )


# ============================================================
# 主程式
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 64)
    log(
        f"台股 AI 選股系統 "
        f"backtest_winrate.py {VERSION}"
    )
    log("=" * 64)

    log(
        "開始時間："
        +
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        # ====================================================
        # 1. 讀取價格
        # ====================================================

        prices_data = load_prices()

        prices = prices_data.get(
            "prices",
            {}
        )

        # ====================================================
        # 2. 回測
        # ====================================================

        section(
            "開始 30 / 60 / 90 日歷史回測"
        )

        stock_results = {}

        total = len(prices)

        skipped = 0

        for index, (
            symbol,
            record
        ) in enumerate(
            prices.items(),
            start=1
        ):

            if index % 50 == 0:

                log(
                    f"進度："
                    f"{index}/{total}"
                )

            try:

                result = backtest_stock(
                    symbol,
                    record
                )

                if result is None:

                    skipped += 1

                    continue

                stock_results[
                    symbol
                ] = result

            except Exception as exc:

                skipped += 1

                log(
                    f"⚠️ {symbol} 回測失敗："
                    f"{exc}"
                )

        log("")
        log(
            f"價格股票：{total}"
        )

        log(
            f"成功回測："
            f"{len(stock_results)}"
        )

        log(
            f"跳過：{skipped}"
        )

        if not stock_results:

            raise RuntimeError(
                "沒有任何股票成功完成回測"
            )

        # ====================================================
        # 3. 全市場統計
        # ====================================================

        market_statistics = (
            aggregate_results(
                stock_results
            )
        )

        # ====================================================
        # 4. 建立輸出
        # ====================================================

        output = build_output(

            prices_data,

            stock_results,

            market_statistics
        )

        # ====================================================
        # 5. 儲存
        # ====================================================

        save_output(
            output
        )

        # ====================================================
        # 6. 摘要
        # ====================================================

        print_summary(
            market_statistics
        )

        elapsed = (
            time.time()
            -
            start_time
        )

        log("")
        log("=" * 64)
        log(
            "✓ backtest_winrate.py "
            "執行完成"
        )
        log("=" * 64)

        log(
            f"成功回測股票："
            f"{len(stock_results)}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出："
            f"{OUTPUT_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 64)
        log(
            "❌ backtest_winrate.py "
            "執行失敗"
        )
        log("=" * 64)

        log(
            f"原因：{exc}"
        )

        return 1


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
