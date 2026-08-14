# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V6.2 正式版
#
# ============================================================
# V6.2 核心架構
#
# 1. 不再內建固定股票清單
# 2. 自動建立台股市場 Universe
# 3. 掃描：
#       - TWSE 上市股票
#       - TWSE ETF
#       - TPEx 上櫃股票
#       - TPEx ETF
# 4. 使用 Yahoo Finance 歷史資料
# 5. 批次下載，降低 API 請求數量
# 6. 計算：
#       - RSI 14
#       - KD 9
#       - MACD 12/26/9
#       - MA5
#       - MA20
#       - MA60
#       - Volume MA5
#       - Volume Ratio
# 7. AI SCORE 0~100
# 8. 核心訊號
# 9. 四段式 DCA
# 10. rankings
# 11. statistics
# 12. RSI 強制限制 0~100
# 13. KD 強制限制 0~100
# 14. 單檔失敗不影響其他股票
# 15. 保持 index.html V6 所需 JSON 結構
#
# ============================================================


import os
import sys
import json
import math
import time
import traceback
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf


# ============================================================
# 基本設定
# ============================================================

VERSION = "V6.2"

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

UNIVERSE_CACHE_FILE = os.path.join(
    DATA_DIR,
    "market_universe.json"
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
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
})


# ============================================================
# Yahoo Finance 批次設定
# ============================================================

YAHOO_BATCH_SIZE = 40

YAHOO_PERIOD = "1y"

YAHOO_INTERVAL = "1d"

YAHOO_RETRY = 3

BATCH_SLEEP = 0.5


# ============================================================
# 市場來源
# ============================================================

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/"
    "e_single_main.jsp"
)

TWSE_STOCK_API = (
    "https://openapi.twse.com.tw/"
    "v1/exchangeReport/STOCK_DAY_ALL"
)

TPEX_STOCK_API = (
    "https://www.tpex.org.tw/"
    "openapi/v1/tpex_mainboard_daily_close_quotes"
)


# ============================================================
# 安全數字轉換
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
            (list, tuple, dict)
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
# Yahoo Symbol
# ============================================================

def yahoo_symbol(
    code,
    market
):

    code = str(
        code
    ).strip()

    if market == "TWSE":

        return code + ".TW"

    if market == "TPEX":

        return code + ".TWO"

    return code + ".TW"


# ============================================================
# HTTP JSON
# ============================================================

def get_json(
    url,
    timeout=20
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
            f"API 讀取失敗：{url}"
        )

        print(
            f"原因：{error}"
        )

        return None


# ============================================================
# 判斷股票代號
# ============================================================

def valid_security_code(
    code
):

    if code is None:
        return False

    code = str(
        code
    ).strip()

    if not code:
        return False

    # 台股一般股票 / ETF 代號
    if not code.isdigit():
        return False

    # 過濾明顯不是一般證券代號的資料
    if len(code) < 4:
        return False

    if len(code) > 6:
        return False

    return True


# ============================================================
# 建立 TWSE 股票清單
#
# 來源：
# TWSE 公開 API / 每日證券資料
#
# ============================================================

def get_twse_stocks():

    print(
        ""
    )

    print(
        "建立 TWSE 上市股票清單..."
    )

    data = get_json(
        TWSE_STOCK_API
    )

    universe = []

    if data:

        for row in data:

            try:

                code = (
                    row.get(
                        "Code"
                    )
                    or
                    row.get(
                        "證券代號"
                    )
                )

                name = (
                    row.get(
                        "Name"
                    )
                    or
                    row.get(
                        "證券名稱"
                    )
                )

                if not valid_security_code(
                    code
                ):

                    continue

                code = str(
                    code
                ).strip()

                name = (
                    str(name).strip()
                    if name is not None
                    else code
                )

                # 排除權證類型代號
                # 一般股票 / ETF 優先保留
                if len(code) < 4:
                    continue

                universe.append({

                    "id": code,

                    "name": name,

                    "market": "TWSE",

                    "type": (
                        "ETF"
                        if code.startswith("00")
                        else "STOCK"
                    ),

                    "symbol":
                        yahoo_symbol(
                            code,
                            "TWSE"
                        )

                })

            except Exception:

                continue

    print(
        f"TWSE API 股票資料："
        f"{len(universe)}"
    )

    return universe


# ============================================================
# TWSE ETF 清單
#
# 透過 ISIN 公開清單補足 ETF。
#
# ============================================================

def get_twse_etf_from_isin():

    print(
        "建立 TWSE / TPEx ETF 清單..."
    )

    try:

        tables = pd.read_html(
            TWSE_ISIN_URL
        )

    except Exception as error:

        print(
            "ISIN 清單讀取失敗："
            f"{error}"
        )

        return []

    etfs = []

    for table in tables:

        if table is None:
            continue

        if table.empty:
            continue

        columns = [
            str(column)
            for column in table.columns
        ]

        # 嘗試辨識證券代號 / 名稱 / 市場 / 類型
        code_column = None
        name_column = None
        market_column = None
        type_column = None

        for column in columns:

            if (
                "Security Code" in column
                or
                "證券代號" in column
            ):
                code_column = column

            elif (
                "Security Name" in column
                or
                "證券名稱" in column
            ):
                name_column = column

            elif (
                "Market" in column
                or
                "市場" in column
            ):
                market_column = column

            elif (
                "Type of security" in column
                or
                "證券種類" in column
            ):
                type_column = column

        if (
            code_column is None
            or
            name_column is None
        ):

            continue

        for _, row in table.iterrows():

            try:

                code = str(
                    row[
                        code_column
                    ]
                ).strip()

                name = str(
                    row[
                        name_column
                    ]
                ).strip()

                market = (
                    str(
                        row[
                            market_column
                        ]
                    ).strip()
                    if market_column
                    else ""
                )

                security_type = (
                    str(
                        row[
                            type_column
                        ]
                    ).strip()
                    if type_column
                    else ""
                )

                if not valid_security_code(
                    code
                ):
                    continue

                if (
                    "ETF"
                    not in security_type.upper()
                    and
                    "ETF"
                    not in name.upper()
                ):
                    continue

                if (
                    "TWSE"
                    in market.upper()
                ):

                    market_code = "TWSE"

                elif (
                    "TPEx"
                    in market
                    or
                    "OTC"
                    in market.upper()
                ):

                    market_code = "TPEX"

                else:

                    continue

                etfs.append({

                    "id": code,

                    "name": name,

                    "market":
                        market_code,

                    "type": "ETF",

                    "symbol":
                        yahoo_symbol(
                            code,
                            market_code
                        )

                })

            except Exception:

                continue

    print(
        f"ETF 清單：{len(etfs)}"
    )

    return etfs


# ============================================================
# TPEX 上櫃股票
# ============================================================

def get_tpex_stocks():

    print(
        ""
    )

    print(
        "建立 TPEx 上櫃股票清單..."
    )

    data = get_json(
        TPEX_STOCK_API
    )

    universe = []

    if data:

        for row in data:

            try:

                code = (
                    row.get(
                        "SecuritiesCompanyCode"
                    )
                    or
                    row.get(
                        "SecuritiesCompanyCode"
                    )
                    or
                    row.get(
                        "證券代號"
                    )
                )

                name = (
                    row.get(
                        "CompanyName"
                    )
                    or
                    row.get(
                        "公司名稱"
                    )
                    or
                    row.get(
                        "證券名稱"
                    )
                )

                if not valid_security_code(
                    code
                ):
                    continue

                code = str(
                    code
                ).strip()

                name = (
                    str(name).strip()
                    if name is not None
                    else code
                )

                universe.append({

                    "id": code,

                    "name": name,

                    "market": "TPEX",

                    "type": (
                        "ETF"
                        if code.startswith("00")
                        else "STOCK"
                    ),

                    "symbol":
                        yahoo_symbol(
                            code,
                            "TPEX"
                        )

                })

            except Exception:

                continue

    print(
        f"TPEx 股票資料："
        f"{len(universe)}"
    )

    return universe


# ============================================================
# 去除重複
# ============================================================

def deduplicate_universe(
    universe
):

    result = {}

    for item in universe:

        code = str(
            item.get(
                "id",
                ""
            )
        ).strip()

        if not code:
            continue

        market = item.get(
            "market",
            "TWSE"
        )

        key = (
            market,
            code
        )

        result[key] = item

    return list(
        result.values()
    )


# ============================================================
# 建立完整市場 Universe
# ============================================================

def build_market_universe():

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "建立全市場股票池 V6.2"
    )

    print(
        "================================================"
    )

    universe = []

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    twse = get_twse_stocks()

    universe.extend(
        twse
    )

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    tpex = get_tpex_stocks()

    universe.extend(
        tpex
    )

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    etfs = get_twse_etf_from_isin()

    universe.extend(
        etfs
    )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    universe = deduplicate_universe(
        universe
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    universe = sorted(
        universe,
        key=lambda item: (
            item.get(
                "market",
                ""
            ),
            item.get(
                "type",
                ""
            ),
            item.get(
                "id",
                ""
            )
        )
    )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    twse_count = sum(
        1
        for item in universe
        if item["market"] == "TWSE"
        and item["type"] == "STOCK"
    )

    tpex_count = sum(
        1
        for item in universe
        if item["market"] == "TPEX"
        and item["type"] == "STOCK"
    )

    etf_count = sum(
        1
        for item in universe
        if item["type"] == "ETF"
    )

    print(
        ""
    )

    print(
        "市場股票池建立完成"
    )

    print(
        f"TWSE 股票：{twse_count}"
    )

    print(
        f"TPEX 股票：{tpex_count}"
    )

    print(
        f"ETF：{etf_count}"
    )

    print(
        f"總數：{len(universe)}"
    )

    return universe


# ============================================================
# 儲存市場股票池
# ============================================================

def save_universe(
    universe
):

    try:

        data = {

            "version":
                VERSION,

            "updated_at":
                datetime.now(
                    TW_TZ
                ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),

            "count":
                len(universe),

            "stocks":
                universe

        }

        with open(
            UNIVERSE_CACHE_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"股票池已儲存："
            f"{UNIVERSE_CACHE_FILE}"
        )

        return True

    except Exception as error:

        print(
            f"股票池儲存失敗："
            f"{error}"
        )

        return False


# ============================================================
# 讀取舊股票池
# ============================================================

def load_cached_universe():

    if not os.path.exists(
        UNIVERSE_CACHE_FILE
    ):

        return []

    try:

        with open(
            UNIVERSE_CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

        stocks = data.get(
            "stocks",
            []
        )

        if isinstance(
            stocks,
            list
        ):

            return stocks

    except Exception as error:

        print(
            f"舊股票池讀取失敗："
            f"{error}"
        )

    return []


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

    # --------------------------------------------------------
    # RSI 計算
    # --------------------------------------------------------

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
            (
                1 + rs
            )
        )
    )

    # 沒有跌幅
    rsi = rsi.where(
        avg_loss != 0,
        100
    )

    # --------------------------------------------------------
    # V6.2 強制 0~100
    # --------------------------------------------------------

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
# Series 最新值
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
# 正規化 Yahoo DataFrame
# ============================================================

def normalize_yahoo_dataframe(
    df
):

    if df is None:
        return None

    if df.empty:
        return None

    try:

        # ----------------------------------------------------
        # MultiIndex
        # ----------------------------------------------------

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            # 如果只有一檔股票
            if len(
                df.columns.get_level_values(
                    0
                ).unique()
            ) == 1:

                df.columns = [
                    str(
                        column[0]
                    ).strip().lower()
                    for column in df.columns
                ]

            else:

                return df

        else:

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
# 下載單一股票
# ============================================================

def download_single_stock(
    symbol
):

    for attempt in range(
        1,
        YAHOO_RETRY + 1
    ):

        try:

            df = yf.download(
                symbol,
                period=YAHOO_PERIOD,
                interval=YAHOO_INTERVAL,
                auto_adjust=False,
                progress=False,
                threads=False
            )

            if df is None:
                continue

            if df.empty:
                continue

            return normalize_yahoo_dataframe(
                df
            )

        except Exception as error:

            print(
                f"{symbol} "
                f"第 {attempt} 次失敗："
                f"{error}"
            )

            time.sleep(
                attempt
            )

    return None


# ============================================================
# 批次下載
# ============================================================

def download_batch(
    symbols
):

    if not symbols:
        return {}

    result = {}

    for attempt in range(
        1,
        YAHOO_RETRY + 1
    ):

        try:

            print(
                ""
            )

            print(
                f"Yahoo 批次下載："
                f"{len(symbols)} 檔"
            )

            data = yf.download(
                tickers=symbols,
                period=YAHOO_PERIOD,
                interval=YAHOO_INTERVAL,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=True
            )

            if data is None:
                continue

            if data.empty:
                continue

            # =================================================
            # Multi ticker
            # =================================================

            if isinstance(
                data.columns,
                pd.MultiIndex
            ):

                level0 = (
                    list(
                        data.columns
                        .get_level_values(0)
                        .unique()
                    )
                )

                level1 = (
                    list(
                        data.columns
                        .get_level_values(1)
                        .unique()
                    )
                )

                # ---------------------------------------------
                # 情況 A：
                # 第一層 = ticker
                # ---------------------------------------------

                if any(
                    symbol in level0
                    for symbol in symbols
                ):

                    for symbol in symbols:

                        if symbol not in level0:
                            continue

                        try:

                            df = data[
                                symbol
                            ].copy()

                            df.columns = [
                                str(
                                    column
                                ).strip().lower()
                                for column
                                in df.columns
                            ]

                            if (
                                "close"
                                not in
                                df.columns
                            ):

                                continue

                            if len(
                                df.dropna(
                                    subset=[
                                        "close"
                                    ]
                                )
                            ) < 35:

                                continue

                            result[
                                symbol
                            ] = df

                        except Exception:

                            continue

                # ---------------------------------------------
                # 情況 B：
                # 第一層 = OHLC
                # 第二層 = ticker
                # ---------------------------------------------

                elif "close" in [
                    str(x).lower()
                    for x in level0
                ]:

                    for symbol in symbols:

                        try:

                            if symbol not in level1:
                                continue

                            df = pd.DataFrame(
                                index=data.index
                            )

                            for field in [
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume"
                            ]:

                                if (
                                    field in level0
                                ):

                                    df[field] = (
                                        data[
                                            field
                                        ][
                                            symbol
                                        ]
                                    )

                            if (
                                "close"
                                not in
                                df.columns
                            ):

                                continue

                            result[
                                symbol
                            ] = df

                        except Exception:

                            continue

            else:

                # 單一 ticker
                if len(symbols) == 1:

                    df = data.copy()

                    df.columns = [
                        str(
                            column
                        ).strip().lower()
                        for column
                        in df.columns
                    ]

                    result[
                        symbols[0]
                    ] = df

            if result:

                return result

        except Exception as error:

            print(
                f"Yahoo 批次下載失敗 "
                f"第 {attempt} 次："
                f"{error}"
            )

            time.sleep(
                attempt * 2
            )

    return result


# ============================================================
# 計算技術分析
# ============================================================

def analyze_stock(
    item,
    df
):

    try:

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

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        df = df.dropna(
            subset=[
                "close"
            ]
        ).copy()

        if len(df) < 35:

            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # ====================================================
        # 技術指標
        # ====================================================

        ma5 = close.rolling(
            5
        ).mean()

        ma20 = close.rolling(
            20
        ).mean()

        ma60 = close.rolling(
            60
        ).mean()

        rsi = calculate_rsi(
            close,
            14
        )

        k, d = calculate_kd(
            high,
            low,
            close,
            9
        )

        (
            macd,
            macd_signal,
            macd_hist
        ) = calculate_macd(
            close
        )

        volume_ma5 = volume.rolling(
            5
        ).mean()

        # ====================================================
        # 最新值
        # ====================================================

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

        # ====================================================
        # RSI 防呆
        # ====================================================

        if current_rsi is not None:

            current_rsi = max(
                0,
                min(
                    100,
                    current_rsi
                )
            )

        # ====================================================
        # KD 防呆
        # ====================================================

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

        # ====================================================
        # Volume Ratio
        # ====================================================

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

        # ====================================================
        # 漲跌
        # ====================================================

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
                    previous_price
                ) * 100

            else:

                change_percent = 0

        else:

            change = None

            change_percent = None

        # ====================================================
        # 前一日
        # ====================================================

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

        # ====================================================
        # MACD 黃金交叉
        # ====================================================

        macd_golden_cross = False

        if (
            previous_macd is not None
            and
            previous_macd_signal is not None
            and
            current_macd is not None
            and
            current_macd_signal is not None
        ):

            macd_golden_cross = (
                previous_macd
                <=
                previous_macd_signal
                and
                current_macd
                >
                current_macd_signal
            )

        # ====================================================
        # KD 黃金交叉
        # ====================================================

        kd_golden_cross = False

        if (
            previous_k is not None
            and
            previous_d is not None
            and
            current_k is not None
            and
            current_d is not None
        ):

            kd_golden_cross = (
                previous_k
                <=
                previous_d
                and
                current_k
                >
                current_d
            )

        # ====================================================
        # 條件
        # ====================================================

        rsi_above_50 = (
            current_rsi is not None
            and
            current_rsi > 50
        )

        volume_over_1_5x = (
            volume_ratio is not None
            and
            volume_ratio >= 1.5
        )

        above_ma20 = (
            current_price is not None
            and
            current_ma20 is not None
            and
            current_price >
            current_ma20
        )

        ma20_up = (
            current_ma20 is not None
            and
            previous_ma20 is not None
            and
            current_ma20 >
            previous_ma20
        )

        macd_positive = (
            current_macd_hist is not None
            and
            current_macd_hist > 0
        )

        # ====================================================
        # 核心訊號
        # ====================================================

        short_term_core = all([

            macd_golden_cross,

            kd_golden_cross,

            rsi_above_50,

            volume_over_1_5x,

            above_ma20,

            ma20_up

        ])

        # ====================================================
        # AI SCORE
        # ====================================================

        score = 0

        # MACD
        if macd_golden_cross:

            score += 20

        elif macd_positive:

            score += 10

        # KD
        if kd_golden_cross:

            score += 15

        elif (
            current_k is not None
            and
            current_d is not None
            and
            current_k >
            current_d
        ):

            score += 8

        # RSI
        if rsi_above_50:

            score += 15

        # Volume
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

        # MA20
        if above_ma20:

            score += 15

        # MA20 trend
        if ma20_up:

            score += 10

        # MACD Histogram
        if macd_positive:

            score += 10

        score = max(
            0,
            min(
                100,
                int(score)
            )
        )

        # ====================================================
        # Signal
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

        # ====================================================
        # DCA Action
        # ====================================================

        if (
            current_price is not None
            and
            current_ma20 is not None
            and
            current_ma20 > 0
        ):

            distance = (
                current_price /
                current_ma20 -
                1
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
        # 日期
        # ====================================================

        last_date = None

        try:

            if len(df.index) > 0:

                last_date = (
                    pd.to_datetime(
                        df.index[-1]
                    ).strftime(
                        "%Y-%m-%d"
                    )
                )

        except Exception:

            last_date = None

        # ====================================================
        # JSON
        # ====================================================

        stock = {

            "id":
                str(
                    item["id"]
                ),

            "name":
                str(
                    item["name"]
                ),

            "symbol":
                str(
                    item["id"]
                ),

            "type":
                str(
                    item.get(
                        "type",
                        "STOCK"
                    )
                ),

            "market":
                str(
                    item.get(
                        "market",
                        "TWSE"
                    )
                ),

            "data_date":
                last_date,

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

        return stock

    except Exception as error:

        print(
            f"{item.get('id')}: "
            f"分析失敗：{error}"
        )

        return None


# ============================================================
# 驗證股票
# ============================================================

def validate_stock(
    stock
):

    if not stock:
        return False

    if not stock.get(
        "id"
    ):

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

    if close <= 0:
        return False

    technical = stock.get(
        "technical",
        {}
    )

    # ========================================================
    # RSI
    # ========================================================

    rsi = safe_float(
        technical.get(
            "rsi"
        )
    )

    if rsi is not None:

        if (
            rsi < 0
            or
            rsi > 100
        ):

            print(
                f"{stock['id']}: "
                f"RSI 異常：{rsi}"
            )

            return False

    # ========================================================
    # KD
    # ========================================================

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
                or
                value > 100
            ):

                print(
                    f"{stock['id']}: "
                    f"{field} 異常："
                    f"{value}"
                )

                return False

    # ========================================================
    # SCORE
    # ========================================================

    score = safe_float(
        stock.get(
            "short_term",
            {}
        ).get(
            "score"
        )
    )

    if score is None:
        return False

    if (
        score < 0
        or
        score > 100
    ):

        return False

    return True


# ============================================================
# Rankings
# ============================================================

def build_rankings(
    stocks
):

    # --------------------------------------------------------
    # AI Score
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

        for stock
        in ranking_data

    ]

    # --------------------------------------------------------
    # Core
    # --------------------------------------------------------

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
            stock.get(
                "short_term",
                {}
            ).get(
                "score",
                0
            ),
        reverse=True
    )

    core = [

        str(
            stock["id"]
        )

        for stock
        in core_stocks

    ]

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

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

        if (
            price > 0
            and
            ma20 > 0
        ):

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
            core,

        "dca":
            dca

    }


# ============================================================
# Statistics
# ============================================================

def build_statistics(
    stocks
):

    total_stocks = len(
        stocks
    )

    total_stock_type = sum(
        1
        for stock
        in stocks
        if stock.get(
            "type"
        ) == "STOCK"
    )

    total_etf_type = sum(
        1
        for stock
        in stocks
        if stock.get(
            "type"
        ) == "ETF"
    )

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
        ) == "TPEX"
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

    return {

        "total_stocks":
            total_stocks,

        "total_stock_type":
            total_stock_type,

        "total_etf_type":
            total_etf_type,

        "twse_count":
            twse_count,

        "tpex_count":
            tpex_count,

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
            above_ma20

    }


# ============================================================
# 儲存 JSON
# ============================================================

def save_json(
    data
):

    try:

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

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        f"台股 AI 選股系統 "
        f"fetch_data.py {VERSION}"
    )

    print(
        f"開始時間："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        "================================================"
    )

    # ========================================================
    # 建立股票池
    # ========================================================

    universe = build_market_universe()

    # --------------------------------------------------------
    # 如果 API 暫時失敗
    # 使用上一份股票池
    # --------------------------------------------------------

    if len(universe) == 0:

        print(
            ""
        )

        print(
            "目前市場清單 API 無法取得。"
        )

        print(
            "嘗試使用上一份股票池..."
        )

        universe = load_cached_universe()

    else:

        save_universe(
            universe
        )

    if len(universe) == 0:

        print(
            ""
        )

        print(
            "錯誤："
            "完全無法建立市場股票池。"
        )

        sys.exit(1)

    # ========================================================
    # 股票統計
    # ========================================================

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "開始全市場 AI 掃描"
    )

    print(
        f"股票池總數："
        f"{len(universe)}"
    )

    print(
        "================================================"
    )

    # ========================================================
    # Yahoo Symbols
    # ========================================================

    symbol_to_item = {}

    for item in universe:

        symbol = item.get(
            "symbol"
        )

        if not symbol:
            continue

        symbol_to_item[
            symbol
        ] = item

    symbols = list(
        symbol_to_item.keys()
    )

    # ========================================================
    # 批次下載
    # ========================================================

    stocks = []

    failed = []

    total_symbols = len(
        symbols
    )

    processed = 0

    for start in range(
        0,
        total_symbols,
        YAHOO_BATCH_SIZE
    ):

        batch_symbols = symbols[
            start:
            start +
            YAHOO_BATCH_SIZE
        ]

        print(
            ""
        )

        print(
            "------------------------------------------------"
        )

        print(
            f"批次 "
            f"{start // YAHOO_BATCH_SIZE + 1}"
            f" / "
            f"{math.ceil(total_symbols / YAHOO_BATCH_SIZE)}"
        )

        print(
            f"進度："
            f"{start + 1}"
            f"-"
            f"{min(start + len(batch_symbols), total_symbols)}"
            f" / "
            f"{total_symbols}"
        )

        print(
            "------------------------------------------------"
        )

        batch_data = download_batch(
            batch_symbols
        )

        # ----------------------------------------------------
        # 如果批次失敗
        # 嘗試逐檔補抓
        # ----------------------------------------------------

        if not batch_data:

            print(
                "批次下載失敗，"
                "啟動逐檔補抓..."
            )

            for symbol in batch_symbols:

                df = download_single_stock(
                    symbol
                )

                if df is None:

                    failed.append(
                        symbol
                    )

                    continue

                item = symbol_to_item.get(
                    symbol
                )

                if item is None:
                    continue

                stock = analyze_stock(
                    item,
                    df
                )

                if stock is None:

                    failed.append(
                        symbol
                    )

                    continue

                if not validate_stock(
                    stock
                ):

                    failed.append(
                        symbol
                    )

                    continue

                stocks.append(
                    stock
                )

        else:

            # ------------------------------------------------
            # 分析批次資料
            # ------------------------------------------------

            for symbol in batch_symbols:

                df = batch_data.get(
                    symbol
                )

                if df is None:

                    failed.append(
                        symbol
                    )

                    continue

                item = symbol_to_item.get(
                    symbol
                )

                if item is None:
                    continue

                stock = analyze_stock(
                    item,
                    df
                )

                if stock is None:

                    failed.append(
                        symbol
                    )

                    continue

                if not validate_stock(
                    stock
                ):

                    failed.append(
                        symbol
                    )

                    continue

                stocks.append(
                    stock
                )

        processed += len(
            batch_symbols
        )

        print(
            f"目前成功："
            f"{len(stocks)}"
        )

        print(
            f"目前失敗："
            f"{len(failed)}"
        )

        time.sleep(
            BATCH_SLEEP
        )

    # ========================================================
    # 沒有資料
    # ========================================================

    if len(stocks) == 0:

        print(
            ""
        )

        print(
            "錯誤："
            "沒有任何股票成功取得資料。"
        )

        sys.exit(1)

    # ========================================================
    # 排序
    # ========================================================

    stocks = sorted(
        stocks,
        key=lambda stock: (
            stock.get(
                "market",
                ""
            ),
            stock.get(
                "id",
                ""
            )
        )
    )

    # ========================================================
    # Rankings
    # ========================================================

    rankings = build_rankings(
        stocks
    )

    # ========================================================
    # Statistics
    # ========================================================

    statistics = build_statistics(
        stocks
    )

    # ========================================================
    # Universe Statistics
    # ========================================================

    universe_statistics = {

        "total":
            len(universe),

        "twse_stock":
            sum(
                1
                for item
                in universe
                if item.get(
                    "market"
                ) == "TWSE"
                and
                item.get(
                    "type"
                ) == "STOCK"
            ),

        "tpex_stock":
            sum(
                1
                for item
                in universe
                if item.get(
                    "market"
                ) == "TPEX"
                and
                item.get(
                    "type"
                ) == "STOCK"
            ),

        "etf":
            sum(
                1
                for item
                in universe
                if item.get(
                    "type"
                ) == "ETF"
            )

    }

    # ========================================================
    # 最終 JSON
    # ========================================================

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
            "TWSE / TPEx / Yahoo Finance",

        "universe":
            universe_statistics,

        "stocks":
            stocks,

        "rankings":
            rankings,

        "statistics":
            statistics

    }

    # ========================================================
    # 儲存
    # ========================================================

    success = save_json(
        output
    )

    if not success:

        sys.exit(1)

    # ========================================================
    # 完成統計
    # ========================================================

    elapsed = (
        time.time()
        -
        start_time
    )

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "V6.2 全市場掃描完成"
    )

    print(
        "================================================"
    )

    print(
        f"市場股票池："
        f"{len(universe)}"
        f" 檔"
    )

    print(
        f"成功分析："
        f"{len(stocks)}"
        f" 檔"
    )

    print(
        f"失敗："
        f"{len(failed)}"
        f" 檔"
    )

    print(
        ""
    )

    print(
        f"TWSE 股票："
        f"{universe_statistics['twse_stock']}"
    )

    print(
        f"TPEX 股票："
        f"{universe_statistics['tpex_stock']}"
    )

    print(
        f"ETF："
        f"{universe_statistics['etf']}"
    )

    print(
        ""
    )

    print(
        f"核心訊號："
        f"{statistics['core_stocks']}"
    )

    print(
        f"MACD 黃金交叉："
        f"{statistics['macd_golden']}"
    )

    print(
        f"RSI > 50："
        f"{statistics['rsi_above_50']}"
    )

    print(
        f"KD 黃金交叉："
        f"{statistics['kd_golden']}"
    )

    print(
        f"成交量 > 1.5x："
        f"{statistics['volume_over_1_5x']}"
    )

    print(
        f"站上 MA20："
        f"{statistics['above_ma20']}"
    )

    print(
        ""
    )

    print(
        f"耗時："
        f"{elapsed:.2f} 秒"
    )

    print(
        "================================================"
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    main()
