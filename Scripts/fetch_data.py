# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V4.1
#
# 個股 + ETF 正式版
#
# 主要修正：
# 1. 個股 / ETF 分離處理
# 2. 使用 Close 作為市場實際價格
# 3. auto_adjust=False
# 4. 不使用 Adj Close 當現價
# 5. ETF 技術指標獨立計算
# 6. 0050 / 0056 / 00878 等 ETF 支援
# 7. 異常價格跳變檢查
# 8. MACD / RSI / KD / MA20
# 9. 成交量倍率
# 10. AI Score
# 11. DCA 四段進場價格
# 12. 動態風控
# 13. Ranking
# 14. Statistics
# 15. GitHub Pages JSON 相容
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf


# ============================================================
# 基本設定
# ============================================================

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

UNIVERSE_FILE = os.path.join(
    DATA_DIR,
    "universe.json"
)


# ------------------------------------------------------------
# 歷史資料範圍
# ------------------------------------------------------------

HISTORY_DAYS = 420


# ------------------------------------------------------------
# Yahoo Finance
# ------------------------------------------------------------

YF_SUFFIX = ".TW"


# ------------------------------------------------------------
# 批次抓取設定
# ------------------------------------------------------------

BATCH_SIZE = 8

RETRY_COUNT = 3

RETRY_SLEEP = 3


# ============================================================
# 預設股票＋ETF
#
# 如果 Data/universe.json 存在
# 將優先使用 universe.json
# ============================================================

DEFAULT_UNIVERSE = [

    # ========================================================
    # 個股
    # ========================================================

    {
        "id": "2330",
        "name": "台積電",
        "type": "STOCK"
    },

    {
        "id": "2317",
        "name": "鴻海",
        "type": "STOCK"
    },

    {
        "id": "2308",
        "name": "台達電",
        "type": "STOCK"
    },

    {
        "id": "2454",
        "name": "聯發科",
        "type": "STOCK"
    },

    {
        "id": "2382",
        "name": "廣達",
        "type": "STOCK"
    },

    {
        "id": "2357",
        "name": "華碩",
        "type": "STOCK"
    },

    {
        "id": "2376",
        "name": "技嘉",
        "type": "STOCK"
    },

    {
        "id": "3231",
        "name": "緯創",
        "type": "STOCK"
    },

    {
        "id": "2324",
        "name": "仁寶",
        "type": "STOCK"
    },

    {
        "id": "2337",
        "name": "旺宏",
        "type": "STOCK"
    },

    {
        "id": "2421",
        "name": "建準",
        "type": "STOCK"
    },

    {
        "id": "2303",
        "name": "聯電",
        "type": "STOCK"
    },

    {
        "id": "3481",
        "name": "群創",
        "type": "STOCK"
    },

    {
        "id": "2409",
        "name": "友達",
        "type": "STOCK"
    },

    {
        "id": "6271",
        "name": "同欣電",
        "type": "STOCK"
    },

    {
        "id": "2425",
        "name": "承啟",
        "type": "STOCK"
    },

    {
        "id": "2498",
        "name": "宏達電",
        "type": "STOCK"
    },


    # ========================================================
    # ETF
    # ========================================================

    {
        "id": "0050",
        "name": "元大台灣50",
        "type": "ETF"
    },

    {
        "id": "0056",
        "name": "元大高股息",
        "type": "ETF"
    },

    {
        "id": "00878",
        "name": "國泰永續高股息",
        "type": "ETF"
    },

    {
        "id": "00919",
        "name": "群益台灣精選高息",
        "type": "ETF"
    },

    {
        "id": "00929",
        "name": "復華台灣科技優息",
        "type": "ETF"
    },

    {
        "id": "006208",
        "name": "富邦台50",
        "type": "ETF"
    },

    {
        "id": "00713",
        "name": "元大台灣高息低波",
        "type": "ETF"
    },

    {
        "id": "00692",
        "name": "富邦公司治理",
        "type": "ETF"
    },

]


# ============================================================
# 時間
# ============================================================

TAIPEI_TZ = timezone(
    timedelta(hours=8)
)


def now_taipei():

    return datetime.now(
        TAIPEI_TZ
    )


# ============================================================
# JSON 清理
#
# NaN / inf 不允許直接輸出到正式 JSON
# ============================================================

def clean_value(value):

    if value is None:
        return None

    if isinstance(
        value,
        (np.integer,)
    ):
        return int(value)

    if isinstance(
        value,
        (np.floating,)
    ):
        value = float(value)

    if isinstance(
        value,
        float
    ):

        if not math.isfinite(value):
            return None

        return round(
            value,
            4
        )

    return value


def clean_object(obj):

    if isinstance(obj, dict):

        return {
            key: clean_object(value)
            for key, value in obj.items()
        }

    if isinstance(obj, list):

        return [
            clean_object(item)
            for item in obj
        ]

    return clean_value(obj)


# ============================================================
# 股票清單
# ============================================================

def load_universe():

    if os.path.exists(
        UNIVERSE_FILE
    ):

        try:

            with open(
                UNIVERSE_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)

            if isinstance(
                data,
                list
            ):

                universe = []

                for item in data:

                    if isinstance(
                        item,
                        str
                    ):

                        universe.append({
                            "id": item,
                            "name": item,
                            "type": (
                                "ETF"
                                if item.startswith("00")
                                else "STOCK"
                            )
                        })

                    elif isinstance(
                        item,
                        dict
                    ):

                        if item.get("id"):

                            universe.append({
                                "id": str(
                                    item["id"]
                                ),
                                "name": item.get(
                                    "name",
                                    str(item["id"])
                                ),
                                "type": (
                                    str(
                                        item.get(
                                            "type",
                                            "STOCK"
                                        )
                                    ).upper()
                                )
                            })

                if universe:

                    print(
                        f"讀取自訂股票清單：{len(universe)} 檔"
                    )

                    return universe

        except Exception as e:

            print(
                "⚠️ universe.json 讀取失敗：",
                e
            )

    print(
        f"使用內建股票清單：{len(DEFAULT_UNIVERSE)} 檔"
    )

    return DEFAULT_UNIVERSE.copy()


# ============================================================
# ETF 判斷
# ============================================================

def is_etf(item):

    if str(
        item.get(
            "type",
            ""
        )
    ).upper() == "ETF":

        return True

    symbol = str(
        item.get(
            "id",
            ""
        )
    )

    return (
        symbol.startswith("00")
    )


# ============================================================
# Yahoo ticker
# ============================================================

def yahoo_symbol(symbol):

    symbol = str(
        symbol
    ).upper().strip()

    if symbol.endswith(".TW"):

        return symbol

    return symbol + YF_SUFFIX


# ============================================================
# 取得歷史資料
#
# 使用 auto_adjust=False
#
# 原因：
# Close = 市場實際成交價格
# Adj Close = 股息 / 公司行動調整後價格
#
# 我們的儀表板現價不能拿 Adj Close。
# ============================================================

def download_history(
    yahoo_tickers
):

    end_date = (
        datetime.now()
        + timedelta(days=1)
    ).strftime(
        "%Y-%m-%d"
    )

    start_date = (
        datetime.now()
        - timedelta(days=HISTORY_DAYS)
    ).strftime(
        "%Y-%m-%d"
    )

    all_data = {}

    tickers = list(
        dict.fromkeys(
            yahoo_tickers
        )
    )

    print(
        f"開始抓取 {len(tickers)} 檔資料"
    )

    for start in range(
        0,
        len(tickers),
        BATCH_SIZE
    ):

        batch = tickers[
            start:
            start + BATCH_SIZE
        ]

        print(
            "\n抓取批次：",
            ", ".join(batch)
        )

        success = False

        for attempt in range(
            1,
            RETRY_COUNT + 1
        ):

            try:

                data = yf.download(
                    tickers=batch,
                    start=start_date,
                    end=end_date,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=False,
                    actions=True,
                    progress=False,
                    threads=False
                )

                if data is None:
                    raise RuntimeError(
                        "Yahoo 回傳空資料"
                    )

                # ------------------------------------------------
                # 多 ticker
                # ------------------------------------------------

                if len(batch) > 1:

                    for ticker in batch:

                        try:

                            if (
                                isinstance(
                                    data.columns,
                                    pd.MultiIndex
                                )
                            ):

                                if ticker not in data.columns.levels[0]:
                                    continue

                                df = data[
                                    ticker
                                ].copy()

                            else:

                                continue

                            df = normalize_dataframe(
                                df
                            )

                            if not df.empty:

                                all_data[
                                    ticker
                                ] = df

                        except Exception as e:

                            print(
                                f"⚠️ {ticker} 解析失敗：{e}"
                            )

                # ------------------------------------------------
                # 單 ticker
                # ------------------------------------------------

                else:

                    ticker = batch[0]

                    if (
                        isinstance(
                            data.columns,
                            pd.MultiIndex
                        )
                    ):

                        try:

                            df = data[
                                ticker
                            ].copy()

                        except Exception:

                            df = data.copy()

                    else:

                        df = data.copy()

                    df = normalize_dataframe(
                        df
                    )

                    if not df.empty:

                        all_data[
                            ticker
                        ] = df

                success = True

                break

            except Exception as e:

                print(
                    f"⚠️ 批次抓取失敗 "
                    f"({attempt}/{RETRY_COUNT})：{e}"
                )

                if attempt < RETRY_COUNT:

                    time.sleep(
                        RETRY_SLEEP * attempt
                    )

        if not success:

            print(
                "❌ 此批次最終失敗"
            )

        time.sleep(1)

    return all_data


# ============================================================
# DataFrame 標準化
# ============================================================

def normalize_dataframe(df):

    if df is None:
        return pd.DataFrame()

    df = df.copy()

    # --------------------------------------------------------
    # MultiIndex
    # --------------------------------------------------------

    if isinstance(
        df.columns,
        pd.MultiIndex
    ):

        df.columns = [
            col[0]
            if isinstance(col, tuple)
            else col
            for col in df.columns
        ]

    # --------------------------------------------------------
    # 欄位名稱
    # --------------------------------------------------------

    rename_map = {}

    for column in df.columns:

        name = str(
            column
        )

        rename_map[column] = name

    df = df.rename(
        columns=rename_map
    )

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]

    for col in required:

        if col not in df.columns:

            df[col] = np.nan

    # --------------------------------------------------------
    # 數字化
    # --------------------------------------------------------

    for col in required:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    # --------------------------------------------------------
    # 日期
    # --------------------------------------------------------

    if not isinstance(
        df.index,
        pd.DatetimeIndex
    ):

        try:

            df.index = pd.to_datetime(
                df.index
            )

        except Exception:

            return pd.DataFrame()

    df = df[
        ~df.index.duplicated(
            keep="last"
        )
    ]

    df = df.sort_index()

    # --------------------------------------------------------
    # 移除無效 Close
    # --------------------------------------------------------

    df = df[
        df["Close"].notna()
    ]

    df = df[
        df["Close"] > 0
    ]

    return df


# ============================================================
# 異常價格檢查
#
# 不直接修改 Yahoo 價格。
# 只標記異常。
#
# 這是為了避免把真正的行情誤修掉。
# ============================================================

def detect_price_anomalies(df):

    result = {
        "detected": False,
        "count": 0,
        "details": []
    }

    if df is None or len(df) < 3:

        return result

    close = df["Close"].astype(
        float
    )

    ratio = (
        close /
        close.shift(1)
    )

    abnormal = (
        (ratio > 1.5) |
        (ratio < 0.67)
    )

    indexes = df.index[
        abnormal.fillna(False)
    ]

    if len(indexes) > 0:

        result["detected"] = True

        result["count"] = len(
            indexes
        )

        for idx in indexes[-5:]:

            try:

                position = df.index.get_loc(
                    idx
                )

                if position <= 0:
                    continue

                previous = float(
                    close.iloc[
                        position - 1
                    ]
                )

                current = float(
                    close.iloc[
                        position
                    ]
                )

                result[
                    "details"
                ].append({
                    "date": idx.strftime(
                        "%Y-%m-%d"
                    ),
                    "previous": previous,
                    "current": current,
                    "ratio": (
                        current /
                        previous
                    )
                })

            except Exception:
                pass

    return result


# ============================================================
# SMA
# ============================================================

def sma(
    series,
    period
):

    return series.rolling(
        period
    ).mean()


# ============================================================
# EMA
# ============================================================

def ema(
    series,
    period
):

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


# ============================================================
# RSI
# ============================================================

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
        min_periods=period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
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
# KD
# ============================================================

def calculate_kd(
    high,
    low,
    close,
    period=9
):

    lowest_low = low.rolling(
        period
    ).min()

    highest_high = high.rolling(
        period
    ).max()

    denominator = (
        highest_high -
        lowest_low
    )

    denominator = denominator.replace(
        0,
        np.nan
    )

    rsv = (
        (close - lowest_low) /
        denominator
    ) * 100

    k = pd.Series(
        index=close.index,
        dtype=float
    )

    d = pd.Series(
        index=close.index,
        dtype=float
    )

    for i in range(
        len(close)
    ):

        if i == 0:

            k.iloc[i] = 50
            d.iloc[i] = 50

            continue

        previous_k = k.iloc[
            i - 1
        ]

        previous_d = d.iloc[
            i - 1
        ]

        current_rsv = rsv.iloc[
            i
        ]

        if pd.isna(
            current_rsv
        ):

            current_rsv = 50

        k.iloc[i] = (
            previous_k * 2 / 3
            +
            current_rsv * 1 / 3
        )

        d.iloc[i] = (
            previous_d * 2 / 3
            +
            k.iloc[i] * 1 / 3
        )

    return k, d


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    close
):

    ema12 = ema(
        close,
        12
    )

    ema26 = ema(
        close,
        26
    )

    macd = (
        ema12 -
        ema26
    )

    signal = ema(
        macd,
        9
    )

    histogram = (
        macd -
        signal
    )

    return (
        macd,
        signal,
        histogram
    )


# ============================================================
# 技術指標
# ============================================================

def calculate_indicators(
    df
):

    df = df.copy()

    close = df["Close"]

    high = df["High"]

    low = df["Low"]

    volume = df["Volume"]

    # --------------------------------------------------------
    # MA
    # --------------------------------------------------------

    df["MA5"] = sma(
        close,
        5
    )

    df["MA10"] = sma(
        close,
        10
    )

    df["MA20"] = sma(
        close,
        20
    )

    df["MA60"] = sma(
        close,
        60
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    df["RSI"] = calculate_rsi(
        close,
        14
    )

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    df["K"], df["D"] = calculate_kd(
        high,
        low,
        close,
        9
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    (
        df["MACD"],
        df["MACD_SIGNAL"],
        df["MACD_HIST"]
    ) = calculate_macd(
        close
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    df["VOL_MA5"] = sma(
        volume,
        5
    )

    df["VOL_RATIO"] = (
        volume /
        df["VOL_MA5"].replace(
            0,
            np.nan
        )
    )

    # --------------------------------------------------------
    # Change
    # --------------------------------------------------------

    df["CHANGE"] = close.diff()

    df["CHANGE_PERCENT"] = (
        close.pct_change() *
        100
    )

    return df


# ============================================================
# 取得最新指標
# ============================================================

def latest_value(
    df,
    column
):

    if column not in df.columns:

        return None

    series = df[column].dropna()

    if series.empty:

        return None

    return float(
        series.iloc[-1]
    )


# ============================================================
# 條件計算
# ============================================================

def calculate_conditions(
    df
):

    if len(df) < 30:

        return {
            "macd_golden_cross": False,
            "kd_golden_cross": False,
            "rsi_above_50": False,
            "volume_over_1_5x": False,
            "above_ma20": False,
            "ma20_up": False,
            "short_term_core": False
        }

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    macd_prev = df[
        "MACD"
    ].iloc[-2]

    signal_prev = df[
        "MACD_SIGNAL"
    ].iloc[-2]

    macd_now = df[
        "MACD"
    ].iloc[-1]

    signal_now = df[
        "MACD_SIGNAL"
    ].iloc[-1]

    macd_golden = (
        macd_prev <= signal_prev
        and
        macd_now > signal_now
    )

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    k_prev = df[
        "K"
    ].iloc[-2]

    d_prev = df[
        "D"
    ].iloc[-2]

    k_now = df[
        "K"
    ].iloc[-1]

    d_now = df[
        "D"
    ].iloc[-1]

    kd_golden = (
        k_prev <= d_prev
        and
        k_now > d_now
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi_now = latest_value(
        df,
        "RSI"
    )

    rsi_good = (
        rsi_now is not None
        and
        rsi_now > 50
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    volume_ratio = latest_value(
        df,
        "VOL_RATIO"
    )

    volume_good = (
        volume_ratio is not None
        and
        volume_ratio >= 1.5
    )

    # --------------------------------------------------------
    # MA20
    # --------------------------------------------------------

    close_now = latest_value(
        df,
        "Close"
    )

    ma20_now = latest_value(
        df,
        "MA20"
    )

    ma20_prev = latest_value(
        df.iloc[:-1],
        "MA20"
    )

    above_ma20 = (
        close_now is not None
        and
        ma20_now is not None
        and
        close_now > ma20_now
    )

    ma20_up = (
        ma20_now is not None
        and
        ma20_prev is not None
        and
        ma20_now > ma20_prev
    )

    # --------------------------------------------------------
    # 核心條件
    # --------------------------------------------------------

    core = all([
        macd_golden,
        kd_golden,
        rsi_good,
        volume_good,
        above_ma20,
        ma20_up
    ])

    return {

        "macd_golden_cross":
            bool(macd_golden),

        "kd_golden_cross":
            bool(kd_golden),

        "rsi_above_50":
            bool(rsi_good),

        "volume_over_1_5x":
            bool(volume_good),

        "above_ma20":
            bool(above_ma20),

        "ma20_up":
            bool(ma20_up),

        "short_term_core":
            bool(core)

    }


# ============================================================
# AI Score
#
# 分數：
#
# MACD      20
# KD        15
# RSI       15
# Volume    20
# MA20      15
# MA20 Up   15
#
# 總分 100
# ============================================================

def calculate_score(
    conditions,
    df
):

    score = 0

    if conditions[
        "macd_golden_cross"
    ]:

        score += 20

    if conditions[
        "kd_golden_cross"
    ]:

        score += 15

    if conditions[
        "rsi_above_50"
    ]:

        score += 15

    if conditions[
        "volume_over_1_5x"
    ]:

        score += 20

    if conditions[
        "above_ma20"
    ]:

        score += 15

    if conditions[
        "ma20_up"
    ]:

        score += 15

    # --------------------------------------------------------
    # 額外趨勢加權
    # --------------------------------------------------------

    if len(df) >= 20:

        close = latest_value(
            df,
            "Close"
        )

        ma20 = latest_value(
            df,
            "MA20"
        )

        if (
            close is not None
            and
            ma20 is not None
            and
            close > ma20 * 1.03
        ):

            score += 3

    score = min(
        score,
        100
    )

    return int(score)


# ============================================================
# 訊號
# ============================================================

def signal_text(
    score,
    conditions
):

    if conditions[
        "short_term_core"
    ]:

        return "核心訊號"

    if score >= 70:

        return "強勢"

    if score >= 50:

        return "偏多"

    if score >= 30:

        return "觀察"

    return "弱勢"


# ============================================================
# DCA
#
# 以 MA20 為基準
#
# 第一批：MA20
# 第二批：MA20 - 3%
# 第三批：MA20 - 6%
# 第四批：MA20 - 10%
# ============================================================

def calculate_dca(
    df
):

    ma20 = latest_value(
        df,
        "MA20"
    )

    close = latest_value(
        df,
        "Close"
    )

    if (
        ma20 is None
        or
        ma20 <= 0
    ):

        return {
            "buy_1": None,
            "buy_2": None,
            "buy_3": None,
            "buy_4": None,
            "action": "等待資料"
        }

    buy_1 = ma20

    buy_2 = ma20 * 0.97

    buy_3 = ma20 * 0.94

    buy_4 = ma20 * 0.90

    if close is None:

        action = "觀察"

    elif close <= buy_4:

        action = "第四批區"

    elif close <= buy_3:

        action = "第三批區"

    elif close <= buy_2:

        action = "第二批區"

    elif close <= buy_1:

        action = "第一批區"

    else:

        action = "等待回檔"

    return {

        "buy_1": buy_1,

        "buy_2": buy_2,

        "buy_3": buy_3,

        "buy_4": buy_4,

        "action": action

    }


# ============================================================
# 動態風控
# ============================================================

def calculate_risk(
    df,
    score,
    is_etf
):

    close = latest_value(
        df,
        "Close"
    )

    ma20 = latest_value(
        df,
        "MA20"
    )

    ma60 = latest_value(
        df,
        "MA60"
    )

    if close is None:

        return {
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "risk_level": "未知"
        }

    # --------------------------------------------------------
    # ETF 相對保守
    # --------------------------------------------------------

    if is_etf:

        stop_percent = 0.08

        tp1_percent = 0.08

        tp2_percent = 0.15

    else:

        stop_percent = 0.07

        tp1_percent = 0.10

        tp2_percent = 0.18

    stop_loss = close * (
        1 - stop_percent
    )

    take_profit_1 = close * (
        1 + tp1_percent
    )

    take_profit_2 = close * (
        1 + tp2_percent
    )

    if score >= 70:

        risk_level = "低"

    elif score >= 50:

        risk_level = "中"

    elif score >= 30:

        risk_level = "偏高"

    else:

        risk_level = "高"

    return {

        "stop_loss":
            stop_loss,

        "take_profit_1":
            take_profit_1,

        "take_profit_2":
            take_profit_2,

        "risk_level":
            risk_level

    }


# ============================================================
# 建立單一股票資料
# ============================================================

def build_stock(
    item,
    df
):

    symbol = str(
        item["id"]
    )

    name = item.get(
        "name",
        symbol
    )

    etf = is_etf(
        item
    )

    # --------------------------------------------------------
    # 技術指標
    # --------------------------------------------------------

    df = calculate_indicators(
        df
    )

    # --------------------------------------------------------
    # 異常價格
    # --------------------------------------------------------

    anomaly = detect_price_anomalies(
        df
    )

    # --------------------------------------------------------
    # 最新行情
    # --------------------------------------------------------

    close = latest_value(
        df,
        "Close"
    )

    previous_close = None

    if len(df) >= 2:

        previous_close = float(
            df["Close"].iloc[-2]
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

        change = (
            close -
            previous_close
        )

        change_percent = (
            change /
            previous_close
        ) * 100

    # --------------------------------------------------------
    # 條件
    # --------------------------------------------------------

    conditions = calculate_conditions(
        df
    )

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    score = calculate_score(
        conditions,
        df
    )

    # --------------------------------------------------------
    # Signal
    # --------------------------------------------------------

    signal = signal_text(
        score,
        conditions
    )

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

    dca = calculate_dca(
        df
    )

    # --------------------------------------------------------
    # Risk
    # --------------------------------------------------------

    risk = calculate_risk(
        df,
        score,
        etf
    )

    # --------------------------------------------------------
    # 最新成交量
    # --------------------------------------------------------

    volume = latest_value(
        df,
        "Volume"
    )

    volume_ma5 = latest_value(
        df,
        "VOL_MA5"
    )

    volume_ratio = latest_value(
        df,
        "VOL_RATIO"
    )

    # --------------------------------------------------------
    # 最新日期
    # --------------------------------------------------------

    latest_date = None

    if len(df) > 0:

        latest_date = (
            df.index[-1]
            .strftime(
                "%Y-%m-%d"
            )
        )

    # --------------------------------------------------------
    # 組合 JSON
    # --------------------------------------------------------

    stock = {

        "id":
            symbol,

        "symbol":
            yahoo_symbol(symbol),

        "name":
            name,

        "type":
            "ETF"
            if etf
            else "STOCK",

        "market":
            "TW",

        "data_source":
            "Yahoo Finance",

        "data_date":
            latest_date,

        "price": {

            "open":
                latest_value(
                    df,
                    "Open"
                ),

            "high":
                latest_value(
                    df,
                    "High"
                ),

            "low":
                latest_value(
                    df,
                    "Low"
                ),

            "close":
                close,

            "previous_close":
                previous_close,

            "change":
                change,

            "change_percent":
                change_percent,

            "volume":
                volume,

            "volume_ma5":
                volume_ma5,

            "volume_ratio":
                volume_ratio

        },

        "technical": {

            "ma5":
                latest_value(
                    df,
                    "MA5"
                ),

            "ma10":
                latest_value(
                    df,
                    "MA10"
                ),

            "ma20":
                latest_value(
                    df,
                    "MA20"
                ),

            "ma60":
                latest_value(
                    df,
                    "MA60"
                ),

            "rsi":
                latest_value(
                    df,
                    "RSI"
                ),

            "k":
                latest_value(
                    df,
                    "K"
                ),

            "d":
                latest_value(
                    df,
                    "D"
                ),

            "macd":
                latest_value(
                    df,
                    "MACD"
                ),

            "macd_signal":
                latest_value(
                    df,
                    "MACD_SIGNAL"
                ),

            "macd_hist":
                latest_value(
                    df,
                    "MACD_HIST"
                ),

            "volume_ratio":
                volume_ratio

        },

        "conditions":
            conditions,

        "short_term": {

            "score":
                score,

            "signal":
                signal

        },

        "dca":
            dca,

        "risk_control":
            risk,

        "data_quality": {

            "price_anomaly_detected":
                anomaly[
                    "detected"
                ],

            "price_anomaly_count":
                anomaly[
                    "count"
                ],

            "price_anomaly_details":
                anomaly[
                    "details"
                ]

        }

    }

    return clean_object(
        stock
    )


# ============================================================
# 排名
# ============================================================

def build_rankings(
    stocks
):

    valid_stocks = [
        stock
        for stock in stocks
        if stock.get(
            "price",
            {}
        ).get(
            "close"
        ) is not None
    ]

    # --------------------------------------------------------
    # AI 短線排名
    # --------------------------------------------------------

    short_term = sorted(
        valid_stocks,
        key=lambda x: (
            x.get(
                "short_term",
                {}
            ).get(
                "score",
                0
            ),
            x.get(
                "price",
                {}
            ).get(
                "volume_ratio",
                0
            ) or 0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # DCA 排名
    #
    # 越接近 MA20 越優先
    # --------------------------------------------------------

    def dca_score(stock):

        close = (
            stock
            .get("price", {})
            .get("close")
        )

        ma20 = (
            stock
            .get("technical", {})
            .get("ma20")
        )

        score = (
            stock
            .get("short_term", {})
            .get("score", 0)
        )

        if (
            close is None
            or
            ma20 is None
            or
            ma20 == 0
        ):

            distance = 999

        else:

            distance = abs(
                close /
                ma20 -
                1
            )

        return (
            distance,
            -score
        )

    dca = sorted(
        valid_stocks,
        key=dca_score
    )

    return {

        "short_term": [
            stock["id"]
            for stock in short_term
        ],

        "dca": [
            stock["id"]
            for stock in dca
        ]

    }


# ============================================================
# Statistics
# ============================================================

def build_statistics(
    stocks
):

    valid = [
        stock
        for stock in stocks
        if stock.get(
            "price",
            {}
        ).get(
            "close"
        ) is not None
    ]

    core_count = sum(
        1
        for stock in valid
        if stock.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )
    )

    macd_count = sum(
        1
        for stock in valid
        if stock.get(
            "conditions",
            {}
        ).get(
            "macd_golden_cross",
            False
        )
    )

    kd_count = sum(
        1
        for stock in valid
        if stock.get(
            "conditions",
            {}
        ).get(
            "kd_golden_cross",
            False
        )
    )

    rsi_count = sum(
        1
        for stock in valid
        if stock.get(
            "conditions",
            {}
        ).get(
            "rsi_above_50",
            False
        )
    )

    volume_count = sum(
        1
        for stock in valid
        if stock.get(
            "conditions",
            {}
        ).get(
            "volume_over_1_5x",
            False
        )
    )

    etf_count = sum(
        1
        for stock in valid
        if stock.get(
            "type"
        ) == "ETF"
    )

    stock_count = sum(
        1
        for stock in valid
        if stock.get(
            "type"
        ) == "STOCK"
    )

    return {

        "total_stocks":
            len(valid),

        "stock_count":
            stock_count,

        "etf_count":
            etf_count,

        "core_stocks":
            core_count,

        "macd_golden":
            macd_count,

        "kd_golden":
            kd_count,

        "rsi_above_50":
            rsi_count,

        "volume_over_1_5x":
            volume_count

    }


# ============================================================
# 主程式
# ============================================================

def main():

    print(
        "\n"
        "====================================================\n"
        " 台股 AI 選股・零股定投・動態風控\n"
        " fetch_data.py V4.1\n"
        " 個股 + ETF 正式版\n"
        "====================================================\n"
    )

    # --------------------------------------------------------
    # 建立 Data
    # --------------------------------------------------------

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    # --------------------------------------------------------
    # 股票清單
    # --------------------------------------------------------

    universe = load_universe()

    print(
        f"\n總標的數：{len(universe)}"
    )

    print(
        "ETF：",
        sum(
            1
            for item in universe
            if is_etf(item)
        )
    )

    print(
        "個股：",
        sum(
            1
            for item in universe
            if not is_etf(item)
        )
    )

    # --------------------------------------------------------
    # Yahoo symbols
    # --------------------------------------------------------

    ticker_map = {}

    for item in universe:

        ticker = yahoo_symbol(
            item["id"]
        )

        ticker_map[
            item["id"]
        ] = ticker

    # --------------------------------------------------------
    # 抓資料
    # --------------------------------------------------------

    historical_data = download_history(
        list(
            ticker_map.values()
        )
    )

    print(
        f"\n成功取得：{len(historical_data)} 檔"
    )

    # --------------------------------------------------------
    # 建立 stocks
    # --------------------------------------------------------

    stocks = []

    failed = []

    for item in universe:

        symbol = str(
            item["id"]
        )

        ticker = ticker_map[
            symbol
        ]

        df = historical_data.get(
            ticker
        )

        if df is None or df.empty:

            failed.append(
                symbol
            )

            continue

        try:

            stock = build_stock(
                item,
                df
            )

            stocks.append(
                stock
            )

            latest_price = (
                stock
                .get("price", {})
                .get("close")
            )

            print(
                f"✓ {symbol:>6} "
                f"{item.get('name', ''):<12} "
                f"{'ETF' if is_etf(item) else '個股':<4} "
                f"價格={latest_price}"
            )

        except Exception as e:

            print(
                f"❌ {symbol} 建立資料失敗：{e}"
            )

            traceback.print_exc()

            failed.append(
                symbol
            )

    # --------------------------------------------------------
    # Rankings
    # --------------------------------------------------------

    rankings = build_rankings(
        stocks
    )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    statistics = build_statistics(
        stocks
    )

    # --------------------------------------------------------
    # 更新資訊
    # --------------------------------------------------------

    current_time = now_taipei()

    metadata = {

        "version":
            "V4.1",

        "generated_at":
            current_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "timezone":
            "Asia/Taipei",

        "data_source":
            "Yahoo Finance",

        "price_field":
            "Close",

        "price_policy":
            "raw_close_auto_adjust_false",

        "history_days":
            HISTORY_DAYS,

        "description":
            "台股個股＋ETF AI選股與零股定投資料",

        "failed_symbols":
            failed

    }

    # --------------------------------------------------------
    # 最終 JSON
    # --------------------------------------------------------

    output = {

        "metadata":
            metadata,

        "statistics":
            statistics,

        "rankings":
            rankings,

        "stocks":
            stocks

    }

    output = clean_object(
        output
    )

    # --------------------------------------------------------
    # 寫入 JSON
    # --------------------------------------------------------

    temp_file = (
        OUTPUT_FILE +
        ".tmp"
    )

    with open(
        temp_file,
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

    os.replace(
        temp_file,
        OUTPUT_FILE
    )

    # --------------------------------------------------------
    # 完成
    # --------------------------------------------------------

    print(
        "\n"
        "===================================================="
    )

    print(
        "✓ Data/prices.json 更新完成"
    )

    print(
        f"✓ 有效標的：{len(stocks)}"
    )

    print(
        f"✓ 個股：{statistics['stock_count']}"
    )

    print(
        f"✓ ETF：{statistics['etf_count']}"
    )

    print(
        f"✓ 核心訊號：{statistics['core_stocks']}"
    )

    print(
        f"✓ MACD 黃金交叉：{statistics['macd_golden']}"
    )

    print(
        f"✓ RSI > 50：{statistics['rsi_above_50']}"
    )

    print(
        f"✓ 量能 > 1.5x：{statistics['volume_over_1_5x']}"
    )

    if failed:

        print(
            "⚠️ 抓取失敗：",
            ", ".join(
                failed
            )
        )

    print(
        "====================================================\n"
    )


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    main()
