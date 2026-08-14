# ============================================================
# 台股 AI 選股・全市場掃描・零股定投・動態風控
# fetch_data.py V7.1 正式修正版
#
# V7.1
#
# 核心：
# 1. 自動取得台股市場清單
# 2. 上市股票
# 3. 上櫃股票
# 4. ETF
# 5. 不再內建固定 12 檔股票
# 6. Yahoo Finance 批次下載
# 7. 批次失敗自動個別補抓
# 8. RSI / KD / MACD
# 9. MA5 / MA20 / MA60
# 10. 成交量比
# 11. AI SCORE
# 12. 核心訊號
# 13. 四段式 DCA
# 14. 全市場排名
# 15. 統計資料
# 16. 與 index.html V6.2 JSON 結構相容
# 17. RSI 強制 0~100
# 18. KD 強制 0~100
# 19. 單一股票失敗不影響整體
# 20. 避免 f-string 巢狀引號 SyntaxError
# ============================================================

import os
import sys
import json
import math
import time
import traceback
import re

from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf


# ============================================================
# 版本
# ============================================================

VERSION = "V7.1"


# ============================================================
# 基本路徑
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
# API 設定
# ============================================================

TWSE_LIST_URL = (
    "https://openapi.twse.com.tw/v1/"
    "opendata/t187ap03_L"
)

TPEx_LIST_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_peratio_analysis"
)


# ============================================================
# Yahoo 設定
# ============================================================

YF_PERIOD = "1y"
YF_INTERVAL = "1d"

BATCH_SIZE = 100

REQUEST_TIMEOUT = 30

BATCH_PAUSE = 0.5


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
            "Chrome/131.0 Safari/537.36"
        )
    }
)


# ============================================================
# 安全 float
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

    number = safe_float(value)

    if number is None:
        return None

    return round(
        number,
        digits
    )


# ============================================================
# 清理代號
# ============================================================

def clean_code(value):

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = text.replace(
        ".0",
        ""
    )

    match = re.search(
        r"\d{4,6}",
        text
    )

    if not match:
        return None

    code = match.group(0)

    return code


# ============================================================
# 清理名稱
# ============================================================

def clean_name(value):

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    return text


# ============================================================
# 找欄位
# ============================================================

def find_column(
    columns,
    candidates
):

    normalized = {}

    for column in columns:

        key = str(column).strip()

        normalized[key] = column

    for candidate in candidates:

        if candidate in normalized:

            return normalized[candidate]

    for column in columns:

        text = str(column).strip()

        for candidate in candidates:

            if candidate in text:

                return column

    return None


# ============================================================
# 解析 TWSE 清單
# ============================================================

def parse_twse_list(
    data
):

    result = []

    if not isinstance(
        data,
        list
    ):
        return result

    for item in data:

        if not isinstance(
            item,
            dict
        ):
            continue

        code = None
        name = None

        code_candidates = [
            "公司代號",
            "股票代號",
            "證券代號",
            "Code",
            "code"
        ]

        name_candidates = [
            "公司簡稱",
            "公司名稱",
            "股票名稱",
            "證券名稱",
            "名稱",
            "Name",
            "name"
        ]

        for key in code_candidates:

            if key in item:

                code = clean_code(
                    item.get(key)
                )

                if code:
                    break

        for key in name_candidates:

            if key in item:

                name = clean_name(
                    item.get(key)
                )

                if name:
                    break

        if not code:
            continue

        if not name:
            name = code

        # 只保留一般股票與 ETF / 指數型商品
        if len(code) < 4:
            continue

        result.append(
            (
                code,
                name,
                "TWSE"
            )
        )

    return result


# ============================================================
# 取得 TWSE 股票清單
# ============================================================

def fetch_twse_list():

    print(
        "取得 TWSE 上市市場清單..."
    )

    urls = [
        TWSE_LIST_URL,
        TWSE_LIST_URL
        + "?TYPEK=%E6%AD%A3%E8%82%A1%2CETF"
    ]

    for url in urls:

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            result = parse_twse_list(
                data
            )

            if result:

                print(
                    "TWSE 清單取得成功："
                    + str(len(result))
                    + " 檔"
                )

                return result

        except Exception as error:

            print(
                "TWSE 清單取得失敗："
                + str(error)
            )

    return []


# ============================================================
# 解析 TPEx 清單
# ============================================================

def parse_tpex_list(
    data
):

    result = []

    if not isinstance(
        data,
        list
    ):
        return result

    for item in data:

        if not isinstance(
            item,
            dict
        ):
            continue

        code = None
        name = None

        code_candidates = [
            "SecuritiesCompanyCode",
            "SecuritiesCode",
            "Code",
            "code",
            "股票代號",
            "證券代號",
            "代號"
        ]

        name_candidates = [
            "CompanyName",
            "SecuritiesCompanyName",
            "Name",
            "name",
            "公司簡稱",
            "股票名稱",
            "證券名稱",
            "名稱"
        ]

        for key in code_candidates:

            if key in item:

                code = clean_code(
                    item.get(key)
                )

                if code:
                    break

        for key in name_candidates:

            if key in item:

                name = clean_name(
                    item.get(key)
                )

                if name:
                    break

        if not code:
            continue

        if not name:
            name = code

        if len(code) < 4:
            continue

        result.append(
            (
                code,
                name,
                "TPEX"
            )
        )

    return result


# ============================================================
# 取得 TPEx 上櫃清單
#
# 第一來源：
# TPEx 官方 OpenAPI
#
# 第二來源：
# TaiwanStockInfo
# ============================================================

def fetch_tpex_list():

    print(
        "取得 TPEx 上櫃市場清單..."
    )

    urls = [
        (
            "https://www.tpex.org.tw/openapi/v1/"
            "tpex_mainboard_peratio_analysis"
        ),
        (
            "https://www.tpex.org.tw/openapi/v1/"
            "tpex_mainboard_quotes"
        ),
        (
            "https://www.tpex.org.tw/openapi/v1/"
            "tpex_mainboard_daily_close_quotes"
        ),
    ]

    for url in urls:

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            result = parse_tpex_list(
                data
            )

            if result:

                print(
                    "TPEx 清單取得成功："
                    + str(len(result))
                    + " 檔"
                )

                return result

        except Exception as error:

            print(
                "TPEx API 失敗："
                + str(error)
            )

    # --------------------------------------------------------
    # fallback
    # --------------------------------------------------------

    print(
        "TPEx 官方 API 未取得清單，"
        "嘗試 TaiwanStockInfo..."
    )

    fallback_url = (
        "https://api.finmindtrade.com/v4/data"
        "?dataset=TaiwanStockInfo"
    )

    try:

        response = SESSION.get(
            fallback_url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        payload = response.json()

        data = payload.get(
            "data",
            []
        )

        result = []

        for item in data:

            code = clean_code(
                item.get("stock_id")
            )

            name = clean_name(
                item.get("stock_name")
            )

            if not code:
                continue

            if not name:
                name = code

            result.append(
                (
                    code,
                    name,
                    "TW"
                )
            )

        if result:

            print(
                "Fallback 市場清單："
                + str(len(result))
                + " 檔"
            )

            return result

    except Exception as error:

        print(
            "Fallback 失敗："
            + str(error)
        )

    return []


# ============================================================
# ETF / 指數型商品判斷
# ============================================================

def is_etf_code(
    code
):

    code = str(code)

    prefixes = (
        "00",
        "01",
        "02",
        "03",
        "04",
        "05",
        "06",
        "07",
        "08",
        "09"
    )

    return code.startswith(
        prefixes
    )


# ============================================================
# 合併市場清單
# ============================================================

def build_market_list():

    twse = fetch_twse_list()

    tpex = fetch_tpex_list()

    combined = []

    seen = set()

    for code, name, market in (
        twse + tpex
    ):

        code = clean_code(
            code
        )

        name = clean_name(
            name
        )

        if not code:
            continue

        if code in seen:
            continue

        # 排除明顯不是台股代號的項目
        if len(code) < 4:
            continue

        seen.add(
            code
        )

        if market == "TWSE":

            market_name = "上市"

        elif market == "TPEX":

            market_name = "上櫃"

        else:

            market_name = "台股"

        if is_etf_code(code):

            stock_type = "ETF"

        else:

            stock_type = "STOCK"

        combined.append(
            {
                "id": code,
                "name": name,
                "market": market_name,
                "type": stock_type
            }
        )

    combined.sort(
        key=lambda x: x["id"]
    )

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "全市場清單統計"
    )

    print(
        "================================================"
    )

    listed = sum(
        1
        for item in combined
        if item["market"] == "上市"
        and item["type"] != "ETF"
    )

    otc = sum(
        1
        for item in combined
        if item["market"] == "上櫃"
        and item["type"] != "ETF"
    )

    etf = sum(
        1
        for item in combined
        if item["type"] == "ETF"
    )

    print(
        "市場清單："
        + str(len(combined))
        + " 檔"
    )

    print(
        "上市股票："
        + str(listed)
        + " 檔"
    )

    print(
        "上櫃股票："
        + str(otc)
        + " 檔"
    )

    print(
        "ETF："
        + str(etf)
        + " 檔"
    )

    print(
        "================================================"
    )

    return combined


# ============================================================
# Yahoo Symbol
# ============================================================

def yahoo_symbol(
    code
):

    return (
        str(code).strip()
        + ".TW"
    )


# ============================================================
# 批次 Yahoo Symbols
# ============================================================

def make_yahoo_symbols(
    market_list
):

    return [
        yahoo_symbol(
            item["id"]
        )
        for item in market_list
    ]


# ============================================================
# 修正 Yahoo MultiIndex
# ============================================================

def normalize_yahoo_dataframe(
    df
):

    if df is None:
        return None

    if df.empty:
        return None

    try:

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            # ----------------------------------------------
            # 單一 ticker
            # ----------------------------------------------

            if len(
                df.columns.levels
            ) >= 2:

                first_level = [
                    str(x).lower()
                    for x in df.columns.get_level_values(0)
                ]

                expected = {
                    "open",
                    "high",
                    "low",
                    "close",
                    "adj close",
                    "volume"
                }

                if any(
                    x in expected
                    for x in first_level
                ):

                    df.columns = (
                        df.columns
                        .get_level_values(0)
                    )

                else:

                    df.columns = (
                        df.columns
                        .get_level_values(1)
                    )

        df.columns = [
            str(column)
            .strip()
            .lower()
            for column in df.columns
        ]

        return df

    except Exception:

        return df


# ============================================================
# 取得單檔歷史資料
# ============================================================

def download_single_stock(
    code
):

    symbol = yahoo_symbol(
        code
    )

    try:

        df = yf.download(
            symbol,
            period=YF_PERIOD,
            interval=YF_INTERVAL,
            auto_adjust=False,
            progress=False,
            threads=False
        )

        df = normalize_yahoo_dataframe(
            df
        )

        if df is None:
            return None

        required = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for column in required:

            if column not in df.columns:

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

    except Exception:

        return None


# ============================================================
# 從批次 DataFrame 取單檔
# ============================================================

def extract_symbol_dataframe(
    batch_df,
    symbol
):

    if batch_df is None:
        return None

    if batch_df.empty:
        return None

    try:

        if isinstance(
            batch_df.columns,
            pd.MultiIndex
        ):

            # Yahoo 批次格式通常：
            #
            # level 0 = Price
            # level 1 = Ticker
            #
            # 或相反。
            #
            # 自動判斷。

            level0 = [
                str(x)
                for x in batch_df.columns
                .get_level_values(0)
            ]

            level1 = [
                str(x)
                for x in batch_df.columns
                .get_level_values(1)
            ]

            if symbol in level1:

                df = batch_df.xs(
                    symbol,
                    axis=1,
                    level=1,
                    drop_level=True
                )

            elif symbol in level0:

                df = batch_df.xs(
                    symbol,
                    axis=1,
                    level=0,
                    drop_level=True
                )

            else:

                return None

        else:

            df = batch_df.copy()

        df = normalize_yahoo_dataframe(
            df
        )

        if df is None:
            return None

        required = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for column in required:

            if column not in df.columns:

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

    except Exception:

        return None


# ============================================================
# 批次下載
# ============================================================

def download_market_data(
    market_list
):

    data_map = {}

    failed_codes = []

    symbols = make_yahoo_symbols(
        market_list
    )

    code_by_symbol = {
        yahoo_symbol(
            item["id"]
        ): item["id"]
        for item in market_list
    }

    total = len(symbols)

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "開始 Yahoo Finance 批次下載"
    )

    print(
        "總數："
        + str(total)
        + " 檔"
    )

    print(
        "批次大小："
        + str(BATCH_SIZE)
    )

    print(
        "================================================"
    )

    for start in range(
        0,
        total,
        BATCH_SIZE
    ):

        batch_symbols = symbols[
            start:
            start + BATCH_SIZE
        ]

        end = min(
            start + BATCH_SIZE,
            total
        )

        print(
            "下載批次 "
            + str(start + 1)
            + "-"
            + str(end)
            + " / "
            + str(total)
        )

        try:

            batch_df = yf.download(
                tickers=batch_symbols,
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=False,
                progress=False,
                group_by="column",
                threads=True
            )

            if (
                batch_df is not None
                and not batch_df.empty
            ):

                for symbol in batch_symbols:

                    code = code_by_symbol.get(
                        symbol
                    )

                    if not code:
                        continue

                    df = extract_symbol_dataframe(
                        batch_df,
                        symbol
                    )

                    if df is not None:

                        data_map[code] = df

                    else:

                        failed_codes.append(
                            code
                        )

            else:

                for symbol in batch_symbols:

                    code = code_by_symbol.get(
                        symbol
                    )

                    if code:
                        failed_codes.append(
                            code
                        )

        except Exception as error:

            print(
                "批次下載失敗："
                + str(error)
            )

            for symbol in batch_symbols:

                code = code_by_symbol.get(
                    symbol
                )

                if code:
                    failed_codes.append(
                        code
                    )

        time.sleep(
            BATCH_PAUSE
        )

    # --------------------------------------------------------
    # 個別補抓
    # --------------------------------------------------------

    failed_codes = list(
        dict.fromkeys(
            failed_codes
        )
    )

    if failed_codes:

        print(
            ""
        )

        print(
            "開始個別補抓："
            + str(len(failed_codes))
            + " 檔"
        )

    recovered = 0

    for index, code in enumerate(
        failed_codes,
        start=1
    ):

        if code in data_map:
            continue

        df = download_single_stock(
            code
        )

        if df is not None:

            data_map[code] = df

            recovered += 1

        if index % 20 == 0:

            print(
                "補抓進度："
                + str(index)
                + " / "
                + str(len(failed_codes))
            )

    print(
        ""
    )

    print(
        "Yahoo 資料下載完成"
    )

    print(
        "成功："
        + str(len(data_map))
        + " 檔"
    )

    print(
        "補抓成功："
        + str(recovered)
        + " 檔"
    )

    return data_map


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

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100 -
        100 / (1 + rs)
    )

    # 完全沒有下跌
    rsi = rsi.where(
        avg_loss != 0,
        100
    )

    # 強制 0~100
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
        (close - lowest_low)
        / denominator
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

    return k, d


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

    signal_line = macd.ewm(
        span=signal,
        adjust=False
    ).mean()

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

        values = pd.to_numeric(
            series,
            errors="coerce"
        ).dropna()

        if len(values) == 0:
            return None

        return float(
            values.iloc[-1]
        )

    except Exception:

        return None


# ============================================================
# 分析單檔股票
# ============================================================

def analyze_stock(
    item,
    df
):

    code = item["id"]
    name = item["name"]
    market = item["market"]
    stock_type = item["type"]

    try:

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
        )

        valid = pd.DataFrame(
            {
                "close": close,
                "high": high,
                "low": low,
                "volume": volume
            }
        ).dropna(
            subset=[
                "close"
            ]
        )

        if len(valid) < 35:

            return None

        close = valid["close"]
        high = valid["high"]
        low = valid["low"]
        volume = valid["volume"]

        # ----------------------------------------------------
        # MA
        # ----------------------------------------------------

        ma5 = close.rolling(
            5
        ).mean()

        ma20 = close.rolling(
            20
        ).mean()

        ma60 = close.rolling(
            60
        ).mean()

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        rsi = calculate_rsi(
            close,
            14
        )

        # ----------------------------------------------------
        # KD
        # ----------------------------------------------------

        k, d = calculate_kd(
            high,
            low,
            close
        )

        # ----------------------------------------------------
        # MACD
        # ----------------------------------------------------

        (
            macd,
            macd_signal,
            macd_hist
        ) = calculate_macd(
            close
        )

        # ----------------------------------------------------
        # Volume
        # ----------------------------------------------------

        volume_ma5 = volume.rolling(
            5
        ).mean()

        # ----------------------------------------------------
        # Latest
        # ----------------------------------------------------

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

        current_macd_signal = (
            get_last_value(
                macd_signal
            )
        )

        current_macd_hist = (
            get_last_value(
                macd_hist
            )
        )

        current_volume = get_last_value(
            volume
        )

        current_volume_ma5 = (
            get_last_value(
                volume_ma5
            )
        )

        # ----------------------------------------------------
        # 前一天
        # ----------------------------------------------------

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

        previous_macd = (
            safe_float(
                macd.iloc[-2]
            )
            if len(macd) >= 2
            else None
        )

        previous_macd_signal = (
            safe_float(
                macd_signal.iloc[-2]
            )
            if len(macd_signal) >= 2
            else None
        )

        previous_ma20 = (
            safe_float(
                ma20.iloc[-2]
            )
            if len(ma20) >= 2
            else None
        )

        # ----------------------------------------------------
        # RSI 防呆
        # ----------------------------------------------------

        if current_rsi is not None:

            current_rsi = max(
                0.0,
                min(
                    100.0,
                    current_rsi
                )
            )

        # ----------------------------------------------------
        # KD 防呆
        # ----------------------------------------------------

        if current_k is not None:

            current_k = max(
                0.0,
                min(
                    100.0,
                    current_k
                )
            )

        if current_d is not None:

            current_d = max(
                0.0,
                min(
                    100.0,
                    current_d
                )
            )

        # ----------------------------------------------------
        # Volume Ratio
        # ----------------------------------------------------

        if (
            current_volume is not None
            and current_volume_ma5 is not None
            and current_volume_ma5 > 0
        ):

            volume_ratio = (
                current_volume
                / current_volume_ma5
            )

        else:

            volume_ratio = None

        # ----------------------------------------------------
        # Change
        # ----------------------------------------------------

        if (
            current_price is not None
            and previous_price is not None
            and previous_price != 0
        ):

            change = (
                current_price
                - previous_price
            )

            change_percent = (
                change
                / previous_price
                * 100
            )

        else:

            change = None
            change_percent = None

        # ====================================================
        # 條件
        # ====================================================

        macd_golden_cross = False

        if (
            previous_macd is not None
            and previous_macd_signal is not None
            and current_macd is not None
            and current_macd_signal is not None
        ):

            macd_golden_cross = (
                previous_macd
                <= previous_macd_signal
                and
                current_macd
                > current_macd_signal
            )

        kd_golden_cross = False

        if (
            previous_k is not None
            and previous_d is not None
            and current_k is not None
            and current_d is not None
        ):

            kd_golden_cross = (
                previous_k <= previous_d
                and
                current_k > current_d
            )

        rsi_above_50 = (
            current_rsi is not None
            and current_rsi > 50
        )

        volume_over_1_5x = (
            volume_ratio is not None
            and volume_ratio >= 1.5
        )

        above_ma20 = (
            current_price is not None
            and current_ma20 is not None
            and current_price > current_ma20
        )

        ma20_up = (
            current_ma20 is not None
            and previous_ma20 is not None
            and current_ma20 > previous_ma20
        )

        macd_positive = (
            current_macd_hist is not None
            and current_macd_hist > 0
        )

        kd_above = (
            current_k is not None
            and current_d is not None
            and current_k > current_d
        )

        # ====================================================
        # 核心訊號
        # ====================================================

        short_term_core = all(
            [
                macd_golden_cross,
                kd_golden_cross,
                rsi_above_50,
                volume_over_1_5x,
                above_ma20,
                ma20_up
            ]
        )

        # ====================================================
        # AI SCORE
        #
        # MACD 黃金交叉       20
        # KD 黃金交叉         15
        # RSI > 50            15
        # Volume >= 1.5       15
        # MA20                15
        # MA20 UP             10
        # MACD 正值           10
        #
        # 總分 100
        # ====================================================

        score = 0

        if macd_golden_cross:

            score += 20

        elif macd_positive:

            score += 10

        if kd_golden_cross:

            score += 15

        elif kd_above:

            score += 8

        if rsi_above_50:

            score += 15

        if (
            volume_ratio is not None
            and volume_ratio >= 1.5
        ):

            score += 15

        elif (
            volume_ratio is not None
            and volume_ratio >= 1
        ):

            score += 8

        if above_ma20:

            score += 15

        if ma20_up:

            score += 10

        if macd_positive:

            score += 10

        score = int(
            max(
                0,
                min(
                    100,
                    score
                )
            )
        )

        # ====================================================
        # 訊號
        # ====================================================

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

        # ====================================================
        # DCA
        # ====================================================

        if current_ma20 is not None:

            buy_1 = current_ma20
            buy_2 = current_ma20 * 0.97
            buy_3 = current_ma20 * 0.94
            buy_4 = current_ma20 * 0.90

        else:

            buy_1 = None
            buy_2 = None
            buy_3 = None
            buy_4 = None

        if (
            current_price is not None
            and current_ma20 is not None
            and current_ma20 != 0
        ):

            distance = (
                current_price
                / current_ma20
                - 1
            ) * 100

            if distance <= -10:

                dca_action = "第四批區域"

            elif distance <= -6:

                dca_action = "第三批區域"

            elif distance <= -3:

                dca_action = "第二批區域"

            elif distance <= 3:

                dca_action = "第一批區域"

            elif distance <= 8:

                dca_action = "等待回測"

            else:

                dca_action = "暫緩追價"

        else:

            dca_action = "資料不足"

        # ====================================================
        # JSON
        # ====================================================

        stock = {

            "id": str(code),

            "name": str(name),

            "symbol": str(code),

            "market": str(market),

            "type": str(stock_type),

            "price": {

                "close": round_value(
                    current_price,
                    2
                ),

                "previous_close": round_value(
                    previous_price,
                    2
                ),

                "change": round_value(
                    change,
                    2
                ),

                "change_percent": round_value(
                    change_percent,
                    2
                )

            },

            "technical": {

                "rsi": round_value(
                    current_rsi,
                    2
                ),

                "k": round_value(
                    current_k,
                    2
                ),

                "d": round_value(
                    current_d,
                    2
                ),

                "macd": round_value(
                    current_macd,
                    4
                ),

                "macd_signal": round_value(
                    current_macd_signal,
                    4
                ),

                "macd_hist": round_value(
                    current_macd_hist,
                    4
                ),

                "ma5": round_value(
                    current_ma5,
                    2
                ),

                "ma20": round_value(
                    current_ma20,
                    2
                ),

                "ma60": round_value(
                    current_ma60,
                    2
                ),

                "volume": round_value(
                    current_volume,
                    0
                ),

                "volume_ma5": round_value(
                    current_volume_ma5,
                    0
                ),

                "volume_ratio": round_value(
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

                "score": int(
                    score
                ),

                "signal": signal

            },

            "dca": {

                "buy_1": round_value(
                    buy_1,
                    2
                ),

                "buy_2": round_value(
                    buy_2,
                    2
                ),

                "buy_3": round_value(
                    buy_3,
                    2
                ),

                "buy_4": round_value(
                    buy_4,
                    2
                ),

                "action": dca_action

            }

        }

        return stock

    except Exception as error:

        print(
            "分析失敗 "
            + str(code)
            + "："
            + str(error)
        )

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

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = safe_float(
        technical.get(
            "rsi"
        )
    )

    if rsi is not None:

        if (
            rsi < 0
            or rsi > 100
        ):

            print(
                "RSI 異常 "
                + str(stock_id)
                + "："
                + str(rsi)
            )

            return False

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    for field in [
        "k",
        "d"
    ]:

        value = safe_float(
            technical.get(
                field
            )
        )

        if value is not None:

            if (
                value < 0
                or value > 100
            ):

                print(
                    "KD 異常 "
                    + str(stock_id)
                    + " "
                    + str(field)
                    + "："
                    + str(value)
                )

                return False

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    score = safe_float(
        stock.get(
            "short_term",
            {}
        ).get(
            "score"
        )
    )

    if score is not None:

        if (
            score < 0
            or score > 100
        ):

            return False

    return True


# ============================================================
# Ranking
# ============================================================

def build_rankings(
    stocks
):

    # --------------------------------------------------------
    # AI Score 排名
    # --------------------------------------------------------

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
        for stock in ranking_data
    ]

    # --------------------------------------------------------
    # 核心訊號
    # --------------------------------------------------------

    core_stocks = [
        stock
        for stock in stocks
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
        key=lambda stock: safe_float(
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

    core = [
        str(
            stock["id"]
        )
        for stock in core_stocks
    ]

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

    dca_stocks = [
        stock
        for stock in stocks
        if stock.get(
            "technical",
            {}
        ).get(
            "ma20"
        ) is not None
    ]

    def dca_key(
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

        if (
            price
            and ma20
        ):

            distance = abs(
                price / ma20 - 1
            )

        else:

            distance = 999

        return (
            score,
            -distance
        )

    dca_stocks.sort(
        key=dca_key,
        reverse=True
    )

    dca = [
        str(
            stock["id"]
        )
        for stock in dca_stocks
    ]

    # --------------------------------------------------------
    # AI >= 70
    # --------------------------------------------------------

    ai70 = [
        stock
        for stock in stocks
        if safe_float(
            stock.get(
                "short_term",
                {}
            ).get(
                "score"
            ),
            0
        ) >= 70
    ]

    ai70.sort(
        key=lambda stock: safe_float(
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

    return {

        "short_term":
            short_term,

        "core":
            core,

        "dca":
            dca,

        "ai_70":
            [
                str(
                    stock["id"]
                )
                for stock in ai70
            ]

    }


# ============================================================
# Statistics
# ============================================================

def build_statistics(
    stocks,
    market_list
):

    total_stocks = len(
        stocks
    )

    market_total = len(
        market_list
    )

    listed = sum(
        1
        for item in market_list
        if item["market"] == "上市"
        and item["type"] != "ETF"
    )

    otc = sum(
        1
        for item in market_list
        if item["market"] == "上櫃"
        and item["type"] != "ETF"
    )

    etf = sum(
        1
        for item in market_list
        if item["type"] == "ETF"
    )

    core = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )
    )

    macd = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "macd_golden_cross",
            False
        )
    )

    rsi = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "rsi_above_50",
            False
        )
    )

    kd = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "kd_golden_cross",
            False
        )
    )

    volume = sum(
        1
        for stock in stocks
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
        for stock in stocks
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
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "ma20_up",
            False
        )
    )

    ai70 = sum(
        1
        for stock in stocks
        if safe_float(
            stock.get(
                "short_term",
                {}
            ).get(
                "score"
            ),
            0
        ) >= 70
    )

    return {

        "market_total":
            market_total,

        "total_stocks":
            total_stocks,

        "successful":
            total_stocks,

        "listed_stocks":
            listed,

        "otc_stocks":
            otc,

        "etf_stocks":
            etf,

        "core_stocks":
            core,

        "ai_70":
            ai70,

        "macd_golden":
            macd,

        "rsi_above_50":
            rsi,

        "kd_golden":
            kd,

        "volume_over_1_5x":
            volume,

        "above_ma20":
            above_ma20,

        "ma20_up":
            ma20_up

    }


# ============================================================
# JSON 儲存
# ============================================================

def save_json(
    data
):

    try:

        temp_file = (
            OUTPUT_FILE
            + ".tmp"
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
                indent=2,
                allow_nan=False
            )

        os.replace(
            temp_file,
            OUTPUT_FILE
        )

        print(
            ""
        )

        print(
            "JSON 已寫入："
            + OUTPUT_FILE
        )

        return True

    except Exception as error:

        print(
            "JSON 寫入失敗："
            + str(error)
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

    print(
        "================================================"
    )

    print(
        "台股 AI 全市場掃描系統 "
        + VERSION
    )

    print(
        "開始時間："
        + now.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print(
        "================================================"
    )

    # ========================================================
    # 1. 取得全市場清單
    # ========================================================

    market_list = build_market_list()

    if not market_list:

        print(
            "錯誤：無法取得市場清單"
        )

        sys.exit(1)

    # ========================================================
    # 2. 批次取得 Yahoo 歷史資料
    # ========================================================

    data_map = download_market_data(
        market_list
    )

    # ========================================================
    # 3. 分析
    # ========================================================

    stocks = []

    failed_codes = []

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "開始 AI 技術分析"
    )

    print(
        "================================================"
    )

    for index, item in enumerate(
        market_list,
        start=1
    ):

        code = item["id"]

        df = data_map.get(
            code
        )

        if df is None:

            failed_codes.append(
                code
            )

            continue

        stock = analyze_stock(
            item,
            df
        )

        if stock is None:

            failed_codes.append(
                code
            )

            continue

        if not validate_stock(
            stock
        ):

            failed_codes.append(
                code
            )

            continue

        stocks.append(
            stock
        )

        if index % 100 == 0:

            print(
                "分析進度："
                + str(index)
                + " / "
                + str(len(market_list))
            )

    # ========================================================
    # 4. 完全無資料
    # ========================================================

    if not stocks:

        print(
            "錯誤：沒有任何股票成功分析"
        )

        sys.exit(1)

    # ========================================================
    # 5. 排名
    # ========================================================

    rankings = build_rankings(
        stocks
    )

    # ========================================================
    # 6. 統計
    # ========================================================

    statistics = build_statistics(
        stocks,
        market_list
    )

    # ========================================================
    # 7. 更新時間
    # ========================================================

    updated_at = now.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # ========================================================
    # 8. 最終 JSON
    # ========================================================

    output = {

        "version":
            VERSION,

        "updated_at":
            updated_at,

        "market":
            "TW",

        "source":
            "TWSE / TPEx / Yahoo Finance",

        "scan_mode":
            "FULL_MARKET",

        "market_list_count":
            len(market_list),

        "successful_count":
            len(stocks),

        "failed_count":
            len(failed_codes),

        "stocks":
            stocks,

        "rankings":
            rankings,

        "statistics":
            statistics

    }

    # ========================================================
    # 9. 儲存
    # ========================================================

    success = save_json(
        output
    )

    if not success:

        sys.exit(1)

    # ========================================================
    # 10. 最終報告
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "全市場掃描完成"
    )

    print(
        "================================================"
    )

    print(
        "市場清單："
        + str(
            len(market_list)
        )
        + " 檔"
    )

    print(
        "成功分析："
        + str(
            len(stocks)
        )
        + " 檔"
    )

    print(
        "失敗："
        + str(
            len(failed_codes)
        )
        + " 檔"
    )

    print(
        ""
    )

    print(
        "上市股票："
        + str(
            statistics[
                "listed_stocks"
            ]
        )
        + " 檔"
    )

    print(
        "上櫃股票："
        + str(
            statistics[
                "otc_stocks"
            ]
        )
        + " 檔"
    )

    print(
        "ETF："
        + str(
            statistics[
                "etf_stocks"
            ]
        )
        + " 檔"
    )

    print(
        ""
    )

    print(
        "AI ≥ 70："
        + str(
            statistics[
                "ai_70"
            ]
        )
        + " 檔"
    )

    print(
        "核心訊號："
        + str(
            statistics[
                "core_stocks"
            ]
        )
        + " 檔"
    )

    print(
        "MACD 黃金交叉："
        + str(
            statistics[
                "macd_golden"
            ]
        )
        + " 檔"
    )

    print(
        "RSI > 50："
        + str(
            statistics[
                "rsi_above_50"
            ]
        )
        + " 檔"
    )

    print(
        "KD 黃金交叉："
        + str(
            statistics[
                "kd_golden"
            ]
        )
        + " 檔"
    )

    print(
        "成交量 > 1.5x："
        + str(
            statistics[
                "volume_over_1_5x"
            ]
        )
        + " 檔"
    )

    print(
        "站上 MA20："
        + str(
            statistics[
                "above_ma20"
            ]
        )
        + " 檔"
    )

    print(
        "MA20 向上："
        + str(
            statistics[
                "ma20_up"
            ]
        )
        + " 檔"
    )

    print(
        ""
    )

    print(
        "耗時："
        + str(
            round(
                elapsed,
                2
            )
        )
        + " 秒"
    )

    print(
        "================================================"
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "程式被中止"
        )

        sys.exit(1)

    except Exception as error:

        print(
            ""
        )

        print(
            "================================================"
        )

        print(
            "程式發生未預期錯誤"
        )

        print(
            str(error)
        )

        print(
            "================================================"
        )

        traceback.print_exc()

        sys.exit(1)
