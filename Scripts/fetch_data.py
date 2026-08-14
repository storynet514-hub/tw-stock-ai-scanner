# -*- coding: utf-8 -*-

"""
台股 AI 選股 + ETF 定投 + 30/60/90 日績效 + 動態風控資料引擎
================================================================

正式版 V3.0

功能：

【個股】
1. 抓取台股日線資料
2. MACD
3. KD
4. RSI
5. MA5 / MA10 / MA20 / MA60
6. 5日均量
7. 成交量倍率
8. 20MA斜率
9. MACD黃金交叉
10. KD黃金交叉
11. RSI > 50
12. 成交量 > 5日均量 × 1.5
13. 股價站上20MA
14. 20MA向上
15. 短線選股評分
16. 短線訊號
17. 零股分批定投價格
18. 動態停損 / 停利

【ETF】
1. ETF獨立分析
2. MA20 / MA60
3. RSI
4. MACD
5. 波動度
6. 30日報酬
7. 60日報酬
8. 90日報酬
9. 30/60/90日方向勝率
10. 最大回撤
11. 回撤程度
12. ETF定投評分
13. ETF定投區間
14. ETF風險等級

【系統】
1. 個股排名
2. ETF排名
3. 個股核心訊號統計
4. ETF統計
5. 30/60/90日績效統計
6. 輸出 Data/prices.json

資料來源：
Yahoo Finance

時區：
Asia/Taipei
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
# 個股清單
# ============================================================

STOCKS = {

    "2330": "台積電",
    "2317": "鴻海",
    "2454": "聯發科",
    "2303": "聯電",
    "2382": "廣達",
    "3231": "緯創",
    "6669": "緯穎",
    "3037": "欣興",
    "3044": "健鼎",
    "2449": "京元電子",
    "3481": "群創",
    "2409": "友達",
    "2301": "光寶科",
    "2376": "技嘉",
    "3490": "單井",
    "2421": "建準",
    "2425": "承啟",
    "3356": "奇偶",
    "6117": "迎廣",
    "6125": "廣運",
    "3324": "雙鴻",
    "3017": "奇鋐",
    "3450": "聯鈞",
    "3338": "泰碩",
    "6176": "瑞儀",
    "3042": "晶技",
    "6290": "良維",
    "3006": "晶豪科",
    "2426": "鼎元",

}


# ============================================================
# ETF 清單
# ============================================================
#
# ETF 與個股分開。
#
# 這些標的主要提供：
#
# 0050  元大台灣50
# 0056  元大高股息
# 006208 富邦台50
# 006203 元大MSCI台灣
# 00692  富邦公司治理
# 00701  國泰股利精選30
# 00713  元大台灣高息低波
# 00878  國泰永續高股息
# 00919  群益台灣精選高息
# 00927  群益半導體收益
#
# 未來可以直接在這裡增加 ETF。
# ============================================================

ETFS = {

    "0050": "元大台灣50",
    "0056": "元大高股息",
    "006208": "富邦台50",
    "006203": "元大MSCI台灣",
    "00692": "富邦公司治理",
    "00701": "國泰股利精選30",
    "00713": "元大台灣高息低波",
    "00878": "國泰永續高股息",
    "00919": "群益台灣精選高息",
    "00927": "群益半導體收益",

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

MA20_PERIOD = 20
MA60_PERIOD = 60

VOLUME_PERIOD = 5

VOLUME_MULTIPLIER = 1.5


# ============================================================
# 工具函式
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


def get_ticker(code):

    return f"{code}.TW"


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
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100 -
        (
            100 /
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
        ema_fast -
        ema_slow
    )

    dem = dif.ewm(
        span=MACD_SIGNAL,
        adjust=False
    ).mean()

    histogram = (
        dif -
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
        .rolling(KD_PERIOD)
        .min()
    )

    highest_high = (
        df["High"]
        .rolling(KD_PERIOD)
        .max()
    )

    denominator = (
        highest_high -
        lowest_low
    )

    rsv = (
        (
            df["Close"] -
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
# 個股技術指標
# ============================================================

def calculate_indicators(df):

    df = df.copy()

    # MA
    df["MA5"] = (
        df["Close"]
        .rolling(5)
        .mean()
    )

    df["MA10"] = (
        df["Close"]
        .rolling(10)
        .mean()
    )

    df["MA20"] = (
        df["Close"]
        .rolling(20)
        .mean()
    )

    df["MA60"] = (
        df["Close"]
        .rolling(60)
        .mean()
    )

    # MA20 slope
    df["MA20_PREV"] = (
        df["MA20"]
        .shift(1)
    )

    df["MA20_SLOPE"] = (
        (
            df["MA20"] -
            df["MA20_PREV"]
        )
        /
        df["MA20_PREV"]
    ) * 100

    # Volume
    df["VOLUME_MA5"] = (
        df["Volume"]
        .rolling(VOLUME_PERIOD)
        .mean()
    )

    df["VOLUME_RATIO"] = (
        df["Volume"] /
        df["VOLUME_MA5"]
    )

    # RSI
    df["RSI"] = calculate_rsi(
        df["Close"]
    )

    # MACD
    (
        df["DIF"],
        df["DEM"],
        df["MACD_HIST"]
    ) = calculate_macd(
        df["Close"]
    )

    # KD
    (
        df["K"],
        df["D"]
    ) = calculate_kd(
        df
    )

    # MACD golden cross
    df["MACD_GOLDEN"] = (

        (df["DIF"] > df["DEM"])

        &

        (
            df["DIF"].shift(1)
            <=
            df["DEM"].shift(1)
        )

    )

    # MACD red
    df["MACD_RED"] = (
        df["MACD_HIST"] > 0
    )

    # KD golden
    df["KD_GOLDEN"] = (

        (df["K"] > df["D"])

        &

        (
            df["K"].shift(1)
            <=
            df["D"].shift(1)
        )

    )

    # KD low golden
    df["KD_LOW_GOLDEN"] = (

        df["KD_GOLDEN"]

        &

        (
            df["K"].shift(1)
            <= 30
        )

    )

    # RSI bullish
    df["RSI_BULLISH"] = (
        df["RSI"] > 50
    )

    # Volume breakout
    df["VOLUME_BREAKOUT"] = (

        df["Volume"]

        >

        (
            df["VOLUME_MA5"] *
            VOLUME_MULTIPLIER
        )

    )

    # Above MA20
    df["ABOVE_MA20"] = (
        df["Close"] >
        df["MA20"]
    )

    # MA20 up
    df["MA20_UP"] = (
        df["MA20"] >
        df["MA20"].shift(1)
    )

    # MA20 turn up
    df["MA20_TURN_UP"] = (

        (
            df["MA20"] >
            df["MA20"].shift(1)
        )

        &

        (
            df["MA20"].shift(1)
            >=
            df["MA20"].shift(2)
        )

    )

    # Core
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
# 個股短線評分
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

    else:

        k = row["K"]
        d = row["D"]

        if (
            k is not None
            and
            d is not None
        ):

            if k > d:

                score += 8

                reasons.append(
                    "KD多方"
                )

    # RSI
    rsi = row["RSI"]

    if rsi is not None:

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

    if volume_ratio is not None:

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
# 個股定投
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

    buy_1 = (
        reference * 0.99
    )

    buy_2 = (
        reference * 0.97
    )

    buy_3 = (
        reference * 0.94
    )

    buy_4 = (
        reference * 0.90
    )

    if ma60 is not None:

        buy_4 = min(
            buy_4,
            ma60 * 1.02
        )

    if close >= buy_1:

        status = "高於第一定投區"
        action = "觀察 / 小額"

    elif close >= buy_2:

        status = "第一定投區"
        action = "第一批"

    elif close >= buy_3:

        status = "第二定投區"
        action = "第二批"

    elif close >= buy_4:

        status = "第三定投區"
        action = "第三批"

    else:

        status = "深度回撤區"
        action = "第四批 / 等待確認"

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
# 個股風控
# ============================================================

def calculate_stock_risk(
    close,
    ma20,
    ma20_up
):

    if close is None:

        return {

            "risk_level": "未知",

            "stop_loss": None,

            "take_profit_1": None,

            "take_profit_2": None

        }

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

    take_profit_1 = (
        close * 1.08
    )

    take_profit_2 = (
        close * 1.15
    )

    if (
        ma20 is not None
        and
        close >= ma20
        and
        ma20_up
    ):

        risk_level = "低～中"

    elif (
        ma20 is not None
        and
        close >= ma20
    ):

        risk_level = "中"

    else:

        risk_level = "中～高"

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
        )

    }


# ============================================================
# 30/60/90 日績效
# ============================================================

def calculate_forward_performance(
    df
):

    result = {}

    periods = [
        30,
        60,
        90
    ]

    for days in periods:

        key = f"{days}d"

        if len(df) <= days:

            result[key] = {

                "return_percent": None,

                "direction_win": None

            }

            continue

        current = float(
            df["Close"].iloc[-1]
        )

        past = float(
            df["Close"].iloc[-days - 1]
        )

        if past == 0:

            result[key] = {

                "return_percent": None,

                "direction_win": None

            }

            continue

        return_percent = (
            (
                current - past
            )
            /
            past
        ) * 100

        result[key] = {

            "return_percent":
                clean_number(
                    return_percent,
                    2
                ),

            "direction_win":
                return_percent > 0

        }

    return result


# ============================================================
# 方向勝率
# ============================================================
#
# 這裡不是把「未來勝率」誤當成預測。
#
# 計算的是：
#
# 過去 N 個交易日中，
# 今日價格相較於 N 日前是否上漲。
#
# 例如 30日方向勝率：
#
# 每一個歷史交易日往前回看30日，
# 如果價格上漲 = Win
#
# 最後統計 Win / 有效樣本。
# ============================================================

def calculate_historical_direction_win_rate(
    df,
    period
):

    if len(df) <= period + 5:

        return None

    close = df["Close"]

    comparison = (
        close >
        close.shift(period)
    )

    valid = comparison.dropna()

    if len(valid) == 0:

        return None

    win_rate = (
        valid.mean() * 100
    )

    return clean_number(
        win_rate,
        2
    )


# ============================================================
# 最大回撤
# ============================================================

def calculate_max_drawdown(
    df,
    period=90
):

    if len(df) < 2:

        return None

    data = (
        df["Close"]
        .tail(period)
        .copy()
    )

    if data.empty:

        return None

    running_max = (
        data.cummax()
    )

    drawdown = (
        (
            data -
            running_max
        )
        /
        running_max
    ) * 100

    max_drawdown = (
        drawdown.min()
    )

    return clean_number(
        max_drawdown,
        2
    )


# ============================================================
# ETF 波動度
# ============================================================

def calculate_volatility(
    df,
    period=30
):

    if len(df) < period:

        return None

    returns = (
        df["Close"]
        .pct_change()
        .dropna()
        .tail(period)
    )

    if returns.empty:

        return None

    volatility = (
        returns.std() *
        math.sqrt(252) *
        100
    )

    return clean_number(
        volatility,
        2
    )


# ============================================================
# ETF 趨勢評分
# ============================================================

def calculate_etf_score(
    close,
    ma20,
    ma60,
    rsi,
    dif,
    dem,
    return_30,
    return_60,
    return_90,
    max_drawdown
):

    score = 0

    reasons = []

    # MA20
    if (
        close is not None
        and
        ma20 is not None
    ):

        if close > ma20:

            score += 20

            reasons.append(
                "站上20MA"
            )

    # MA60
    if (
        close is not None
        and
        ma60 is not None
    ):

        if close > ma60:

            score += 20

            reasons.append(
                "站上60MA"
            )

    # RSI
    if rsi is not None:

        if rsi >= 55:

            score += 15

            reasons.append(
                "RSI偏多"
            )

        elif rsi >= 50:

            score += 10

            reasons.append(
                "RSI站上50"
            )

    # MACD
    if (
        dif is not None
        and
        dem is not None
    ):

        if dif > dem:

            score += 15

            reasons.append(
                "MACD多方"
            )

    # 30D
    if return_30 is not None:

        if return_30 > 0:

            score += 10

            reasons.append(
                "30日正報酬"
            )

    # 60D
    if return_60 is not None:

        if return_60 > 0:

            score += 10

            reasons.append(
                "60日正報酬"
            )

    # 90D
    if return_90 is not None:

        if return_90 > 0:

            score += 10

            reasons.append(
                "90日正報酬"
            )

    # Drawdown
    if max_drawdown is not None:

        if max_drawdown > -5:

            score += 10

            reasons.append(
                "回撤低"
            )

        elif max_drawdown > -10:

            score += 5

            reasons.append(
                "回撤可控"
            )

    return (
        min(score, 100),
        reasons
    )


# ============================================================
# ETF 定投策略
# ============================================================

def calculate_etf_dca(
    close,
    ma20,
    ma60,
    rsi
):

    if close is None:

        return {

            "status": "資料不足",

            "action": "觀望",

            "levels": {}

        }

    reference = (
        ma20
        if ma20 is not None
        else close
    )

    # ETF 定投不採用個股停損式邏輯
    # 而是採「回撤分批」
    #
    # Level 1：MA20附近
    # Level 2：MA20 - 3%
    # Level 3：MA20 - 6%
    # Level 4：MA20 - 10%

    level_1 = (
        reference * 0.99
    )

    level_2 = (
        reference * 0.97
    )

    level_3 = (
        reference * 0.94
    )

    level_4 = (
        reference * 0.90
    )

    if (
        close >= level_1
    ):

        action = "觀察 / 正常定投"

        status = "正常區"

    elif (
        close >= level_2
    ):

        action = "第一批加碼"

        status = "小幅回撤"

    elif (
        close >= level_3
    ):

        action = "第二批加碼"

        status = "中度回撤"

    elif (
        close >= level_4
    ):

        action = "第三批加碼"

        status = "較深回撤"

    else:

        action = "第四批 / 等待趨勢確認"

        status = "深度回撤"

    # RSI 過熱提示
    if (
        rsi is not None
        and
        rsi >= 70
    ):

        status = (
            status +
            " / RSI偏熱"
        )

        action = (
            "降低加碼"
        )

    return {

        "status": status,

        "action": action,

        "levels": {

            "level_1":
                clean_number(
                    level_1,
                    2
                ),

            "level_2":
                clean_number(
                    level_2,
                    2
                ),

            "level_3":
                clean_number(
                    level_3,
                    2
                ),

            "level_4":
                clean_number(
                    level_4,
                    2
                )

        }

    }


# ============================================================
# ETF 風險
# ============================================================

def calculate_etf_risk(
    volatility,
    max_drawdown,
    close,
    ma20,
    ma60
):

    if (
        volatility is None
        or
        max_drawdown is None
    ):

        return "資料不足"

    score = 0

    # 波動
    if volatility > 35:

        score += 3

    elif volatility > 25:

        score += 2

    elif volatility > 15:

        score += 1

    # 回撤
    if max_drawdown < -25:

        score += 3

    elif max_drawdown < -15:

        score += 2

    elif max_drawdown < -8:

        score += 1

    # 趨勢
    if (
        close is not None
        and
        ma20 is not None
        and
        close < ma20
    ):

        score += 1

    if (
        close is not None
        and
        ma60 is not None
        and
        close < ma60
    ):

        score += 1

    if score >= 6:

        return "高"

    if score >= 4:

        return "中～高"

    if score >= 2:

        return "中"

    return "低～中"


# ============================================================
# ETF 分析
# ============================================================

def analyze_etf(
    etf_id,
    etf_name
):

    ticker = get_ticker(
        etf_id
    )

    print(
        f"正在分析 ETF："
        f"{etf_id} "
        f"{etf_name}"
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
            f"  ETF抓取失敗：{e}"
        )

        return None

    if (
        df is None
        or
        df.empty
    ):

        print(
            "  ETF無資料"
        )

        return None

    # MultiIndex
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
                f"  ETF缺少欄位："
                f"{column}"
            )

            return None

    df = (
        df[
            required_columns
        ]
        .copy()
    )

    for column in required_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df = df.dropna(
        subset=["Close"]
    )

    if len(df) < 120:

        print(
            "  ETF歷史資料不足"
        )

        return None

    # 指標
    df = calculate_indicators(
        df
    )

    latest = df.iloc[-1]

    previous = df.iloc[-2]

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
            close -
            previous_close,
            2
        )

        change_percent = clean_number(

            (
                (
                    close -
                    previous_close
                )
                /
                previous_close
            )
            * 100,

            2

        )

    # 技術
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

    ma20_slope = clean_number(
        latest["MA20_SLOPE"],
        2
    )

    # 30/60/90
    performance = (
        calculate_forward_performance(
            df
        )
    )

    return_30 = (
        performance["30d"]
        ["return_percent"]
    )

    return_60 = (
        performance["60d"]
        ["return_percent"]
    )

    return_90 = (
        performance["90d"]
        ["return_percent"]
    )

    # 歷史方向勝率
    win_30 = (
        calculate_historical_direction_win_rate(
            df,
            30
        )
    )

    win_60 = (
        calculate_historical_direction_win_rate(
            df,
            60
        )
    )

    win_90 = (
        calculate_historical_direction_win_rate(
            df,
            90
        )
    )

    # 最大回撤
    max_drawdown = (
        calculate_max_drawdown(
            df,
            90
        )
    )

    # 波動
    volatility = (
        calculate_volatility(
            df,
            30
        )
    )

    # 評分
    score, reasons = (
        calculate_etf_score(

            close,
            ma20,
            ma60,
            rsi,
            dif,
            dem,
            return_30,
            return_60,
            return_90,
            max_drawdown

        )
    )

    # 訊號
    if score >= 80:

        signal = "強勢定投"

    elif score >= 65:

        signal = "適合定投"

    elif score >= 50:

        signal = "正常觀察"

    elif score >= 35:

        signal = "等待回撤"

    else:

        signal = "暫緩加碼"

    # 定投
    dca = calculate_etf_dca(

        close,
        ma20,
        ma60,
        rsi

    )

    # 風險
    risk_level = calculate_etf_risk(

        volatility,
        max_drawdown,
        close,
        ma20,
        ma60

    )

    result = {

        "id": etf_id,

        "symbol": ticker,

        "name": etf_name,

        "type": "ETF",

        "date": str(
            df.index[-1].date()
        ),

        "price": {

            "open": open_price,

            "high": high,

            "low": low,

            "close": close,

            "previous_close":
                previous_close,

            "change": change,

            "change_percent":
                change_percent,

            "volume": volume

        },

        "technical": {

            "ma20": ma20,

            "ma60": ma60,

            "ma20_slope":
                ma20_slope,

            "rsi": rsi,

            "dif": dif,

            "dem": dem,

            "macd_hist":
                macd_hist,

            "k": k,

            "d": d

        },

        "performance": {

            "30d": {

                "return_percent":
                    return_30,

                "historical_direction_win_rate":
                    win_30

            },

            "60d": {

                "return_percent":
                    return_60,

                "historical_direction_win_rate":
                    win_60

            },

            "90d": {

                "return_percent":
                    return_90,

                "historical_direction_win_rate":
                    win_90

            }

        },

        "risk_metrics": {

            "volatility_30d":
                volatility,

            "max_drawdown_90d":
                max_drawdown

        },

        "signal": {

            "score": score,

            "signal": signal,

            "reasons": reasons

        },

        "dca": dca,

        "risk_control": {

            "risk_level":
                risk_level

        },

        "strategy": {

            "type":
                "ETF長期定投",

            "action":
                dca["action"],

            "risk_level":
                risk_level

        }

    }

    print(

        f"  收盤：{close} | "
        f"評分：{score} | "
        f"訊號：{signal}"

    )

    return result


# ============================================================
# 個股分析
# ============================================================

def analyze_stock(
    stock_id,
    stock_name
):

    ticker = get_ticker(
        stock_id
    )

    print(
        f"正在分析："
        f"{stock_id} "
        f"{stock_name}"
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
                f"  缺少欄位："
                f"{column}"
            )

            return None

    df = (
        df[
            required_columns
        ]
        .copy()
    )

    for column in required_columns:

        df[column] = pd.to_numeric(

            df[column],

            errors="coerce"

        )

    df = df.dropna(
        subset=["Close"]
    )

    if len(df) < 120:

        print(
            "  歷史資料不足"
        )

        return None

    df = calculate_indicators(
        df
    )

    latest = df.iloc[-1]

    previous = df.iloc[-2]

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
            close -
            previous_close,
            2
        )

        change_percent = clean_number(

            (
                (
                    close -
                    previous_close
                )
                /
                previous_close
            )
            * 100,

            2

        )

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

    # Conditions
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

    # Score
    score, reasons = (
        calculate_short_score(
            latest
        )
    )

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

    # DCA
    dca = calculate_dca_strategy(

        close,
        ma20,
        ma60

    )

    # Risk
    risk = calculate_stock_risk(

        close,
        ma20,
        ma20_up

    )

    # Performance
    performance = (
        calculate_forward_performance(
            df
        )
    )

    result = {

        "id": stock_id,

        "symbol": ticker,

        "name": stock_name,

        "type": "STOCK",

        "date": str(
            df.index[-1].date()
        ),

        "price": {

            "open": open_price,

            "high": high,

            "low": low,

            "close": close,

            "previous_close":
                previous_close,

            "change": change,

            "change_percent":
                change_percent,

            "volume": volume

        },

        "technical": {

            "ma5": ma5,

            "ma10": ma10,

            "ma20": ma20,

            "ma60": ma60,

            "ma20_slope":
                ma20_slope,

            "rsi": rsi,

            "dif": dif,

            "dem": dem,

            "macd_hist":
                macd_hist,

            "k": k,

            "d": d,

            "volume_ma5":
                volume_ma5,

            "volume_ratio":
                volume_ratio

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

        "performance": performance,

        "dca": dca,

        "risk_control": risk,

        "strategy": {

            "type":
                "個股短線 / 零股定投",

            "short_term":
                (
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
        f"訊號：{signal}"

    )

    return result


# ============================================================
# 抓取全部個股
# ============================================================

def fetch_all_stocks():

    results = []

    total = len(
        STOCKS
    )

    print("")
    print("=" * 70)
    print("開始抓取台股個股資料")
    print(
        f"股票數量：{total}"
    )
    print("=" * 70)
    print("")

    for index, (
        stock_id,
        stock_name
    ) in enumerate(
        STOCKS.items(),
        start=1
    ):

        print(
            f"[{index}/{total}] "
            f"{stock_id} "
            f"{stock_name}"
        )

        try:

            result = analyze_stock(

                stock_id,

                stock_name

            )

            if result is not None:

                results.append(
                    result
                )

        except Exception as e:

            print(
                f"  分析錯誤：{e}"
            )

        time.sleep(
            0.5
        )

    return results


# ============================================================
# 抓取全部 ETF
# ============================================================

def fetch_all_etfs():

    results = []

    total = len(
        ETFS
    )

    print("")
    print("=" * 70)
    print("開始抓取台股 ETF 資料")
    print(
        f"ETF 數量：{total}"
    )
    print("=" * 70)
    print("")

    for index, (
        etf_id,
        etf_name
    ) in enumerate(
        ETFS.items(),
        start=1
    ):

        print(
            f"[{index}/{total}] "
            f"{etf_id} "
            f"{etf_name}"
        )

        try:

            result = analyze_etf(

                etf_id,

                etf_name

            )

            if result is not None:

                results.append(
                    result
                )

        except Exception as e:

            print(
                f"  ETF分析錯誤："
                f"{e}"
            )

        time.sleep(
            0.5
        )

    return results


# ============================================================
# 個股排名
# ============================================================

def create_stock_rankings(
    results
):

    short_term = sorted(

        results,

        key=lambda x: (

            x["short_term"]
            ["score"],

            x["technical"]
            ["volume_ratio"]
            or 0

        ),

        reverse=True

    )

    core = [

        stock

        for stock in results

        if stock[
            "conditions"
        ][
            "short_term_core"
        ]

    ]

    return {

        "short_term": [

            stock["id"]

            for stock
            in short_term[:10]

        ],

        "core": [

            stock["id"]

            for stock
            in core

        ]

    }


# ============================================================
# ETF 排名
# ============================================================

def create_etf_rankings(
    results
):

    ranked = sorted(

        results,

        key=lambda x: (

            x["signal"]
            ["score"],

            x["performance"]
            ["90d"]
            ["historical_direction_win_rate"]
            or 0

        ),

        reverse=True

    )

    return {

        "dca": [

            etf["id"]

            for etf
            in ranked[:10]

        ],

        "top": [

            etf["id"]

            for etf
            in ranked[:5]

        ]

    }


# ============================================================
# 個股統計
# ============================================================

def create_stock_statistics(
    results
):

    total = len(
        results
    )

    core_count = sum(

        1

        for stock
        in results

        if stock[
            "conditions"
        ][
            "short_term_core"
        ]

    )

    macd_count = sum(

        1

        for stock
        in results

        if stock[
            "conditions"
        ][
            "macd_golden_cross"
        ]

    )

    kd_count = sum(

        1

        for stock
        in results

        if stock[
            "conditions"
        ][
            "kd_golden_cross"
        ]

    )

    rsi_count = sum(

        1

        for stock
        in results

        if stock[
            "conditions"
        ][
            "rsi_above_50"
        ]

    )

    volume_count = sum(

        1

        for stock
        in results

        if stock[
            "conditions"
        ][
            "volume_over_1_5x"
        ]

    )

    ma20_count = sum(

        1

        for stock
        in results

        if stock[
            "conditions"
        ][
            "above_ma20"
        ]

    )

    return {

        "total_stocks":
            total,

        "core_stocks":
            core_count,

        "macd_golden":
            macd_count,

        "kd_golden":
            kd_count,

        "rsi_above_50":
            rsi_count,

        "volume_breakout":
            volume_count,

        "above_ma20":
            ma20_count

    }


# ============================================================
# ETF 統計
# ============================================================

def create_etf_statistics(
    results
):

    total = len(
        results
    )

    strong = sum(

        1

        for etf
        in results

        if etf[
            "signal"
        ][
            "score"
        ] >= 80

    )

    suitable = sum(

        1

        for etf
        in results

        if etf[
            "signal"
        ][
            "score"
        ] >= 65

    )

    positive_30 = sum(

        1

        for etf
        in results

        if (
            etf[
                "performance"
            ][
                "30d"
            ][
                "return_percent"
            ]
            is not None

            and

            etf[
                "performance"
            ][
                "30d"
            ][
                "return_percent"
            ] > 0
        )

    )

    positive_60 = sum(

        1

        for etf
        in results

        if (
            etf[
                "performance"
            ][
                "60d"
            ][
                "return_percent"
            ]
            is not None

            and

            etf[
                "performance"
            ][
                "60d"
            ][
                "return_percent"
            ] > 0
        )

    )

    positive_90 = sum(

        1

        for etf
        in results

        if (
            etf[
                "performance"
            ][
                "90d"
            ][
                "return_percent"
            ]
            is not None

            and

            etf[
                "performance"
            ][
                "90d"
            ][
                "return_percent"
            ] > 0
        )

    )

    return {

        "total_etfs":
            total,

        "strong_etfs":
            strong,

        "suitable_for_dca":
            suitable,

        "positive_30d":
            positive_30,

        "positive_60d":
            positive_60,

        "positive_90d":
            positive_90

    }


# ============================================================
# 系統總結
# ============================================================

def create_system_summary(
    stocks,
    etfs
):

    total = (
        len(stocks)
        +
        len(etfs)
    )

    return {

        "total_assets":
            total,

        "stock_count":
            len(stocks),

        "etf_count":
            len(etfs),

        "asset_types": [

            "STOCK",
            "ETF"

        ]

    }


# ============================================================
# 寫入 JSON
# ============================================================

def save_json(
    stocks,
    etfs
):

    stock_rankings = (
        create_stock_rankings(
            stocks
        )
    )

    etf_rankings = (
        create_etf_rankings(
            etfs
        )
    )

    stock_statistics = (
        create_stock_statistics(
            stocks
        )
    )

    etf_statistics = (
        create_etf_statistics(
            etfs
        )
    )

    system_summary = (
        create_system_summary(
            stocks,
            etfs
        )
    )

    now = datetime.now(
        TAIPEI_TZ
    )

    output = {

        "system": {

            "name":
                "台股 AI 選股＋ETF 定投＋動態風控儀表板",

            "version":
                "3.0",

            "generated_at":
                now.isoformat(),

            "timezone":
                "Asia/Taipei",

            "data_source":
                "Yahoo Finance",

            "engine":
                "STOCK + ETF"

        },

        "summary":
            system_summary,

        "strategy": {

            "stock": {

                "type":
                    "短線選股",

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

            "etf": {

                "type":
                    "長期零股定投",

                "trend_reference":
                    "MA20 + MA60",

                "momentum":
                    "RSI + MACD",

                "performance":
                    [
                        "30D",
                        "60D",
                        "90D"
                    ],

                "risk":
                    [
                        "30D volatility",
                        "90D max drawdown"
                    ]

            }

        },

        "statistics": {

            "stocks":
                stock_statistics,

            "etfs":
                etf_statistics

        },

        "rankings": {

            "stocks":
                stock_rankings,

            "etfs":
                etf_rankings

        },

        "stocks":
            stocks,

        "etfs":
            etfs

    }

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

    print("")
    print("=" * 70)
    print("資料寫入完成")
    print("=" * 70)
    print("")

    print(
        f"檔案："
        f"{OUTPUT_FILE}"
    )

    print("")

    print(
        f"個股："
        f"{len(stocks)}"
    )

    print(
        f"ETF："
        f"{len(etfs)}"
    )

    print(
        f"總標的："
        f"{len(stocks) + len(etfs)}"
    )

    print("")

    print(
        "個股短線排名："
    )

    for rank, stock_id in enumerate(

        stock_rankings[
            "short_term"
        ],

        start=1

    ):

        stock = next(

            (

                s

                for s in stocks

                if s["id"] ==
                stock_id

            ),

            None

        )

        if stock:

            print(

                f"{rank}. "
                f"{stock['id']} "
                f"{stock['name']} "
                f""
                f"評分 "
                f"{stock['short_term']['score']}"

            )

    print("")

    print(
        "ETF 定投排名："
    )

    for rank, etf_id in enumerate(

        etf_rankings[
            "dca"
        ],

        start=1

    ):

        etf = next(

            (

                e

                for e in etfs

                if e["id"] ==
                etf_id

            ),

            None

        )

        if etf:

            print(

                f"{rank}. "
                f"{etf['id']} "
                f"{etf['name']} "
                f""
                f"評分 "
                f"{etf['signal']['score']}"

            )

    print("")


# ============================================================
# 主程式
# ============================================================

def main():

    print("")
    print("=" * 70)
    print(
        "台股 AI 選股＋ETF 定投"
    )
    print(
        "＋30/60/90日績效"
    )
    print(
        "＋動態風控資料引擎"
    )
    print(
        "Version 3.0"
    )
    print("=" * 70)
    print("")

    start_time = time.time()

    # 個股
    stocks = (
        fetch_all_stocks()
    )

    # ETF
    etfs = (
        fetch_all_etfs()
    )

    if (
        not stocks
        and
        not etfs
    ):

        print("")
        print(
            "錯誤："
            "沒有成功取得任何資料。"
        )
        print("")

        sys.exit(1)

    save_json(

        stocks,

        etfs

    )

    elapsed = (
        time.time() -
        start_time
    )

    print(
        f"完成，耗時："
        f"{elapsed:.1f} 秒"
    )

    print("")

    print(
        "下一步："
        "GitHub Actions 將更新 Data/prices.json"
    )

    print("")


# ============================================================
# 執行
# ============================================================

if __name__ == "__main__":

    main()
