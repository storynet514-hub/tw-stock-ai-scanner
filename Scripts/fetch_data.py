# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V7.1
#
# 全市場高速掃描正式版
#
# 功能：
# 1. 自動取得 TWSE 上市股票
# 2. 自動取得 TPEx 上櫃股票
# 3. 自動納入 ETF
# 4. 不使用固定 STOCK_LIST
# 5. Yahoo Finance 批次下載
# 6. 每批 100 檔，避免逐檔 HTTP request
# 7. 批次失敗時才逐檔補抓
# 8. RSI / KD / MACD / MA5 / MA20 / MA60
# 9. 成交量 / 5日均量 / 成交量比
# 10. MACD 黃金交叉
# 11. KD 黃金交叉
# 12. RSI > 50
# 13. 成交量 > 1.5倍5日均量
# 14. 股價站上 MA20
# 15. MA20 向上
# 16. AI SCORE 0~100
# 17. 核心訊號
# 18. 四段式 DCA
# 19. 全市場排名
# 20. 統計資料
# 21. RSI 強制限制 0~100
# 22. KD 強制限制 0~100
# 23. 單檔失敗不影響其他股票
# 24. 保留 index.html V6.2 JSON 結構
#
# V7.1 主要修正：
#
# ★ 不再每檔股票單獨 yf.download()
# ★ 改成批次下載
# ★ 大幅降低 Yahoo Finance HTTP request 數量
# ★ 移除每檔 sleep(0.3)
# ★ 批次失敗才進行單檔 fallback
#
# ============================================================

import os
import sys
import json
import math
import time
import traceback

from datetime import datetime, timezone, timedelta

import requests
import numpy as np
import pandas as pd
import yfinance as yf


# ============================================================
# 基本設定
# ============================================================

VERSION = "V7.1"

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
# 台灣時區
# ============================================================

TW_TZ = timezone(
    timedelta(hours=8)
)


# ============================================================
# 官方 API
# ============================================================

TWSE_BASE_URL = (
    "https://openapi.twse.com.tw/v1"
)

TWSE_LIST_URL = (
    TWSE_BASE_URL +
    "/opendata/t187ap03_L"
)

TPEx_BASE_URL = (
    "https://www.tpex.org.tw/openapi/v1"
)

TPEx_QUOTES_URL = (
    TPEx_BASE_URL +
    "/tpex_mainboard_quotes"
)


# ============================================================
# Yahoo Finance
# ============================================================

YF_TIMEOUT = 30

# 每批股票數量
#
# 100 是速度與穩定性的折衷。
#
# 如果 Yahoo 未來限制更嚴格，
# 可以降到 50。
# ============================================================

BATCH_SIZE = 100


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({

    "User-Agent":
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36",

    "Accept":
        "application/json,text/plain,*/*"

})


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

        if isinstance(
            value,
            (
                list,
                tuple,
                dict
            )
        ):
            return default

        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except Exception:

        return default


# ============================================================
# 四捨五入
# ============================================================

def round_value(
    value,
    digits=2
):

    number = safe_float(
        value
    )

    if number is None:
        return None

    return round(
        number,
        digits
    )


# ============================================================
# 清理股票代號
# ============================================================

def clean_code(
    code
):

    if code is None:
        return None

    code = str(
        code
    ).strip()

    if not code:
        return None

    code = code.replace(
        " ",
        ""
    )

    return code


# ============================================================
# ETF 判斷
# ============================================================

def is_etf_code(
    code
):

    code = clean_code(
        code
    )

    if not code:
        return False

    #
    # 台股 ETF 主要以 00 開頭
    #
    return code.startswith(
        "00"
    )


# ============================================================
# Yahoo Symbol
#
# TWSE -> XXXX.TW
# TPEx -> XXXX.TWO
# ============================================================

def yahoo_symbol(
    code,
    market
):

    code = clean_code(
        code
    )

    if market == "TPEx":

        return (
            code +
            ".TWO"
        )

    return (
        code +
        ".TW"
    )


# ============================================================
# HTTP JSON
# ============================================================

def get_json(
    url,
    timeout=30
):

    try:

        response = SESSION.get(
            url,
            timeout=timeout
        )

        response.raise_for_status()

        return response.json()

    except Exception as error:

        print(
            f"API 取得失敗：{url}"
        )

        print(
            f"原因：{error}"
        )

        return None


# ============================================================
# TWSE 上市股票
# ============================================================

def get_twse_stocks():

    print("")
    print(
        "================================================"
    )
    print(
        "取得 TWSE 上市市場清單..."
    )
    print(
        "================================================"
    )

    data = get_json(
        TWSE_LIST_URL
    )

    if not data:

        print(
            "TWSE 清單取得失敗"
        )

        return []

    stocks = []

    for item in data:

        try:

            code = clean_code(

                item.get(
                    "公司代號"
                )

                or item.get(
                    "Code"
                )

                or item.get(
                    "股票代號"
                )

            )

            name = (

                item.get(
                    "公司簡稱"
                )

                or item.get(
                    "Name"
                )

                or item.get(
                    "公司名稱"
                )

                or ""

            )

            if not code:
                continue

            #
            # 排除明顯不是正常交易標的
            #
            if len(code) < 4:
                continue

            stock_type = (
                "ETF"
                if is_etf_code(code)
                else "STOCK"
            )

            stocks.append({

                "id":
                    code,

                "name":
                    str(name).strip(),

                "market":
                    "TWSE",

                "type":
                    stock_type

            })

        except Exception:

            continue

    #
    # 去重
    #

    unique = {}

    for stock in stocks:

        unique[
            stock["id"]
        ] = stock

    stocks = list(
        unique.values()
    )

    print(
        f"TWSE 清單：{len(stocks)} 檔"
    )

    return stocks


# ============================================================
# TPEx 上櫃股票
# ============================================================

def get_tpex_stocks():

    print("")
    print(
        "================================================"
    )
    print(
        "取得 TPEx 上櫃市場清單..."
    )
    print(
        "================================================"
    )

    data = get_json(
        TPEx_QUOTES_URL
    )

    if not data:

        print(
            "TPEx 清單取得失敗"
        )

        return []

    stocks = []

    for item in data:

        try:

            code = clean_code(

                item.get(
                    "SecuritiesCompanyCode"
                )

                or item.get(
                    "Code"
                )

                or item.get(
                    "股票代號"
                )

            )

            name = (

                item.get(
                    "CompanyName"
                )

                or item.get(
                    "Name"
                )

                or item.get(
                    "公司名稱"
                )

                or item.get(
                    "股票名稱"
                )

                or ""

            )

            if not code:
                continue

            if len(code) < 4:
                continue

            stock_type = (
                "ETF"
                if is_etf_code(code)
                else "STOCK"
            )

            stocks.append({

                "id":
                    code,

                "name":
                    str(name).strip(),

                "market":
                    "TPEx",

                "type":
                    stock_type

            })

        except Exception:

            continue

    #
    # 去重
    #

    unique = {}

    for stock in stocks:

        unique[
            stock["id"]
        ] = stock

    stocks = list(
        unique.values()
    )

    print(
        f"TPEx 清單：{len(stocks)} 檔"
    )

    return stocks


# ============================================================
# 建立全市場清單
# ============================================================

def build_market_list():

    print("")
    print(
        "################################################"
    )
    print(
        "# 台股全市場 AI 掃描"
    )
    print(
        "################################################"
    )

    twse = get_twse_stocks()

    tpex = get_tpex_stocks()

    all_stocks = (
        twse +
        tpex
    )

    #
    # 以市場 + 代號去重
    #

    unique = {}

    for stock in all_stocks:

        key = (
            stock["market"],
            stock["id"]
        )

        unique[
            key
        ] = stock

    all_stocks = list(
        unique.values()
    )

    #
    # 排序
    #

    all_stocks.sort(
        key=lambda x: (
            x["market"],
            x["id"]
        )
    )

    #
    # 統計
    #

    twse_count = sum(
        1
        for x in all_stocks
        if x["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for x in all_stocks
        if x["market"] == "TPEx"
    )

    etf_count = sum(
        1
        for x in all_stocks
        if x["type"] == "ETF"
    )

    stock_count = sum(
        1
        for x in all_stocks
        if x["type"] == "STOCK"
    )

    print("")
    print(
        "================================================"
    )

    print(
        "全市場清單建立完成"
    )

    print(
        f"上市：{twse_count} 檔"
    )

    print(
        f"上櫃：{tpex_count} 檔"
    )

    print(
        f"ETF：{etf_count} 檔"
    )

    print(
        f"一般股票：{stock_count} 檔"
    )

    print(
        f"市場清單總數："
        f"{len(all_stocks)} 檔"
    )

    print(
        "================================================"
    )

    return all_stocks


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    close,
    period=14
):

    close = pd.to_numeric(
        close,
        errors="coerce"
    )

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

    rsi = pd.Series(
        np.nan,
        index=close.index
    )

    #
    # 正常狀態
    #

    normal = (
        avg_loss > 0
    )

    rsi.loc[normal] = (

        100 -

        (
            100 /

            (
                1 +

                avg_gain.loc[normal] /
                avg_loss.loc[normal]

            )
        )

    )

    #
    # 完全沒有跌幅
    #

    no_loss = (

        (avg_loss == 0)
        &
        (avg_gain > 0)

    )

    rsi.loc[
        no_loss
    ] = 100

    #
    # 完全沒有變化
    #

    flat = (

        (avg_loss == 0)
        &
        (avg_gain == 0)

    )

    rsi.loc[
        flat
    ] = 50

    #
    # RSI 強制 0~100
    #

    rsi = rsi.clip(
        lower=0,
        upper=100
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

    lowest_low = (
        low.rolling(
            period
        ).min()
    )

    highest_high = (
        high.rolling(
            period
        ).max()
    )

    denominator = (
        highest_high -
        lowest_low
    )

    denominator = (
        denominator.replace(
            0,
            np.nan
        )
    )

    rsv = (

        (
            close -
            lowest_low
        )

        /

        denominator

    ) * 100

    rsv = rsv.clip(
        0,
        100
    )

    k = rsv.ewm(
        com=2,
        adjust=False
    ).mean()

    d = k.ewm(
        com=2,
        adjust=False
    ).mean()

    k = k.clip(
        0,
        100
    )

    d = d.clip(
        0,
        100
    )

    return (
        k,
        d
    )


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    close,
    fast=12,
    slow=26,
    signal=9
):

    ema_fast = close.ewm(
        span=fast,
        adjust=False
    ).mean()

    ema_slow = close.ewm(
        span=slow,
        adjust=False
    ).mean()

    macd = (
        ema_fast -
        ema_slow
    )

    signal_line = (
        macd.ewm(
            span=signal,
            adjust=False
        ).mean()
    )

    histogram = (
        macd -
        signal_line
    )

    return (
        macd,
        signal_line,
        histogram
    )


# ============================================================
# 最新值
# ============================================================

def get_last_value(
    series
):

    try:

        series = pd.to_numeric(
            series,
            errors="coerce"
        ).dropna()

        if len(series) == 0:
            return None

        return float(
            series.iloc[-1]
        )

    except Exception:

        return None


# ============================================================
# 清理 Yahoo DataFrame
# ============================================================

def normalize_dataframe(
    df
):

    if df is None:
        return None

    if df.empty:
        return None

    try:

        #
        # Yahoo Finance MultiIndex
        #

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            #
            # 這裡不直接 flatten，
            # 因為批次下載需要保留 ticker。
            #
            return df

        df.columns = [
            str(column)
            .strip()
            .lower()
            for column in df.columns
        ]

        return df

    except Exception:

        return None


# ============================================================
# 批次下載 Yahoo Finance
# ============================================================

def download_batch(
    stocks
):

    if not stocks:
        return {}

    symbols = [
        yahoo_symbol(
            stock["id"],
            stock["market"]
        )
        for stock in stocks
    ]

    symbol_to_stock = {}

    for stock, symbol in zip(
        stocks,
        symbols
    ):

        symbol_to_stock[
            symbol
        ] = stock

    print("")
    print(
        "------------------------------------------------"
    )

    print(
        f"批次下載："
        f"{len(symbols)} 檔"
    )

    print(
        f"Yahoo symbols："
        f"{symbols[0]} "
        f"... "
        f"{symbols[-1]}"
    )

    print(
        "------------------------------------------------"
    )

    result = {}

    try:

        df = yf.download(

            tickers=symbols,

            period="1y",

            interval="1d",

            auto_adjust=False,

            progress=False,

            threads=True,

            group_by="column",

            timeout=YF_TIMEOUT

        )

        if df is None:
            return result

        if df.empty:
            return result

        #
        # 批次 Yahoo 通常會得到：
        #
        # MultiIndex
        #
        # Level 0:
        # Open High Low Close ...
        #
        # Level 1:
        # ticker
        #

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            level0 = list(
                df.columns
                .get_level_values(0)
            )

            level1 = list(
                df.columns
                .get_level_values(1)
            )

            required = {
                "Open",
                "High",
                "Low",
                "Close",
                "Volume"
            }

            #
            # 正常格式：
            # Open / Close / ...
            # 在第一層
            #

            if required.intersection(
                set(level0)
            ):

                for symbol in symbols:

                    try:

                        data = df.xs(
                            symbol,
                            axis=1,
                            level=1,
                            drop_level=True
                        )

                        data.columns = [
                            str(x)
                            .strip()
                            .lower()
                            for x in data.columns
                        ]

                        if "close" not in data.columns:
                            continue

                        result[
                            symbol
                        ] = data

                    except Exception:

                        continue

            #
            # 某些 Yahoo 格式：
            # ticker 在第一層
            #

            else:

                for symbol in symbols:

                    try:

                        data = df[
                            symbol
                        ].copy()

                        data.columns = [
                            str(x)
                            .strip()
                            .lower()
                            for x in data.columns
                        ]

                        if "close" not in data.columns:
                            continue

                        result[
                            symbol
                        ] = data

                    except Exception:

                        continue

        else:

            #
            # 單一 symbol 的特殊情況
            #

            if len(symbols) == 1:

                data = df.copy()

                data.columns = [
                    str(x)
                    .strip()
                    .lower()
                    for x in data.columns
                ]

                result[
                    symbols[0]
                ] = data

    except Exception as error:

        print(
            f"批次下載失敗：{error}"
        )

        return {}

    #
    # 清理資料
    #

    clean_result = {}

    for symbol, data in result.items():

        try:

            required = [
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]

            if not all(
                column in data.columns
                for column in required
            ):
                continue

            for column in required:

                data[column] = pd.to_numeric(
                    data[column],
                    errors="coerce"
                )

            data = data.dropna(
                subset=[
                    "close"
                ]
            )

            if len(data) < 35:
                continue

            clean_result[
                symbol
            ] = data

        except Exception:

            continue

    print(
        f"批次成功取得："
        f"{len(clean_result)} 檔"
    )

    return clean_result


# ============================================================
# 單檔 fallback
#
# 只有批次沒有取得資料時才使用
# ============================================================

def download_single(
    stock
):

    code = stock["id"]

    market = stock["market"]

    symbol = yahoo_symbol(
        code,
        market
    )

    try:

        df = yf.download(

            symbol,

            period="1y",

            interval="1d",

            auto_adjust=False,

            progress=False,

            threads=False,

            timeout=YF_TIMEOUT

        )

        if df is None:
            return None

        if df.empty:
            return None

        #
        # MultiIndex
        #

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            columns = []

            for column in df.columns:

                if isinstance(
                    column,
                    tuple
                ):

                    columns.append(
                        str(
                            column[0]
                        )
                    )

                else:

                    columns.append(
                        str(column)
                    )

            df.columns = columns

        df.columns = [
            str(x)
            .strip()
            .lower()
            for x in df.columns
        ]

        required = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        if not all(
            column in df.columns
            for column in required
        ):

            return None

        for column in required:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        df = df.dropna(
            subset=[
                "close"
            ]
        )

        if len(df) < 35:
            return None

        return df

    except Exception as error:

        print(
            f"{code} fallback 失敗："
            f"{error}"
        )

        return None


# ============================================================
# 分析股票
# ============================================================

def analyze_stock(
    stock,
    df
):

    code = stock["id"]

    name = stock["name"]

    market = stock["market"]

    stock_type = stock["type"]

    try:

        close = df["close"]

        high = df["high"]

        low = df["low"]

        volume = df["volume"]

        #
        # MA
        #

        ma5 = close.rolling(
            5
        ).mean()

        ma20 = close.rolling(
            20
        ).mean()

        ma60 = close.rolling(
            60
        ).mean()

        #
        # RSI
        #

        rsi = calculate_rsi(
            close,
            14
        )

        #
        # KD
        #

        k, d = calculate_kd(
            high,
            low,
            close
        )

        #
        # MACD
        #

        (
            macd,
            macd_signal,
            macd_hist
        ) = calculate_macd(
            close
        )

        #
        # Volume
        #

        volume_ma5 = volume.rolling(
            5
        ).mean()

        #
        # 最新值
        #

        current_price = get_last_value(
            close
        )

        previous_price = (

            safe_float(
                close.iloc[-2]
            )

            if len(close) >= 2

            else None

        )

        current_ma5 = get_last_value(
            ma5
        )

        current_ma20 = get_last_value(
            ma20
        )

        current_ma60 = get_last_value(
            ma60
        )

        current_rsi = get_last_value(
            rsi
        )

        current_k = get_last_value(
            k
        )

        current_d = get_last_value(
            d
        )

        current_macd = get_last_value(
            macd
        )

        current_macd_signal = get_last_value(
            macd_signal
        )

        current_macd_hist = get_last_value(
            macd_hist
        )

        current_volume = get_last_value(
            volume
        )

        current_volume_ma5 = get_last_value(
            volume_ma5
        )

        #
        # RSI 防呆
        #

        if current_rsi is not None:

            current_rsi = max(
                0,
                min(
                    100,
                    current_rsi
                )
            )

        #
        # KD 防呆
        #

        if current_k is not None:

            current_k = max(
                0,
                min(
                    100,
                    current_k
                )
            )

        if current_d is not None:

            current_d = max(
                0,
                min(
                    100,
                    current_d
                )
            )

        #
        # Volume Ratio
        #

        if (

            current_volume is not None

            and

            current_volume_ma5 is not None

            and

            current_volume_ma5 > 0

        ):

            volume_ratio = (

                current_volume /
                current_volume_ma5

            )

        else:

            volume_ratio = None

        #
        # 漲跌
        #

        if (

            current_price is not None

            and

            previous_price is not None

        ):

            change = (

                current_price -
                previous_price

            )

            if previous_price != 0:

                change_percent = (

                    change /
                    previous_price *
                    100

                )

            else:

                change_percent = 0

        else:

            change = None

            change_percent = None

        #
        # 前一日 KD
        #

        previous_k = (

            safe_float(
                k.iloc[-2]
            )

            if len(k) >= 2

            else None

        )

        previous_d = (

            safe_float(
                d.iloc[-2]
            )

            if len(d) >= 2

            else None

        )

        #
        # 前一日 MACD
        #

        previous_macd = (

            safe_float(
                macd.iloc[-2]
            )

            if len(macd) >= 2

            else None

        )

        previous_signal = (

            safe_float(
                macd_signal.iloc[-2]
            )

            if len(macd_signal) >= 2

            else None

        )

        #
        # 前一日 MA20
        #

        previous_ma20 = (

            safe_float(
                ma20.iloc[-2]
            )

            if len(ma20) >= 2

            else None

        )

        #
        # MACD 黃金交叉
        #

        macd_golden_cross = (

            previous_macd is not None

            and

            previous_signal is not None

            and

            current_macd is not None

            and

            current_macd_signal is not None

            and

            previous_macd <= previous_signal

            and

            current_macd > current_macd_signal

        )

        #
        # KD 黃金交叉
        #

        kd_golden_cross = (

            previous_k is not None

            and

            previous_d is not None

            and

            current_k is not None

            and

            current_d is not None

            and

            previous_k <= previous_d

            and

            current_k > current_d

        )

        #
        # RSI > 50
        #

        rsi_above_50 = (

            current_rsi is not None

            and

            current_rsi > 50

        )

        #
        # Volume > 1.5x
        #

        volume_over_1_5x = (

            volume_ratio is not None

            and

            volume_ratio >= 1.5

        )

        #
        # 股價站上 MA20
        #

        above_ma20 = (

            current_price is not None

            and

            current_ma20 is not None

            and

            current_price > current_ma20

        )

        #
        # MA20 向上
        #

        ma20_up = (

            current_ma20 is not None

            and

            previous_ma20 is not None

            and

            current_ma20 > previous_ma20

        )

        #
        # MACD Histogram > 0
        #

        macd_positive = (

            current_macd_hist is not None

            and

            current_macd_hist > 0

        )

        #
        # 核心訊號
        #

        short_term_core = all([

            macd_golden_cross,

            kd_golden_cross,

            rsi_above_50,

            volume_over_1_5x,

            above_ma20,

            ma20_up

        ])

        #
        # AI SCORE
        #
        # MACD 黃金交叉 20
        # KD 黃金交叉 15
        # RSI > 50 15
        # Volume >= 1.5 15
        # MA20 15
        # MA20 UP 10
        # MACD Positive 10
        #
        # 總分 100
        #

        score = 0

        #
        # MACD
        #

        if macd_golden_cross:

            score += 20

        elif macd_positive:

            score += 10

        #
        # KD
        #

        if kd_golden_cross:

            score += 15

        elif (

            current_k is not None

            and

            current_d is not None

            and

            current_k > current_d

        ):

            score += 8

        #
        # RSI
        #

        if rsi_above_50:

            score += 15

        #
        # Volume
        #

        if (

            volume_ratio is not None

            and

            volume_ratio >= 1.5

        ):

            score += 15

        elif (

            volume_ratio is not None

            and

            volume_ratio >= 1

        ):

            score += 8

        #
        # MA20
        #

        if above_ma20:

            score += 15

        #
        # MA20 UP
        #

        if ma20_up:

            score += 10

        #
        # MACD Positive
        #

        if macd_positive:

            score += 10

        #
        # 限制 0~100
        #

        score = max(
            0,
            min(
                100,
                int(score)
            )
        )

        #
        # 訊號
        #

        if short_term_core:

            signal = "強勢核心"

        elif score >= 70:

            signal = "強勢"

        elif score >= 50:

            signal = "偏多"

        elif score >= 30:

            signal = "觀察"

        else:

            signal = "弱勢"

        #
        # DCA
        #

        if current_ma20 is not None:

            buy_1 = current_ma20

            buy_2 = (
                current_ma20 *
                0.97
            )

            buy_3 = (
                current_ma20 *
                0.94
            )

            buy_4 = (
                current_ma20 *
                0.90
            )

        else:

            buy_1 = None
            buy_2 = None
            buy_3 = None
            buy_4 = None

        #
        # DCA Action
        #

        if (

            current_price is not None

            and

            current_ma20 is not None

            and

            current_ma20 != 0

        ):

            distance = (

                current_price /
                current_ma20 -
                1

            ) * 100

            if distance <= -10:

                dca_action = (
                    "第四批區域"
                )

            elif distance <= -6:

                dca_action = (
                    "第三批區域"
                )

            elif distance <= -3:

                dca_action = (
                    "第二批區域"
                )

            elif distance <= 3:

                dca_action = (
                    "第一批區域"
                )

            elif distance <= 8:

                dca_action = (
                    "等待回測"
                )

            else:

                dca_action = (
                    "暫緩追價"
                )

        else:

            dca_action = (
                "資料不足"
            )

        #
        # JSON
        #

        stock_data = {

            "id":
                str(code),

            "name":
                str(name),

            "symbol":
                str(code),

            "type":
                stock_type,

            "market":
                market,

            "price": {

                "close":
                    round_value(
                        current_price,
                        2
                    ),

                "previous_close":
                    round_value(
                        previous_price,
                        2
                    ),

                "change":
                    round_value(
                        change,
                        2
                    ),

                "change_percent":
                    round_value(
                        change_percent,
                        2
                    )

            },

            "technical": {

                "rsi":
                    round_value(
                        current_rsi,
                        2
                    ),

                "k":
                    round_value(
                        current_k,
                        2
                    ),

                "d":
                    round_value(
                        current_d,
                        2
                    ),

                "macd":
                    round_value(
                        current_macd,
                        4
                    ),

                "macd_signal":
                    round_value(
                        current_macd_signal,
                        4
                    ),

                "macd_hist":
                    round_value(
                        current_macd_hist,
                        4
                    ),

                "ma5":
                    round_value(
                        current_ma5,
                        2
                    ),

                "ma20":
                    round_value(
                        current_ma20,
                        2
                    ),

                "ma60":
                    round_value(
                        current_ma60,
                        2
                    ),

                "volume":
                    round_value(
                        current_volume,
                        0
                    ),

                "volume_ma5":
                    round_value(
                        current_volume_ma5,
                        0
                    ),

                "volume_ratio":
                    round_value(
                        volume_ratio,
                        2
                    )

            },

            "conditions": {

                "macd_golden_cross":
                    bool(
                        macd_golden_cross
                    ),

                "kd_golden_cross":
                    bool(
                        kd_golden_cross
                    ),

                "rsi_above_50":
                    bool(
                        rsi_above_50
                    ),

                "volume_over_1_5x":
                    bool(
                        volume_over_1_5x
                    ),

                "above_ma20":
                    bool(
                        above_ma20
                    ),

                "ma20_up":
                    bool(
                        ma20_up
                    ),

                "short_term_core":
                    bool(
                        short_term_core
                    )

            },

            "short_term": {

                "score":
                    int(score),

                "signal":
                    signal

            },

            "dca": {

                "buy_1":
                    round_value(
                        buy_1,
                        2
                    ),

                "buy_2":
                    round_value(
                        buy_2,
                        2
                    ),

                "buy_3":
                    round_value(
                        buy_3,
                        2
                    ),

                "buy_4":
                    round_value(
                        buy_4,
                        2
                    ),

                "action":
                    dca_action

            }

        }

        return stock_data

    except Exception as error:

        print(
            f"{code}: 分析失敗："
            f"{error}"
        )

        traceback.print_exc()

        return None


# ============================================================
# 驗證股票資料
# ============================================================

def validate_stock(
    stock
):

    if not stock:
        return False

    stock_id = stock.get(
        "id"
    )

    if not stock_id:
        return False

    price = stock.get(
        "price",
        {}
    )

    close = safe_float(
        price.get(
            "close"
        )
    )

    if close is None:
        return False

    technical = stock.get(
        "technical",
        {}
    )

    #
    # RSI
    #

    rsi = technical.get(
        "rsi"
    )

    if rsi is not None:

        rsi = safe_float(
            rsi
        )

        if (

            rsi is None

            or

            rsi < 0

            or

            rsi > 100

        ):

            print(
                f"{stock_id}: "
                f"RSI 異常 {rsi}"
            )

            return False

    #
    # KD
    #

    for field in [
        "k",
        "d"
    ]:

        value = technical.get(
            field
        )

        if value is not None:

            value = safe_float(
                value
            )

            if (

                value is None

                or

                value < 0

                or

                value > 100

            ):

                print(
                    f"{stock_id}: "
                    f"{field} 異常 "
                    f"{value}"
                )

                return False

    return True


# ============================================================
# 排名
# ============================================================

def build_rankings(
    stocks
):

    #
    # 短線
    #

    ranking_data = sorted(

        stocks,

        key=lambda stock: (

            safe_float(

                stock.get(
                    "short_term",
                    {}
                ).get(
                    "score"
                ),

                0

            ),

            safe_float(

                stock.get(
                    "technical",
                    {}
                ).get(
                    "volume_ratio"
                ),

                0

            )

        ),

        reverse=True

    )

    short_term = [

        str(
            stock["id"]
        )

        for stock
        in ranking_data

    ]

    #
    # 核心
    #

    core_stocks = [

        stock

        for stock
        in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )

    ]

    core_stocks = sorted(

        core_stocks,

        key=lambda stock:

            safe_float(

                stock.get(
                    "short_term",
                    {}
                ).get(
                    "score"
                ),

                0

            ),

        reverse=True

    )

    #
    # DCA
    #

    dca_stocks = [

        stock

        for stock
        in stocks

        if stock.get(
            "technical",
            {}
        ).get(
            "ma20"
        ) is not None

    ]

    def dca_score(
        stock
    ):

        score = safe_float(

            stock.get(
                "short_term",
                {}
            ).get(
                "score"
            ),

            0

        )

        price = safe_float(

            stock.get(
                "price",
                {}
            ).get(
                "close"
            ),

            0

        )

        ma20 = safe_float(

            stock.get(
                "technical",
                {}
            ).get(
                "ma20"
            ),

            0

        )

        if price and ma20:

            distance = abs(

                price /
                ma20 -
                1

            )

        else:

            distance = 999

        return (
            score,
            -distance
        )

    dca_stocks = sorted(

        dca_stocks,

        key=dca_score,

        reverse=True

    )

    dca = [

        str(
            stock["id"]
        )

        for stock
        in dca_stocks

    ]

    return {

        "short_term":
            short_term,

        "core":
            [

                str(
                    stock["id"]
                )

                for stock
                in core_stocks

            ],

        "dca":
            dca

    }


# ============================================================
# 統計
# ============================================================

def build_statistics(
    stocks
):

    total_stocks = len(
        stocks
    )

    core_stocks = sum(

        1

        for stock
        in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )

    )

    macd_golden = sum(

        1

        for stock
        in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "macd_golden_cross",
            False
        )

    )

    rsi_above_50 = sum(

        1

        for stock
        in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "rsi_above_50",
            False
        )

    )

    kd_golden = sum(

        1

        for stock
        in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "kd_golden_cross",
            False
        )

    )

    volume_over_1_5x = sum(

        1

        for stock
        in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "volume_over_1_5x",
            False
        )

    )

    above_ma20 = sum(

        1

        for stock
        in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "above_ma20",
            False
        )

    )

    ma20_up = sum(

        1

        for stock
        in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "ma20_up",
            False
        )

    )

    #
    # 市場分類
    #

    twse_count = sum(

        1

        for stock
        in stocks

        if stock.get(
            "market"
        ) == "TWSE"

    )

    tpex_count = sum(

        1

        for stock
        in stocks

        if stock.get(
            "market"
        ) == "TPEx"

    )

    etf_count = sum(

        1

        for stock
        in stocks

        if stock.get(
            "type"
        ) == "ETF"

    )

    return {

        "total_stocks":
            total_stocks,

        "core_stocks":
            core_stocks,

        "macd_golden":
            macd_golden,

        "rsi_above_50":
            rsi_above_50,

        "kd_golden":
            kd_golden,

        "volume_over_1_5x":
            volume_over_1_5x,

        "above_ma20":
            above_ma20,

        "ma20_up":
            ma20_up,

        "twse_stocks":
            twse_count,

        "tpex_stocks":
            tpex_count,

        "etf_stocks":
            etf_count

    }


# ============================================================
# 儲存 JSON
# ============================================================

def save_json(
    data
):

    try:

        #
        # 先寫入暫存檔
        #
        # 避免 GitHub Actions 中途失敗
        # 造成 prices.json 被寫成半份。
        #

        temp_file = (
            OUTPUT_FILE +
            ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(

                data,

                file,

                ensure_ascii=False,

                indent=2

            )

        os.replace(
            temp_file,
            OUTPUT_FILE
        )

        print("")

        print(
            f"資料已寫入："
            f"{OUTPUT_FILE}"
        )

        return True

    except Exception as error:

        print(
            f"JSON 寫入失敗："
            f"{error}"
        )

        return False


# ============================================================
# 主程式
# ============================================================

def main():

    start_time = time.time()

    now = datetime.now(
        TW_TZ
    )

    print("")
    print(
        "================================================"
    )

    print(
        "台股 AI 選股系統"
    )

    print(
        f"fetch_data.py {VERSION}"
    )

    print(
        "全市場高速掃描正式版"
    )

    print(
        f"開始時間："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        "================================================"
    )

    #
    # ========================================================
    # 1. 建立全市場清單
    # ========================================================
    #

    market_stocks = (
        build_market_list()
    )

    if not market_stocks:

        print(
            ""
        )

        print(
            "錯誤："
            "無法建立市場清單。"
        )

        sys.exit(1)

    #
    # ========================================================
    # 2. 批次下載
    # ========================================================
    #

    all_data = {}

    failed_download = []

    total = len(
        market_stocks
    )

    batch_count = (
        math.ceil(
            total /
            BATCH_SIZE
        )
    )

    print("")
    print(
        "################################################"
    )

    print(
        f"開始 Yahoo Finance 批次下載"
    )

    print(
        f"市場清單：{total} 檔"
    )

    print(
        f"批次大小：{BATCH_SIZE}"
    )

    print(
        f"預計批次：{batch_count}"
    )

    print(
        "################################################"
    )

    for batch_index in range(
        0,
        total,
        BATCH_SIZE
    ):

        batch = market_stocks[
            batch_index:
            batch_index + BATCH_SIZE
        ]

        current_batch_number = (
            batch_index //
            BATCH_SIZE
            + 1
        )

        print("")
        print(
            f"[批次 "
            f"{current_batch_number}/"
            f"{batch_count}]"
        )

        result = download_batch(
            batch
        )

        #
        # 儲存批次成功結果
        #

        all_data.update(
            result
        )

        #
        # 找出本批次沒有成功的股票
        #

        for stock in batch:

            symbol = yahoo_symbol(
                stock["id"],
                stock["market"]
            )

            if symbol not in result:

                failed_download.append(
                    stock
                )

    #
    # ========================================================
    # 3. Fallback
    # ========================================================
    #
    # 只有批次抓不到的股票才逐檔補抓。
    #

    if failed_download:

        print("")
        print(
            "################################################"
        )

        print(
            f"批次未取得："
            f"{len(failed_download)} 檔"
        )

        print(
            "啟動單檔 fallback 補抓..."
        )

        print(
            "################################################"
        )

        fallback_success = 0

        remaining_failed = []

        for index, stock in enumerate(
            failed_download,
            start=1
        ):

            print(
                f"Fallback "
                f"{index}/"
                f"{len(failed_download)}："
                f"{stock['id']} "
                f"{stock['name']}"
            )

            df = download_single(
                stock
            )

            if df is not None:

                symbol = yahoo_symbol(
                    stock["id"],
                    stock["market"]
                )

                all_data[
                    symbol
                ] = df

                fallback_success += 1

            else:

                remaining_failed.append(
                    stock
                )

        failed_download = (
            remaining_failed
        )

        print(
            f"Fallback 成功："
            f"{fallback_success} 檔"
        )

    #
    # ========================================================
    # 4. 分析
    # ========================================================
    #

    print("")
    print(
        "################################################"
    )

    print(
        "開始計算技術指標與 AI SCORE"
    )

    print(
        "################################################"
    )

    stocks = []

    failed_analysis = []

    for index, stock in enumerate(
        market_stocks,
        start=1
    ):

        symbol = yahoo_symbol(
            stock["id"],
            stock["market"]
        )

        df = all_data.get(
            symbol
        )

        if df is None:

            failed_analysis.append(
                stock
            )

            continue

        analyzed = analyze_stock(
            stock,
            df
        )

        if analyzed is None:

            failed_analysis.append(
                stock
            )

            continue

        if not validate_stock(
            analyzed
        ):

            failed_analysis.append(
                stock
            )

            continue

        stocks.append(
            analyzed
        )

        #
        # 不要每一檔都 print
        #
        # 每 50 檔顯示一次進度。
        #

        if (

            index == 1

            or

            index % 50 == 0

            or

            index == total

        ):

            print(
                f"分析進度："
                f"{index}/{total}"
                f" | 成功："
                f"{len(stocks)}"
            )

    #
    # ========================================================
    # 5. 完全沒有資料
    # ========================================================
    #

    if not stocks:

        print("")
        print(
            "錯誤："
            "沒有任何股票成功分析。"
        )

        sys.exit(1)

    #
    # ========================================================
    # 6. 排名
    # ========================================================
    #

    rankings = build_rankings(
        stocks
    )

    #
    # ========================================================
    # 7. 統計
    # ========================================================
    #

    statistics = build_statistics(
        stocks
    )

    #
    # ========================================================
    # 8. 最終 JSON
    # ========================================================
    #

    output = {

        "version":
            VERSION,

        "updated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "market":
            "TW",

        "source":
            "Yahoo Finance",

        "stocks":
            stocks,

        "rankings":
            rankings,

        "statistics":
            statistics

    }

    #
    # ========================================================
    # 9. 儲存
    # ========================================================
    #

    success = save_json(
        output
    )

    if not success:

        sys.exit(1)

    #
    # ========================================================
    # 10. 最終統計
    # ========================================================
    #

    elapsed = (
        time.time() -
        start_time
    )

    success_count = len(
        stocks
    )

    failed_count = (
        len(
            market_stocks
        )
        -
        success_count
    )

    print("")
    print(
        "================================================"
    )

    print(
        "全市場 AI 掃描完成"
    )

    print(
        "================================================"
    )

    print(
        f"市場清單："
        f"{len(market_stocks)} 檔"
    )

    print(
        f"成功分析："
        f"{success_count} 檔"
    )

    print(
        f"失敗："
        f"{failed_count} 檔"
    )

    print("")
    print(
        f"上市股票："
        f"{statistics['twse_stocks']} 檔"
    )

    print(
        f"上櫃股票："
        f"{statistics['tpex_stocks']} 檔"
    )

    print(
        f"ETF："
        f"{statistics['etf_stocks']} 檔"
    )

    print("")
    print(
        f"AI ≥ 70："
        f"{sum("
        "1 for stock in stocks "
        "if stock.get('short_term', {})"
        ".get('score', 0) >= 70"
        ")} 檔"
    )

    print(
        f"核心訊號："
        f"{statistics['core_stocks']} 檔"
    )

    print(
        f"MACD 黃金交叉："
        f"{statistics['macd_golden']} 檔"
    )

    print(
        f"RSI > 50："
        f"{statistics['rsi_above_50']} 檔"
    )

    print(
        f"KD 黃金交叉："
        f"{statistics['kd_golden']} 檔"
    )

    print(
        f"成交量 > 1.5x："
        f"{statistics['volume_over_1_5x']} 檔"
    )

    print(
        f"站上 MA20："
        f"{statistics['above_ma20']} 檔"
    )

    print(
        f"MA20 向上："
        f"{statistics['ma20_up']} 檔"
    )

    print("")
    print(
        f"耗時："
        f"{elapsed:.2f} 秒"
    )

    print(
        "================================================"
    )

    #
    # 額外提示
    #

    if failed_count > 0:

        print("")
        print(
            "注意："
            f"有 {failed_count} 檔"
            "沒有成功取得完整資料。"
        )

        print(
            "這些股票不會寫入 stocks。"
        )

    print("")
    print(
        "V7.1 完成。"
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    main()
