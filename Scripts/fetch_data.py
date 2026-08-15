# ================================================================
# 台股 AI 選股系統
# fetch_data.py V8.0 FINAL
#
# V8.0：
# 1. TWSE / TPEx Universe
# 2. 排除 ETF / 特殊商品
# 3. Yahoo Finance 歷史行情
# 4. RSI
# 5. MACD
# 6. KD
# 7. MA5 / MA20 / MA60
# 8. 成交量 / MA5 / Volume Ratio
# 9. 六項正式核心條件
# 10. 今日 6/6
# 11. Top30
# 12. AI Score
# 13. A/B 歷史回測
# 14. prices.json V8.0 Data Contract
# 15. backtest.json
# 16. 嚴格資料驗證
# 17. 驗證失敗禁止覆蓋正常資料
# 18. Atomic Write
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


VERSION = "V8.0"

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

TOP_N = 10
TOP30 = 30

BACKTEST_HORIZONS = [5, 10, 20]
BACKTEST_MIN_HISTORY = 120


# ================================================================
# V8.0 六項正式條件
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
# V8.0 股票資料欄位契約
# ================================================================

STOCK_REQUIRED_FIELDS = {
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

    "macd_golden_cross",
    "rsi_over_50",
    "kd_golden_cross",
    "volume_expand",
    "price_over_ma20",
    "ma20_up",

    "core_score",
    "core_total",
    "core_pass",

    "strength_score",
    "ai_score",
    "signal"
}


ETF_REQUIRED_FIELDS = {
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


# ================================================================
# 特殊商品
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


ETF_CODE_SUFFIXES = {
    "A",
    "B",
    "C",
    "D",
    "K",
    "L",
    "R",
    "T",
    "U"
}


# ================================================================
# 基礎工具
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

    if value.endswith(".TW"):
        value = value[:-3]

    if value.endswith(".TWO"):
        value = value[:-4]

    return value


def safe_float(value):
    try:
        value = float(value)

        if not math.isfinite(value):
            return None

        return round(value, 4)

    except Exception:
        return None


def safe_int(value):
    try:
        return int(float(value))
    except Exception:
        return 0


def is_invalid_security(name):
    name = clean_text(name)

    return any(
        keyword in name
        for keyword in INVALID_SECURITY_KEYWORDS
    )


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

    if (
        len(code) >= 5
        and code[-1] in ETF_CODE_SUFFIXES
    ):
        return True

    return False


def yahoo_symbol(
    code,
    market="TW"
):
    code = clean_code(code)

    if market == "TWO":
        return f"{code}.TWO"

    return f"{code}.TW"


# ================================================================
# TWSE Universe
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

        if not code.isdigit():
            continue

        if is_invalid_security(name):
            continue

        universe.append({
            "code": code,
            "name": name,
            "market": "TW",
            "is_etf": is_etf(
                code,
                name
            )
        })

    return universe


# ================================================================
# TPEx Universe
# ================================================================

def fetch_tpex_universe():

    urls = [
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_mainboard_daily_close"
        ),
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_esb_latest_statistics"
        )
    ]

    for url in urls:

        try:

            response = requests.get(
                url,
                timeout=30
            )

            if response.status_code != 200:
                continue

            data = response.json()

            if not isinstance(data, list):
                continue

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

                if not code.isdigit():
                    continue

                if is_invalid_security(name):
                    continue

                universe.append({
                    "code": code,
                    "name": name,
                    "market": "TWO",
                    "is_etf": is_etf(
                        code,
                        name
                    )
                })

            if universe:
                return universe

        except Exception as exc:

            print(
                f"[WARN] TPEx API：{exc}"
            )

    return []


# ================================================================
# 建立 Universe
# ================================================================

def build_universe():

    combined = {}

    sources = []

    try:
        sources.extend(
            fetch_twse_universe()
        )
    except Exception as exc:
        print(
            f"[WARN] TWSE Universe：{exc}"
        )

    try:
        sources.extend(
            fetch_tpex_universe()
        )
    except Exception as exc:
        print(
            f"[WARN] TPEx Universe：{exc}"
        )

    for item in sources:

        code = item["code"]

        if code not in combined:
            combined[code] = item

    return list(
        combined.values()
    )


# ================================================================
# 歷史行情
# ================================================================

def download_history(
    code,
    market
):

    symbol = yahoo_symbol(
        code,
        market
    )

    for attempt in range(
        1,
        MAX_RETRY + 1
    ):

        try:

            ticker = yf.Ticker(symbol)

            df = ticker.history(
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=False,
                actions=False
            )

            if df is None or df.empty:
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
                        f"缺少 {column}"
                    )

            df = df[
                required
            ].copy()

            df = df.dropna(
                subset=["Close"]
            )

            df = df[
                ~df.index.duplicated(
                    keep="last"
                )
            ]

            df = df.sort_index()

            if len(df) < 30:
                raise RuntimeError(
                    "歷史資料不足 30 日"
                )

            return df

        except Exception as exc:

            if attempt >= MAX_RETRY:

                print(
                    f"[FAIL] {symbol}: {exc}"
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
        100 / (1 + rs)
    )


# ================================================================
# MACD
# ================================================================

def calculate_macd(close):

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    macd = ema12 - ema26

    signal = macd.ewm(
        span=9,
        adjust=False
    ).mean()

    histogram = (
        macd -
        signal
    )

    return (
        macd,
        signal,
        histogram
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

    return k, d


# ================================================================
# 技術指標
# ================================================================

def calculate_indicators(df):

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
# 六項正式條件
# ================================================================

def evaluate_core(df):

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    macd_pass = (
        pd.notna(latest["MACD"])
        and
        pd.notna(
            latest["MACD_SIGNAL"]
        )
        and
        latest["MACD"]
        >
        latest["MACD_SIGNAL"]
    )

    rsi_pass = (
        pd.notna(latest["RSI"])
        and
        latest["RSI"] > 50
    )

    kd_pass = (
        pd.notna(latest["K"])
        and
        pd.notna(latest["D"])
        and
        latest["K"]
        >
        latest["D"]
    )

    volume_pass = (
        pd.notna(latest["Volume"])
        and
        pd.notna(latest["VOL_MA5"])
        and
        latest["VOL_MA5"] > 0
        and
        latest["Volume"]
        >=
        latest["VOL_MA5"] * 1.5
    )

    price_ma20_pass = (
        pd.notna(latest["Close"])
        and
        pd.notna(latest["MA20"])
        and
        latest["Close"]
        >
        latest["MA20"]
    )

    ma20_up_pass = (
        pd.notna(latest["MA20"])
        and
        pd.notna(previous["MA20"])
        and
        latest["MA20"]
        >
        previous["MA20"]
    )

    conditions = {
        "macd_golden_cross":
            bool(macd_pass),

        "rsi_over_50":
            bool(rsi_pass),

        "kd_golden_cross":
            bool(kd_pass),

        "volume_expand":
            bool(volume_pass),

        "price_over_ma20":
            bool(price_ma20_pass),

        "ma20_up":
            bool(ma20_up_pass)
    }

    score = sum(
        conditions.values()
    )

    conditions["core_score"] = int(
        score
    )

    conditions["core_total"] = 6

    conditions["core_pass"] = (
        score == 6
    )

    return conditions


# ================================================================
# Strength Score
# ================================================================

def calculate_strength(df):

    latest = df.iloc[-1]

    score = 0.0

    rsi = latest["RSI"]

    if pd.notna(rsi):

        score += max(
            0,
            min(
                30,
                (
                    float(rsi) -
                    50
                ) * 0.8
            )
        )

    close = latest["Close"]
    ma20 = latest["MA20"]

    if (
        pd.notna(close)
        and
        pd.notna(ma20)
        and
        ma20 != 0
    ):

        distance = (
            float(close) /
            float(ma20) -
            1
        )

        score += max(
            0,
            min(
                25,
                distance * 100
            )
        )

    if (
        pd.notna(latest["MACD"])
        and
        pd.notna(
            latest["MACD_SIGNAL"]
        )
        and
        latest["MACD"]
        >
        latest["MACD_SIGNAL"]
    ):

        score += 20

    if (
        pd.notna(latest["K"])
        and
        pd.notna(latest["D"])
        and
        latest["K"]
        >
        latest["D"]
    ):

        score += 15

    if (
        pd.notna(latest["Volume"])
        and
        pd.notna(latest["VOL_MA5"])
        and
        latest["VOL_MA5"] > 0
    ):

        ratio = (
            latest["Volume"] /
            latest["VOL_MA5"]
        )

        score += max(
            0,
            min(
                10,
                (ratio - 1) * 10
            )
        )

    return round(
        max(
            0,
            min(
                100,
                score
            )
        ),
        2
    )


# ================================================================
# Signal
# ================================================================

def make_signal(
    core_score,
    security_type
):

    if security_type == "etf":
        return "強勢 ETF"

    if core_score == 6:
        return "今日精選"

    if core_score >= 4:
        return "強勢"

    if core_score >= 2:
        return "偏強"

    return "觀察"


# ================================================================
# 單一商品分析
# ================================================================

def analyze_security(item):

    code = item["code"]
    name = item["name"]
    market = item["market"]

    security_type = (
        "etf"
        if item.get("is_etf", False)
        else "stock"
    )

    df = download_history(
        code,
        market
    )

    if df is None:
        return None

    df = calculate_indicators(
        df
    )

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    close = safe_float(
        latest["Close"]
    )

    previous_close = safe_float(
        previous["Close"]
    )

    change_pct = None

    if (
        close is not None
        and
        previous_close not in (
            None,
            0
        )
    ):

        change_pct = (
            close /
            previous_close -
            1
        ) * 100

    volume_ratio = None

    if (
        pd.notna(latest["Volume"])
        and
        pd.notna(latest["VOL_MA5"])
        and
        latest["VOL_MA5"] > 0
    ):

        volume_ratio = (
            latest["Volume"] /
            latest["VOL_MA5"]
        )

    result = {

        "code":
            code,

        "symbol":
            yahoo_symbol(
                code,
                market
            ),

        "name":
            name,

        "type":
            security_type,

        "market":
            market,

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
            safe_int(
                latest["Volume"]
            ),

        "volume_ma5":
            safe_int(
                latest["VOL_MA5"]
            ),

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
            )
    }

    if security_type == "stock":

        core = evaluate_core(
            df
        )

        result.update(core)

        result["strength_score"] = (
            calculate_strength(df)
        )

        result["ai_score"] = round(
            result["strength_score"] * 0.6
            +
            (
                result["core_score"] / 6
            ) * 40,
            2
        )

        result["signal"] = make_signal(
            result["core_score"],
            "stock"
        )

    else:

        result.update({

            "strength_score":
                calculate_strength(df),

            "ai_score":
                calculate_strength(df),

            "signal":
                "強勢 ETF"
        })

    return result


# ================================================================
# 全市場分析
# ================================================================

def process_universe(universe):

    results = []

    total = len(universe)

    for index, item in enumerate(
        universe,
        start=1
    ):

        print(
            f"[{index}/{total}] "
            f"{item['code']} "
            f"{item['name']}"
        )

        result = analyze_security(
            item
        )

        if result is not None:
            results.append(result)

        if (
            index % BATCH_SIZE == 0
        ):
            time.sleep(
                BATCH_DELAY
            )

    return results


# ================================================================
# 排序
# ================================================================

def sort_stocks(stocks):

    return sorted(
        stocks,
        key=lambda x: (
            x.get(
                "core_score",
                0
            ),
            x.get(
                "ai_score",
                0
            ),
            x.get(
                "change_pct",
                -999
            )
        ),
        reverse=True
    )


def sort_etfs(etfs):

    return sorted(
        etfs,
        key=lambda x: (
            x.get(
                "strength_score",
                0
            ),
            x.get(
                "change_pct",
                -999
            )
        ),
        reverse=True
    )


# ================================================================
# 今日 6/6
# ================================================================

def get_today_selected(stocks):

    selected = [
        stock
        for stock in stocks
        if stock.get(
            "core_score"
        ) == 6
        and stock.get(
            "core_pass"
        ) is True
    ]

    return sorted(
        selected,
        key=lambda x: (
            x.get(
                "ai_score",
                0
            ),
            x.get(
                "strength_score",
                0
            )
        ),
        reverse=True
    )


# ================================================================
# A/B 回測訊號
# ================================================================

def calculate_backtest_signals(df):

    df = df.copy()

    df["A_MACD"] = (
        (
            df["MACD"]
            >
            df["MACD_SIGNAL"]
        )
        &
        (
            df["MACD"].shift(1)
            <=
            df["MACD_SIGNAL"].shift(1)
        )
    )

    df["A_KD"] = (
        (
            df["K"]
            >
            df["D"]
        )
        &
        (
            df["K"].shift(1)
            <=
            df["D"].shift(1)
        )
    )

    df["B_MACD"] = (
        df["MACD"]
        >
        df["MACD_SIGNAL"]
    )

    df["B_KD"] = (
        df["K"]
        >
        df["D"]
    )

    df["RSI_PASS"] = (
        df["RSI"] > 50
    )

    df["VOLUME_PASS"] = (
        df["Volume"]
        >=
        df["VOL_MA5"] * 1.5
    )

    df["PRICE_MA20_PASS"] = (
        df["Close"]
        >
        df["MA20"]
    )

    df["MA20_UP_PASS"] = (
        df["MA20"]
        >
        df["MA20"].shift(1)
    )

    df["A_SIGNAL"] = (
        df["A_MACD"]
        &
        df["RSI_PASS"]
        &
        df["A_KD"]
        &
        df["VOLUME_PASS"]
        &
        df["PRICE_MA20_PASS"]
        &
        df["MA20_UP_PASS"]
    )

    df["B_SIGNAL"] = (
        df["B_MACD"]
        &
        df["RSI_PASS"]
        &
        df["B_KD"]
        &
        df["VOLUME_PASS"]
        &
        df["PRICE_MA20_PASS"]
        &
        df["MA20_UP_PASS"]
    )

    return df


# ================================================================
# 單股回測
# ================================================================

def backtest_security(
    code,
    name,
    df
):

    if len(df) < BACKTEST_MIN_HISTORY:
        return None

    df = calculate_backtest_signals(
        df
    )

    result = {

        "code":
            code,

        "name":
            name,

        "sample_start":
            str(
                df.index[0].date()
            ),

        "sample_end":
            str(
                df.index[-1].date()
            ),

        "strategies": {

            "A_today_cross": {},

            "B_current_bullish": {}
        }
    }

    strategies = {

        "A_today_cross":
            "A_SIGNAL",

        "B_current_bullish":
            "B_SIGNAL"
    }

    for strategy_name, signal_column in strategies.items():

        signal_indices = np.where(
            df[signal_column]
            .fillna(False)
            .to_numpy()
        )[0]

        for horizon in BACKTEST_HORIZONS:

            returns = []

            for idx in signal_indices:

                future_idx = (
                    idx +
                    horizon
                )

                if future_idx >= len(df):
                    continue

                entry = float(
                    df.iloc[idx]["Close"]
                )

                exit_price = float(
                    df.iloc[
                        future_idx
                    ]["Close"]
                )

                if entry <= 0:
                    continue

                ret = (
                    exit_price /
                    entry -
                    1
                )

                if math.isfinite(ret):
                    returns.append(ret)

            count = len(returns)

            if count:

                wins = sum(
                    r > 0
                    for r in returns
                )

                losses = count - wins

                win_rate = (
                    wins /
                    count *
                    100
                )

                average_return = (
                    np.mean(returns) *
                    100
                )

                median_return = (
                    np.median(returns) *
                    100
                )

                max_return = (
                    np.max(returns) *
                    100
                )

                min_return = (
                    np.min(returns) *
                    100
                )

            else:

                wins = 0
                losses = 0
                win_rate = 0
                average_return = 0
                median_return = 0
                max_return = 0
                min_return = 0

            result[
                "strategies"
            ][
                strategy_name
            ][
                f"{horizon}d"
            ] = {

                "signals":
                    int(count),

                "wins":
                    int(wins),

                "losses":
                    int(losses),

                "win_rate":
                    round(
                        float(win_rate),
                        2
                    ),

                "average_return":
                    round(
                        float(
                            average_return
                        ),
                        4
                    ),

                "median_return":
                    round(
                        float(
                            median_return
                        ),
                        4
                    ),

                "max_return":
                    round(
                        float(
                            max_return
                        ),
                        4
                    ),

                "min_return":
                    round(
                        float(
                            min_return
                        ),
                        4
                    )
            }

    return result


# ================================================================
# 回測彙總
# ================================================================

def aggregate_backtest(records):

    strategies = [
        "A_today_cross",
        "B_current_bullish"
    ]

    summary = {}

    for strategy in strategies:

        summary[strategy] = {}

        for horizon in BACKTEST_HORIZONS:

            signal_count = 0
            win_count = 0
            weighted_return_sum = 0.0

            for record in records:

                stats = (
                    record
                    .get(
                        "strategies",
                        {}
                    )
                    .get(
                        strategy,
                        {}
                    )
                    .get(
                        f"{horizon}d",
                        {}
                    )
                )

                count = int(
                    stats.get(
                        "signals",
                        0
                    )
                )

                wins = int(
                    stats.get(
                        "wins",
                        0
                    )
                )

                avg = float(
                    stats.get(
                        "average_return",
                        0
                    )
                )

                signal_count += count
                win_count += wins

                weighted_return_sum += (
                    avg * count
                )

            if signal_count:

                win_rate = (
                    win_count /
                    signal_count *
                    100
                )

                average_return = (
                    weighted_return_sum /
                    signal_count
                )

            else:

                win_rate = 0
                average_return = 0

            summary[
                strategy
            ][
                f"{horizon}d"
            ] = {

                "signals":
                    int(signal_count),

                "wins":
                    int(win_count),

                "losses":
                    int(
                        signal_count -
                        win_count
                    ),

                "win_rate":
                    round(
                        win_rate,
                        2
                    ),

                "average_return":
                    round(
                        average_return,
                        4
                    )
            }

    a_rate = summary[
        "A_today_cross"
    ][
        "10d"
    ][
        "win_rate"
    ]

    b_rate = summary[
        "B_current_bullish"
    ][
        "10d"
    ][
        "win_rate"
    ]

    if a_rate == 0 and b_rate == 0:
        better = "insufficient_data"

    elif b_rate > a_rate:
        better = "B_current_bullish"

    elif a_rate > b_rate:
        better = "A_today_cross"

    else:
        better = "tie"

    return {

        "status":
            "completed",

        "comparison_horizon":
            10,

        "strategy_A":
            "當日黃金交叉",

        "strategy_B":
            "目前維持多方",

        "better_by_win_rate":
            better,

        "strategies":
            summary
    }


# ================================================================
# 全市場回測
# ================================================================

def run_backtest(universe):

    print(
        "\n" +
        "=" * 64
    )

    print(
        "開始後台 A/B 歷史回測"
    )

    print(
        f"資料期間：{YF_PERIOD}"
    )

    print(
        "A：當日黃金交叉"
    )

    print(
        "B：目前維持多方"
    )

    print(
        "=" * 64
    )

    records = []

    stock_universe = [
        item
        for item in universe
        if not item.get(
            "is_etf",
            False
        )
    ]

    total = len(
        stock_universe
    )

    for index, item in enumerate(
        stock_universe,
        start=1
    ):

        code = item["code"]
        name = item["name"]

        print(
            f"[BT {index}/{total}] "
            f"{code} {name}"
        )

        df = download_history(
            code,
            item["market"]
        )

        if df is None:
            continue

        if len(df) < BACKTEST_MIN_HISTORY:
            continue

        df = calculate_indicators(
            df
        )

        record = backtest_security(
            code,
            name,
            df
        )

        if record is not None:
            records.append(record)

    comparison = aggregate_backtest(
        records
    )

    return {

        "version":
            VERSION,

        "status":
            "completed",

        "generated_at":
            datetime.now(
                TW_TZ
            ).isoformat(),

        "data_period":
            YF_PERIOD,

        "horizons":
            BACKTEST_HORIZONS,

        "sample_stocks":
            len(records),

        "comparison":
            comparison,

        "stocks":
            records
    }


# ================================================================
# 建立 prices.json
# ================================================================

def build_output(
    stocks,
    etfs,
    backtest
):

    stocks = sort_stocks(
        stocks
    )

    etfs = sort_etfs(
        etfs
    )

    today_selected = (
        get_today_selected(
            stocks
        )
    )

    top30 = stocks[:TOP30]

    strong_stocks = stocks[:TOP_N]

    strong_etfs = etfs[:TOP_N]

    now = datetime.now(
        TW_TZ
    )

    return {

        "version":
            VERSION,

        "schema_version":
            "prices.v8",

        "status":
            "success",

        "updated_at":
            now.isoformat(),

        "updated_at_tw":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "date":
            now.strftime(
                "%Y-%m-%d"
            ),

        "stocks":
            stocks,

        "etfs":
            etfs,

        "top30":
            top30,

        "strong_stocks":
            strong_stocks,

        "strong_etfs":
            strong_etfs,

        "today_selected":
            today_selected,

        "backtest_summary":
            backtest.get(
                "comparison",
                {}
            ),

        "statistics": {

            "stock_count":
                len(stocks),

            "etf_count":
                len(etfs),

            "top30_count":
                len(top30),

            "today_selected_count":
                len(
                    today_selected
                ),

            "core_6_count":
                len(
                    today_selected
                ),

            "backtest_stock_count":
                backtest.get(
                    "sample_stocks",
                    0
                )
        },

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
        }
    }


# ================================================================
# 嚴格驗證單筆股票
# ================================================================

def validate_stock_item(item):

    missing = (
        STOCK_REQUIRED_FIELDS -
        set(item.keys())
    )

    if missing:
        raise RuntimeError(
            f"{item.get('code', 'UNKNOWN')} "
            f"缺少欄位："
            f"{sorted(missing)}"
        )

    if item["type"] != "stock":
        raise RuntimeError(
            f"{item['code']} type 必須為 stock"
        )

    if item["core_total"] != 6:
        raise RuntimeError(
            f"{item['code']} core_total != 6"
        )

    if not isinstance(
        item["core_score"],
        int
    ):
        raise RuntimeError(
            f"{item['code']} core_score 型別錯誤"
        )

    if not (
        0 <= item["core_score"] <= 6
    ):
        raise RuntimeError(
            f"{item['code']} core_score 超出範圍"
        )

    expected_pass = (
        item["core_score"] == 6
    )

    if item["core_pass"] != expected_pass:
        raise RuntimeError(
            f"{item['code']} core_pass 不一致"
        )

    if (
        item["core_pass"]
        and
        item["signal"] != "今日精選"
    ):
        raise RuntimeError(
            f"{item['code']} 6/6 signal 錯誤"
        )


# ================================================================
# 嚴格驗證 ETF
# ================================================================

def validate_etf_item(item):

    missing = (
        ETF_REQUIRED_FIELDS -
        set(item.keys())
    )

    if missing:
        raise RuntimeError(
            f"{item.get('code', 'UNKNOWN')} "
            f"ETF 缺少欄位："
            f"{sorted(missing)}"
        )

    if item["type"] != "etf":
        raise RuntimeError(
            f"{item['code']} type 必須為 etf"
        )


# ================================================================
# prices.json 驗證
# ================================================================

def validate_prices(output):

    if not isinstance(output, dict):
        raise RuntimeError(
            "prices.json 必須是 object"
        )

    if output.get("version") != VERSION:
        raise RuntimeError(
            "version 錯誤"
        )

    if output.get(
        "schema_version"
    ) != "prices.v8":
        raise RuntimeError(
            "schema_version 錯誤"
        )

    if output.get("status") != "success":
        raise RuntimeError(
            "status 不是 success"
        )

    stocks = output.get(
        "stocks"
    )

    etfs = output.get(
        "etfs"
    )

    top30 = output.get(
        "top30"
    )

    today_selected = output.get(
        "today_selected"
    )

    if not isinstance(stocks, list):
        raise RuntimeError(
            "stocks 必須是 list"
        )

    if not isinstance(etfs, list):
        raise RuntimeError(
            "etfs 必須是 list"
        )

    if not isinstance(top30, list):
        raise RuntimeError(
            "top30 必須是 list"
        )

    if not isinstance(
        today_selected,
        list
    ):
        raise RuntimeError(
            "today_selected 必須是 list"
        )

    if len(stocks) < 50:
        raise RuntimeError(
            "stocks 少於 50 筆"
        )

    if len(top30) > 30:
        raise RuntimeError(
            "top30 超過 30 筆"
        )

    if not top30:
        raise RuntimeError(
            "top30 不得為空"
        )

    for item in stocks:
        validate_stock_item(item)

    for item in etfs:
        validate_etf_item(item)

    for item in top30:

        if item not in stocks:
            raise RuntimeError(
                f"Top30 出現不存在於 stocks 的資料："
                f"{item.get('code')}"
            )

    for item in today_selected:

        if item not in stocks:
            raise RuntimeError(
                f"today_selected 出現不存在於 stocks 的資料："
                f"{item.get('code')}"
            )

        if item["core_score"] != 6:
            raise RuntimeError(
                f"{item['code']} "
                "today_selected 不是 6/6"
            )

        if item["core_pass"] is not True:
            raise RuntimeError(
                f"{item['code']} "
                "today_selected core_pass 錯誤"
            )

    stats = output.get(
        "statistics"
    )

    if not isinstance(stats, dict):
        raise RuntimeError(
            "statistics 格式錯誤"
        )

    if stats.get(
        "stock_count"
    ) != len(stocks):
        raise RuntimeError(
            "stock_count 不一致"
        )

    if stats.get(
        "etf_count"
    ) != len(etfs):
        raise RuntimeError(
            "etf_count 不一致"
        )

    if stats.get(
        "top30_count"
    ) != len(top30):
        raise RuntimeError(
            "top30_count 不一致"
        )

    if stats.get(
        "today_selected_count"
    ) != len(today_selected):
        raise RuntimeError(
            "today_selected_count 不一致"
        )

    core = output.get(
        "core_conditions"
    )

    if not isinstance(core, dict):
        raise RuntimeError(
            "core_conditions 格式錯誤"
        )

    if core.get("total") != 6:
        raise RuntimeError(
            "core_conditions total 錯誤"
        )

    if core.get("names") != CORE_CONDITIONS:
        raise RuntimeError(
            "core_conditions names 錯誤"
        )

    print(
        "✓ prices.json V8.0 驗證成功"
    )

    return True


# ================================================================
# 回測驗證
# ================================================================

def validate_backtest(data):

    if not isinstance(data, dict):
        raise RuntimeError(
            "backtest 格式錯誤"
        )

    if data.get(
        "version"
    ) != VERSION:
        raise RuntimeError(
            "backtest version 錯誤"
        )

    if data.get(
        "status"
    ) != "completed":
        raise RuntimeError(
            "backtest 未完成"
        )

    comparison = data.get(
        "comparison"
    )

    if not comparison:
        raise RuntimeError(
            "backtest comparison 為空"
        )

    strategies = comparison.get(
        "strategies"
    )

    if not strategies:
        raise RuntimeError(
            "backtest strategies 為空"
        )

    for strategy in [
        "A_today_cross",
        "B_current_bullish"
    ]:

        if strategy not in strategies:
            raise RuntimeError(
                f"缺少回測策略：{strategy}"
            )

        for horizon in BACKTEST_HORIZONS:

            key = f"{horizon}d"

            if key not in strategies[strategy]:
                raise RuntimeError(
                    f"{strategy} "
                    f"缺少 {key}"
                )

    print(
        "✓ backtest.json V8.0 驗證成功"
    )

    return True


# ================================================================
# Atomic Write
# ================================================================

def atomic_write_json(
    path,
    data
):

    directory = os.path.dirname(
        path
    )

    fd, temp_path = tempfile.mkstemp(
        prefix="tmp_v8_",
        suffix=".json",
        dir=directory
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
            os.unlink(
                temp_path
            )
        except Exception:
            pass

        raise


# ================================================================
# 主程式
# ================================================================

def main():

    start = time.time()

    print("=" * 64)

    print(
        f"台股 AI 選股系統 "
        f"fetch_data.py {VERSION}"
    )

    print(
        "後台資料結構：prices.v8"
    )

    print(
        "正式模式：目前維持多方"
    )

    print(
        "前台：READ ONLY"
    )

    print(
        datetime.now(
            TW_TZ
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print("=" * 64)

    # ------------------------------------------------------------
    # 1. Universe
    # ------------------------------------------------------------

    print(
        "\n[1/6] 建立市場 Universe..."
    )

    universe = build_universe()

    if not universe:
        raise RuntimeError(
            "官方 Universe 為空，停止寫入"
        )

    stocks_universe = [
        item
        for item in universe
        if not item.get(
            "is_etf",
            False
        )
    ]

    etfs_universe = [
        item
        for item in universe
        if item.get(
            "is_etf",
            False
        )
    ]

    print(
        f"Universe：{len(universe)}"
    )

    print(
        f"股票 Universe："
        f"{len(stocks_universe)}"
    )

    print(
        f"ETF Universe："
        f"{len(etfs_universe)}"
    )

    # ------------------------------------------------------------
    # 2. 股票
    # ------------------------------------------------------------

    print(
        "\n[2/6] 分析股票..."
    )

    stocks = process_universe(
        stocks_universe
    )

    print(
        f"股票完成：{len(stocks)}"
    )

    # ------------------------------------------------------------
    # 3. ETF
    # ------------------------------------------------------------

    print(
        "\n[3/6] 分析 ETF..."
    )

    etfs = process_universe(
        etfs_universe
    )

    print(
        f"ETF 完成：{len(etfs)}"
    )

    # ------------------------------------------------------------
    # 4. 回測
    # ------------------------------------------------------------

    print(
        "\n[4/6] 執行 A/B 歷史回測..."
    )

    backtest = run_backtest(
        universe
    )

    validate_backtest(
        backtest
    )

    # ------------------------------------------------------------
    # 5. prices.json
    # ------------------------------------------------------------

    print(
        "\n[5/6] 建立 prices.json..."
    )

    output = build_output(
        stocks,
        etfs,
        backtest
    )

    validate_prices(
        output
    )

    atomic_write_json(
        OUTPUT_FILE,
        output
    )

    # ------------------------------------------------------------
    # 6. backtest.json
    # ------------------------------------------------------------

    print(
        "\n[6/6] 建立 backtest.json..."
    )

    atomic_write_json(
        BACKTEST_FILE,
        backtest
    )

    elapsed = (
        time.time() -
        start
    )

    selected_count = len(
        output["today_selected"]
    )

    print("\n" + "=" * 64)

    print(
        "✓ V8.0 後台資料更新完成"
    )

    print(
        f"stocks：{len(stocks)}"
    )

    print(
        f"etfs：{len(etfs)}"
    )

    print(
        f"top30：{len(output['top30'])}"
    )

    print(
        f"今日 6/6：{selected_count}"
    )

    print(
        f"prices.json：{OUTPUT_FILE}"
    )

    print(
        f"backtest.json：{BACKTEST_FILE}"
    )

    print(
        f"耗時：{elapsed:.1f} 秒"
    )

    print("=" * 64)


if __name__ == "__main__":
    main()
