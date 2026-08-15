# ================================================================
# 台股 AI 選股系統
# fetch_data.py V8.0 FINAL
#
# 正式版
#
# 核心：
# 1. 自動建立 TWSE / TPEx Universe
# 2. 不手動指定任何股票
# 3. 不手動指定任何 ETF
# 4. 排除權證、可轉債等非一般股票商品
# 5. ETF 與股票分離
# 6. 六項正式核心條件
# 7. MACD / KD 採「目前維持多方」
# 8. RSI > 50
# 9. Volume >= MA5 * 1.5
# 10. Close > MA20
# 11. MA20 今日 > 昨日
# 12. 自動建立 Top30
# 13. A/B 歷史回測
# 14. 回測資料另存 Data/backtest.json
# 15. prices.json 寫入 backtest_summary
# 16. JSON 原子寫入
# 17. 單一股票資料不足不影響整體流程
# 18. 不因前台需求修改後台核心判斷
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
# 行情
# ================================================================

YF_PERIOD = "2y"
YF_INTERVAL = "1d"

MAX_RETRY = 3
RETRY_DELAY = 2

BATCH_SIZE = 40
BATCH_DELAY = 1.0

TOP_N = 10
TOP30 = 30


# ================================================================
# 回測
# ================================================================

BACKTEST_HORIZONS = [
    5,
    10,
    20,
]

BACKTEST_MIN_HISTORY = 120


# ================================================================
# 六項核心條件
# ================================================================

CORE_CONDITIONS = [
    "MACD 多方",
    "RSI > 50",
    "KD 多方",
    "成交量放大",
    "股價 > MA20",
    "MA20 向上",
]


# ================================================================
# 排除商品
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
    "轉換公司債",
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
    "U",
}


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

    value = clean_text(
        value
    ).upper()

    if value.endswith(".TW"):
        value = value[:-3]

    if value.endswith(".TWO"):
        value = value[:-4]

    return value


def safe_float(value):

    try:

        value = float(
            value
        )

        if not math.isfinite(
            value
        ):
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

    name = clean_text(
        name
    )

    return any(
        keyword in name
        for keyword in
        INVALID_SECURITY_KEYWORDS
    )


# ================================================================
# ETF 判斷
# ================================================================

def is_etf(
    code,
    name,
    security_type=None
):

    code = clean_code(
        code
    )

    name = clean_text(
        name
    )

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


# ================================================================
# Yahoo
# ================================================================

def yahoo_symbol(
    code,
    market="TW"
):

    code = clean_code(
        code
    )

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

    if not isinstance(
        data,
        list
    ):

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

        if is_invalid_security(
            name
        ):
            continue

        universe.append({

            "code": code,

            "name": name,

            "market": "TW",

            "is_etf":
                is_etf(
                    code,
                    name
                ),
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
        ),
    ]

    for url in urls:

        try:

            response = requests.get(
                url,
                timeout=30
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(
                data,
                list
            ):
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

                if is_invalid_security(
                    name
                ):
                    continue

                universe.append({

                    "code": code,

                    "name": name,

                    "market": "TWO",

                    "is_etf":
                        is_etf(
                            code,
                            name
                        ),
                })

            if universe:
                return universe

        except Exception as exc:

            print(
                f"[WARN] TPEx API：{exc}"
            )

    return []


# ================================================================
# Universe
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
# 歷史資料
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
                "Volume",
            ]

            missing = [
                col
                for col in required
                if col not in df.columns
            ]

            if missing:

                raise RuntimeError(
                    f"缺少欄位：{missing}"
                )

            df = df[
                required
            ].copy()

            df = df.dropna(
                subset=["Close"]
            )

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

    return (
        k,
        d
    )


# ================================================================
# 指標
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
# 六項正式條件
# ================================================================

def evaluate_core(
    df
):

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    macd_pass = (
        pd.notna(latest["MACD"])
        and
        pd.notna(latest["MACD_SIGNAL"])
        and
        latest["MACD"] >
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
        latest["K"] >
        latest["D"]
    )

    volume_pass = (
        pd.notna(latest["Volume"])
        and
        pd.notna(latest["VOL_MA5"])
        and
        latest["VOL_MA5"] > 0
        and
        latest["Volume"] >=
        latest["VOL_MA5"] * 1.5
    )

    price_ma20_pass = (
        pd.notna(latest["Close"])
        and
        pd.notna(latest["MA20"])
        and
        latest["Close"] >
        latest["MA20"]
    )

    ma20_up_pass = (
        pd.notna(latest["MA20"])
        and
        pd.notna(previous["MA20"])
        and
        latest["MA20"] >
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
            bool(ma20_up_pass),
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
# 回測訊號
# ================================================================

def calculate_backtest_signals(
    df
):

    df = df.copy()

    df["A_MACD"] = (
        df["MACD"] >
        df["MACD_SIGNAL"]
    ) & (
        df["MACD"].shift(1) <=
        df["MACD_SIGNAL"].shift(1)
    )

    df["A_KD"] = (
        df["K"] >
        df["D"]
    ) & (
        df["K"].shift(1) <=
        df["D"].shift(1)
    )

    df["B_MACD"] = (
        df["MACD"] >
        df["MACD_SIGNAL"]
    )

    df["B_KD"] = (
        df["K"] >
        df["D"]
    )

    df["RSI_PASS"] = (
        df["RSI"] > 50
    )

    df["VOLUME_PASS"] = (
        df["Volume"] >=
        df["VOL_MA5"] * 1.5
    )

    df["PRICE_MA20_PASS"] = (
        df["Close"] >
        df["MA20"]
    )

    df["MA20_UP_PASS"] = (
        df["MA20"] >
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

        "code": code,

        "name": name,

        "sample_start":
            str(df.index[0].date()),

        "sample_end":
            str(df.index[-1].date()),

        "strategies": {

            "A_today_cross": {},

            "B_current_bullish": {},
        },
    }

    strategies = {

        "A_today_cross":
            "A_SIGNAL",

        "B_current_bullish":
            "B_SIGNAL",
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

                if (
                    entry <= 0
                    or
                    not math.isfinite(entry)
                    or
                    not math.isfinite(exit_price)
                ):
                    continue

                ret = (
                    exit_price /
                    entry -
                    1
                )

                if math.isfinite(ret):
                    returns.append(ret)

            count = len(
                returns
            )

            wins = sum(
                r > 0
                for r in returns
            )

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
                    int(count - wins),

                "win_rate":
                    round(
                        (
                            wins /
                            count *
                            100
                        )
                        if count
                        else 0,
                        2
                    ),

                "average_return":
                    round(
                        (
                            np.mean(
                                returns
                            ) * 100
                        )
                        if count
                        else 0,
                        4
                    ),

                "median_return":
                    round(
                        (
                            np.median(
                                returns
                            ) * 100
                        )
                        if count
                        else 0,
                        4
                    ),

                "max_return":
                    round(
                        (
                            np.max(
                                returns
                            ) * 100
                        )
                        if count
                        else 0,
                        4
                    ),

                "min_return":
                    round(
                        (
                            np.min(
                                returns
                            ) * 100
                        )
                        if count
                        else 0,
                        4
                    ),
            }

    return result


# ================================================================
# 全市場回測
# ================================================================

def aggregate_backtest(
    records
):

    strategies = [
        "A_today_cross",
        "B_current_bullish",
    ]

    summary = {}

    for strategy in strategies:

        summary[strategy] = {}

        for horizon in BACKTEST_HORIZONS:

            all_returns = []

            for record in records:

                stock_stats = (
                    record
                    .get("strategies", {})
                    .get(strategy, {})
                    .get(f"{horizon}d", {})
                )

                count = int(
                    stock_stats.get(
                        "signals",
                        0
                    )
                )

                avg = float(
                    stock_stats.get(
                        "average_return",
                        0
                    )
                )

                wins = int(
                    stock_stats.get(
                        "wins",
                        0
                    )
                )

                if count:

                    all_returns.extend(
                        [avg / 100] * count
                    )

            signals = len(
                all_returns
            )

            wins = 0

            for record in records:

                stock_stats = (
                    record
                    .get("strategies", {})
                    .get(strategy, {})
                    .get(f"{horizon}d", {})
                )

                wins += int(
                    stock_stats.get(
                        "wins",
                        0
                    )
                )

            summary[strategy][
                f"{horizon}d"
            ] = {

                "signals":
                    signals,

                "wins":
                    wins,

                "losses":
                    signals - wins,

                "win_rate":
                    round(
                        (
                            wins /
                            signals *
                            100
                        )
                        if signals
                        else 0,
                        2
                    ),

                "average_return":
                    round(
                        (
                            np.mean(
                                all_returns
                            ) * 100
                        )
                        if all_returns
                        else 0,
                        4
                    ),
            }

    comparison_horizon = 10

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

    if (
        a_rate == 0
        and
        b_rate == 0
    ):

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
            comparison_horizon,

        "strategy_A":
            "當日黃金交叉",

        "strategy_B":
            "目前維持多方",

        "better_by_win_rate":
            better,

        "strategies":
            summary,
    }


# ================================================================
# 回測
# ================================================================

def run_backtest(
    universe
):

    print(
        "\n" + "=" * 64
    )

    print(
        "開始後台 A/B 歷史回測"
    )

    print(
        f"資料期間：{YF_PERIOD}"
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

            print(
                f"[SKIP] {code}: "
                f"歷史資料不足 "
                f"{BACKTEST_MIN_HISTORY} 日"
            )

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

            records.append(
                record
            )

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
            records,
    }


# ================================================================
# 強勢分數
# ================================================================

def calculate_strength(
    df
):

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
        pd.notna(latest["MACD_SIGNAL"])
        and
        latest["MACD"] >
        latest["MACD_SIGNAL"]
    ):

        score += 20

    if (
        pd.notna(latest["K"])
        and
        pd.notna(latest["D"])
        and
        latest["K"] >
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
                (
                    ratio -
                    1
                ) * 10
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
# 分析商品
# ================================================================

def analyze_security(
    item
):

    code = item["code"]
    name = item["name"]
    market = item["market"]

    security_type = (
        "etf"
        if item.get(
            "is_etf",
            False
        )
        else
        "stock"
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
            (
                safe_float(
                    latest["Volume"] /
                    latest["VOL_MA5"]
                )
                if (
                    pd.notna(
                        latest["Volume"]
                    )
                    and
                    pd.notna(
                        latest["VOL_MA5"]
                    )
                    and
                    latest["VOL_MA5"] > 0
                )
                else None
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
    }

    if security_type == "stock":

        core = evaluate_core(
            df
        )

        result.update(
            core
        )

        result[
            "strength_score"
        ] = calculate_strength(
            df
        )

        result[
            "ai_score"
        ] = round(
            (
                result[
                    "strength_score"
                ] * 0.6
                +
                (
                    result[
                        "core_score"
                    ] / 6
                ) * 40
            ),
            2
        )

        result[
            "signal"
        ] = make_signal(
            result[
                "core_score"
            ],
            "stock"
        )

    else:

        result.update({

            "macd_golden_cross":
                None,

            "rsi_over_50":
                None,

            "kd_golden_cross":
                None,

            "volume_expand":
                None,

            "price_over_ma20":
                None,

            "ma20_up":
                None,

            "core_score":
                None,

            "core_total":
                None,

            "core_pass":
                None,
        })

        result[
            "strength_score"
        ] = calculate_strength(
            df
        )

        result[
            "ai_score"
        ] = result[
            "strength_score"
        ]

        result[
            "signal"
        ] = "強勢 ETF"

    return result


# ================================================================
# Universe 分析
# ================================================================

def process_universe(
    universe
):

    results = []

    total = len(
        universe
    )

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

            results.append(
                result
            )

        if (
            index %
            BATCH_SIZE
            == 0
        ):

            time.sleep(
                BATCH_DELAY
            )

    return results


# ================================================================
# 排序
# ================================================================

def sort_stocks(
    stocks
):

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
            ),
        ),

        reverse=True
    )


def sort_etfs(
    etfs
):

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
            ),
        ),

        reverse=True
    )


# ================================================================
# 今日 6/6
# ================================================================

def get_today_selected(
    stocks
):

    selected = [

        stock

        for stock in stocks

        if stock.get(
            "core_score"
        ) == 6
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
            ),
        ),

        reverse=True
    )


# ================================================================
# Output
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

    strong_stocks = stocks[:TOP_N]
    strong_etfs = etfs[:TOP_N]

    top30 = stocks[:TOP30]

    now = datetime.now(
        TW_TZ
    )

    return {

        "version":
            VERSION,

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

        "top_stocks":
            strong_stocks,

        "top_etfs":
            strong_etfs,

        "today_selected":
            today_selected,

        "today_picks":
            today_selected,

        "featured_stocks":
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
                ),
        },

        "core_conditions": {

            "total":
                6,

            "names":
                CORE_CONDITIONS,

            "definition":
                "目前維持多方",
        },
    }


# ================================================================
# 原子寫入
# ================================================================

def atomic_write_json(
    path,
    data
):

    directory = os.path.dirname(
        path
    )

    fd, temp_path = tempfile.mkstemp(
        prefix="tmp_",
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
# 驗證 prices
# ================================================================

def validate_prices(
    output
):

    stocks = output.get(
        "stocks",
        []
    )

    etfs = output.get(
        "etfs",
        []
    )

    top30 = output.get(
        "top30",
        []
    )

    if not isinstance(
        stocks,
        list
    ):
        raise RuntimeError(
            "stocks 格式錯誤"
        )

    if not isinstance(
        etfs,
        list
    ):
        raise RuntimeError(
            "etfs 格式錯誤"
        )

    if len(stocks) < 50:

        raise RuntimeError(
            f"stocks 少於 50 筆："
            f"{len(stocks)}"
        )

    if not top30:

        raise RuntimeError(
            "top30 為空"
        )

    required = {
        "code",
        "name",
        "price",
        "ai_score",
        "signal",
    }

    for item in top30:

        missing = (
            required -
            set(item.keys())
        )

        if missing:

            raise RuntimeError(
                f"{item.get('code')} "
                f"缺少欄位：{missing}"
            )

    return True


# ================================================================
# 驗證回測
# ================================================================

def validate_backtest(
    data
):

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
        "B_current_bullish",
    ]:

        if strategy not in strategies:

            raise RuntimeError(
                f"缺少回測策略：{strategy}"
            )

        for horizon in BACKTEST_HORIZONS:

            key = f"{horizon}d"

            if key not in strategies[
                strategy
            ]:

                raise RuntimeError(
                    f"{strategy} 缺少 {key}"
                )

    return True


# ================================================================
# 主程式
# ================================================================

def main():

    start = time.time()

    print(
        "=" * 64
    )

    print(
        f"台股 AI 選股系統 "
        f"fetch_data.py {VERSION}"
    )

    print(
        "正式核心模式：目前維持多方"
    )

    print(
        "禁止手動指定任何個股 / ETF"
    )

    print(
        "啟用 A/B 歷史回測"
    )

    print(
        datetime.now(
            TW_TZ
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print(
        "=" * 64
    )

    # ------------------------------------------------------------
    # 1
    # ------------------------------------------------------------

    print(
        "\n[1/6] 建立官方市場 Universe..."
    )

    universe = build_universe()

    if not universe:

        raise RuntimeError(
            "官方 Universe 為空，"
            "停止寫入資料"
        )

    print(
        f"Universe：{len(universe)}"
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
        f"股票 Universe："
        f"{len(stocks_universe)}"
    )

    print(
        f"ETF Universe："
        f"{len(etfs_universe)}"
    )

    # ------------------------------------------------------------
    # 2
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
    # 3
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
    # 4
    # ------------------------------------------------------------

    print(
        "\n[4/6] 執行後台歷史回測..."
    )

    backtest = run_backtest(
        universe
    )

    validate_backtest(
        backtest
    )

    # ------------------------------------------------------------
    # 5
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
    # 6
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

    comparison = backtest[
        "comparison"
    ]

    print(
        "\n" + "=" * 64
    )

    print(
        "✓ 後台資料更新完成"
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
        f"今日 6/6："
        f"{len(output['today_selected'])}"
    )

    print(
        "\n===== A/B 回測結果 ====="
    )

    print(
        f"A 10日勝率："
        f"{comparison['strategies']['A_today_cross']['10d']['win_rate']}%"
    )

    print(
        f"B 10日勝率："
        f"{comparison['strategies']['B_current_bullish']['10d']['win_rate']}%"
    )

    print(
        "勝率較高："
        f"{comparison['better_by_win_rate']}"
    )

    print(
        f"\n輸出：{OUTPUT_FILE}"
    )

    print(
        f"回測：{BACKTEST_FILE}"
    )

    print(
        f"耗時：{elapsed:.1f} 秒"
    )

    print(
        "=" * 64
    )


if __name__ == "__main__":
    main()
