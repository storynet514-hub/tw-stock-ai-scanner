# -*- coding: utf-8 -*-

"""
台股 AI 選股・零股定投・動態風控資料引擎
============================================================

V3 正式版

主要功能：

1. 台股個股 + ETF
2. 技術指標
   - MA5
   - MA10
   - MA20
   - MA60
   - RSI
   - MACD
   - KD
   - 成交量
   - 20MA 斜率

3. 短期選股條件
   - MACD 黃金交叉
   - KD 黃金交叉
   - RSI > 50
   - 成交量 > 5日均量 × 1.5
   - 股價站上20MA
   - 20MA向上

4. 短線評分

5. 零股定投
   - 第一區
   - 第二區
   - 第三區
   - 第四區

6. 動態風控
   - 停損
   - 第一停利
   - 第二停利

7. 歷史勝率
   - 30日
   - 60日
   - 90日

8. 個股 / ETF 分類排名

9. 輸出：
   Data/prices.json

資料來源：
Yahoo Finance

============================================================
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("錯誤：找不到 yfinance")
    print("請確認 requirements.txt 已加入 yfinance")
    sys.exit(1)


# ============================================================
# 基本設定
# ============================================================

TAIPEI_TZ = timezone(timedelta(hours=8))

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DATA_DIR = os.path.join(
    BASE_DIR,
    "Data"
)

OUTPUT_FILE = os.path.join(
    DATA_DIR,
    "prices.json"
)

os.makedirs(
    DATA_DIR,
    exist_ok=True
)


# ============================================================
# 股票 / ETF 清單
# ============================================================
#
# type：
#
# STOCK = 個股
# ETF   = ETF
#
# 未來如果要增加標的，
# 只需要在這裡增加即可。
#
# ============================================================

STOCKS = {

    # --------------------------------------------------------
    # 大型 / 核心
    # --------------------------------------------------------

    "2330": {
        "name": "台積電",
        "type": "STOCK"
    },

    "2317": {
        "name": "鴻海",
        "type": "STOCK"
    },

    "2454": {
        "name": "聯發科",
        "type": "STOCK"
    },

    "2303": {
        "name": "聯電",
        "type": "STOCK"
    },

    "2382": {
        "name": "廣達",
        "type": "STOCK"
    },

    "3231": {
        "name": "緯創",
        "type": "STOCK"
    },

    "6669": {
        "name": "緯穎",
        "type": "STOCK"
    },

    # --------------------------------------------------------
    # AI / 半導體 / 電子
    # --------------------------------------------------------

    "3037": {
        "name": "欣興",
        "type": "STOCK"
    },

    "3044": {
        "name": "健鼎",
        "type": "STOCK"
    },

    "2449": {
        "name": "京元電子",
        "type": "STOCK"
    },

    "2301": {
        "name": "光寶科",
        "type": "STOCK"
    },

    "2376": {
        "name": "技嘉",
        "type": "STOCK"
    },

    "2421": {
        "name": "建準",
        "type": "STOCK"
    },

    "3324": {
        "name": "雙鴻",
        "type": "STOCK"
    },

    "3017": {
        "name": "奇鋐",
        "type": "STOCK"
    },

    "3450": {
        "name": "聯鈞",
        "type": "STOCK"
    },

    "3338": {
        "name": "泰碩",
        "type": "STOCK"
    },

    "6176": {
        "name": "瑞儀",
        "type": "STOCK"
    },

    "3042": {
        "name": "晶技",
        "type": "STOCK"
    },

    "6290": {
        "name": "良維",
        "type": "STOCK"
    },

    "3006": {
        "name": "晶豪科",
        "type": "STOCK"
    },

    "2426": {
        "name": "鼎元",
        "type": "STOCK"
    },

    "3490": {
        "name": "單井",
        "type": "STOCK"
    },

    "6125": {
        "name": "廣運",
        "type": "STOCK"
    },

    "2425": {
        "name": "承啟",
        "type": "STOCK"
    },

    # --------------------------------------------------------
    # 其他
    # --------------------------------------------------------

    "3356": {
        "name": "奇偶",
        "type": "STOCK"
    },

    "6117": {
        "name": "迎廣",
        "type": "STOCK"
    },

    "3481": {
        "name": "群創",
        "type": "STOCK"
    },

    "2409": {
        "name": "友達",
        "type": "STOCK"
    },

    # ========================================================
    # ETF
    # ========================================================

    "0050": {
        "name": "元大台灣50",
        "type": "ETF"
    },

    "0056": {
        "name": "元大高股息",
        "type": "ETF"
    },

    "00878": {
        "name": "國泰永續高股息",
        "type": "ETF"
    },

    "00919": {
        "name": "群益台灣精選高息",
        "type": "ETF"
    },

    "00713": {
        "name": "元大台灣高息低波",
        "type": "ETF"
    },

    "00929": {
        "name": "復華台灣科技優息",
        "type": "ETF"
    },

    "00940": {
        "name": "元大台灣價值高息",
        "type": "ETF"
    },

    "006208": {
        "name": "富邦台50",
        "type": "ETF"
    },

    "00679B": {
        "name": "元大美債20年",
        "type": "ETF"
    },

    "00830": {
        "name": "國泰費城半導體",
        "type": "ETF"
    }
}


# ============================================================
# 技術指標設定
# ============================================================

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

RSI_PERIOD = 14

KD_PERIOD = 9
KD_SMOOTH_K = 3
KD_SMOOTH_D = 3

MA5_PERIOD = 5
MA10_PERIOD = 10
MA20_PERIOD = 20
MA60_PERIOD = 60

VOLUME_PERIOD = 5

VOLUME_MULTIPLIER = 1.5


# ============================================================
# 回測設定
# ============================================================

BACKTEST_DAYS = [
    30,
    60,
    90
]

# 勝率判斷：
#
# 在訊號出現後 N 個交易日，
# 只要價格高於訊號日價格，
# 就視為成功。
#
# 同時另外計算平均報酬率。
#

MIN_BACKTEST_DATA = 120


# ============================================================
# 工具
# ============================================================

def clean_number(value, digits=4):

    if value is None:
        return None

    try:

        value = float(value)

        if math.isnan(value):
            return None

        if math.isinf(value):
            return None

        return round(
            value,
            digits
        )

    except Exception:

        return None


def safe_bool(value):

    try:
        return bool(value)

    except Exception:

        return False


def get_ticker(stock_id):

    return f"{stock_id}.TW"


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    close,
    period=RSI_PERIOD
):

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = (
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    return rsi


# ============================================================
# MACD
# ============================================================

def calculate_macd(close):

    ema_fast = close.ewm(
        span=MACD_FAST,
        adjust=False
    ).mean()

    ema_slow = close.ewm(
        span=MACD_SLOW,
        adjust=False
    ).mean()

    dif = (
        ema_fast
        -
        ema_slow
    )

    dem = dif.ewm(
        span=MACD_SIGNAL,
        adjust=False
    ).mean()

    histogram = (
        dif
        -
        dem
    )

    return (
        dif,
        dem,
        histogram
    )


# ============================================================
# KD
# ============================================================

def calculate_kd(df):

    lowest_low = (
        df["Low"]
        .rolling(
            KD_PERIOD
        )
        .min()
    )

    highest_high = (
        df["High"]
        .rolling(
            KD_PERIOD
        )
        .max()
    )

    denominator = (
        highest_high
        -
        lowest_low
    )

    rsv = (
        (
            df["Close"]
            -
            lowest_low
        )
        /
        denominator.replace(
            0,
            np.nan
        )
    ) * 100

    k = rsv.ewm(
        alpha=1 / KD_SMOOTH_K,
        adjust=False
    ).mean()

    d = k.ewm(
        alpha=1 / KD_SMOOTH_D,
        adjust=False
    ).mean()

    return (
        k,
        d
    )


# ============================================================
# 技術指標
# ============================================================

def calculate_indicators(df):

    df = df.copy()

    # --------------------------------------------------------
    # 均線
    # --------------------------------------------------------

    df["MA5"] = (
        df["Close"]
        .rolling(
            MA5_PERIOD
        )
        .mean()
    )

    df["MA10"] = (
        df["Close"]
        .rolling(
            MA10_PERIOD
        )
        .mean()
    )

    df["MA20"] = (
        df["Close"]
        .rolling(
            MA20_PERIOD
        )
        .mean()
    )

    df["MA60"] = (
        df["Close"]
        .rolling(
            MA60_PERIOD
        )
        .mean()
    )

    # --------------------------------------------------------
    # MA20斜率
    # --------------------------------------------------------

    df["MA20_PREV"] = (
        df["MA20"]
        .shift(1)
    )

    df["MA20_SLOPE"] = (
        (
            df["MA20"]
            -
            df["MA20_PREV"]
        )
        /
        df["MA20_PREV"]
    ) * 100

    # --------------------------------------------------------
    # 成交量
    # --------------------------------------------------------

    df["VOLUME_MA5"] = (
        df["Volume"]
        .rolling(
            VOLUME_PERIOD
        )
        .mean()
    )

    df["VOLUME_RATIO"] = (
        df["Volume"]
        /
        df["VOLUME_MA5"]
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    df["RSI"] = calculate_rsi(
        df["Close"]
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    (
        df["DIF"],
        df["DEM"],
        df["MACD_HIST"]
    ) = calculate_macd(
        df["Close"]
    )

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    (
        df["K"],
        df["D"]
    ) = calculate_kd(
        df
    )

    # --------------------------------------------------------
    # MACD黃金交叉
    # --------------------------------------------------------

    df["MACD_GOLDEN"] = (
        (df["DIF"] > df["DEM"])
        &
        (
            df["DIF"].shift(1)
            <=
            df["DEM"].shift(1)
        )
    )

    # --------------------------------------------------------
    # MACD柱狀圖翻紅
    # --------------------------------------------------------

    df["MACD_RED"] = (
        df["MACD_HIST"] > 0
    )

    # --------------------------------------------------------
    # KD黃金交叉
    # --------------------------------------------------------

    df["KD_GOLDEN"] = (
        (df["K"] > df["D"])
        &
        (
            df["K"].shift(1)
            <=
            df["D"].shift(1)
        )
    )

    # --------------------------------------------------------
    # KD低位黃金交叉
    # --------------------------------------------------------

    df["KD_LOW_GOLDEN"] = (
        df["KD_GOLDEN"]
        &
        (
            df["K"].shift(1)
            <= 30
        )
    )

    # --------------------------------------------------------
    # RSI多方
    # --------------------------------------------------------

    df["RSI_BULLISH"] = (
        df["RSI"] > 50
    )

    # --------------------------------------------------------
    # 成交量突破
    # --------------------------------------------------------

    df["VOLUME_BREAKOUT"] = (
        df["Volume"]
        >
        (
            df["VOLUME_MA5"]
            *
            VOLUME_MULTIPLIER
        )
    )

    # --------------------------------------------------------
    # 站上20MA
    # --------------------------------------------------------

    df["ABOVE_MA20"] = (
        df["Close"]
        >
        df["MA20"]
    )

    # --------------------------------------------------------
    # 20MA向上
    # --------------------------------------------------------

    df["MA20_UP"] = (
        df["MA20"]
        >
        df["MA20"].shift(1)
    )

    # --------------------------------------------------------
    # 20MA向上勾
    # --------------------------------------------------------

    df["MA20_TURN_UP"] = (
        (df["MA20"] > df["MA20"].shift(1))
        &
        (
            df["MA20"].shift(1)
            >=
            df["MA20"].shift(2)
        )
    )

    # --------------------------------------------------------
    # 短期核心
    # --------------------------------------------------------

    df["SHORT_TERM_CORE"] = (
        df["MACD_GOLDEN"]
        &
        df["KD_GOLDEN"]
        &
        df["RSI_BULLISH"]
        &
        df["VOLUME_BREAKOUT"]
        &
        df["ABOVE_MA20"]
        &
        df["MA20_UP"]
    )

    return df


# ============================================================
# 短線評分
# ============================================================

def calculate_short_score(row):

    score = 0

    reasons = []

    # MACD
    if safe_bool(
        row["MACD_GOLDEN"]
    ):

        score += 25

        reasons.append(
            "MACD黃金交叉"
        )

    elif safe_bool(
        row["MACD_RED"]
    ):

        score += 10

        reasons.append(
            "MACD柱狀圖翻紅"
        )

    # KD
    if safe_bool(
        row["KD_LOW_GOLDEN"]
    ):

        score += 25

        reasons.append(
            "KD低位黃金交叉"
        )

    elif safe_bool(
        row["KD_GOLDEN"]
    ):

        score += 20

        reasons.append(
            "KD黃金交叉"
        )

    elif (
        pd.notna(row["K"])
        and
        pd.notna(row["D"])
        and
        row["K"] > row["D"]
    ):

        score += 8

        reasons.append(
            "KD多方"
        )

    # RSI
    rsi = row["RSI"]

    if pd.notna(rsi):

        if rsi >= 55:

            score += 15

            reasons.append(
                "RSI多方"
            )

        elif rsi > 50:

            score += 10

            reasons.append(
                "RSI站上50"
            )

    # Volume
    volume_ratio = (
        row["VOLUME_RATIO"]
    )

    if pd.notna(
        volume_ratio
    ):

        if volume_ratio >= 2:

            score += 20

            reasons.append(
                "成交量爆量"
            )

        elif volume_ratio >= 1.5:

            score += 15

            reasons.append(
                "成交量放大"
            )

        elif volume_ratio >= 1.2:

            score += 5

    # MA20
    if safe_bool(
        row["ABOVE_MA20"]
    ):

        score += 8

        reasons.append(
            "站上20MA"
        )

    # MA20 up
    if safe_bool(
        row["MA20_UP"]
    ):

        score += 5

        reasons.append(
            "20MA向上"
        )

    # MA20 turn
    if safe_bool(
        row["MA20_TURN_UP"]
    ):

        score += 7

        reasons.append(
            "20MA向上勾"
        )

    return (
        score,
        reasons
    )


# ============================================================
# 定投策略
# ============================================================

def calculate_dca_strategy(
    close,
    ma20,
    ma60
):

    if close is None:

        return {
            "status": "資料不足",
            "action": "觀望",
            "buy_1": None,
            "buy_2": None,
            "buy_3": None,
            "buy_4": None
        }

    reference = (
        ma20
        if ma20 is not None
        else close
    )

    buy_1 = reference * 0.99
    buy_2 = reference * 0.97
    buy_3 = reference * 0.94
    buy_4 = reference * 0.90

    if ma60 is not None:

        buy_4 = min(
            buy_4,
            ma60 * 1.02
        )

    if close >= buy_1:

        status = (
            "高於第一定投區"
        )

        action = (
            "觀察 / 小額"
        )

    elif close >= buy_2:

        status = (
            "第一定投區"
        )

        action = (
            "第一批"
        )

    elif close >= buy_3:

        status = (
            "第二定投區"
        )

        action = (
            "第二批"
        )

    elif close >= buy_4:

        status = (
            "第三定投區"
        )

        action = (
            "第三批"
        )

    else:

        status = (
            "深度回撤區"
        )

        action = (
            "第四批 / 等待確認"
        )

    return {

        "status": status,

        "action": action,

        "buy_1": clean_number(
            buy_1,
            2
        ),

        "buy_2": clean_number(
            buy_2,
            2
        ),

        "buy_3": clean_number(
            buy_3,
            2
        ),

        "buy_4": clean_number(
            buy_4,
            2
        )
    }


# ============================================================
# 動態風控
# ============================================================

def calculate_risk_control(
    close,
    ma20,
    ma60,
    ma20_up
):

    if close is None:

        return {

            "risk_level": "未知",

            "stop_loss": None,

            "take_profit_1": None,

            "take_profit_2": None,

            "risk_reward_1": None,

            "risk_reward_2": None
        }

    # --------------------------------------------------------
    # 基礎停損
    # --------------------------------------------------------

    stop_loss = (
        close * 0.93
    )

    if (
        ma20 is not None
        and
        close < ma20
    ):

        stop_loss = (
            close * 0.95
        )

    # --------------------------------------------------------
    # 停利
    # --------------------------------------------------------

    take_profit_1 = (
        close * 1.08
    )

    take_profit_2 = (
        close * 1.15
    )

    # --------------------------------------------------------
    # 風險
    # --------------------------------------------------------

    if ma20 is None:

        risk_level = "資料不足"

    elif (
        close >= ma20
        and
        ma20_up
    ):

        risk_level = "低～中"

    elif close >= ma20:

        risk_level = "中"

    else:

        risk_level = "中～高"

    # --------------------------------------------------------
    # 報酬風險比
    # --------------------------------------------------------

    risk_amount = (
        close
        -
        stop_loss
    )

    if risk_amount > 0:

        rr1 = (
            take_profit_1
            -
            close
        ) / risk_amount

        rr2 = (
            take_profit_2
            -
            close
        ) / risk_amount

    else:

        rr1 = None
        rr2 = None

    return {

        "risk_level": risk_level,

        "stop_loss": clean_number(
            stop_loss,
            2
        ),

        "take_profit_1": clean_number(
            take_profit_1,
            2
        ),

        "take_profit_2": clean_number(
            take_profit_2,
            2
        ),

        "risk_reward_1": clean_number(
            rr1,
            2
        ),

        "risk_reward_2": clean_number(
            rr2,
            2
        )
    }


# ============================================================
# 歷史勝率回測
# ============================================================

def calculate_backtest(
    df,
    days_list=BACKTEST_DAYS
):

    result = {}

    # --------------------------------------------------------
    # 只使用完整資料
    # --------------------------------------------------------

    if (
        df is None
        or
        len(df) < MIN_BACKTEST_DATA
    ):

        for days in days_list:

            result[str(days)] = {

                "days": days,

                "signals": 0,

                "wins": 0,

                "losses": 0,

                "win_rate": None,

                "average_return": None,

                "best_return": None,

                "worst_return": None
            }

        return result

    # --------------------------------------------------------
    # 核心訊號
    # --------------------------------------------------------

    signal_index = (
        df.index[
            df["SHORT_TERM_CORE"]
            .fillna(False)
        ]
    )

    for days in days_list:

        returns = []

        for index in signal_index:

            try:

                position = (
                    df.index.get_loc(
                        index
                    )
                )

                future_position = (
                    position + days
                )

                if (
                    future_position
                    >= len(df)
                ):

                    continue

                entry_price = float(
                    df.iloc[
                        position
                    ]["Close"]
                )

                future_price = float(
                    df.iloc[
                        future_position
                    ]["Close"]
                )

                if (
                    entry_price <= 0
                    or
                    future_price <= 0
                ):

                    continue

                return_pct = (
                    (
                        future_price
                        -
                        entry_price
                    )
                    /
                    entry_price
                ) * 100

                returns.append(
                    return_pct
                )

            except Exception:

                continue

        signals = len(
            returns
        )

        wins = sum(
            1
            for value in returns
            if value > 0
        )

        losses = sum(
            1
            for value in returns
            if value <= 0
        )

        if signals > 0:

            win_rate = (
                wins
                /
                signals
            ) * 100

            average_return = (
                sum(returns)
                /
                signals
            )

            best_return = max(
                returns
            )

            worst_return = min(
                returns
            )

        else:

            win_rate = None

            average_return = None

            best_return = None

            worst_return = None

        result[str(days)] = {

            "days": days,

            "signals": signals,

            "wins": wins,

            "losses": losses,

            "win_rate": clean_number(
                win_rate,
                2
            ),

            "average_return": clean_number(
                average_return,
                2
            ),

            "best_return": clean_number(
                best_return,
                2
            ),

            "worst_return": clean_number(
                worst_return,
                2
            )
        }

    return result


# ============================================================
# 股票分析
# ============================================================

def analyze_stock(
    stock_id,
    stock_info
):

    stock_name = stock_info[
        "name"
    ]

    asset_type = stock_info[
        "type"
    ]

    ticker = get_ticker(
        stock_id
    )

    print(
        f"正在分析："
        f"{stock_id} "
        f"{stock_name} "
        f"[{asset_type}]"
    )

    try:

        df = yf.download(
            ticker,
            period="2y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False
        )

    except Exception as e:

        print(
            f"  抓取失敗：{e}"
        )

        return None

    if (
        df is None
        or
        df.empty
    ):

        print(
            "  無資料"
        )

        return None

    # --------------------------------------------------------
    # Yahoo 多層欄位
    # --------------------------------------------------------

    if isinstance(
        df.columns,
        pd.MultiIndex
    ):

        try:

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        except Exception:

            pass

    required_columns = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]

    for column in required_columns:

        if column not in df.columns:

            print(
                f"  缺少欄位：{column}"
            )

            return None

    # --------------------------------------------------------
    # 清理
    # --------------------------------------------------------

    df = df[
        required_columns
    ].copy()

    for column in required_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "Close"
        ]
    )

    if len(df) < 120:

        print(
            "  歷史資料不足"
        )

        return None

    # --------------------------------------------------------
    # 技術指標
    # --------------------------------------------------------

    df = calculate_indicators(
        df
    )

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    # --------------------------------------------------------
    # 價格
    # --------------------------------------------------------

    close = clean_number(
        latest["Close"],
        2
    )

    open_price = clean_number(
        latest["Open"],
        2
    )

    high = clean_number(
        latest["High"],
        2
    )

    low = clean_number(
        latest["Low"],
        2
    )

    volume = clean_number(
        latest["Volume"],
        0
    )

    previous_close = clean_number(
        previous["Close"],
        2
    )

    change = None
    change_percent = None

    if (
        close is not None
        and
        previous_close is not None
        and
        previous_close != 0
    ):

        change = clean_number(
            close - previous_close,
            2
        )

        change_percent = clean_number(
            (
                (
                    close
                    -
                    previous_close
                )
                /
                previous_close
            ) * 100,
            2
        )

    # --------------------------------------------------------
    # 技術指標
    # --------------------------------------------------------

    ma5 = clean_number(
        latest["MA5"],
        2
    )

    ma10 = clean_number(
        latest["MA10"],
        2
    )

    ma20 = clean_number(
        latest["MA20"],
        2
    )

    ma60 = clean_number(
        latest["MA60"],
        2
    )

    rsi = clean_number(
        latest["RSI"],
        2
    )

    dif = clean_number(
        latest["DIF"],
        4
    )

    dem = clean_number(
        latest["DEM"],
        4
    )

    macd_hist = clean_number(
        latest["MACD_HIST"],
        4
    )

    k = clean_number(
        latest["K"],
        2
    )

    d = clean_number(
        latest["D"],
        2
    )

    volume_ma5 = clean_number(
        latest["VOLUME_MA5"],
        0
    )

    volume_ratio = clean_number(
        latest["VOLUME_RATIO"],
        2
    )

    ma20_slope = clean_number(
        latest["MA20_SLOPE"],
        2
    )

    # --------------------------------------------------------
    # 條件
    # --------------------------------------------------------

    macd_golden = safe_bool(
        latest["MACD_GOLDEN"]
    )

    macd_red = safe_bool(
        latest["MACD_RED"]
    )

    kd_golden = safe_bool(
        latest["KD_GOLDEN"]
    )

    kd_low_golden = safe_bool(
        latest["KD_LOW_GOLDEN"]
    )

    rsi_bullish = safe_bool(
        latest["RSI_BULLISH"]
    )

    volume_breakout = safe_bool(
        latest["VOLUME_BREAKOUT"]
    )

    above_ma20 = safe_bool(
        latest["ABOVE_MA20"]
    )

    ma20_up = safe_bool(
        latest["MA20_UP"]
    )

    ma20_turn_up = safe_bool(
        latest["MA20_TURN_UP"]
    )

    short_term_core = safe_bool(
        latest["SHORT_TERM_CORE"]
    )

    # --------------------------------------------------------
    # 評分
    # --------------------------------------------------------

    score, reasons = (
        calculate_short_score(
            latest
        )
    )

    # --------------------------------------------------------
    # 訊號
    # --------------------------------------------------------

    if short_term_core:

        signal = "強勢啟動"

    elif score >= 75:

        signal = "高度關注"

    elif score >= 60:

        signal = "偏多"

    elif score >= 45:

        signal = "觀察"

    elif score >= 30:

        signal = "弱勢"

    else:

        signal = "不符合"

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

    dca = (
        calculate_dca_strategy(
            close,
            ma20,
            ma60
        )
    )

    # --------------------------------------------------------
    # Risk
    # --------------------------------------------------------

    risk = (
        calculate_risk_control(
            close,
            ma20,
            ma60,
            ma20_up
        )
    )

    # --------------------------------------------------------
    # Backtest
    # --------------------------------------------------------

    backtest = (
        calculate_backtest(
            df
        )
    )

    # --------------------------------------------------------
    # 回測摘要
    # --------------------------------------------------------

    valid_win_rates = []

    for days in BACKTEST_DAYS:

        item = backtest[
            str(days)
        ]

        if item[
            "win_rate"
        ] is not None:

            valid_win_rates.append(
                item["win_rate"]
            )

    if valid_win_rates:

        overall_win_rate = (
            sum(valid_win_rates)
            /
            len(valid_win_rates)
        )

    else:

        overall_win_rate = None

    # --------------------------------------------------------
    # 最終資料
    # --------------------------------------------------------

    result = {

        "id": stock_id,

        "symbol": ticker,

        "name": stock_name,

        "asset_type": asset_type,

        "date": str(
            df.index[-1].date()
        ),

        "price": {

            "open": open_price,

            "high": high,

            "low": low,

            "close": close,

            "previous_close": previous_close,

            "change": change,

            "change_percent": change_percent,

            "volume": volume
        },

        "technical": {

            "ma5": ma5,

            "ma10": ma10,

            "ma20": ma20,

            "ma60": ma60,

            "ma20_slope": ma20_slope,

            "rsi": rsi,

            "dif": dif,

            "dem": dem,

            "macd_hist": macd_hist,

            "k": k,

            "d": d,

            "volume_ma5": volume_ma5,

            "volume_ratio": volume_ratio
        },

        "conditions": {

            "macd_golden_cross":
                macd_golden,

            "macd_red":
                macd_red,

            "kd_golden_cross":
                kd_golden,

            "kd_low_golden_cross":
                kd_low_golden,

            "rsi_above_50":
                rsi_bullish,

            "volume_over_1_5x":
                volume_breakout,

            "above_ma20":
                above_ma20,

            "ma20_up":
                ma20_up,

            "ma20_turn_up":
                ma20_turn_up,

            "short_term_core":
                short_term_core
        },

        "short_term": {

            "score": score,

            "max_score": 120,

            "signal": signal,

            "reasons": reasons,

            "core_conditions": {

                "macd":
                    macd_golden,

                "kd":
                    kd_golden,

                "rsi":
                    rsi_bullish,

                "volume":
                    volume_breakout,

                "ma20":
                    above_ma20,

                "ma20_up":
                    ma20_up
            }
        },

        "backtest": {

            "overall_win_rate":
                clean_number(
                    overall_win_rate,
                    2
                ),

            "30d":
                backtest["30"],

            "60d":
                backtest["60"],

            "90d":
                backtest["90"]
        },

        "dca": dca,

        "risk_control": risk,

        "strategy": {

            "short_term": (
                "符合全部核心條件"
                if short_term_core
                else
                "尚未完全符合短期核心條件"
            ),

            "dca_action":
                dca["action"],

            "risk_level":
                risk["risk_level"]
        }
    }

    print(
        f"  收盤：{close} | "
        f"評分：{score} | "
        f"訊號：{signal} | "
        f"30D勝率："
        f"{backtest['30']['win_rate']}"
    )

    return result


# ============================================================
# 全部股票
# ============================================================

def fetch_all_stocks():

    results = []

    total = len(
        STOCKS
    )

    print("")
    print("=" * 70)
    print("開始抓取台股個股 + ETF")
    print(
        f"標的數量：{total}"
    )
    print("=" * 70)
    print("")

    for index, (
        stock_id,
        stock_info
    ) in enumerate(
        STOCKS.items(),
        start=1
    ):

        print(
            f"[{index}/{total}]"
        )

        try:

            result = analyze_stock(
                stock_id,
                stock_info
            )

            if result is not None:

                results.append(
                    result
                )

        except Exception as e:

            print(
                f"  分析錯誤：{e}"
            )

        # Yahoo API 節流
        time.sleep(
            0.4
        )

    return results


# ============================================================
# 排名
# ============================================================

def create_rankings(
    results
):

    # --------------------------------------------------------
    # 全部短線排名
    # --------------------------------------------------------

    short_term = sorted(
        results,
        key=lambda x: (
            x["short_term"]["score"],
            x["technical"][
                "volume_ratio"
            ] or 0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # 個股排名
    # --------------------------------------------------------

    stocks = [
        item
        for item in results
        if item["asset_type"]
        == "STOCK"
    ]

    stocks = sorted(
        stocks,
        key=lambda x: (
            x["short_term"]["score"],
            x["technical"][
                "volume_ratio"
            ] or 0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # ETF排名
    # --------------------------------------------------------

    etfs = [
        item
        for item in results
        if item["asset_type"]
        == "ETF"
    ]

    etfs = sorted(
        etfs,
        key=lambda x: (
            x["short_term"]["score"],
            x["technical"][
                "volume_ratio"
            ] or 0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # 核心條件
    # --------------------------------------------------------

    core = [
        item
        for item in results
        if item["conditions"][
            "short_term_core"
        ]
    ]

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

    dca = sorted(
        results,
        key=lambda x: (
            x["short_term"]["score"],
            x["technical"][
                "ma20_slope"
            ] or 0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # 回測勝率
    # --------------------------------------------------------

    backtest = sorted(
        results,
        key=lambda x: (
            x["backtest"][
                "overall_win_rate"
            ]
            if
            x["backtest"][
                "overall_win_rate"
            ] is not None
            else -1
        ),
        reverse=True
    )

    return {

        "short_term": [
            item["id"]
            for item in short_term[:10]
        ],

        "stocks": [
            item["id"]
            for item in stocks[:10]
        ],

        "etf": [
            item["id"]
            for item in etfs[:10]
        ],

        "core": [
            item["id"]
            for item in core
        ],

        "dca": [
            item["id"]
            for item in dca[:10]
        ],

        "backtest": [
            item["id"]
            for item in backtest[:10]
        ]
    }


# ============================================================
# 系統統計
# ============================================================

def create_statistics(
    results
):

    total = len(
        results
    )

    stock_count = sum(
        1
        for item in results
        if item["asset_type"]
        == "STOCK"
    )

    etf_count = sum(
        1
        for item in results
        if item["asset_type"]
        == "ETF"
    )

    core_count = sum(
        1
        for item in results
        if item["conditions"][
            "short_term_core"
        ]
    )

    macd_count = sum(
        1
        for item in results
        if item["conditions"][
            "macd_golden_cross"
        ]
    )

    kd_count = sum(
        1
        for item in results
        if item["conditions"][
            "kd_golden_cross"
        ]
    )

    rsi_count = sum(
        1
        for item in results
        if item["conditions"][
            "rsi_above_50"
        ]
    )

    volume_count = sum(
        1
        for item in results
        if item["conditions"][
            "volume_over_1_5x"
        ]
    )

    ma20_count = sum(
        1
        for item in results
        if item["conditions"][
            "above_ma20"
        ]
    )

    # --------------------------------------------------------
    # 平均回測勝率
    # --------------------------------------------------------

    backtest_average = {}

    for days in BACKTEST_DAYS:

        values = []

        for item in results:

            value = item[
                "backtest"
            ][
                f"{days}d"
            ][
                "win_rate"
            ]

            if value is not None:

                values.append(
                    value
                )

        if values:

            backtest_average[
                f"{days}d"
            ] = clean_number(
                sum(values)
                /
                len(values),
                2
            )

        else:

            backtest_average[
                f"{days}d"
            ] = None

    return {

        "total_stocks": total,

        "stock_count": stock_count,

        "etf_count": etf_count,

        "core_stocks": core_count,

        "macd_golden": macd_count,

        "kd_golden": kd_count,

        "rsi_above_50": rsi_count,

        "volume_breakout": volume_count,

        "above_ma20": ma20_count,

        "backtest_average": backtest_average
    }


# ============================================================
# 儀表板摘要
# ============================================================

def create_dashboard_summary(
    results
):

    if not results:

        return {

            "market_status":
                "無資料",

            "top_stock":
                None,

            "top_etf":
                None,

            "core_count":
                0,

            "average_win_rate_30d":
                None,

            "average_win_rate_60d":
                None,

            "average_win_rate_90d":
                None
        }

    stocks = [
        item
        for item in results
        if item["asset_type"]
        == "STOCK"
    ]

    etfs = [
        item
        for item in results
        if item["asset_type"]
        == "ETF"
    ]

    stocks = sorted(
        stocks,
        key=lambda x:
        x["short_term"]["score"],
        reverse=True
    )

    etfs = sorted(
        etfs,
        key=lambda x:
        x["short_term"]["score"],
        reverse=True
    )

    core_count = sum(
        1
        for item in results
        if item["conditions"][
            "short_term_core"
        ]
    )

    def average_win_rate(
        days
    ):

        values = []

        for item in results:

            value = item[
                "backtest"
            ][
                f"{days}d"
            ][
                "win_rate"
            ]

            if value is not None:

                values.append(
                    value
                )

        if not values:

            return None

        return clean_number(
            sum(values)
            /
            len(values),
            2
        )

    # --------------------------------------------------------
    # 市場狀態
    # --------------------------------------------------------

    core_ratio = (
        core_count
        /
        len(results)
    )

    if core_ratio >= 0.30:

        market_status = (
            "多方偏強"
        )

    elif core_ratio >= 0.15:

        market_status = (
            "震盪偏多"
        )

    elif core_ratio >= 0.05:

        market_status = (
            "震盪觀察"
        )

    else:

        market_status = (
            "偏弱"
        )

    return {

        "market_status":
            market_status,

        "top_stock":
            stocks[0]["id"]
            if stocks
            else None,

        "top_etf":
            etfs[0]["id"]
            if etfs
            else None,

        "core_count":
            core_count,

        "average_win_rate_30d":
            average_win_rate(30),

        "average_win_rate_60d":
            average_win_rate(60),

        "average_win_rate_90d":
            average_win_rate(90)
    }


# ============================================================
# 儲存 JSON
# ============================================================

def save_json(
    results
):

    rankings = create_rankings(
        results
    )

    statistics = create_statistics(
        results
    )

    dashboard = (
        create_dashboard_summary(
            results
        )
    )

    now = datetime.now(
        TAIPEI_TZ
    )

    output = {

        "system": {

            "name":
                "台股 AI 選股・零股定投・動態風控儀表板",

            "version":
                "3.0",

            "generated_at":
                now.isoformat(),

            "timezone":
                "Asia/Taipei",

            "data_source":
                "Yahoo Finance",

            "engine":
                "fetch_data.py V3",

            "backtest_enabled":
                True
        },

        "strategy": {

            "short_term": {

                "macd_golden_cross":
                    True,

                "kd_golden_cross":
                    True,

                "rsi_above":
                    50,

                "volume_multiplier":
                    1.5,

                "above_ma20":
                    True,

                "ma20_up":
                    True
            },

            "dca": {

                "reference":
                    "20MA",

                "levels": [

                    "MA20附近",

                    "MA20下方3%",

                    "MA20下方6%",

                    "MA20下方10%"
                ]
            },

            "backtest": {

                "enabled":
                    True,

                "periods": [

                    30,

                    60,

                    90
                ],

                "win_definition":
                    "訊號後N個交易日收盤價高於訊號日收盤價"
            }
        },

        "dashboard":
            dashboard,

        "statistics":
            statistics,

        "rankings":
            rankings,

        "stocks":
            results
    }

    # --------------------------------------------------------
    # 寫入
    # --------------------------------------------------------

    with open(
        OUTPUT_FILE,
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
    # Console
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print("資料寫入完成")
    print("=" * 70)
    print("")

    print(
        f"檔案："
        f"{OUTPUT_FILE}"
    )

    print(
        f"總標的："
        f"{statistics['total_stocks']}"
    )

    print(
        f"個股："
        f"{statistics['stock_count']}"
    )

    print(
        f"ETF："
        f"{statistics['etf_count']}"
    )

    print(
        f"符合核心條件："
        f"{statistics['core_stocks']}"
    )

    print("")

    print(
        "市場狀態："
        f"{dashboard['market_status']}"
    )

    print("")

    print(
        "30日平均勝率："
        f"{dashboard['average_win_rate_30d']}"
    )

    print(
        "60日平均勝率："
        f"{dashboard['average_win_rate_60d']}"
    )

    print(
        "90日平均勝率："
        f"{dashboard['average_win_rate_90d']}"
    )

    print("")

    print(
        "短線排名："
    )

    for rank, stock_id in enumerate(
        rankings["short_term"],
        start=1
    ):

        stock = next(
            (
                item
                for item in results
                if item["id"]
                == stock_id
            ),
            None
        )

        if stock:

            print(
                f"{rank}. "
                f"{stock['id']} "
                f"{stock['name']} "
                f"[{stock['asset_type']}] "
                f"評分："
                f"{stock['short_term']['score']}"
            )

    print("")

    print(
        "ETF排名："
    )

    for rank, stock_id in enumerate(
        rankings["etf"],
        start=1
    ):

        stock = next(
            (
                item
                for item in results
                if item["id"]
                == stock_id
            ),
            None
        )

        if stock:

            print(
                f"{rank}. "
                f"{stock['id']} "
                f"{stock['name']} "
                f"評分："
                f"{stock['short_term']['score']}"
            )

    print("")


# ============================================================
# 主程式
# ============================================================

def main():

    print("")
    print("=" * 70)
    print(
        "台股 AI 選股 + 零股定投"
    )

    print(
        "+ 動態風控 + 30/60/90日勝率"
    )

    print(
        "V3 正式版"
    )

    print("=" * 70)
    print("")

    start_time = time.time()

    results = fetch_all_stocks()

    if not results:

        print("")
        print(
            "錯誤："
            "沒有成功取得任何股票資料。"
        )
        print("")

        sys.exit(1)

    save_json(
        results
    )

    elapsed = (
        time.time()
        -
        start_time
    )

    print(
        f"完成，耗時："
        f"{elapsed:.1f} 秒"
    )

    print("")


# ============================================================
# 執行
# ============================================================

if __name__ == "__main__":

    main()
