#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 fetch_data.py V10.1

V10.1 主要修正
============================================================
1. 非交易日不得被當成市場收盤日
2. latest_market_date 一律使用真正存在行情的最新交易日
3. date 與 latest_market_date 保持一致
4. 不再把缺少行情的 change_pct 當成 0
5. 所有技術指標使用同一個有效交易日
6. RSI / MACD / KD / MA5 / MA20 統一重新計算
7. 六項核心條件必須全部在同一個交易日成立
8. today_selected 代表「最新有效交易日」選股結果
9. market_breadth 只統計最新有效交易日
10. 避免週六、週日、國定假日產生虛假收盤資料
11. 缺少足夠歷史資料時，不允許誤判為符合條件
12. 保留 stocks.json 作為既有股票清單來源
13. 若 stocks.json 結構不同，提供多種格式相容解析
14. ETF / Bond 分類保留
15. backtest 使用有效交易日，不使用日曆日
16. JSON 輸出加入資料品質資訊
============================================================
"""

import os
import sys
import json
import math
import time
import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np
import requests

warnings.filterwarnings("ignore")

# ============================================================
# 基本設定
# ============================================================

VERSION = "V10.1"
SCHEMA_VERSION = "ui.v10"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "Data")

PRICES_FILE = os.path.join(DATA_DIR, "prices.json")
STOCKS_FILE = os.path.join(DATA_DIR, "stocks.json")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

TIMEZONE_TW = timezone(timedelta(hours=8))

# 技術指標所需歷史天數
HISTORY_PERIOD_DAYS = 260

# 至少需要的有效交易資料
MIN_HISTORY_ROWS = 80

# 回測
BACKTEST_HORIZON = 10

# Yahoo API 請求間隔
REQUEST_SLEEP = 0.15

# ============================================================
# 六大核心條件
# ============================================================

CORE_CONDITION_NAMES = [
    "MACD 多方",
    "RSI > 50",
    "KD 多方",
    "成交量 ≥ MA5 × 1.5",
    "股價 > MA20",
    "MA20 今日 > 昨日",
]

CORE_TOTAL = len(CORE_CONDITION_NAMES)


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
)


# ============================================================
# 工具函式
# ============================================================

def now_tw():
    return datetime.now(TIMEZONE_TW)


def today_tw_date():
    return now_tw().date()


def safe_float(value, default=None):
    """
    安全轉換數字。
    NaN / inf / None 一律視為無效。
    """
    try:
        if value is None:
            return default

        x = float(value)

        if not math.isfinite(x):
            return default

        return x

    except Exception:
        return default


def clean_json_value(value):
    """
    確保輸出的 JSON 不含 NaN / inf。
    """

    if isinstance(value, dict):
        return {
            str(k): clean_json_value(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [clean_json_value(v) for v in value]

    if isinstance(value, (np.integer,)):
        return int(value)

    if isinstance(value, (np.floating,)):
        if not np.isfinite(value):
            return None
        return float(value)

    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value

    if pd.isna(value):
        return None

    return value


def normalize_symbol(symbol):
    """
    將常見台股代碼轉成 Yahoo symbol。
    """

    if symbol is None:
        return None

    s = str(symbol).strip()

    if not s:
        return None

    # 已經是 Yahoo 格式
    if s.endswith(".TW") or s.endswith(".TWO"):
        return s

    # 港股 / 美股等非台股不在本版核心掃描
    if "." in s:
        return s

    # 台股代碼
    # 英文字尾，例如 2887G、6958A
    if s.isdigit():
        if len(s) <= 4:
            return s.zfill(4) + ".TW"

    # 例如 2887G / 6958A
    if len(s) >= 4:
        return s + ".TW"

    return s


def extract_code(symbol):
    if symbol is None:
        return ""

    s = str(symbol).strip()

    if "." in s:
        s = s.split(".")[0]

    return s


def infer_market(symbol):
    if symbol is None:
        return "TW"

    s = str(symbol)

    if s.endswith(".TWO"):
        return "TWO"

    if s.endswith(".TW"):
        return "TW"

    return "OTHER"


def infer_type(code, name="", existing_type=None):
    """
    優先使用 stocks.json 的 type。
    否則依代碼 / 名稱進行保守分類。
    """

    if existing_type:
        t = str(existing_type).lower()

        if t in ("stock", "stocks", "equity"):
            return "stock"

        if t in ("etf",):
            return "etf"

        if t in ("bond", "bond_etf", "bond-fund"):
            return "bond"

    c = str(code).upper()
    n = str(name)

    # 債券 / 反向債券 / 債券 ETF 常見命名
    bond_keywords = [
        "美債",
        "公債",
        "公司債",
        "投等債",
        "金融債",
        "高收益債",
        "債券",
        "新興公債",
        "債",
    ]

    if any(k in n for k in bond_keywords):
        return "bond"

    # 常見 ETF 命名
    etf_keywords = [
        "ETF",
        "高股息",
        "台灣50",
        "永續",
        "正2",
        "反1",
        "正2",
        "科技",
        "AI",
        "航運",
        "資安",
        "收息",
        "優選",
        "成長",
        "Smart",
        "ESG",
    ]

    if c.startswith("00"):
        # 00 開頭多為 ETF / ETN / 特殊證券
        return "etf"

    if any(k in n.upper() for k in [k.upper() for k in etf_keywords]):
        return "etf"

    return "stock"


# ============================================================
# 讀取 stocks.json
# ============================================================

def load_existing_universe():
    """
    優先使用既有 Data/stocks.json。

    支援：
    1. list
    2. {"stocks":[...]}
    3. {"data":[...]}
    4. {"items":[...]}
    """

    if not os.path.exists(STOCKS_FILE):
        print("⚠️ 找不到 Data/stocks.json")
        return []

    try:
        with open(STOCKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

    except Exception as e:
        print(f"⚠️ stocks.json 讀取失敗：{e}")
        return []

    if isinstance(data, list):
        raw_items = data

    elif isinstance(data, dict):
        for key in ("stocks", "data", "items", "universe", "symbols"):
            if isinstance(data.get(key), list):
                raw_items = data[key]
                break
        else:
            raw_items = []

    else:
        raw_items = []

    result = []

    for item in raw_items:

        if isinstance(item, str):
            symbol = normalize_symbol(item)

            if symbol:
                result.append(
                    {
                        "code": extract_code(symbol),
                        "symbol": symbol,
                        "name": extract_code(symbol),
                        "market": infer_market(symbol),
                        "type": infer_type(extract_code(symbol)),
                    }
                )

            continue

        if not isinstance(item, dict):
            continue

        symbol = (
            item.get("symbol")
            or item.get("ticker")
            or item.get("yahoo_symbol")
            or item.get("yf_symbol")
        )

        code = (
            item.get("code")
            or item.get("stock_code")
            or item.get("id")
        )

        if not symbol and code:
            symbol = normalize_symbol(code)
        else:
            symbol = normalize_symbol(symbol)

        if not symbol:
            continue

        code = str(code or extract_code(symbol)).strip()

        name = (
            item.get("name")
            or item.get("stock_name")
            or item.get("title")
            or code
        )

        existing_type = (
            item.get("type")
            or item.get("category")
            or item.get("asset_type")
        )

        market = (
            item.get("market")
            or infer_market(symbol)
        )

        asset_type = infer_type(
            code,
            name,
            existing_type,
        )

        result.append(
            {
                "code": code,
                "symbol": symbol,
                "name": str(name),
                "market": market,
                "type": asset_type,
            }
        )

    # 去除重複 symbol
    unique = {}

    for item in result:
        unique[item["symbol"]] = item

    result = list(unique.values())

    print(f"📚 stocks.json universe：{len(result)}")

    return result


# ============================================================
# Yahoo Finance
# ============================================================

def fetch_yahoo_history(symbol):
    """
    從 Yahoo Chart API 取得歷史資料。

    不使用 end=今天，
    而是直接要求最近一段時間，避免週末被誤判。
    """

    period2 = int(time.time())
    period1 = period2 - HISTORY_PERIOD_DAYS * 86400

    url = YAHOO_CHART_URL.format(symbol=symbol)

    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=20,
        )

        if response.status_code != 200:
            return None

        payload = response.json()

        chart = payload.get("chart", {})

        if chart.get("error"):
            return None

        results = chart.get("result")

        if not results:
            return None

        result = results[0]

        timestamps = result.get("timestamp")

        indicators = result.get("indicators", {})

        quote_list = indicators.get("quote", [])

        if not timestamps or not quote_list:
            return None

        quote = quote_list[0]

        adjclose_list = indicators.get("adjclose", [])

        adjclose = None

        if adjclose_list:
            adjclose = adjclose_list[0].get("adjclose")

        rows = []

        for i, ts in enumerate(timestamps):

            try:
                dt = datetime.fromtimestamp(
                    ts,
                    tz=timezone.utc,
                ).astimezone(TIMEZONE_TW)

                date_str = dt.strftime("%Y-%m-%d")

            except Exception:
                continue

            close = (
                quote.get("close", [None] * len(timestamps))[i]
                if i < len(quote.get("close", []))
                else None
            )

            open_price = (
                quote.get("open", [None] * len(timestamps))[i]
                if i < len(quote.get("open", []))
                else None
            )

            high = (
                quote.get("high", [None] * len(timestamps))[i]
                if i < len(quote.get("high", []))
                else None
            )

            low = (
                quote.get("low", [None] * len(timestamps))[i]
                if i < len(quote.get("low", []))
                else None
            )

            volume = (
                quote.get("volume", [None] * len(timestamps))[i]
                if i < len(quote.get("volume", []))
                else None
            )

            adj = (
                adjclose[i]
                if adjclose is not None and i < len(adjclose)
                else close
            )

            close = safe_float(close)

            if close is None:
                continue

            rows.append(
                {
                    "date": date_str,
                    "open": safe_float(open_price),
                    "high": safe_float(high),
                    "low": safe_float(low),
                    "close": close,
                    "adj_close": safe_float(adj, close),
                    "volume": safe_float(volume, 0),
                }
            )

        if not rows:
            return None

        df = pd.DataFrame(rows)

        df["date"] = pd.to_datetime(
            df["date"],
            errors="coerce",
        )

        df = df.dropna(subset=["date", "close"])

        df = df.sort_values("date")

        df = df.drop_duplicates(
            subset=["date"],
            keep="last",
        )

        df = df.reset_index(drop=True)

        return df

    except Exception as e:
        print(f"⚠️ {symbol} Yahoo 讀取失敗：{e}")
        return None


# ============================================================
# 技術指標
# ============================================================

def calculate_indicators(df):
    df = df.copy()

    close = pd.to_numeric(
        df["close"],
        errors="coerce",
    )

    high = pd.to_numeric(
        df["high"],
        errors="coerce",
    )

    low = pd.to_numeric(
        df["low"],
        errors="coerce",
    )

    volume = pd.to_numeric(
        df["volume"],
        errors="coerce",
    ).fillna(0)

    # --------------------------------------------------------
    # MA
    # --------------------------------------------------------

    df["ma5"] = close.rolling(
        5,
        min_periods=5,
    ).mean()

    df["ma20"] = close.rolling(
        20,
        min_periods=20,
    ).mean()

    # --------------------------------------------------------
    # EMA / MACD
    # --------------------------------------------------------

    ema12 = close.ewm(
        span=12,
        adjust=False,
        min_periods=12,
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False,
        min_periods=26,
    ).mean()

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df["macd"].ewm(
        span=9,
        adjust=False,
        min_periods=9,
    ).mean()

    df["macd_hist"] = (
        df["macd"] -
        df["macd_signal"]
    )

    # --------------------------------------------------------
    # RSI 14
    # --------------------------------------------------------

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan,
    )

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # 特殊情況：
    # avg_loss = 0 且 avg_gain > 0 → RSI = 100
    df.loc[
        (avg_loss == 0) &
        (avg_gain > 0),
        "rsi"
    ] = 100.0

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    lowest_low = low.rolling(
        9,
        min_periods=9,
    ).min()

    highest_high = high.rolling(
        9,
        min_periods=9,
    ).max()

    denominator = (
        highest_high -
        lowest_low
    )

    rsv = (
        (close - lowest_low) /
        denominator.replace(0, np.nan)
    ) * 100

    # 常見 9,3,3 KD
    k_values = []
    d_values = []

    previous_k = 50.0
    previous_d = 50.0

    for value in rsv:

        if pd.isna(value):
            k_values.append(np.nan)
            d_values.append(np.nan)
            continue

        current_k = (
            previous_k * 2 / 3 +
            float(value) / 3
        )

        current_d = (
            previous_d * 2 / 3 +
            current_k / 3
        )

        k_values.append(current_k)
        d_values.append(current_d)

        previous_k = current_k
        previous_d = current_d

    df["k"] = k_values
    df["d"] = d_values

    # --------------------------------------------------------
    # Volume MA5
    # --------------------------------------------------------

    df["volume_ma5"] = volume.rolling(
        5,
        min_periods=5,
    ).mean()

    return df


# ============================================================
# 六大核心條件
# ============================================================

def evaluate_core_conditions(df):
    """
    嚴格使用最後兩個有效交易日。

    注意：
    不足資料 → 不通過。
    NaN → 不通過。
    """

    if df is None or len(df) < MIN_HISTORY_ROWS:
        return {
            "core_score": 0,
            "core_total": CORE_TOTAL,
            "core_pass": False,
            "conditions": {},
        }

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    conditions = {}

    # 1. MACD 多方
    conditions["MACD 多方"] = (
        pd.notna(latest["macd"]) and
        pd.notna(latest["macd_signal"]) and
        latest["macd"] > latest["macd_signal"]
    )

    # 2. RSI > 50
    conditions["RSI > 50"] = (
        pd.notna(latest["rsi"]) and
        latest["rsi"] > 50
    )

    # 3. KD 多方
    conditions["KD 多方"] = (
        pd.notna(latest["k"]) and
        pd.notna(latest["d"]) and
        latest["k"] > latest["d"]
    )

    # 4. 成交量 >= MA5 × 1.5
    conditions["成交量 ≥ MA5 × 1.5"] = (
        pd.notna(latest["volume"]) and
        pd.notna(latest["volume_ma5"]) and
        latest["volume"] >= (
            latest["volume_ma5"] * 1.5
        )
    )

    # 5. 股價 > MA20
    conditions["股價 > MA20"] = (
        pd.notna(latest["close"]) and
        pd.notna(latest["ma20"]) and
        latest["close"] > latest["ma20"]
    )

    # 6. MA20 今日 > 昨日
    conditions["MA20 今日 > 昨日"] = (
        pd.notna(latest["ma20"]) and
        pd.notna(previous["ma20"]) and
        latest["ma20"] > previous["ma20"]
    )

    score = sum(
        1 for v in conditions.values()
        if bool(v)
    )

    return {
        "core_score": score,
        "core_total": CORE_TOTAL,
        "core_pass": score == CORE_TOTAL,
        "conditions": conditions,
    }


# ============================================================
# AI / Strength Score
# ============================================================

def calculate_score(df, core):
    """
    V10.1 分數不會因缺失資料直接灌滿。
    """

    if df is None or len(df) < MIN_HISTORY_ROWS:
        return 0.0, 0.0

    latest = df.iloc[-1]

    score = 0.0

    # 六大條件
    score += (
        core["core_score"] /
        CORE_TOTAL
    ) * 70.0

    # RSI
    rsi = safe_float(latest.get("rsi"))

    if rsi is not None:

        if rsi >= 70:
            score += 5.0
        elif rsi > 50:
            score += 8.0
        elif rsi >= 45:
            score += 3.0

    # MACD 柱體
    macd_hist = safe_float(
        latest.get("macd_hist")
    )

    if macd_hist is not None and macd_hist > 0:
        score += 5.0

    # 股價 / MA20
    close = safe_float(
        latest.get("close")
    )

    ma20 = safe_float(
        latest.get("ma20")
    )

    if close is not None and ma20:
        bias = (close / ma20 - 1) * 100

        if bias >= 0:
            score += min(
                max(bias, 0),
                5
            )

    # 成交量
    volume = safe_float(
        latest.get("volume")
    )

    volume_ma5 = safe_float(
        latest.get("volume_ma5")
    )

    if volume is not None and volume_ma5 and volume_ma5 > 0:

        ratio = volume / volume_ma5

        if ratio >= 1.5:
            score += 7.0
        elif ratio >= 1.0:
            score += 3.0

    score = min(max(score, 0), 100)

    # strength score
    strength = (
        core["core_score"] /
        CORE_TOTAL
    ) * 100

    if macd_hist is not None and macd_hist > 0:
        strength += 3

    if rsi is not None and rsi > 50:
        strength += 3

    strength = min(max(strength, 0), 100)

    return round(score, 2), round(strength, 2)


# ============================================================
# Rating / Signal
# ============================================================

def rating_from_score(score):
    if score >= 90:
        return "A+"

    if score >= 80:
        return "A"

    if score >= 70:
        return "B"

    if score >= 60:
        return "C"

    return "D"


def signal_from_score(score):
    if score >= 80:
        return "強勢多方"

    if score >= 65:
        return "偏多"

    if score >= 50:
        return "中性"

    if score >= 35:
        return "偏弱"

    return "弱勢"


def recommendation_from_core(core_score):
    if core_score == CORE_TOTAL:
        return f"符合 {CORE_TOTAL}/{CORE_TOTAL} 核心條件"

    if core_score >= CORE_TOTAL - 1:
        return "接近核心條件"

    if core_score >= CORE_TOTAL - 2:
        return "部分符合條件"

    return "暫不操作"


# ============================================================
# 單一標的分析
# ============================================================

def analyze_symbol(item):
    symbol = item["symbol"]

    df = fetch_yahoo_history(symbol)

    if df is None:
        return None

    if len(df) < MIN_HISTORY_ROWS:
        return None

    df = calculate_indicators(df)

    # 去掉沒有收盤價的資料
    df = df.dropna(
        subset=["close"]
    ).reset_index(drop=True)

    if len(df) < MIN_HISTORY_ROWS:
        return None

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    # --------------------------------------------------------
    # 嚴格檢查最新交易日
    # --------------------------------------------------------

    latest_date = pd.Timestamp(
        latest["date"]
    ).date()

    previous_date = pd.Timestamp(
        previous["date"]
    ).date()

    # 最新日期不得是未來
    if latest_date > today_tw_date():
        return None

    # --------------------------------------------------------
    # 漲跌幅
    # --------------------------------------------------------

    close = safe_float(
        latest["close"]
    )

    previous_close = safe_float(
        previous["close"]
    )

    if (
        close is None or
        previous_close is None or
        previous_close == 0
    ):
        change_pct = None
    else:
        change_pct = (
            (close - previous_close) /
            previous_close
        ) * 100

    # --------------------------------------------------------
    # 核心條件
    # --------------------------------------------------------

    core = evaluate_core_conditions(df)

    ai_score, strength_score = calculate_score(
        df,
        core,
    )

    rating = rating_from_score(
        ai_score
    )

    signal = signal_from_score(
        strength_score
    )

    recommendation = recommendation_from_core(
        core["core_score"]
    )

    result = {
        "code": item["code"],
        "symbol": symbol,
        "name": item["name"],
        "market": item["market"],
        "type": item["type"],

        "price": round(close, 4)
        if close is not None
        else None,

        "change_pct": round(
            change_pct,
            4
        )
        if change_pct is not None
        else None,

        "ai_score": ai_score,
        "strength_score": strength_score,

        "signal": signal,
        "rating": rating,
        "recommendation": recommendation,

        "core_score": core["core_score"],
        "core_total": CORE_TOTAL,
        "core_pass": core["core_pass"],

        # 資料品質欄位
        "market_date": latest_date.isoformat(),
        "previous_market_date": previous_date.isoformat(),

        # 指標資訊
        "indicators": {
            "rsi": safe_float(latest["rsi"]),
            "macd": safe_float(latest["macd"]),
            "macd_signal": safe_float(
                latest["macd_signal"]
            ),
            "macd_hist": safe_float(
                latest["macd_hist"]
            ),
            "k": safe_float(latest["k"]),
            "d": safe_float(latest["d"]),
            "ma5": safe_float(latest["ma5"]),
            "ma20": safe_float(latest["ma20"]),
            "previous_ma20": safe_float(
                previous["ma20"]
            ),
            "volume": safe_float(
                latest["volume"]
            ),
            "volume_ma5": safe_float(
                latest["volume_ma5"]
            ),
        },

        "core_conditions": {
            k: bool(v)
            for k, v in core["conditions"].items()
        },
    }

    return result


# ============================================================
# 最新市場日期
# ============================================================

def determine_latest_market_date(results):
    """
    從實際成功取得的行情中找最新日期。

    絕不使用：
        today_tw_date()

    來硬寫 latest_market_date。
    """

    dates = []

    for item in results:

        d = item.get("market_date")

        if not d:
            continue

        try:
            dt = pd.Timestamp(d).date()

            if dt <= today_tw_date():
                dates.append(dt)

        except Exception:
            continue

    if not dates:
        return None

    return max(dates)


# ============================================================
# 清理日期
# ============================================================

def filter_to_latest_market_date(
    results,
    latest_market_date,
):
    if latest_market_date is None:
        return []

    target = latest_market_date.isoformat()

    output = []

    for item in results:

        if item.get("market_date") != target:
            continue

        output.append(item)

    return output


# ============================================================
# Market Breadth
# ============================================================

def calculate_market_breadth(results):
    rising = 0
    falling = 0
    unchanged = 0

    for item in results:

        change = safe_float(
            item.get("change_pct")
        )

        if change is None:
            continue

        if change > 0:
            rising += 1

        elif change < 0:
            falling += 1

        else:
            unchanged += 1

    return {
        "rising": rising,
        "falling": falling,
        "unchanged": unchanged,
    }


# ============================================================
# Backtest
# ============================================================

def backtest_result_for_symbol(
    symbol,
    core_signal,
    df,
):
    """
    10 個「交易日」後的勝率。

    不使用：
        date + 10 calendar days

    而使用：
        index + 10 trading rows
    """

    if df is None:
        return None

    if len(df) < MIN_HISTORY_ROWS + BACKTEST_HORIZON:
        return None

    try:
        signal_idx = len(df) - 1 - BACKTEST_HORIZON

        if signal_idx < 1:
            return None

        signal_row = df.iloc[signal_idx]

        future_row = df.iloc[
            signal_idx + BACKTEST_HORIZON
        ]

        entry = safe_float(
            signal_row["close"]
        )

        exit_price = safe_float(
            future_row["close"]
        )

        if entry is None or exit_price is None:
            return None

        return exit_price > entry

    except Exception:
        return None


def calculate_backtest(
    analyzed_items,
    history_cache,
):
    """
    保守回測：

    A:
        最新交易日符合 6/6

    B:
        前一交易日符合 6/6

    比較 10 個交易日後上漲比例。
    """

    a_results = []
    b_results = []

    for item in analyzed_items:

        symbol = item["symbol"]

        df = history_cache.get(symbol)

        if df is None:
            continue

        if len(df) < MIN_HISTORY_ROWS + BACKTEST_HORIZON:
            continue

        df = calculate_indicators(df)

        # A：使用倒數第 11 個交易日作為訊號日
        idx_a = len(df) - 1 - BACKTEST_HORIZON

        # B：再往前一天
        idx_b = idx_a - 1

        if idx_b < 1:
            continue

        for idx, bucket in [
            (idx_a, a_results),
            (idx_b, b_results),
        ]:

            row = df.iloc[idx]
            prev = df.iloc[idx - 1]

            conditions = [
                (
                    pd.notna(row["macd"]) and
                    pd.notna(row["macd_signal"]) and
                    row["macd"] > row["macd_signal"]
                ),

                (
                    pd.notna(row["rsi"]) and
                    row["rsi"] > 50
                ),

                (
                    pd.notna(row["k"]) and
                    pd.notna(row["d"]) and
                    row["k"] > row["d"]
                ),

                (
                    pd.notna(row["volume"]) and
                    pd.notna(row["volume_ma5"]) and
                    row["volume"] >= (
                        row["volume_ma5"] * 1.5
                    )
                ),

                (
                    pd.notna(row["close"]) and
                    pd.notna(row["ma20"]) and
                    row["close"] > row["ma20"]
                ),

                (
                    pd.notna(row["ma20"]) and
                    pd.notna(prev["ma20"]) and
                    row["ma20"] > prev["ma20"]
                ),
            ]

            if not all(conditions):
                continue

            future_idx = idx + BACKTEST_HORIZON

            if future_idx >= len(df):
                continue

            entry = safe_float(
                row["close"]
            )

            future = safe_float(
                df.iloc[future_idx]["close"]
            )

            if entry is None or future is None:
                continue

            bucket.append(
                future > entry
            )

    def win_rate(values):
        if not values:
            return None

        return round(
            sum(values) /
            len(values) *
            100,
            2
        )

    a_rate = win_rate(a_results)
    b_rate = win_rate(b_results)

    if a_rate is None and b_rate is None:
        better = None

    elif b_rate is None:
        better = "A_latest_cross"

    elif a_rate is None:
        better = "B_previous_cross"

    elif a_rate > b_rate:
        better = "A_latest_cross"

    elif b_rate > a_rate:
        better = "B_previous_cross"

    else:
        better = "tie"

    return {
        "comparison_horizon": BACKTEST_HORIZON,
        "better_by_win_rate": better,
        "A_10d_win_rate": a_rate,
        "B_10d_win_rate": b_rate,
        "A_sample_count": len(a_results),
        "B_sample_count": len(b_results),
    }


# ============================================================
# 儲存
# ============================================================

def save_json(data):
    os.makedirs(DATA_DIR, exist_ok=True)

    data = clean_json_value(data)

    temp_file = PRICES_FILE + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )

        f.write("\n")

    os.replace(
        temp_file,
        PRICES_FILE,
    )


# ============================================================
# 主程式
# ============================================================

def main():

    print("=" * 64)
    print(
        f"台股 AI 選股系統 fetch_data.py {VERSION}"
    )
    print("=" * 64)

    start_time = now_tw()

    print(
        "開始時間：",
        start_time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print()

    # --------------------------------------------------------
    # 1. 載入股票清單
    # --------------------------------------------------------

    universe = load_existing_universe()

    if not universe:
        print(
            "❌ 無法取得股票 universe。"
        )
        print(
            "❌ 請確認 Data/stocks.json 存在且不是空檔案。"
        )
        sys.exit(1)

    # --------------------------------------------------------
    # 2. 逐檔下載
    # --------------------------------------------------------

    analyzed = []

    history_cache = {}

    total = len(universe)

    success_count = 0
    fail_count = 0

    for idx, item in enumerate(
        universe,
        start=1,
    ):

        symbol = item["symbol"]

        if idx == 1 or idx % 50 == 0:
            print(
                f"[{idx}/{total}] "
                f"成功 {success_count} / "
                f"失敗 {fail_count}"
            )

        df = fetch_yahoo_history(
            symbol
        )

        if df is None:
            fail_count += 1
            time.sleep(REQUEST_SLEEP)
            continue

        if len(df) < MIN_HISTORY_ROWS:
            fail_count += 1
            time.sleep(REQUEST_SLEEP)
            continue

        df = calculate_indicators(
            df
        )

        history_cache[symbol] = df

        result = analyze_symbol(
            item
        )

        if result is None:
            fail_count += 1
            time.sleep(REQUEST_SLEEP)
            continue

        analyzed.append(result)

        success_count += 1

        time.sleep(REQUEST_SLEEP)

    print()
    print(
        f"行情成功：{success_count}"
    )
    print(
        f"行情失敗：{fail_count}"
    )

    # --------------------------------------------------------
    # 3. 決定真正最新交易日
    # --------------------------------------------------------

    latest_market_date = determine_latest_market_date(
        analyzed
    )

    if latest_market_date is None:

        print(
            "❌ 找不到任何有效交易日。"
        )

        sys.exit(1)

    print()
    print(
        "最新有效交易日：",
        latest_market_date.isoformat()
    )

    print(
        "今天台灣日期：",
        today_tw_date().isoformat()
    )

    if latest_market_date < today_tw_date():
        print(
            "ℹ️ 今天不是最新交易日，"
            "使用最近一個有效交易日。"
        )

    # --------------------------------------------------------
    # 4. 嚴格只保留最新交易日
    # --------------------------------------------------------

    analyzed = filter_to_latest_market_date(
        analyzed,
        latest_market_date,
    )

    # --------------------------------------------------------
    # 5. 分類
    # --------------------------------------------------------

    stocks = [
        x for x in analyzed
        if x["type"] == "stock"
    ]

    etfs = [
        x for x in analyzed
        if x["type"] == "etf"
    ]

    bonds = [
        x for x in analyzed
        if x["type"] == "bond"
    ]

    # --------------------------------------------------------
    # 6. 核心選股
    # --------------------------------------------------------

    today_selected = [
        x for x in stocks
        if x["core_pass"] is True
    ]

    # --------------------------------------------------------
    # 7. Top 10
    # --------------------------------------------------------

    top10 = sorted(
        today_selected,
        key=lambda x: (
            x.get("ai_score") or 0,
            x.get("strength_score") or 0,
        ),
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # 8. ETF 排序
    # --------------------------------------------------------

    etfs = sorted(
        etfs,
        key=lambda x: (
            x.get("ai_score") or 0,
            x.get("strength_score") or 0,
        ),
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # 9. Bond 排序
    # --------------------------------------------------------

    bonds = sorted(
        bonds,
        key=lambda x: (
            x.get("ai_score") or 0,
            x.get("strength_score") or 0,
        ),
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # 10. Market Breadth
    # --------------------------------------------------------

    market_breadth = calculate_market_breadth(
        analyzed
    )

    # --------------------------------------------------------
    # 11. Backtest
    # --------------------------------------------------------

    backtest = calculate_backtest(
        stocks,
        history_cache,
    )

    # --------------------------------------------------------
    # 12. Universe 統計
    # --------------------------------------------------------

    universe_stock_count = sum(
        1
        for x in universe
        if x["type"] == "stock"
    )

    universe_etf_count = sum(
        1
        for x in universe
        if x["type"] == "etf"
    )

    universe_bond_count = sum(
        1
        for x in universe
        if x["type"] == "bond"
    )

    # --------------------------------------------------------
    # 13. JSON
    # --------------------------------------------------------

    output = {

        "version": VERSION,

        "schema_version": SCHEMA_VERSION,

        "status": "success",

        # ----------------------------------------------------
        # 最重要：
        # date 不再使用今天日期
        # 而是使用真正最新市場交易日
        # ----------------------------------------------------

        "date": latest_market_date.isoformat(),

        "latest_market_date":
            latest_market_date.isoformat(),

        "updated_at":
            start_time.isoformat(),

        "updated_at_tw":
            start_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            f"fetch_data.py {VERSION}",

        "data_quality": {

            "today_is_market_date":
                latest_market_date ==
                today_tw_date(),

            "latest_market_date_valid":
                True,

            "non_trading_day_protected":
                latest_market_date !=
                today_tw_date(),

            "analyzed_count":
                len(analyzed),

            "successful_history_count":
                success_count,

            "failed_history_count":
                fail_count,

            "min_history_rows":
                MIN_HISTORY_ROWS,

            "backtest_horizon":
                BACKTEST_HORIZON,
        },

        "summary": {

            "stock_count":
                len(stocks),

            "etf_count":
                len(etfs),

            "bond_count":
                len(bonds),

            "today_selected_count":
                len(today_selected),

            "top10_count":
                len(top10),

            "market_breadth":
                market_breadth,

            "core_condition_count":
                CORE_TOTAL,
        },

        "core_conditions": {

            "total":
                CORE_TOTAL,

            "names":
                CORE_CONDITION_NAMES,
        },

        "today_selected":
            today_selected,

        "top10":
            top10,

        "etfs":
            etfs,

        "bonds":
            bonds,

        "backtest_summary":
            backtest,

        "universe": {

            "stock_count":
                universe_stock_count,

            "etf_count":
                universe_etf_count,

            "bond_count":
                universe_bond_count,

            "total_count":
                len(universe),
        },
    }

    # --------------------------------------------------------
    # 14. 寫檔
    # --------------------------------------------------------

    save_json(output)

    # --------------------------------------------------------
    # 15. 最終驗證
    # --------------------------------------------------------

    print()
    print("=" * 64)
    print("V10.1 完成")
    print("=" * 64)

    print(
        "date：",
        output["date"]
    )

    print(
        "latest_market_date：",
        output["latest_market_date"]
    )

    print(
        "股票：",
        len(stocks)
    )

    print(
        "ETF：",
        len(etfs)
    )

    print(
        "債券：",
        len(bonds)
    )

    print(
        "6/6 核心選股：",
        len(today_selected)
    )

    print(
        "Top 10：",
        len(top10)
    )

    print(
        "市場上漲：",
        market_breadth["rising"]
    )

    print(
        "市場下跌：",
        market_breadth["falling"]
    )

    print(
        "市場平盤：",
        market_breadth["unchanged"]
    )

    print()
    print(
        "輸出檔案：",
        PRICES_FILE
    )

    print(
        "完成時間：",
        now_tw().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print("=" * 64)


if __name__ == "__main__":
    main()
