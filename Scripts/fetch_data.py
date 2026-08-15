# ================================================================
# 台股 AI 選股系統
# fetch_data.py V8.0 FINAL
#
# V8.0 BACKEND DATA ENGINE
#
# 掃描範圍：
#   1. TWSE 上市股票
#   2. TPEx 上櫃股票
#   3. TWSE ETF
#   4. TPEx ETF
#
# 核心：
#   - RSI
#   - MACD
#   - KD
#   - MA5 / MA20 / MA60
#   - 成交量 / MA5
#   - 六項核心條件
#   - 今日 6/6
#   - AI Score
#   - Top30
#   - A/B 歷史回測
#
# Data Contract：
#   version       = V8.0
#   schema_version = prices.v8
#
# 前台：
#   READ ONLY
#   不重新計算
#
# ================================================================

import os
import json
import math
import time
import tempfile
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf


# ================================================================
# 基本設定
# ================================================================

VERSION = "V8.0"
SCHEMA_VERSION = "prices.v8"

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

BACKTEST_FILE = os.path.join(
    DATA_DIR,
    "backtest.json"
)

os.makedirs(
    DATA_DIR,
    exist_ok=True
)


# ================================================================
# 時區
# ================================================================

TW_TZ = timezone(
    timedelta(hours=8)
)


# ================================================================
# 行情設定
# ================================================================

YF_PERIOD = "2y"
YF_INTERVAL = "1d"

MAX_RETRY = 3
RETRY_DELAY = 2

BATCH_SIZE = 40
BATCH_DELAY = 1.0

TOP30 = 30

BACKTEST_HORIZONS = [
    5,
    10,
    20
]

BACKTEST_MIN_HISTORY = 120


# ================================================================
# 六項正式條件
# ================================================================

CORE_CONDITIONS = [
    "MACD 多方",
    "RSI > 50",
    "KD 多方",
    "成交量 ≥ MA5 × 1.5",
    "股價 > MA20",
    "MA20 今日 > 昨日"
]


# ================================================================
# 不納入股票掃描的特殊商品
# ================================================================

INVALID_SECURITY_KEYWORDS = [
    "權證",
    "認購權證",
    "認售權證",
    "牛熊證",
    "ETN",
    "海外存託憑證",
    "存託憑證",
    "可轉債",
    "轉換公司債"
]


# ================================================================
# 工具
# ================================================================

def clean_text(value):

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


def clean_code(value):

    value = clean_text(value).upper()

    if value.endswith(".TWO"):
        value = value[:-4]

    if value.endswith(".TW"):
        value = value[:-3]

    return value


def safe_float(value):

    try:

        value = float(value)

        if not math.isfinite(value):
            return None

        return round(
            value,
            4
        )

    except Exception:

        return None


def safe_int(value):

    try:

        return int(
            float(value)
        )

    except Exception:

        return 0


def is_invalid_security(name):

    name = clean_text(name)

    return any(
        keyword in name
        for keyword in INVALID_SECURITY_KEYWORDS
    )


# ================================================================
# ETF 判斷
#
# 台灣 ETF 代號主要集中在：
#   0050
#   0056
#   006xx
#   007xx
#   008xx
#   009xx
#
# 另包含：
#   00986B
#   009xxA
#   009xxB
#   009xxC
#   009xxD
#   009xxK
#   009xxL
#   009xxR
#   009xxT
#   009xxU
#
# 因此不能只看最後一碼。
# ================================================================

def is_etf_code(code):

    code = clean_code(code)

    if not code:
        return False

    # 00 開頭為 ETF 主要代號區域
    if code.startswith("00"):

        if len(code) in (
            4,
            5,
            6
        ):
            return True

    return False


def is_etf(
    code,
    name,
    security_type=None
):

    code = clean_code(code)
    name = clean_text(name)

    if security_type:

        security_type = clean_text(
            security_type
        ).upper()

        if "ETF" in security_type:
            return True

    if "ETF" in name.upper():
        return True

    if is_etf_code(code):
        return True

    return False


# ================================================================
# Yahoo Symbol
# ================================================================

def yahoo_symbol(
    code,
    market
):

    code = clean_code(code)

    if market == "TWO":
        return f"{code}.TWO"

    return f"{code}.TW"


# ================================================================
# TWSE Universe
#
# TWSE STOCK_DAY_ALL 會包含市場可交易證券。
#
# ETF 不再排除。
# ================================================================

def fetch_twse_universe():

    url = (
        "https://openapi.twse.com.tw/"
        "v1/exchangeReport/STOCK_DAY_ALL"
    )

    response = requests.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):

        raise RuntimeError(
            "TWSE API 回傳格式錯誤"
        )

    universe = []

    for item in data:

        code = clean_code(
            item.get("Code")
        )

        name = clean_text(
            item.get("Name")
        )

        if not code:
            continue

        if not code.isalnum():
            continue

        if is_invalid_security(name):
            continue

        security_is_etf = is_etf(
            code,
            name
        )

        universe.append({

            "code": code,

            "name": name,

            "market": "TW",

            "type":
                "etf"
                if security_is_etf
                else "stock",

            "is_etf":
                security_is_etf

        })

    return universe


# ================================================================
# TPEx Universe
#
# 正式 TPEx OpenAPI：
#
# /tpex_mainboard_daily_close_quotes
#
# ================================================================

def fetch_tpex_universe():

    url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/"
        "tpex_mainboard_daily_close_quotes"
    )

    response = requests.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):

        raise RuntimeError(
            "TPEx API 回傳格式錯誤"
        )

    universe = []

    for item in data:

        code = clean_code(
            item.get(
                "SecuritiesCompanyCode"
            )
            or item.get("Code")
            or item.get("股票代號")
        )

        name = clean_text(
            item.get(
                "CompanyName"
            )
            or item.get("Name")
            or item.get("公司名稱")
        )

        if not code:
            continue

        if not code.isalnum():
            continue

        if is_invalid_security(name):
            continue

        security_is_etf = is_etf(
            code,
            name
        )

        universe.append({

            "code": code,

            "name": name,

            "market": "TWO",

            "type":
                "etf"
                if security_is_etf
                else "stock",

            "is_etf":
                security_is_etf

        })

    return universe


# ================================================================
# 建立完整 Universe
# ================================================================

def build_universe():

    twse = []
    tpex = []

    print(
        "================================================"
    )

    print(
        "建立 V8.0 完整掃描 Universe"
    )

    print(
        "================================================"
    )

    # ------------------------------------------------------------
    # TWSE
    # ------------------------------------------------------------

    try:

        twse = fetch_twse_universe()

        print(
            f"[TWSE] 原始證券：{len(twse)}"
        )

    except Exception as exc:

        print(
            f"[ERROR] TWSE Universe：{exc}"
        )

        raise

    # ------------------------------------------------------------
    # TPEx
    # ------------------------------------------------------------

    try:

        tpex = fetch_tpex_universe()

        print(
            f"[TPEx] 原始證券：{len(tpex)}"
        )

    except Exception as exc:

        print(
            f"[ERROR] TPEx Universe：{exc}"
        )

        raise

    # ------------------------------------------------------------
    # 合併
    #
    # key 使用：
    # market + code
    #
    # 避免 TW / TWO 同代號衝突
    # ------------------------------------------------------------

    combined = {}

    for item in (
        twse +
        tpex
    ):

        key = (
            item["market"],
            item["code"]
        )

        combined[key] = item

    universe = list(
        combined.values()
    )

    # ------------------------------------------------------------
    # 統計
    # ------------------------------------------------------------

    twse_stocks = [
        x for x in universe
        if x["market"] == "TW"
        and x["type"] == "stock"
    ]

    tpex_stocks = [
        x for x in universe
        if x["market"] == "TWO"
        and x["type"] == "stock"
    ]

    twse_etfs = [
        x for x in universe
        if x["market"] == "TW"
        and x["type"] == "etf"
    ]

    tpex_etfs = [
        x for x in universe
        if x["market"] == "TWO"
        and x["type"] == "etf"
    ]

    statistics = {

        "twse_stock_universe":
            len(twse_stocks),

        "tpex_stock_universe":
            len(tpex_stocks),

        "twse_etf_universe":
            len(twse_etfs),

        "tpex_etf_universe":
            len(tpex_etfs),

        "stock_universe":
            len(twse_stocks)
            +
            len(tpex_stocks),

        "etf_universe":
            len(twse_etfs)
            +
            len(tpex_etfs),

        "total_universe":
            len(universe)

    }

    print(
        "------------------------------------------------"
    )

    print(
        f"上市股票：{statistics['twse_stock_universe']}"
    )

    print(
        f"上櫃股票：{statistics['tpex_stock_universe']}"
    )

    print(
        f"上市 ETF：{statistics['twse_etf_universe']}"
    )

    print(
        f"上櫃 ETF：{statistics['tpex_etf_universe']}"
    )

    print(
        f"股票總數：{statistics['stock_universe']}"
    )

    print(
        f"ETF 總數：{statistics['etf_universe']}"
    )

    print(
        f"完整 Universe：{statistics['total_universe']}"
    )

    print(
        "------------------------------------------------"
    )

    # ------------------------------------------------------------
    # 最低防呆
    # ------------------------------------------------------------

    if len(twse_stocks) < 500:

        raise RuntimeError(
            "TWSE 股票 Universe 異常過少"
        )

    if len(tpex_stocks) < 300:

        raise RuntimeError(
            "TPEx 股票 Universe 異常過少"
        )

    if len(twse_etfs) == 0:

        raise RuntimeError(
            "TWSE ETF Universe 為 0"
        )

    return (
        universe,
        statistics
    )


# ================================================================
# 下載歷史行情
# ================================================================

def download_history(
    symbol
):

    for attempt in range(
        1,
        MAX_RETRY + 1
    ):

        try:

            ticker = yf.Ticker(
                symbol
            )

            df = ticker.history(
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=False,
                actions=False
            )

            if (
                df is None
                or df.empty
            ):

                raise RuntimeError(
                    "沒有行情資料"
                )

            required = [
                "Open",
                "High",
                "Low",
                "Close",
                "Volume"
            ]

            for column in required:

                if column not in df.columns:

                    raise RuntimeError(
                        f"缺少欄位：{column}"
                    )

            df = df[
                required
            ].copy()

            df = df.dropna(
                subset=[
                    "Close"
                ]
            )

            df = df[
                ~df.index.duplicated(
                    keep="last"
                )
            ]

            df = df.sort_index()

            if len(df) < 60:

                raise RuntimeError(
                    "歷史資料不足 60 個交易日"
                )

            return df

        except Exception as exc:

            if attempt >= MAX_RETRY:

                print(
                    f"[FAIL] {symbol}：{exc}"
                )

                return None

            time.sleep(
                RETRY_DELAY
            )

    return None


# ================================================================
# RSI
# ================================================================

def calculate_rsi(
    close,
    period=14
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

    return (
        100 -
        100 /
        (1 + rs)
    )


# ================================================================
# MACD
# ================================================================

def calculate_macd(
    close
):

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    macd = (
        ema12 -
        ema26
    )

    signal = macd.ewm(
        span=9,
        adjust=False
    ).mean()

    hist = (
        macd -
        signal
    )

    return (
        macd,
        signal,
        hist
    )


# ================================================================
# KD
# ================================================================

def calculate_kd(
    high,
    low,
    close
):

    low9 = (
        low
        .rolling(9)
        .min()
    )

    high9 = (
        high
        .rolling(9)
        .max()
    )

    denominator = (
        high9 -
        low9
    ).replace(
        0,
        np.nan
    )

    rsv = (
        (
            close -
            low9
        )
        /
        denominator
        *
        100
    )

    k = rsv.ewm(
        alpha=1 / 3,
        adjust=False
    ).mean()

    d = k.ewm(
        alpha=1 / 3,
        adjust=False
    ).mean()

    return (
        k,
        d
    )


# ================================================================
# 技術指標
# ================================================================

def calculate_indicators(
    df
):

    df = df.copy()

    df["MA5"] = (
        df["Close"]
        .rolling(5)
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

    df["RSI"] = calculate_rsi(
        df["Close"]
    )

    (
        df["MACD"],
        df["MACD_SIGNAL"],
        df["MACD_HIST"]
    ) = calculate_macd(
        df["Close"]
    )

    (
        df["K"],
        df["D"]
    ) = calculate_kd(
        df["High"],
        df["Low"],
        df["Close"]
    )

    df["VOL_MA5"] = (
        df["Volume"]
        .rolling(5)
        .mean()
    )

    return df


# ================================================================
# 核心條件
# ================================================================

def evaluate_core(
    df
):

    if len(df) < 21:

        return None

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    values = {

        "macd_golden_cross":
            bool(
                latest["MACD"]
                >
                latest["MACD_SIGNAL"]
            ),

        "rsi_over_50":
            bool(
                latest["RSI"]
                >
                50
            ),

        "kd_golden_cross":
            bool(
                latest["K"]
                >
                latest["D"]
            ),

        "volume_expand":
            bool(
                latest["Volume"]
                >=
                latest["VOL_MA5"]
                *
                1.5
            ),

        "price_over_ma20":
            bool(
                latest["Close"]
                >
                latest["MA20"]
            ),

        "ma20_up":
            bool(
                latest["MA20"]
                >
                previous["MA20"]
            )

    }

    score = sum(
        1
        for value in values.values()
        if value
    )

    values[
        "core_score"
    ] = score

    values[
        "core_total"
    ] = 6

    values[
        "core_pass"
    ] = (
        score == 6
    )

    return values


# ================================================================
# 強勢分數
# ================================================================

def calculate_strength_score(
    df
):

    latest = df.iloc[-1]

    score = 0.0

    # RSI
    rsi = float(
        latest["RSI"]
    )

    if rsi >= 70:
        score += 20

    elif rsi >= 60:
        score += 16

    elif rsi >= 50:
        score += 12

    # MACD
    if (
        latest["MACD"]
        >
        latest["MACD_SIGNAL"]
    ):
        score += 20

    # KD
    if (
        latest["K"]
        >
        latest["D"]
    ):
        score += 15

    # 價格 / MA20
    if (
        latest["Close"]
        >
        latest["MA20"]
    ):
        score += 20

    # MA20
    if len(df) >= 2:

        if (
            latest["MA20"]
            >
            df.iloc[-2]["MA20"]
        ):
            score += 15

    # 成交量
    if (
        latest["VOL_MA5"]
        and
        latest["Volume"]
        >=
        latest["VOL_MA5"]
        *
        1.5
    ):
        score += 10

    return round(
        min(
            score,
            100
        ),
        2
    )


# ================================================================
# AI Score
# ================================================================

def calculate_ai_score(
    strength_score,
    core_score,
    change_pct
):

    strength = float(
        strength_score
        or 0
    )

    core = float(
        core_score
        or 0
    )

    change = float(
        change_pct
        or 0
    )

    momentum_bonus = min(
        max(
            change,
            -10
        ),
        10
    )

    score = (
        strength * 0.65
        +
        (core / 6) * 30
        +
        momentum_bonus * 0.5
    )

    return round(
        max(
            0,
            min(
                score,
                100
            )
        ),
        2
    )


# ================================================================
# Signal
# ================================================================

def make_signal(
    core_pass,
    core_score,
    ai_score
):

    if core_pass:

        return "今日精選"

    if core_score >= 4:

        return "強勢觀察"

    if ai_score >= 70:

        return "多方觀察"

    if ai_score >= 50:

        return "中性"

    return "弱勢"


# ================================================================
# 建立單一資料物件
# ================================================================

def build_security_record(
    item,
    df
):

    df = calculate_indicators(
        df
    )

    if len(df) < 60:

        return None

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    core = evaluate_core(
        df
    )

    if core is None:

        return None

    close = safe_float(
        latest["Close"]
    )

    previous_close = safe_float(
        previous["Close"]
    )

    if (
        close is None
        or
        previous_close is None
    ):

        return None

    change_pct = 0.0

    if previous_close != 0:

        change_pct = (
            (
                close -
                previous_close
            )
            /
            previous_close
            *
            100
        )

    strength_score = (
        calculate_strength_score(
            df
        )
    )

    ai_score = (
        calculate_ai_score(
            strength_score,
            core["core_score"],
            change_pct
        )
    )

    signal = make_signal(
        core["core_pass"],
        core["core_score"],
        ai_score
    )

    volume_ma5 = safe_float(
        latest["VOL_MA5"]
    )

    volume = safe_int(
        latest["Volume"]
    )

    volume_ratio = None

    if (
        volume_ma5
        and
        volume_ma5 > 0
    ):

        volume_ratio = (
            volume /
            volume_ma5
        )

    record = {

        "code":
            item["code"],

        "symbol":
            yahoo_symbol(
                item["code"],
                item["market"]
            ),

        "name":
            item["name"],

        "type":
            item["type"],

        "market":
            item["market"],

        "price":
            close,

        "close":
            close,

        "previous_close":
            previous_close,

        "change_pct":
            safe_float(
                change_pct
            ),

        "volume":
            volume,

        "volume_ma5":
            volume_ma5,

        "volume_ratio":
            safe_float(
                volume_ratio
            ),

        "rsi":
            safe_float(
                latest["RSI"]
            ),

        "k":
            safe_float(
                latest["K"]
            ),

        "d":
            safe_float(
                latest["D"]
            ),

        "macd":
            safe_float(
                latest["MACD"]
            ),

        "macd_signal":
            safe_float(
                latest["MACD_SIGNAL"]
            ),

        "macd_hist":
            safe_float(
                latest["MACD_HIST"]
            ),

        "ma5":
            safe_float(
                latest["MA5"]
            ),

        "ma20":
            safe_float(
                latest["MA20"]
            ),

        "ma60":
            safe_float(
                latest["MA60"]
            ),

        "macd_golden_cross":
            core[
                "macd_golden_cross"
            ],

        "rsi_over_50":
            core[
                "rsi_over_50"
            ],

        "kd_golden_cross":
            core[
                "kd_golden_cross"
            ],

        "volume_expand":
            core[
                "volume_expand"
            ],

        "price_over_ma20":
            core[
                "price_over_ma20"
            ],

        "ma20_up":
            core[
                "ma20_up"
            ],

        "core_score":
            core[
                "core_score"
            ],

        "core_total":
            6,

        "core_pass":
            core[
                "core_pass"
            ],

        "strength_score":
            strength_score,

        "ai_score":
            ai_score,

        "signal":
            signal

    }

    return record


# ================================================================
# 掃描 Universe
# ================================================================

def scan_universe(
    universe
):

    stocks = []
    etfs = []
    failed = []

    total = len(
        universe
    )

    print(
        "================================================"
    )

    print(
        f"開始掃描：{total} 個標的"
    )

    print(
        "================================================"
    )

    for index, item in enumerate(
        universe,
        start=1
    ):

        symbol = yahoo_symbol(
            item["code"],
            item["market"]
        )

        try:

            print(
                f"[{index}/{total}] "
                f"{symbol} "
                f"{item['name']}"
            )

            df = download_history(
                symbol
            )

            if df is None:

                failed.append({

                    "code":
                        item["code"],

                    "symbol":
                        symbol,

                    "name":
                        item["name"],

                    "market":
                        item["market"],

                    "reason":
                        "history_failed"

                })

                continue

            record = (
                build_security_record(
                    item,
                    df
                )
            )

            if record is None:

                failed.append({

                    "code":
                        item["code"],

                    "symbol":
                        symbol,

                    "name":
                        item["name"],

                    "market":
                        item["market"],

                    "reason":
                        "indicator_failed"

                })

                continue

            if item["type"] == "etf":

                etfs.append(
                    record
                )

            else:

                stocks.append(
                    record
                )

        except Exception as exc:

            print(
                f"[FAIL] {symbol}: {exc}"
            )

            failed.append({

                "code":
                    item["code"],

                "symbol":
                    symbol,

                "name":
                    item["name"],

                "market":
                    item["market"],

                "reason":
                    str(exc)

            )

        if (
            index %
            BATCH_SIZE
            == 0
        ):

            time.sleep(
                BATCH_DELAY
            )

    return (
        stocks,
        etfs,
        failed
    )


# ================================================================
# 排序
# ================================================================

def sort_by_ai_score(
    items
):

    return sorted(
        items,
        key=lambda x:
            float(
                x.get(
                    "ai_score",
                    0
                )
                or 0
            ),
        reverse=True
    )


# ================================================================
# Top30
# ================================================================

def build_top30(
    stocks
):

    return (
        sort_by_ai_score(
            stocks
        )[:TOP30]
    )


# ================================================================
# 今日 6/6
# ================================================================

def build_today_selected(
    stocks
):

    selected = [

        item

        for item in stocks

        if item.get(
            "core_pass"
        )
        is True

    ]

    return sort_by_ai_score(
        selected
    )


# ================================================================
# ETF 排序
# ================================================================

def build_etf_result(
    etfs
):

    return sort_by_ai_score(
        etfs
    )


# ================================================================
# 歷史回測
#
# A：
# 當日發生 MACD 黃金交叉
#
# B：
# MACD 維持多方
# ================================================================

def backtest_stock(
    df
):

    if len(df) < BACKTEST_MIN_HISTORY:

        return None

    df = calculate_indicators(
        df
    )

    results = {

        "A_today_cross": {},

        "B_current_bullish": {}

    }

    for horizon in (
        BACKTEST_HORIZONS
    ):

        a_returns = []
        b_returns = []

        for i in range(
            1,
            len(df) - horizon
        ):

            today = df.iloc[i]

            yesterday = df.iloc[i - 1]

            future = df.iloc[
                i + horizon
            ]

            today_close = (
                today["Close"]
            )

            future_close = (
                future["Close"]
            )

            if (
                pd.isna(
                    today_close
                )
                or
                pd.isna(
                    future_close
                )
            ):
                continue

            if (
                today_close == 0
            ):
                continue

            future_return = (
                (
                    future_close -
                    today_close
                )
                /
                today_close
                *
                100
            )

            # ----------------------------------------------------
            # A：當日 MACD 黃金交叉
            # ----------------------------------------------------

            macd_cross = (

                today["MACD"]
                >
                today["MACD_SIGNAL"]

                and

                yesterday["MACD"]
                <=
                yesterday["MACD_SIGNAL"]

            )

            if macd_cross:

                a_returns.append(
                    future_return
                )

            # ----------------------------------------------------
            # B：目前維持多方
            # ----------------------------------------------------

            current_bullish = (

                today["MACD"]
                >
                today["MACD_SIGNAL"]

                and

                today["RSI"]
                >
                50

                and

                today["K"]
                >
                today["D"]

                and

                today["Close"]
                >
                today["MA20"]

            )

            if current_bullish:

                b_returns.append(
                    future_return
                )

        def summarize(
            returns
        ):

            if not returns:

                return {

                    "signals": 0,

                    "wins": 0,

                    "losses": 0,

                    "win_rate": 0,

                    "average_return": 0

                }

            wins = sum(
                1
                for value in returns
                if value > 0
            )

            losses = (
                len(returns) -
                wins
            )

            return {

                "signals":
                    len(returns),

                "wins":
                    wins,

                "losses":
                    losses,

                "win_rate":
                    round(
                        wins /
                        len(returns)
                        *
                        100,
                        2
                    ),

                "average_return":
                    round(
                        float(
                            np.mean(
                                returns
                            )
                        ),
                        4
                    )

            }

        results[
            "A_today_cross"
        ][f"{horizon}d"] = (
            summarize(
                a_returns
            )
        )

        results[
            "B_current_bullish"
        ][f"{horizon}d"] = (
            summarize(
                b_returns
            )
        )

    return results


# ================================================================
# 建立整體回測
# ================================================================

def build_backtest(
    stocks
):

    aggregate = {

        "A_today_cross": {
            h: []
            for h in BACKTEST_HORIZONS
        },

        "B_current_bullish": {
            h: []
            for h in BACKTEST_HORIZONS
        }

    }

    count = 0

    for item in stocks:

        symbol = item[
            "symbol"
        ]

        try:

            df = download_history(
                symbol
            )

            if df is None:
                continue

            if len(df) < BACKTEST_MIN_HISTORY:
                continue

            result = backtest_stock(
                df
            )

            if result is None:
                continue

            count += 1

            for strategy in (
                "A_today_cross",
                "B_current_bullish"
            ):

                for horizon in (
                    BACKTEST_HORIZONS
                ):

                    data = result[
                        strategy
                    ][
                        f"{horizon}d"
                    ]

                    aggregate[
                        strategy
                    ][
                        horizon
                    ].append(
                        data
                    )

        except Exception:

            continue

    def merge(
        values
    ):

        if not values:

            return {

                "signals": 0,

                "wins": 0,

                "losses": 0,

                "win_rate": 0,

                "average_return": 0

            }

        signals = sum(
            x["signals"]
            for x in values
        )

        wins = sum(
            x["wins"]
            for x in values
        )

        losses = sum(
            x["losses"]
            for x in values
        )

        weighted_returns = []

        for x in values:

            if x["signals"] > 0:

                weighted_returns.extend(
                    [x["average_return"]]
                    *
                    x["signals"]
                )

        average_return = (
            float(
                np.mean(
                    weighted_returns
                )
            )
            if weighted_returns
            else 0
        )

        return {

            "signals":
                signals,

            "wins":
                wins,

            "losses":
                losses,

            "win_rate":
                round(
                    wins /
                    signals
                    *
                    100,
                    2
                )
                if signals
                else 0,

            "average_return":
                round(
                    average_return,
                    4
                )

        }

    final = {

        "status":
            "completed",

        "comparison_horizon":
            10,

        "strategy_A":
            "當日黃金交叉",

        "strategy_B":
            "目前維持多方",

        "strategies": {

            "A_today_cross": {},

            "B_current_bullish": {}

        }

    }

    for strategy in (
        "A_today_cross",
        "B_current_bullish"
    ):

        for horizon in (
            BACKTEST_HORIZONS
        ):

            final[
                "strategies"
            ][
                strategy
            ][
                f"{horizon}d"
            ] = merge(
                aggregate[
                    strategy
                ][
                    horizon
                ]
            )

    a10 = final[
        "strategies"
    ][
        "A_today_cross"
    ][
        "10d"
    ][
        "win_rate"
    ]

    b10 = final[
        "strategies"
    ][
        "B_current_bullish"
    ][
        "10d"
    ][
        "win_rate"
    ]

    if b10 > a10:

        final[
            "better_by_win_rate"
        ] = "B_current_bullish"

    elif a10 > b10:

        final[
            "better_by_win_rate"
        ] = "A_today_cross"

    else:

        final[
            "better_by_win_rate"
        ] = "tie"

    final[
        "backtest_stock_count"
    ] = count

    return final


# ================================================================
# 資料驗證
# ================================================================

def validate_record(
    item,
    is_etf_record=False
):

    required = {

        "code",
        "symbol",
        "name",
        "type",
        "market",

        "price",
        "close",
        "previous_close",
        "change_pct",

        "volume",
        "volume_ma5",
        "volume_ratio",

        "rsi",
        "k",
        "d",

        "macd",
        "macd_signal",
        "macd_hist",

        "ma5",
        "ma20",
        "ma60",

        "strength_score",
        "ai_score",
        "signal"

    }

    if not is_etf_record:

        required.update({

            "macd_golden_cross",
            "rsi_over_50",
            "kd_golden_cross",
            "volume_expand",
            "price_over_ma20",
            "ma20_up",

            "core_score",
            "core_total",
            "core_pass"

        })

    missing = [
        field
        for field in required
        if field not in item
    ]

    if missing:

        raise RuntimeError(
            "資料欄位缺失："
            +
            ",".join(
                missing
            )
        )

    if not item["symbol"]:

        raise RuntimeError(
            "symbol 空白"
        )

    if item["price"] is None:

        raise RuntimeError(
            f"{item['symbol']} price 空白"
        )

    return True


# ================================================================
# JSON Atomic Write
# ================================================================

def atomic_write_json(
    path,
    data
):

    directory = os.path.dirname(
        path
    )

    fd, temp_path = (
        tempfile.mkstemp(
            prefix=".tmp_",
            suffix=".json",
            dir=directory
        )
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False
            )

            file.write("\n")

        os.replace(
            temp_path,
            path
        )

    except Exception:

        try:

            os.remove(
                temp_path
            )

        except Exception:

            pass

        raise


# ================================================================
# 建立 prices.v8
# ================================================================

def build_prices_json(
    stocks,
    etfs,
    failed,
    universe_statistics,
    backtest
):

    now = datetime.now(
        TW_TZ
    )

    today = now.strftime(
        "%Y-%m-%d"
    )

    updated_at_tw = (
        now.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    # ------------------------------------------------------------
    # 驗證股票
    # ------------------------------------------------------------

    for item in stocks:

        validate_record(
            item,
            False
        )

    # ------------------------------------------------------------
    # 驗證 ETF
    # ------------------------------------------------------------

    for item in etfs:

        validate_record(
            item,
            True
        )

    # ------------------------------------------------------------
    # 今日 6/6
    # ------------------------------------------------------------

    today_selected = (
        build_today_selected(
            stocks
        )
    )

    # ------------------------------------------------------------
    # Top30
    # ------------------------------------------------------------

    top30 = build_top30(
        stocks
    )

    # ------------------------------------------------------------
    # ETF
    # ------------------------------------------------------------

    etfs_sorted = (
        build_etf_result(
            etfs
        )
    )

    # ------------------------------------------------------------
    # 統計
    # ------------------------------------------------------------

    stock_tw = sum(
        1
        for x in stocks
        if x["market"] == "TW"
    )

    stock_two = sum(
        1
        for x in stocks
        if x["market"] == "TWO"
    )

    etf_tw = sum(
        1
        for x in etfs
        if x["market"] == "TW"
    )

    etf_two = sum(
        1
        for x in etfs
        if x["market"] == "TWO"
    )

    result = {

        "version":
            VERSION,

        "schema_version":
            SCHEMA_VERSION,

        "status":
            "success",

        "updated_at":
            now.isoformat(),

        "updated_at_tw":
            updated_at_tw,

        "date":
            today,

        # --------------------------------------------------------
        # 完整股票
        # --------------------------------------------------------

        "stocks":
            stocks,

        # --------------------------------------------------------
        # ETF
        # --------------------------------------------------------

        "etfs":
            etfs_sorted,

        # --------------------------------------------------------
        # 今日精選
        # --------------------------------------------------------

        "today_selected":
            today_selected,

        # --------------------------------------------------------
        # Top30
        # --------------------------------------------------------

        "top30":
            top30,

        # --------------------------------------------------------
        # 回測
        # --------------------------------------------------------

        "backtest_summary":
            backtest,

        # --------------------------------------------------------
        # 統計
        # --------------------------------------------------------

        "statistics": {

            "stock_count":
                len(stocks),

            "stock_twse_count":
                stock_tw,

            "stock_tpex_count":
                stock_two,

            "etf_count":
                len(etfs),

            "etf_twse_count":
                etf_tw,

            "etf_tpex_count":
                etf_two,

            "top30_count":
                len(top30),

            "today_selected_count":
                len(today_selected),

            "core_6_count":
                len(today_selected),

            "backtest_stock_count":
                backtest.get(
                    "backtest_stock_count",
                    0
                ),

            "failed_count":
                len(failed),

            # Universe
            "twse_stock_universe":
                universe_statistics[
                    "twse_stock_universe"
                ],

            "tpex_stock_universe":
                universe_statistics[
                    "tpex_stock_universe"
                ],

            "twse_etf_universe":
                universe_statistics[
                    "twse_etf_universe"
                ],

            "tpex_etf_universe":
                universe_statistics[
                    "tpex_etf_universe"
                ],

            "total_universe":
                universe_statistics[
                    "total_universe"
                ]

        },

        # --------------------------------------------------------
        # 核心條件
        # --------------------------------------------------------

        "core_conditions": {

            "total":
                6,

            "names":
                CORE_CONDITIONS,

            "definition":
                "目前維持多方",

            "rules": {

                "macd_golden_cross":
                    "MACD > MACD Signal",

                "rsi_over_50":
                    "RSI > 50",

                "kd_golden_cross":
                    "K > D",

                "volume_expand":
                    "Volume >= MA5 Volume × 1.5",

                "price_over_ma20":
                    "Close > MA20",

                "ma20_up":
                    "MA20 今日 > MA20 昨日"

            }

        },

        # --------------------------------------------------------
        # 失敗清單
        # --------------------------------------------------------

        "failed":
            failed

    }

    return result


# ================================================================
# Backtest JSON
# ================================================================

def write_backtest(
    backtest
):

    atomic_write_json(
        BACKTEST_FILE,
        backtest
    )


# ================================================================
# 主程式
# ================================================================

def main():

    start_time = datetime.now(
        TW_TZ
    )

    print(
        "================================================"
    )

    print(
        "台股 AI 選股系統 fetch_data.py V8.0"
    )

    print(
        "TWSE + TPEx + ETF"
    )

    print(
        "開始時間："
        +
        start_time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print(
        "================================================"
    )

    # ------------------------------------------------------------
    # 1. Universe
    # ------------------------------------------------------------

    (
        universe,
        universe_statistics
    ) = build_universe()

    if not universe:

        raise RuntimeError(
            "Universe 為空"
        )

    # ------------------------------------------------------------
    # 2. 掃描
    # ------------------------------------------------------------

    (
        stocks,
        etfs,
        failed
    ) = scan_universe(
        universe
    )

    # ------------------------------------------------------------
    # 3. 強制資料檢查
    # ------------------------------------------------------------

    print(
        "================================================"
    )

    print(
        "掃描結果"
    )

    print(
        "================================================"
    )

    print(
        f"股票成功：{len(stocks)}"
    )

    print(
        f"ETF 成功：{len(etfs)}"
    )

    print(
        f"失敗：{len(failed)}"
    )

    print(
        "------------------------------------------------"
    )

    print(
        f"上市股票成功："
        f"{sum(1 for x in stocks if x['market'] == 'TW')}"
    )

    print(
        f"上櫃股票成功："
        f"{sum(1 for x in stocks if x['market'] == 'TWO')}"
    )

    print(
        f"上市 ETF 成功："
        f"{sum(1 for x in etfs if x['market'] == 'TW')}"
    )

    print(
        f"上櫃 ETF 成功："
        f"{sum(1 for x in etfs if x['market'] == 'TWO')}"
    )

    # ------------------------------------------------------------
    # 4. 防止「ETF 0」假成功
    # ------------------------------------------------------------

    if len(etfs) == 0:

        raise RuntimeError(
            "ETF 掃描結果為 0，"
            "禁止產生 status=success 的 prices.json"
        )

    # ------------------------------------------------------------
    # 5. 防止股票掃描池異常
    # ------------------------------------------------------------

    if len(stocks) < 500:

        raise RuntimeError(
            "股票成功數量異常過低："
            f"{len(stocks)}"
        )

    # ------------------------------------------------------------
    # 6. 回測
    # ------------------------------------------------------------

    print(
        "================================================"
    )

    print(
        "開始 A/B 歷史回測"
    )

    print(
        "================================================"
    )

    backtest = build_backtest(
        stocks
    )

    # ------------------------------------------------------------
    # 7. prices.json
    # ------------------------------------------------------------

    prices = build_prices_json(
        stocks,
        etfs,
        failed,
        universe_statistics,
        backtest
    )

    # ------------------------------------------------------------
    # 8. 最終驗證
    # ------------------------------------------------------------

    if prices[
        "version"
    ] != "V8.0":

        raise RuntimeError(
            "version 驗證失敗"
        )

    if prices[
        "schema_version"
    ] != "prices.v8":

        raise RuntimeError(
            "schema_version 驗證失敗"
        )

    if prices[
        "status"
    ] != "success":

        raise RuntimeError(
            "status 驗證失敗"
        )

    if not prices[
        "stocks"
    ]:

        raise RuntimeError(
            "stocks 為空"
        )

    if not prices[
        "etfs"
    ]:

        raise RuntimeError(
            "etfs 為空"
        )

    if len(
        prices["top30"]
    ) != min(
        TOP30,
        len(
            prices["stocks"]
        )
    ):

        raise RuntimeError(
            "Top30 數量驗證失敗"
        )

    # ------------------------------------------------------------
    # 9. Atomic Write
    # ------------------------------------------------------------

    atomic_write_json(
        OUTPUT_FILE,
        prices
    )

    # ------------------------------------------------------------
    # 10. backtest.json
    # ------------------------------------------------------------

    write_backtest(
        backtest
    )

    # ------------------------------------------------------------
    # 完成
    # ------------------------------------------------------------

    end_time = datetime.now(
        TW_TZ
    )

    elapsed = (
        end_time -
        start_time
    ).total_seconds()

    print(
        "================================================"
    )

    print(
        "V8.0 完成"
    )

    print(
        "================================================"
    )

    print(
        f"股票：{len(stocks)}"
    )

    print(
        f"ETF：{len(etfs)}"
    )

    print(
        f"今日 6/6："
        f"{len(prices['today_selected'])}"
    )

    print(
        f"Top30："
        f"{len(prices['top30'])}"
    )

    print(
        f"失敗：{len(failed)}"
    )

    print(
        f"耗時：{elapsed:.1f} 秒"
    )

    print(
        "------------------------------------------------"
    )

    print(
        f"prices.json："
        f"{OUTPUT_FILE}"
    )

    print(
        f"backtest.json："
        f"{BACKTEST_FILE}"
    )

    print(
        "------------------------------------------------"
    )

    print(
        "V8.0 Data Contract：OK"
    )

    print(
        "schema_version：prices.v8"
    )

    print(
        "status：success"
    )

    print(
        "================================================"
    )


# ================================================================
# Entry
# ================================================================

if __name__ == "__main__":

    main()
