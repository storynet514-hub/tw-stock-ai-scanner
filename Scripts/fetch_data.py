# -*- coding: utf-8 -*-

"""
台股 AI 選股 + 零股定投 + 動態風控資料引擎
------------------------------------------------
功能：
1. 抓取台股日線資料
2. 計算：
   - MACD
   - KD
   - RSI
   - 5日均量
   - 20MA
   - 20MA斜率
3. 短期核心條件：
   - MACD 黃金交叉
   - KD 黃金交叉
   - RSI > 50
   - 成交量 > 5日均量 × 1.5
   - 股價站上20MA
   - 20MA向上
4. 短期選股評分
5. 定投價格區間與風控資訊
6. 輸出 Data/prices.json
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    print("錯誤：找不到 yfinance")
    sys.exit(1)


# ============================================================
# 基本設定
# ============================================================

TAIPEI_TZ = timezone(timedelta(hours=8))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "Data")
OUTPUT_FILE = os.path.join(DATA_DIR, "prices.json")

os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# 股票清單
# ============================================================
#
# 這裡統一管理追蹤標的。
#
# 如果你之後要增加股票，只需要：
#
# STOCKS = {
#     "2330": "台積電",
#     "2317": "鴻海",
# }
#
# 不需要修改下面的分析程式。
#
# 目前先放常見標的作為基礎清單。
# 之後可以直接依你的正式股票清單修改。
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
    "3231": "緯創",
    "3450": "聯鈞",
    "3338": "泰碩",
    "6176": "瑞儀",
    "3042": "晶技",
    "6290": "良維",
    "3006": "晶豪科",
    "2426": "鼎元",
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
VOLUME_PERIOD = 5

VOLUME_MULTIPLIER = 1.5


# ============================================================
# 工具函式
# ============================================================

def clean_number(value, digits=4):
    """
    將 numpy / pandas 數字轉成 JSON 可以正常輸出的數字。
    NaN / inf 轉成 None。
    """

    if value is None:
        return None

    try:
        value = float(value)

        if math.isnan(value) or math.isinf(value):
            return None

        return round(value, digits)

    except Exception:
        return None


def safe_bool(value):
    """
    將 numpy.bool_ 等型別轉成標準 Python bool。
    """

    try:
        return bool(value)
    except Exception:
        return False


def get_ticker(stock_id):
    """
    台股 Yahoo Finance 代號。
    """

    return f"{stock_id}.TW"


# ============================================================
# RSI
# ============================================================

def calculate_rsi(close, period=14):
    """
    RSI 使用 Wilder 平滑方式。
    """

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

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

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    return rsi


# ============================================================
# MACD
# ============================================================

def calculate_macd(close):
    """
    MACD：

    DIF = EMA12 - EMA26
    DEM = DIF 的 EMA9
    Histogram = DIF - DEM
    """

    ema_fast = close.ewm(
        span=MACD_FAST,
        adjust=False
    ).mean()

    ema_slow = close.ewm(
        span=MACD_SLOW,
        adjust=False
    ).mean()

    dif = ema_fast - ema_slow

    dem = dif.ewm(
        span=MACD_SIGNAL,
        adjust=False
    ).mean()

    histogram = dif - dem

    return dif, dem, histogram


# ============================================================
# KD
# ============================================================

def calculate_kd(df):
    """
    RSV：

    (Close - Lowest Low)
    /
    (Highest High - Lowest Low)

    K = RSV 的平滑
    D = K 的平滑
    """

    lowest_low = df["Low"].rolling(
        KD_PERIOD
    ).min()

    highest_high = df["High"].rolling(
        KD_PERIOD
    ).max()

    denominator = highest_high - lowest_low

    rsv = (
        (df["Close"] - lowest_low)
        / denominator.replace(0, np.nan)
    ) * 100

    k = rsv.ewm(
        alpha=1 / KD_SMOOTH_K,
        adjust=False
    ).mean()

    d = k.ewm(
        alpha=1 / KD_SMOOTH_D,
        adjust=False
    ).mean()

    return k, d


# ============================================================
# 技術分析
# ============================================================

def calculate_indicators(df):

    df = df.copy()

    # --------------------------------------------------------
    # 基本均線
    # --------------------------------------------------------

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    # --------------------------------------------------------
    # MA20 斜率
    # --------------------------------------------------------

    df["MA20_PREV"] = df["MA20"].shift(1)

    df["MA20_SLOPE"] = (
        (df["MA20"] - df["MA20_PREV"])
        / df["MA20_PREV"]
    ) * 100

    # --------------------------------------------------------
    # 成交量
    # --------------------------------------------------------

    df["VOLUME_MA5"] = (
        df["Volume"]
        .rolling(VOLUME_PERIOD)
        .mean()
    )

    df["VOLUME_RATIO"] = (
        df["Volume"]
        / df["VOLUME_MA5"]
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    df["RSI"] = calculate_rsi(
        df["Close"],
        RSI_PERIOD
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    (
        df["DIF"],
        df["DEM"],
        df["MACD_HIST"]
    ) = calculate_macd(df["Close"])

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    (
        df["K"],
        df["D"]
    ) = calculate_kd(df)

    # --------------------------------------------------------
    # MACD 黃金交叉
    # --------------------------------------------------------

    df["MACD_GOLDEN"] = (
        (df["DIF"] > df["DEM"]) &
        (df["DIF"].shift(1) <= df["DEM"].shift(1))
    )

    # --------------------------------------------------------
    # MACD 柱狀圖翻紅
    # --------------------------------------------------------

    df["MACD_RED"] = (
        df["MACD_HIST"] > 0
    )

    # --------------------------------------------------------
    # KD 黃金交叉
    # --------------------------------------------------------

    df["KD_GOLDEN"] = (
        (df["K"] > df["D"]) &
        (df["K"].shift(1) <= df["D"].shift(1))
    )

    # --------------------------------------------------------
    # KD 低位黃金交叉
    # --------------------------------------------------------

    df["KD_LOW_GOLDEN"] = (
        df["KD_GOLDEN"] &
        (
            df["K"].shift(1) <= 30
        )
    )

    # --------------------------------------------------------
    # RSI > 50
    # --------------------------------------------------------

    df["RSI_BULLISH"] = (
        df["RSI"] > 50
    )

    # --------------------------------------------------------
    # 成交量放大
    # --------------------------------------------------------

    df["VOLUME_BREAKOUT"] = (
        df["Volume"]
        >
        df["VOLUME_MA5"] * VOLUME_MULTIPLIER
    )

    # --------------------------------------------------------
    # 股價站上20MA
    # --------------------------------------------------------

    df["ABOVE_MA20"] = (
        df["Close"] > df["MA20"]
    )

    # --------------------------------------------------------
    # 20MA 向上
    # --------------------------------------------------------

    df["MA20_UP"] = (
        df["MA20"] > df["MA20"].shift(1)
    )

    # --------------------------------------------------------
    # 20MA 向上勾
    # --------------------------------------------------------

    df["MA20_TURN_UP"] = (
        (df["MA20"] > df["MA20"].shift(1)) &
        (df["MA20"].shift(1) >= df["MA20"].shift(2))
    )

    # --------------------------------------------------------
    # 綜合核心條件
    # --------------------------------------------------------

    df["SHORT_TERM_CORE"] = (
        df["MACD_GOLDEN"] &
        df["KD_GOLDEN"] &
        df["RSI_BULLISH"] &
        df["VOLUME_BREAKOUT"] &
        df["ABOVE_MA20"] &
        df["MA20_UP"]
    )

    return df


# ============================================================
# 短期選股評分
# ============================================================

def calculate_short_score(row):

    score = 0

    reasons = []

    # --------------------------------------------------------
    # MACD 黃金交叉
    # --------------------------------------------------------

    if safe_bool(row["MACD_GOLDEN"]):
        score += 25
        reasons.append("MACD黃金交叉")

    elif safe_bool(row["MACD_RED"]):
        score += 10
        reasons.append("MACD柱狀圖翻紅")

    # --------------------------------------------------------
    # KD 黃金交叉
    # --------------------------------------------------------

    if safe_bool(row["KD_LOW_GOLDEN"]):
        score += 25
        reasons.append("KD低位黃金交叉")

    elif safe_bool(row["KD_GOLDEN"]):
        score += 20
        reasons.append("KD黃金交叉")

    elif row["K"] is not None and row["D"] is not None:
        if row["K"] > row["D"]:
            score += 8
            reasons.append("KD多方")

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = row["RSI"]

    if rsi is not None:

        if rsi >= 55:
            score += 15
            reasons.append("RSI多方")

        elif rsi > 50:
            score += 10
            reasons.append("RSI站上50")

    # --------------------------------------------------------
    # 成交量
    # --------------------------------------------------------

    volume_ratio = row["VOLUME_RATIO"]

    if volume_ratio is not None:

        if volume_ratio >= 2:
            score += 20
            reasons.append("成交量爆量")

        elif volume_ratio >= 1.5:
            score += 15
            reasons.append("成交量放大")

        elif volume_ratio >= 1.2:
            score += 5

    # --------------------------------------------------------
    # 20MA
    # --------------------------------------------------------

    if safe_bool(row["ABOVE_MA20"]):
        score += 8
        reasons.append("站上20MA")

    # --------------------------------------------------------
    # 20MA 趨勢
    # --------------------------------------------------------

    if safe_bool(row["MA20_UP"]):
        score += 5
        reasons.append("20MA向上")

    # --------------------------------------------------------
    # 20MA 向上勾
    # --------------------------------------------------------

    if safe_bool(row["MA20_TURN_UP"]):
        score += 7
        reasons.append("20MA向上勾")

    return score, reasons


# ============================================================
# 定投策略
# ============================================================

def calculate_dca_strategy(row):

    close = row["Close"]
    ma20 = row["MA20"]
    ma60 = row["MA60"]

    if close is None:
        return {
            "status": "資料不足",
            "action": "觀望"
        }

    # --------------------------------------------------------
    # 參考均線
    # --------------------------------------------------------

    reference = ma20 if ma20 is not None else close

    # --------------------------------------------------------
    # 定投價格區間
    #
    # 第一區：
    # MA20 附近
    #
    # 第二區：
    # MA20 - 3%
    #
    # 第三區：
    # MA20 - 6%
    #
    # 第四區：
    # MA20 - 10%
    # --------------------------------------------------------

    buy_1 = reference * 0.99
    buy_2 = reference * 0.97
    buy_3 = reference * 0.94
    buy_4 = reference * 0.90

    # --------------------------------------------------------
    # 如果有60MA，加入長期支撐參考
    # --------------------------------------------------------

    if ma60 is not None:

        long_support = ma60

        # 不讓第四區偏離60MA太誇張
        buy_4 = min(
            buy_4,
            long_support * 1.02
        )

    # --------------------------------------------------------
    # 判斷目前價格位置
    # --------------------------------------------------------

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
        "buy_1": clean_number(buy_1, 2),
        "buy_2": clean_number(buy_2, 2),
        "buy_3": clean_number(buy_3, 2),
        "buy_4": clean_number(buy_4, 2),
    }


# ============================================================
# 動態風控
# ============================================================

def calculate_risk_control(row):

    close = row["Close"]
    ma20 = row["MA20"]
    ma60 = row["MA60"]

    if close is None:
        return {
            "risk_level": "未知",
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None
        }

    # --------------------------------------------------------
    # 基礎停損
    # --------------------------------------------------------

    stop_loss = close * 0.93

    # 如果跌破20MA，
    # 風險提高
    if ma20 is not None and close < ma20:
        stop_loss = close * 0.95

    # --------------------------------------------------------
    # 停利
    # --------------------------------------------------------

    take_profit_1 = close * 1.08
    take_profit_2 = close * 1.15

    # --------------------------------------------------------
    # 風險判斷
    # --------------------------------------------------------

    if ma20 is None:

        risk_level = "資料不足"

    elif close >= ma20 and row["MA20_UP"]:

        risk_level = "低～中"

    elif close >= ma20:

        risk_level = "中"

    else:

        risk_level = "中～高"

    return {
        "risk_level": risk_level,
        "stop_loss": clean_number(stop_loss, 2),
        "take_profit_1": clean_number(take_profit_1, 2),
        "take_profit_2": clean_number(take_profit_2, 2)
    }


# ============================================================
# 單一股票分析
# ============================================================

def analyze_stock(stock_id, stock_name):

    ticker = get_ticker(stock_id)

    print(f"正在分析：{stock_id} {stock_name}")

    try:

        df = yf.download(
            ticker,
            period="1y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False
        )

    except Exception as e:

        print(f"  抓取失敗：{e}")

        return None

    if df is None or df.empty:

        print("  無資料")

        return None

    # --------------------------------------------------------
    # 處理 Yahoo Finance 多層欄位
    # --------------------------------------------------------

    if isinstance(df.columns, pd.MultiIndex):

        try:
            df.columns = df.columns.get_level_values(0)

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

            print(f"  缺少欄位：{column}")

            return None

    # --------------------------------------------------------
    # 清理資料
    # --------------------------------------------------------

    df = df[required_columns].copy()

    for column in required_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df = df.dropna(
        subset=["Close"]
    )

    if len(df) < 70:

        print("  歷史資料不足")

        return None

    # --------------------------------------------------------
    # 計算技術指標
    # --------------------------------------------------------

    df = calculate_indicators(df)

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    # --------------------------------------------------------
    # 股票基本資料
    # --------------------------------------------------------

    close = clean_number(latest["Close"], 2)

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
    # 核心條件
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

    score, reasons = calculate_short_score(
        latest
    )

    # --------------------------------------------------------
    # 等級
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
    # 定投
    # --------------------------------------------------------

    dca = calculate_dca_strategy(
        {
            "Close": close,
            "MA20": ma20,
            "MA60": ma60
        }
    )

    # --------------------------------------------------------
    # 風控
    # --------------------------------------------------------

    risk = calculate_risk_control(
        {
            "Close": close,
            "MA20": ma20,
            "MA60": ma60,
            "MA20_UP": ma20_up
        }
    )

    # --------------------------------------------------------
    # 漲跌
    # --------------------------------------------------------

    previous_close = clean_number(
        previous["Close"],
        2
    )

    change = None
    change_percent = None

    if (
        close is not None and
        previous_close is not None and
        previous_close != 0
    ):

        change = clean_number(
            close - previous_close,
            2
        )

        change_percent = clean_number(
            (
                (close - previous_close)
                / previous_close
            ) * 100,
            2
        )

    # --------------------------------------------------------
    # 最終資料
    # --------------------------------------------------------

    result = {

        "id": stock_id,

        "symbol": ticker,

        "name": stock_name,

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

            "macd_golden_cross": macd_golden,

            "macd_red": macd_red,

            "kd_golden_cross": kd_golden,

            "kd_low_golden_cross": kd_low_golden,

            "rsi_above_50": rsi_bullish,

            "volume_over_1_5x": volume_breakout,

            "above_ma20": above_ma20,

            "ma20_up": ma20_up,

            "ma20_turn_up": ma20_turn_up,

            "short_term_core": short_term_core
        },

        "short_term": {

            "score": score,

            "signal": signal,

            "reasons": reasons,

            "core_conditions": {

                "macd": macd_golden,

                "kd": kd_golden,

                "rsi": rsi_bullish,

                "volume": volume_breakout,

                "ma20": above_ma20,

                "ma20_up": ma20_up
            }
        },

        "dca": dca,

        "risk_control": risk,

        "strategy": {

            "short_term": (
                "符合全部核心條件"
                if short_term_core
                else "尚未完全符合短期核心條件"
            ),

            "dca_action": dca["action"],

            "risk_level": risk["risk_level"]
        }

    }

    print(
        f"  收盤：{close} | "
        f"評分：{score} | "
        f"訊號：{signal}"
    )

    return result


# ============================================================
# 取得股票資料
# ============================================================

def fetch_all_stocks():

    results = []

    total = len(STOCKS)

    print("")
    print("=" * 60)
    print("開始抓取台股資料")
    print(f"股票數量：{total}")
    print("=" * 60)
    print("")

    for index, (stock_id, stock_name) in enumerate(
        STOCKS.items(),
        start=1
    ):

        print(
            f"[{index}/{total}] "
            f"{stock_id} {stock_name}"
        )

        try:

            result = analyze_stock(
                stock_id,
                stock_name
            )

            if result is not None:

                results.append(result)

        except Exception as e:

            print(
                f"  分析錯誤：{e}"
            )

        # 避免過度頻繁呼叫 Yahoo Finance
        time.sleep(0.5)

    return results


# ============================================================
# 排名
# ============================================================

def create_rankings(results):

    # --------------------------------------------------------
    # 短期排名
    # --------------------------------------------------------

    short_term = sorted(
        results,
        key=lambda x: (
            x["short_term"]["score"],
            x["technical"]["volume_ratio"] or 0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # 符合全部核心條件
    # --------------------------------------------------------

    core_stocks = [

        stock

        for stock in results

        if stock["conditions"]["short_term_core"]
    ]

    # --------------------------------------------------------
    # 定投排序
    # --------------------------------------------------------

    dca_candidates = sorted(
        results,
        key=lambda x: (
            x["short_term"]["score"],
            x["technical"]["ma20_slope"] or 0
        ),
        reverse=True
    )

    return {

        "short_term": [
            stock["id"]
            for stock in short_term[:10]
        ],

        "core": [
            stock["id"]
            for stock in core_stocks
        ],

        "dca": [
            stock["id"]
            for stock in dca_candidates[:10]
        ]
    }


# ============================================================
# 系統統計
# ============================================================

def create_statistics(results):

    total = len(results)

    core_count = sum(
        1
        for stock in results
        if stock["conditions"]["short_term_core"]
    )

    macd_count = sum(
        1
        for stock in results
        if stock["conditions"]["macd_golden_cross"]
    )

    kd_count = sum(
        1
        for stock in results
        if stock["conditions"]["kd_golden_cross"]
    )

    rsi_count = sum(
        1
        for stock in results
        if stock["conditions"]["rsi_above_50"]
    )

    volume_count = sum(
        1
        for stock in results
        if stock["conditions"]["volume_over_1_5x"]
    )

    ma20_count = sum(
        1
        for stock in results
        if stock["conditions"]["above_ma20"]
    )

    return {

        "total_stocks": total,

        "core_stocks": core_count,

        "macd_golden": macd_count,

        "kd_golden": kd_count,

        "rsi_above_50": rsi_count,

        "volume_breakout": volume_count,

        "above_ma20": ma20_count
    }


# ============================================================
# 寫入 JSON
# ============================================================

def save_json(results):

    rankings = create_rankings(
        results
    )

    statistics = create_statistics(
        results
    )

    now = datetime.now(
        TAIPEI_TZ
    )

    output = {

        "system": {

            "name": "台股 AI 選股與零股定投動態風控儀表板",

            "version": "2.0",

            "generated_at": now.isoformat(),

            "timezone": "Asia/Taipei",

            "data_source": "Yahoo Finance"
        },

        "strategy": {

            "short_term": {

                "macd_golden_cross": True,

                "kd_golden_cross": True,

                "rsi_above": 50,

                "volume_multiplier": 1.5,

                "above_ma20": True,

                "ma20_up": True
            },

            "dca": {

                "reference": "20MA",

                "levels": [

                    "MA20附近",

                    "MA20下方3%",

                    "MA20下方6%",

                    "MA20下方10%"
                ]
            }
        },

        "statistics": statistics,

        "rankings": rankings,

        "stocks": results
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

    print("")
    print("=" * 60)
    print("資料寫入完成")
    print("=" * 60)
    print("")
    print(
        f"檔案：{OUTPUT_FILE}"
    )

    print(
        f"股票數量：{statistics['total_stocks']}"
    )

    print(
        f"符合全部短期核心條件："
        f"{statistics['core_stocks']}"
    )

    print("")
    print("短期排名：")

    for rank, stock_id in enumerate(
        rankings["short_term"],
        start=1
    ):

        stock = next(
            (
                s
                for s in results
                if s["id"] == stock_id
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


# ============================================================
# 主程式
# ============================================================

def main():

    print("")
    print("=" * 60)
    print("台股 AI 選股 + 零股定投資料引擎")
    print("Version 2.0")
    print("=" * 60)
    print("")

    start_time = time.time()

    results = fetch_all_stocks()

    if not results:

        print("")
        print("錯誤：沒有成功取得任何股票資料。")
        print("")
        sys.exit(1)

    save_json(
        results
    )

    elapsed = time.time() - start_time

    print(
        f"完成，耗時：{elapsed:.1f} 秒"
    )


# ============================================================
# 執行
# ============================================================

if __name__ == "__main__":

    main()
