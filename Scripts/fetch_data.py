#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 fetch_data.py V10.3

V10.3 核心修正
============================================================
1. stocks.json 不再是不可恢復的前置依賴
2. stocks.json 不存在 / 空檔 / 格式錯誤時，自動恢復 universe
3. 優先使用 stocks.json
4. stocks.json 無法使用時，優先從既有 prices.json["universe"]["items"] 恢復
5. 不再從 today_selected / top10 / etfs / bonds 反推 Universe
6. prices.json 保存完整 universe items，避免 Universe 自我縮小
7. 若 prices.json 也無法恢復，使用固定 FALLBACK_UNIVERSE
8. 不使用今天日期假造市場收盤日
9. latest_market_date 一律使用實際取得行情的最新交易日
10. date 與 latest_market_date 完全一致
11. change_pct 無資料時保持 null，不灌 0
12. RSI / MACD / KD / MA5 / MA20 統一使用同一交易日
13. 六項核心條件必須同一有效交易日全部成立
14. today_selected = 最新有效交易日 6/6 結果
15. market_breadth 只統計最新有效交易日
16. 非交易日不產生虛假收盤資料
17. 歷史資料不足不允許誤判
18. ETF / Bond / Stock 分類保留
19. backtest 使用交易日而非日曆日
20. JSON 加入資料品質資訊
21. Universe source / recovery 狀態寫入 prices.json
22. 原子寫入 prices.json
23. 避免 fetch_data.py 因 stocks.json 缺失直接 exit 1
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

VERSION = "V10.3"
SCHEMA_VERSION = "ui.v10"

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DATA_DIR = os.path.join(
    BASE_DIR,
    "Data"
)

PRICES_FILE = os.path.join(
    DATA_DIR,
    "prices.json"
)

STOCKS_FILE = os.path.join(
    DATA_DIR,
    "stocks.json"
)

YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

TIMEZONE_TW = timezone(
    timedelta(hours=8)
)

HISTORY_PERIOD_DAYS = 260

MIN_HISTORY_ROWS = 80

BACKTEST_HORIZON = 10

REQUEST_SLEEP = 0.15


# ============================================================
# 六項核心條件
# ============================================================

CORE_CONDITION_NAMES = [
    "MACD 多方",
    "RSI > 50",
    "KD 多方",
    "成交量 ≥ MA5 × 1.5",
    "股價 > MA20",
    "MA20 今日 > 昨日",
]

CORE_TOTAL = len(
    CORE_CONDITION_NAMES
)


# ============================================================
# 固定恢復 Universe
#
# 僅在：
# 1. stocks.json 無法使用
# 2. prices.json 也沒有完整 universe
#
# 時才啟用。
# ============================================================

FALLBACK_UNIVERSE = [
    {
        "code": "0050",
        "symbol": "0050.TW",
        "name": "元大台灣50",
        "market": "TW",
        "type": "etf",
    },
    {
        "code": "0056",
        "symbol": "0056.TW",
        "name": "元大高股息",
        "market": "TW",
        "type": "etf",
    },
    {
        "code": "00713",
        "symbol": "00713.TW",
        "name": "元大台灣高息低波",
        "market": "TW",
        "type": "etf",
    },
    {
        "code": "2884",
        "symbol": "2884.TW",
        "name": "玉山金",
        "market": "TW",
        "type": "stock",
    },
    {
        "code": "2891",
        "symbol": "2891.TW",
        "name": "中信金",
        "market": "TW",
        "type": "stock",
    },
    {
        "code": "2330",
        "symbol": "2330.TW",
        "name": "台積電",
        "market": "TW",
        "type": "stock",
    },
    {
        "code": "3081",
        "symbol": "3081.TWO",
        "name": "聯亞",
        "market": "TWO",
        "type": "stock",
    },
    {
        "code": "2368",
        "symbol": "2368.TW",
        "name": "金像電",
        "market": "TW",
        "type": "stock",
    },
    {
        "code": "6669",
        "symbol": "6669.TW",
        "name": "緯穎",
        "market": "TW",
        "type": "stock",
    },
    {
        "code": "1303",
        "symbol": "1303.TW",
        "name": "南亞",
        "market": "TW",
        "type": "stock",
    },
    {
        "code": "3017",
        "symbol": "3017.TW",
        "name": "奇鋐",
        "market": "TW",
        "type": "stock",
    },
]


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Accept": (
            "application/json,text/plain,*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,en;q=0.8"
        ),
    }
)


# ============================================================
# 時間
# ============================================================

def now_tw():
    return datetime.now(
        TIMEZONE_TW
    )


def today_tw_date():
    return now_tw().date()


# ============================================================
# 安全數字
# ============================================================

def safe_float(
    value,
    default=None
):
    try:

        if value is None:
            return default

        x = float(value)

        if not math.isfinite(x):
            return default

        return x

    except Exception:
        return default


# ============================================================
# JSON 清理
# ============================================================

def clean_json_value(value):

    if isinstance(value, dict):
        return {
            str(k): clean_json_value(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            clean_json_value(v)
            for v in value
        ]

    if isinstance(
        value,
        np.integer
    ):
        return int(value)

    if isinstance(
        value,
        np.floating
    ):

        if not np.isfinite(value):
            return None

        return float(value)

    if isinstance(value, float):

        if not math.isfinite(value):
            return None

        return value

    try:

        if pd.isna(value):
            return None

    except Exception:
        pass

    return value


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(symbol):

    if symbol is None:
        return None

    s = str(symbol).strip()

    if not s:
        return None

    if (
        s.endswith(".TW")
        or s.endswith(".TWO")
    ):
        return s

    if "." in s:
        return s

    if s.isdigit():

        if len(s) <= 4:
            return (
                s.zfill(4)
                + ".TW"
            )

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


# ============================================================
# 分類
# ============================================================

def infer_type(
    code,
    name="",
    existing_type=None
):

    if existing_type:

        t = str(
            existing_type
        ).lower()

        if t in (
            "stock",
            "stocks",
            "equity"
        ):
            return "stock"

        if t == "etf":
            return "etf"

        if t in (
            "bond",
            "bond_etf",
            "bond-fund"
        ):
            return "bond"

    c = str(code).upper()

    n = str(name)

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

    if any(
        k in n
        for k in bond_keywords
    ):
        return "bond"

    if c.startswith("00"):
        return "etf"

    etf_keywords = [
        "ETF",
        "高股息",
        "台灣50",
        "永續",
        "正2",
        "反1",
        "科技",
        "AI",
        "航運",
        "資安",
        "收息",
        "優選",
        "成長",
        "ESG",
    ]

    upper_name = n.upper()

    if any(
        k.upper() in upper_name
        for k in etf_keywords
    ):
        return "etf"

    return "stock"


# ============================================================
# Universe normalize
# ============================================================

def normalize_universe_items(
    raw_items
):

    result = []

    if not isinstance(
        raw_items,
        list
    ):
        return []

    for item in raw_items:

        if isinstance(
            item,
            str
        ):

            symbol = normalize_symbol(
                item
            )

            if symbol:

                code = extract_code(
                    symbol
                )

                result.append(
                    {
                        "code": code,
                        "symbol": symbol,
                        "name": code,
                        "market":
                            infer_market(
                                symbol
                            ),
                        "type":
                            infer_type(
                                code
                            ),
                    }
                )

            continue

        if not isinstance(
            item,
            dict
        ):
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

            symbol = normalize_symbol(
                code
            )

        else:

            symbol = normalize_symbol(
                symbol
            )

        if not symbol:
            continue

        code = str(
            code
            or extract_code(symbol)
        ).strip()

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
            existing_type
        )

        result.append(
            {
                "code": code,
                "symbol": symbol,
                "name": str(name),
                "market": str(market),
                "type": asset_type,
            }
        )

    unique = {}

    for item in result:

        unique[
            item["symbol"]
        ] = item

    return list(
        unique.values()
    )


# ============================================================
# stocks.json
# ============================================================

def load_stocks_json():

    if not os.path.exists(
        STOCKS_FILE
    ):

        print(
            "⚠️ 找不到 Data/stocks.json"
        )

        return []

    try:

        if os.path.getsize(
            STOCKS_FILE
        ) == 0:

            print(
                "⚠️ Data/stocks.json 是空檔案"
            )

            return []

    except Exception:
        pass

    try:

        with open(
            STOCKS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except Exception as e:

        print(
            f"⚠️ stocks.json 讀取失敗：{e}"
        )

        return []

    if isinstance(
        data,
        list
    ):

        raw_items = data

    elif isinstance(
        data,
        dict
    ):

        raw_items = []

        for key in (
            "stocks",
            "data",
            "items",
            "universe",
            "symbols"
        ):

            if isinstance(
                data.get(key),
                list
            ):

                raw_items = data[key]
                break

    else:

        raw_items = []

    result = normalize_universe_items(
        raw_items
    )

    print(
        f"📚 stocks.json universe：{len(result)}"
    )

    return result


# ============================================================
# 從 prices.json 恢復完整 Universe
#
# V10.3 重要修正：
#
# 只接受：
#
# prices.json["universe"]["items"]
#
# 不再從：
#
# today_selected
# top10
# etfs
# bonds
# data
#
# 反推 Universe。
# ============================================================

def load_universe_from_prices():

    if not os.path.exists(
        PRICES_FILE
    ):

        print(
            "ℹ️ 找不到既有 prices.json"
        )

        return []

    try:

        if os.path.getsize(
            PRICES_FILE
        ) == 0:

            print(
                "⚠️ 既有 prices.json 是空檔"
            )

            return []

    except Exception:
        pass

    try:

        with open(
            PRICES_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except Exception as e:

        print(
            f"⚠️ prices.json 讀取失敗：{e}"
        )

        return []

    if not isinstance(
        data,
        dict
    ):

        return []

    universe = data.get(
        "universe"
    )

    if not isinstance(
        universe,
        dict
    ):

        print(
            "⚠️ prices.json 沒有完整 universe 結構"
        )

        return []

    items = universe.get(
        "items"
    )

    if not isinstance(
        items,
        list
    ):

        print(
            "⚠️ prices.json universe.items 不存在或格式錯誤"
        )

        return []

    result = normalize_universe_items(
        items
    )

    if result:

        print(
            "♻️ 已從既有 prices.json "
            f"恢復完整 universe：{len(result)} 檔"
        )

    return result


# ============================================================
# Universe
# ============================================================

def load_existing_universe():

    # --------------------------------------------------------
    # 第一順位：stocks.json
    # --------------------------------------------------------

    result = load_stocks_json()

    if result:

        print(
            "✅ Universe source：stocks.json"
        )

        return result, "stocks.json", False

    # --------------------------------------------------------
    # 第二順位：prices.json 完整 universe
    # --------------------------------------------------------

    result = load_universe_from_prices()

    if result:

        print(
            "⚠️ stocks.json 無法使用"
        )

        print(
            "✅ Universe source：prices.json"
        )

        return result, "prices.json", True

    # --------------------------------------------------------
    # 第三順位：固定恢復清單
    # --------------------------------------------------------

    print(
        "⚠️ 無法從 stocks.json / prices.json "
        "取得完整 universe"
    )

    print(
        "♻️ Universe source：fallback"
    )

    result = normalize_universe_items(
        FALLBACK_UNIVERSE
    )

    print(
        f"✅ 固定恢復 universe：{len(result)} 檔"
    )

    return result, "fallback", True


# ============================================================
# Yahoo
# ============================================================

def fetch_yahoo_history(
    symbol
):

    period2 = int(
        time.time()
    )

    period1 = (
        period2
        - HISTORY_PERIOD_DAYS * 86400
    )

    url = YAHOO_CHART_URL.format(
        symbol=symbol
    )

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
            timeout=20
        )

        if response.status_code != 200:

            print(
                f"⚠️ {symbol} HTTP "
                f"{response.status_code}"
            )

            return None

        payload = response.json()

        chart = payload.get(
            "chart",
            {}
        )

        if chart.get("error"):

            return None

        results = chart.get(
            "result"
        )

        if not results:
            return None

        result = results[0]

        timestamps = result.get(
            "timestamp"
        )

        indicators = result.get(
            "indicators",
            {}
        )

        quote_list = indicators.get(
            "quote",
            []
        )

        if (
            not timestamps
            or not quote_list
        ):
            return None

        quote = quote_list[0]

        adjclose_list = indicators.get(
            "adjclose",
            []
        )

        adjclose = None

        if adjclose_list:

            adjclose = (
                adjclose_list[0]
                .get("adjclose")
            )

        rows = []

        close_list = quote.get(
            "close",
            []
        )

        open_list = quote.get(
            "open",
            []
        )

        high_list = quote.get(
            "high",
            []
        )

        low_list = quote.get(
            "low",
            []
        )

        volume_list = quote.get(
            "volume",
            []
        )

        for i, ts in enumerate(
            timestamps
        ):

            try:

                dt = datetime.fromtimestamp(
                    ts,
                    tz=timezone.utc
                ).astimezone(
                    TIMEZONE_TW
                )

                date_str = dt.strftime(
                    "%Y-%m-%d"
                )

            except Exception:

                continue

            close = (
                close_list[i]
                if i < len(close_list)
                else None
            )

            open_price = (
                open_list[i]
                if i < len(open_list)
                else None
            )

            high = (
                high_list[i]
                if i < len(high_list)
                else None
            )

            low = (
                low_list[i]
                if i < len(low_list)
                else None
            )

            volume = (
                volume_list[i]
                if i < len(volume_list)
                else None
            )

            adj = (
                adjclose[i]
                if (
                    adjclose is not None
                    and i < len(adjclose)
                )
                else close
            )

            close = safe_float(
                close
            )

            if close is None:
                continue

            rows.append(
                {
                    "date": date_str,

                    "open":
                        safe_float(
                            open_price
                        ),

                    "high":
                        safe_float(
                            high
                        ),

                    "low":
                        safe_float(
                            low
                        ),

                    "close":
                        close,

                    "adj_close":
                        safe_float(
                            adj,
                            close
                        ),

                    "volume":
                        safe_float(
                            volume,
                            0
                        ),
                }
            )

        if not rows:
            return None

        df = pd.DataFrame(
            rows
        )

        df["date"] = pd.to_datetime(
            df["date"],
            errors="coerce"
        )

        df = df.dropna(
            subset=[
                "date",
                "close"
            ]
        )

        df = df.sort_values(
            "date"
        )

        df = df.drop_duplicates(
            subset=["date"],
            keep="last"
        )

        df = df.reset_index(
            drop=True
        )

        return df

    except Exception as e:

        print(
            f"⚠️ {symbol} Yahoo 讀取失敗：{e}"
        )

        return None


# ============================================================
# 技術指標
# ============================================================

def calculate_indicators(
    df
):

    df = df.copy()

    close = pd.to_numeric(
        df["close"],
        errors="coerce"
    )

    high = pd.to_numeric(
        df["high"],
        errors="coerce"
    )

    low = pd.to_numeric(
        df["low"],
        errors="coerce"
    )

    volume = pd.to_numeric(
        df["volume"],
        errors="coerce"
    ).fillna(0)

    # --------------------------------------------------------
    # MA5
    # --------------------------------------------------------

    df["ma5"] = close.rolling(
        5,
        min_periods=5
    ).mean()

    # --------------------------------------------------------
    # MA20
    # --------------------------------------------------------

    df["ma20"] = close.rolling(
        20,
        min_periods=20
    ).mean()

    # --------------------------------------------------------
    # MACD 12 / 26 / 9
    # --------------------------------------------------------

    ema12 = close.ewm(
        span=12,
        adjust=False,
        min_periods=12
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False,
        min_periods=26
    ).mean()

    df["macd"] = (
        ema12 - ema26
    )

    df["macd_signal"] = (
        df["macd"].ewm(
            span=9,
            adjust=False,
            min_periods=9
        ).mean()
    )

    df["macd_hist"] = (
        df["macd"]
        - df["macd_signal"]
    )

    # --------------------------------------------------------
    # RSI 14
    # --------------------------------------------------------

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            np.nan
        )
    )

    df["rsi"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    df.loc[
        (
            avg_loss == 0
        )
        & (
            avg_gain > 0
        ),
        "rsi"
    ] = 100.0

    # --------------------------------------------------------
    # KD 9 / 3 / 3
    # --------------------------------------------------------

    lowest_low = low.rolling(
        9,
        min_periods=9
    ).min()

    highest_high = high.rolling(
        9,
        min_periods=9
    ).max()

    denominator = (
        highest_high
        - lowest_low
    )

    rsv = (
        (
            close
            - lowest_low
        )
        / denominator.replace(
            0,
            np.nan
        )
    ) * 100

    k_values = []
    d_values = []

    previous_k = 50.0
    previous_d = 50.0

    for value in rsv:

        if pd.isna(value):

            k_values.append(
                np.nan
            )

            d_values.append(
                np.nan
            )

            continue

        current_k = (
            previous_k * 2 / 3
            + float(value) / 3
        )

        current_d = (
            previous_d * 2 / 3
            + current_k / 3
        )

        k_values.append(
            current_k
        )

        d_values.append(
            current_d
        )

        previous_k = current_k
        previous_d = current_d

    df["k"] = k_values

    df["d"] = d_values

    # --------------------------------------------------------
    # Volume MA5
    # --------------------------------------------------------

    df["volume_ma5"] = volume.rolling(
        5,
        min_periods=5
    ).mean()

    return df


# ============================================================
# 六項條件
# ============================================================

def evaluate_core_conditions(
    df
):

    if (
        df is None
        or len(df) < MIN_HISTORY_ROWS
    ):

        return {
            "core_score": 0,
            "core_total": CORE_TOTAL,
            "core_pass": False,
            "conditions": {},
        }

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    conditions = {}

    # --------------------------------------------------------
    # 1. MACD 多方
    # --------------------------------------------------------

    conditions[
        "MACD 多方"
    ] = (
        pd.notna(
            latest["macd"]
        )
        and pd.notna(
            latest["macd_signal"]
        )
        and latest["macd"]
        > latest["macd_signal"]
    )

    # --------------------------------------------------------
    # 2. RSI > 50
    # --------------------------------------------------------

    conditions[
        "RSI > 50"
    ] = (
        pd.notna(
            latest["rsi"]
        )
        and latest["rsi"] > 50
    )

    # --------------------------------------------------------
    # 3. KD 多方
    # --------------------------------------------------------

    conditions[
        "KD 多方"
    ] = (
        pd.notna(
            latest["k"]
        )
        and pd.notna(
            latest["d"]
        )
        and latest["k"]
        > latest["d"]
    )

    # --------------------------------------------------------
    # 4. 成交量 >= MA5 * 1.5
    # --------------------------------------------------------

    conditions[
        "成交量 ≥ MA5 × 1.5"
    ] = (
        pd.notna(
            latest["volume"]
        )
        and pd.notna(
            latest["volume_ma5"]
        )
        and latest["volume"]
        >= (
            latest["volume_ma5"]
            * 1.5
        )
    )

    # --------------------------------------------------------
    # 5. 股價 > MA20
    # --------------------------------------------------------

    conditions[
        "股價 > MA20"
    ] = (
        pd.notna(
            latest["close"]
        )
        and pd.notna(
            latest["ma20"]
        )
        and latest["close"]
        > latest["ma20"]
    )

    # --------------------------------------------------------
    # 6. MA20 今日 > 昨日
    # --------------------------------------------------------

    conditions[
        "MA20 今日 > 昨日"
    ] = (
        pd.notna(
            latest["ma20"]
        )
        and pd.notna(
            previous["ma20"]
        )
        and latest["ma20"]
        > previous["ma20"]
    )

    score = sum(
        1
        for value in conditions.values()
        if bool(value)
    )

    return {
        "core_score": score,
        "core_total": CORE_TOTAL,
        "core_pass":
            score == CORE_TOTAL,
        "conditions":
            conditions,
    }


# ============================================================
# Score
# ============================================================

def calculate_score(
    df,
    core
):

    if (
        df is None
        or len(df) < MIN_HISTORY_ROWS
    ):
        return 0.0, 0.0

    latest = df.iloc[-1]

    score = (
        core["core_score"]
        / CORE_TOTAL
    ) * 70.0

    rsi = safe_float(
        latest.get("rsi")
    )

    if rsi is not None:

        if rsi >= 70:

            score += 5.0

        elif rsi > 50:

            score += 8.0

        elif rsi >= 45:

            score += 3.0

    macd_hist = safe_float(
        latest.get(
            "macd_hist"
        )
    )

    if (
        macd_hist is not None
        and macd_hist > 0
    ):

        score += 5.0

    close = safe_float(
        latest.get("close")
    )

    ma20 = safe_float(
        latest.get("ma20")
    )

    if (
        close is not None
        and ma20 is not None
        and ma20 != 0
    ):

        bias = (
            close / ma20 - 1
        ) * 100

        if bias >= 0:

            score += min(
                max(bias, 0),
                5
            )

    volume = safe_float(
        latest.get("volume")
    )

    volume_ma5 = safe_float(
        latest.get(
            "volume_ma5"
        )
    )

    if (
        volume is not None
        and volume_ma5 is not None
        and volume_ma5 > 0
    ):

        ratio = (
            volume
            / volume_ma5
        )

        if ratio >= 1.5:

            score += 7.0

        elif ratio >= 1.0:

            score += 3.0

    score = min(
        max(score, 0),
        100
    )

    strength = (
        core["core_score"]
        / CORE_TOTAL
    ) * 100

    if (
        macd_hist is not None
        and macd_hist > 0
    ):

        strength += 3

    if (
        rsi is not None
        and rsi > 50
    ):

        strength += 3

    strength = min(
        max(strength, 0),
        100
    )

    return (
        round(score, 2),
        round(strength, 2)
    )


# ============================================================
# Rating
# ============================================================

def rating_from_score(
    score
):

    if score >= 90:
        return "A+"

    if score >= 80:
        return "A"

    if score >= 70:
        return "B"

    if score >= 60:
        return "C"

    return "D"


def signal_from_score(
    score
):

    if score >= 80:
        return "強勢多方"

    if score >= 65:
        return "偏多"

    if score >= 50:
        return "中性"

    if score >= 35:
        return "偏弱"

    return "弱勢"


def recommendation_from_core(
    score
):

    if score == CORE_TOTAL:

        return (
            f"符合 {CORE_TOTAL}/{CORE_TOTAL} 核心條件"
        )

    if score >= CORE_TOTAL - 1:

        return "接近核心條件"

    if score >= CORE_TOTAL - 2:

        return "部分符合條件"

    return "暫不操作"


# ============================================================
# Analyze
# ============================================================

def analyze_symbol(
    item,
    df
):

    if df is None:
        return None

    if len(df) < MIN_HISTORY_ROWS:
        return None

    df = calculate_indicators(
        df
    )

    df = df.dropna(
        subset=["close"]
    ).reset_index(
        drop=True
    )

    if len(df) < MIN_HISTORY_ROWS:
        return None

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    latest_date = pd.Timestamp(
        latest["date"]
    ).date()

    previous_date = pd.Timestamp(
        previous["date"]
    ).date()

    # --------------------------------------------------------
    # 嚴格禁止未來日期
    # --------------------------------------------------------

    if latest_date > today_tw_date():
        return None

    close = safe_float(
        latest["close"]
    )

    previous_close = safe_float(
        previous["close"]
    )

    if (
        close is None
        or previous_close is None
        or previous_close == 0
    ):

        change_pct = None

    else:

        change_pct = (
            (
                close
                - previous_close
            )
            / previous_close
        ) * 100

    core = evaluate_core_conditions(
        df
    )

    ai_score, strength_score = (
        calculate_score(
            df,
            core
        )
    )

    return {

        "code":
            item["code"],

        "symbol":
            item["symbol"],

        "name":
            item["name"],

        "market":
            item["market"],

        "type":
            item["type"],

        "price":
            round(
                close,
                4
            )
            if close is not None
            else None,

        "change_pct":
            round(
                change_pct,
                4
            )
            if change_pct is not None
            else None,

        "ai_score":
            ai_score,

        "strength_score":
            strength_score,

        "signal":
            signal_from_score(
                strength_score
            ),

        "rating":
            rating_from_score(
                ai_score
            ),

        "recommendation":
            recommendation_from_core(
                core["core_score"]
            ),

        "core_score":
            core["core_score"],

        "core_total":
            CORE_TOTAL,

        "core_pass":
            core["core_pass"],

        "market_date":
            latest_date.isoformat(),

        "previous_market_date":
            previous_date.isoformat(),

        "indicators": {

            "rsi":
                safe_float(
                    latest["rsi"]
                ),

            "macd":
                safe_float(
                    latest["macd"]
                ),

            "macd_signal":
                safe_float(
                    latest["macd_signal"]
                ),

            "macd_hist":
                safe_float(
                    latest["macd_hist"]
                ),

            "k":
                safe_float(
                    latest["k"]
                ),

            "d":
                safe_float(
                    latest["d"]
                ),

            "ma5":
                safe_float(
                    latest["ma5"]
                ),

            "ma20":
                safe_float(
                    latest["ma20"]
                ),

            "previous_ma20":
                safe_float(
                    previous["ma20"]
                ),

            "volume":
                safe_float(
                    latest["volume"]
                ),

            "volume_ma5":
                safe_float(
                    latest["volume_ma5"]
                ),
        },

        "core_conditions": {
            key: bool(value)
            for key, value
            in core["conditions"].items()
        },
    }


# ============================================================
# 最新交易日
# ============================================================

def determine_latest_market_date(
    results
):

    dates = []

    for item in results:

        value = item.get(
            "market_date"
        )

        if not value:
            continue

        try:

            dt = pd.Timestamp(
                value
            ).date()

            if dt <= today_tw_date():

                dates.append(
                    dt
                )

        except Exception:

            continue

    if not dates:
        return None

    return max(dates)


# ============================================================
# 僅保留最新交易日
# ============================================================

def filter_to_latest_market_date(
    results,
    latest_market_date
):

    if latest_market_date is None:
        return []

    target = (
        latest_market_date
        .isoformat()
    )

    return [
        item
        for item in results
        if item.get(
            "market_date"
        ) == target
    ]


# ============================================================
# Breadth
# ============================================================

def calculate_market_breadth(
    results
):

    rising = 0
    falling = 0
    unchanged = 0

    for item in results:

        change = safe_float(
            item.get(
                "change_pct"
            )
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

        "rising":
            rising,

        "falling":
            falling,

        "unchanged":
            unchanged,

        "total_with_change":
            rising
            + falling
            + unchanged,
    }


# ============================================================
# Backtest
# ============================================================

def calculate_backtest(
    universe,
    history_cache
):

    a_results = []
    b_results = []

    eligible_symbols = 0

    for item in universe:

        symbol = item[
            "symbol"
        ]

        df = history_cache.get(
            symbol
        )

        if df is None:
            continue

        if len(df) < (
            MIN_HISTORY_ROWS
            + BACKTEST_HORIZON
        ):

            continue

        eligible_symbols += 1

        df = calculate_indicators(
            df
        )

        idx_a = (
            len(df)
            - 1
            - BACKTEST_HORIZON
        )

        idx_b = idx_a - 1

        if idx_b < 1:
            continue

        for idx, bucket in (
            (idx_a, a_results),
            (idx_b, b_results),
        ):

            row = df.iloc[
                idx
            ]

            prev = df.iloc[
                idx - 1
            ]

            conditions = [

                (
                    pd.notna(
                        row["macd"]
                    )
                    and pd.notna(
                        row["macd_signal"]
                    )
                    and row["macd"]
                    > row["macd_signal"]
                ),

                (
                    pd.notna(
                        row["rsi"]
                    )
                    and row["rsi"] > 50
                ),

                (
                    pd.notna(
                        row["k"]
                    )
                    and pd.notna(
                        row["d"]
                    )
                    and row["k"]
                    > row["d"]
                ),

                (
                    pd.notna(
                        row["volume"]
                    )
                    and pd.notna(
                        row["volume_ma5"]
                    )
                    and row["volume"]
                    >= (
                        row["volume_ma5"]
                        * 1.5
                    )
                ),

                (
                    pd.notna(
                        row["close"]
                    )
                    and pd.notna(
                        row["ma20"]
                    )
                    and row["close"]
                    > row["ma20"]
                ),

                (
                    pd.notna(
                        row["ma20"]
                    )
                    and pd.notna(
                        prev["ma20"]
                    )
                    and row["ma20"]
                    > prev["ma20"]
                ),
            ]

            if not all(
                conditions
            ):
                continue

            future_idx = (
                idx
                + BACKTEST_HORIZON
            )

            if future_idx >= len(df):
                continue

            entry = safe_float(
                row["close"]
            )

            future = safe_float(
                df.iloc[
                    future_idx
                ]["close"]
            )

            if (
                entry is None
                or future is None
            ):

                continue

            bucket.append(
                future > entry
            )

    def win_rate(values):

        if not values:
            return None

        return round(
            sum(values)
            / len(values)
            * 100,
            2
        )

    a_rate = win_rate(
        a_results
    )

    b_rate = win_rate(
        b_results
    )

    if (
        a_rate is None
        and b_rate is None
    ):

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

        "comparison_horizon":
            BACKTEST_HORIZON,

        "method":
            "trading_days",

        "better_by_win_rate":
            better,

        "A_10d_win_rate":
            a_rate,

        "B_10d_win_rate":
            b_rate,

        "A_sample_count":
            len(a_results),

        "B_sample_count":
            len(b_results),

        "eligible_history_count":
            eligible_symbols,
    }


# ============================================================
# Universe summary
# ============================================================

def build_universe_summary(
    universe
):

    stock_count = sum(
        1
        for x in universe
        if x["type"] == "stock"
    )

    etf_count = sum(
        1
        for x in universe
        if x["type"] == "etf"
    )

    bond_count = sum(
        1
        for x in universe
        if x["type"] == "bond"
    )

    return {

        "stock_count":
            stock_count,

        "etf_count":
            etf_count,

        "bond_count":
            bond_count,

        "total_count":
            len(universe),

        "items":
            universe,
    }


# ============================================================
# Save
# ============================================================

def save_json(
    data
):

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    data = clean_json_value(
        data
    )

    temp_file = (
        PRICES_FILE
        + ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        )

        f.write("\n")

    os.replace(
        temp_file,
        PRICES_FILE
    )


# ============================================================
# Main
# ============================================================

def main():

    print("=" * 64)

    print(
        "台股 AI 選股系統 "
        f"fetch_data.py {VERSION}"
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

    # ========================================================
    # 1. Universe
    # ========================================================

    (
        universe,
        universe_source,
        universe_recovered
    ) = load_existing_universe()

    if not universe:

        print(
            "❌ 無法建立股票 Universe"
        )

        sys.exit(1)

    print(
        f"📊 本次掃描 Universe："
        f"{len(universe)} 檔"
    )

    print(
        f"📌 Universe source："
        f"{universe_source}"
    )

    print(
        f"📌 Universe recovered："
        f"{universe_recovered}"
    )

    print()

    # ========================================================
    # 2. Fetch
    # ========================================================

    analyzed = []

    history_cache = {}

    total = len(
        universe
    )

    success_count = 0
    fail_count = 0

    for idx, item in enumerate(
        universe,
        start=1
    ):

        symbol = item[
            "symbol"
        ]

        if (
            idx == 1
            or idx % 20 == 0
            or idx == total
        ):

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

            time.sleep(
                REQUEST_SLEEP
            )

            continue

        if len(df) < MIN_HISTORY_ROWS:

            fail_count += 1

            time.sleep(
                REQUEST_SLEEP
            )

            continue

        history_cache[
            symbol
        ] = df

        result = analyze_symbol(
            item,
            df
        )

        if result is None:

            fail_count += 1

            time.sleep(
                REQUEST_SLEEP
            )

            continue

        analyzed.append(
            result
        )

        success_count += 1

        time.sleep(
            REQUEST_SLEEP
        )

    print()

    print(
        f"行情成功：{success_count}"
    )

    print(
        f"行情失敗：{fail_count}"
    )

    if not analyzed:

        print(
            "❌ 沒有任何有效行情資料"
        )

        sys.exit(1)

    # ========================================================
    # 3. 最新有效交易日
    # ========================================================

    latest_market_date = (
        determine_latest_market_date(
            analyzed
        )
    )

    if latest_market_date is None:

        print(
            "❌ 找不到有效交易日"
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

    if (
        latest_market_date
        < today_tw_date()
    ):

        print(
            "ℹ️ 今天沒有新的有效行情，"
            "使用最近一個有效交易日。"
        )

    # ========================================================
    # 4. 同一交易日
    # ========================================================

    analyzed = (
        filter_to_latest_market_date(
            analyzed,
            latest_market_date
        )
    )

    if not analyzed:

        print(
            "❌ 最新交易日沒有有效資料"
        )

        sys.exit(1)

    # ========================================================
    # 5. Classification
    # ========================================================

    stocks = [
        x
        for x in analyzed
        if x["type"] == "stock"
    ]

    etfs = [
        x
        for x in analyzed
        if x["type"] == "etf"
    ]

    bonds = [
        x
        for x in analyzed
        if x["type"] == "bond"
    ]

    # ========================================================
    # 6. 6/6
    # ========================================================

    today_selected = [
        x
        for x in stocks
        if x["core_pass"] is True
    ]

    # ========================================================
    # 7. Top10
    # ========================================================

    top10 = sorted(
        today_selected,
        key=lambda x: (
            x.get(
                "ai_score"
            ) or 0,

            x.get(
                "strength_score"
            ) or 0,
        ),
        reverse=True
    )[:10]

    # ========================================================
    # 8. ETF
    # ========================================================

    etfs = sorted(
        etfs,
        key=lambda x: (
            x.get(
                "ai_score"
            ) or 0,

            x.get(
                "strength_score"
            ) or 0,
        ),
        reverse=True
    )[:10]

    # ========================================================
    # 9. Bond
    # ========================================================

    bonds = sorted(
        bonds,
        key=lambda x: (
            x.get(
                "ai_score"
            ) or 0,

            x.get(
                "strength_score"
            ) or 0,
        ),
        reverse=True
    )[:10]

    # ========================================================
    # 10. Breadth
    # ========================================================

    market_breadth = (
        calculate_market_breadth(
            analyzed
        )
    )

    # ========================================================
    # 11. Backtest
    #
    # 注意：
    # 使用完整 history_cache，
    # 不使用已經過濾成 latest_market_date 的 analyzed。
    # ========================================================

    backtest = (
        calculate_backtest(
            universe,
            history_cache
        )
    )

    # ========================================================
    # 12. Universe summary
    # ========================================================

    universe_summary = (
        build_universe_summary(
            universe
        )
    )

    # ========================================================
    # 13. Output
    # ========================================================

    output = {

        # ----------------------------------------------------
        # Version
        # ----------------------------------------------------

        "version":
            VERSION,

        "schema_version":
            SCHEMA_VERSION,

        "status":
            "success",

        # ----------------------------------------------------
        # Market date
        # ----------------------------------------------------

        "date":
            latest_market_date.isoformat(),

        "latest_market_date":
            latest_market_date.isoformat(),

        # ----------------------------------------------------
        # Update time
        # ----------------------------------------------------

        "updated_at":
            start_time.isoformat(),

        "updated_at_tw":
            start_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            f"fetch_data.py {VERSION}",

        # ----------------------------------------------------
        # Data quality
        # ----------------------------------------------------

        "data_quality": {

            "today_is_market_date":
                (
                    latest_market_date
                    == today_tw_date()
                ),

            "latest_market_date_valid":
                True,

            "non_trading_day_protected":
                (
                    latest_market_date
                    != today_tw_date()
                ),

            "analyzed_count":
                len(analyzed),

            "successful_history_count":
                success_count,

            "failed_history_count":
                fail_count,

            "universe_count":
                len(universe),

            "universe_source":
                universe_source,

            "universe_recovered":
                universe_recovered,

            "min_history_rows":
                MIN_HISTORY_ROWS,

            "backtest_horizon":
                BACKTEST_HORIZON,

            "backtest_uses_trading_days":
                True,

            "six_of_six_same_market_date":
                True,
        },

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

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

            "latest_market_date":
                latest_market_date.isoformat(),
        },

        # ----------------------------------------------------
        # Core conditions
        # ----------------------------------------------------

        "core_conditions": {

            "total":
                CORE_TOTAL,

            "names":
                CORE_CONDITION_NAMES,

            "logic": {

                "macd":
                    "MACD > MACD Signal",

                "rsi":
                    "RSI > 50",

                "kd":
                    "K > D",

                "volume":
                    "Volume >= MA5 Volume × 1.5",

                "price_ma20":
                    "Close > MA20",

                "ma20_rising":
                    "MA20[today] > MA20[yesterday]",
            },
        },

        # ----------------------------------------------------
        # Selected
        # ----------------------------------------------------

        "today_selected":
            today_selected,

        # ----------------------------------------------------
        # Top10
        # ----------------------------------------------------

        "top10":
            top10,

        # ----------------------------------------------------
        # ETF
        # ----------------------------------------------------

        "etfs":
            etfs,

        # ----------------------------------------------------
        # Bond
        # ----------------------------------------------------

        "bonds":
            bonds,

        # ----------------------------------------------------
        # Backtest
        # ----------------------------------------------------

        "backtest_summary":
            backtest,

        # ----------------------------------------------------
        # 完整 Universe
        #
        # V10.3 最重要修正：
        # items 保存完整 Universe。
        # 下一次 stocks.json 壞掉時，
        # fetch_data.py 可以從這裡完整恢復。
        # ----------------------------------------------------

        "universe":
            universe_summary,
    }

    # ========================================================
    # 14. Save
    # ========================================================

    save_json(
        output
    )

    # ========================================================
    # 15. Final
    # ========================================================

    print()

    print("=" * 64)

    print(
        f"{VERSION} 完成"
    )

    print("=" * 64)

    print(
        "date：",
        output["date"]
    )

    print(
        "latest_market_date：",
        output[
            "latest_market_date"
        ]
    )

    print(
        "Universe source：",
        universe_source
    )

    print(
        "Universe recovered：",
        universe_recovered
    )

    print(
        "Universe：",
        len(universe)
    )

    print(
        "有效分析：",
        len(analyzed)
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
        market_breadth[
            "rising"
        ]
    )

    print(
        "市場下跌：",
        market_breadth[
            "falling"
        ]
    )

    print(
        "市場平盤：",
        market_breadth[
            "unchanged"
        ]
    )

    print()

    print(
        "輸出：",
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
