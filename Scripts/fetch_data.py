# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V7.0
#
# 「全市場 AI 掃描正式版」
#
# 功能：
# 1. 自動取得台股上市股票清單
# 2. 自動取得台股上櫃股票清單
# 3. 自動取得台股 ETF 清單
# 4. 不再內建固定個股清單
# 5. 自動轉換 Yahoo Finance ticker
# 6. 計算 RSI
# 7. 計算 KD
# 8. 計算 MACD
# 9. 計算 MA5 / MA20 / MA60
# 10. 計算成交量比
# 11. 計算 MACD 黃金交叉
# 12. 計算 KD 黃金交叉
# 13. 計算 RSI > 50
# 14. 計算成交量 > 5日均量 × 1.5
# 15. 計算站上 MA20
# 16. 計算 MA20 向上
# 17. AI SCORE 0~100
# 18. 核心訊號判斷
# 19. 四段式零股定投價格
# 20. 全市場短線排名
# 21. 全市場核心訊號排名
# 22. 全市場 DCA 排名
# 23. 市場統計
# 24. 單一股票失敗不影響其他股票
# 25. RSI 強制限制 0~100
# 26. KD 強制限制 0~100
# 27. 保持前端 V6.2 / V7 JSON 相容
#
# 資料來源：
# Yahoo Finance
#
# 市場清單來源：
# TWSE / TPEx 公開資料
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

VERSION = "V7.0"

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

MARKET_FILE = os.path.join(
    DATA_DIR,
    "market_list.json"
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
# 網路設定
# ============================================================

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent":
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
}


# ============================================================
# 市場清單來源
#
# TWSE：
# 上市股票
#
# TPEx：
# 上櫃股票
#
# ETF：
# TWSE / TPEx 公開資料
# ============================================================

TWSE_STOCK_URL = (
    "https://openapi.twse.com.tw/"
    "v1/opendata/t187ap03_L"
)

TPEx_STOCK_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_close_quotes/"
    "st43.php"
)

TWSE_ETF_URL = (
    "https://openapi.twse.com.tw/"
    "v1/opendata/t187ap47_L"
)

TPEx_ETF_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_close_quotes/"
    "st44.php"
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
            (
                list,
                tuple,
                dict
            )
        ):
            return default

        number = float(value)

        if not math.isfinite(
            number
        ):
            return default

        return number

    except Exception:

        return default


# ============================================================
# 安全整數
# ============================================================

def safe_int(
    value,
    default=0
):

    try:

        return int(
            float(value)
        )

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
# HTTP GET
# ============================================================

def http_get_json(
    url,
    params=None
):

    try:

        response = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as error:

        print(
            f"HTTP 取得失敗：{url}"
        )

        print(
            f"原因：{error}"
        )

        return None


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

    # --------------------------------------------------------
    # 只保留一般台股代號
    # --------------------------------------------------------

    if not code.isdigit():

        return None

    if len(code) < 4:

        return None

    return code


# ============================================================
# 判斷 ETF
# ============================================================

def is_etf_code(
    code
):

    code = str(
        code
    )

    # --------------------------------------------------------
    # 台股 ETF 常見代號：
    # 00xx
    #
    # 但並非所有 00 開頭都一定是 ETF，
    # 所以主要仍以市場清單類型判斷。
    # --------------------------------------------------------

    return code.startswith(
        "00"
    )


# ============================================================
# Yahoo Finance ticker
# ============================================================

def yahoo_symbol(
    code,
    market="TW"
):

    code = str(
        code
    ).strip()

    if market == "TPEX":

        return code + ".TWO"

    return code + ".TW"


# ============================================================
# 取得上市股票
# ============================================================

def fetch_twse_stocks():

    print(
        ""
    )

    print(
        "取得 TWSE 上市股票清單..."
    )

    data = http_get_json(
        TWSE_STOCK_URL
    )

    stocks = []

    if not isinstance(
        data,
        list
    ):

        print(
            "TWSE 股票清單取得失敗"
        )

        return stocks

    for item in data:

        if not isinstance(
            item,
            dict
        ):

            continue

        code = (
            item.get("公司代號")
            or item.get("Code")
            or item.get("證券代號")
        )

        name = (
            item.get("公司名稱")
            or item.get("Name")
            or item.get("證券名稱")
        )

        code = clean_code(
            code
        )

        if not code:

            continue

        if not name:

            name = code

        stocks.append({

            "code":
                code,

            "name":
                str(name).strip(),

            "market":
                "TWSE",

            "type":
                "STOCK"

        })

    print(
        f"TWSE 上市股票：{len(stocks)}"
    )

    return stocks


# ============================================================
# 取得上櫃股票
# ============================================================

def fetch_tpex_stocks():

    print(
        ""
    )

    print(
        "取得 TPEx 上櫃股票清單..."
    )

    # --------------------------------------------------------
    # TPEx API
    # --------------------------------------------------------

    params = {

        "l": "zh-tw",

        "d":
            datetime.now(
                TW_TZ
            ).strftime(
                "%Y%m%d"
            ),

        "s": "0,asc,0",

        "o": "json"

    }

    try:

        response = requests.get(
            TPEx_STOCK_URL,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

    except Exception as error:

        print(
            f"TPEx 股票清單取得失敗：{error}"
        )

        return []


    stocks = []


    # --------------------------------------------------------
    # TPEx JSON 結構可能包含 tables
    # --------------------------------------------------------

    tables = []

    if isinstance(
        data,
        dict
    ):

        if isinstance(
            data.get("tables"),
            list
        ):

            tables = data.get(
                "tables"
            )

        elif isinstance(
            data.get("aaData"),
            list
        ):

            tables = [
                {
                    "data":
                        data.get("aaData")
                }
            ]

    # --------------------------------------------------------
    # 處理 aaData
    # --------------------------------------------------------

    rows = []

    if isinstance(
        data,
        dict
    ):

        if isinstance(
            data.get("aaData"),
            list
        ):

            rows = data.get(
                "aaData"
            )

    # --------------------------------------------------------
    # tables
    # --------------------------------------------------------

    if tables:

        for table in tables:

            if not isinstance(
                table,
                dict
            ):

                continue

            table_rows = (
                table.get("data")
                or table.get("rows")
                or []
            )

            if isinstance(
                table_rows,
                list
            ):

                rows.extend(
                    table_rows
                )

    # --------------------------------------------------------
    # 自動解析
    # --------------------------------------------------------

    for row in rows:

        code = None
        name = None

        if isinstance(
            row,
            dict
        ):

            code = (
                row.get("SecuritiesCompanyCode")
                or row.get("Code")
                or row.get("代號")
                or row.get("證券代號")
            )

            name = (
                row.get("CompanyName")
                or row.get("Name")
                or row.get("名稱")
                or row.get("證券名稱")
            )

        elif isinstance(
            row,
            list
        ):

            if len(row) >= 2:

                code = row[0]
                name = row[1]

        code = clean_code(
            code
        )

        if not code:

            continue

        if not name:

            name = code

        stocks.append({

            "code":
                code,

            "name":
                str(name).strip(),

            "market":
                "TPEX",

            "type":
                "STOCK"

        })


    # --------------------------------------------------------
    # 去除重複
    # --------------------------------------------------------

    unique = {}

    for stock in stocks:

        unique[
            stock["code"]
        ] = stock

    stocks = list(
        unique.values()
    )

    print(
        f"TPEx 上櫃股票：{len(stocks)}"
    )

    return stocks


# ============================================================
# 取得 TWSE ETF
# ============================================================

def fetch_twse_etf():

    print(
        ""
    )

    print(
        "取得 TWSE ETF 清單..."
    )

    data = http_get_json(
        TWSE_ETF_URL
    )

    etfs = []

    if not isinstance(
        data,
        list
    ):

        print(
            "TWSE ETF 清單取得失敗"
        )

        return etfs

    for item in data:

        if not isinstance(
            item,
            dict
        ):

            continue

        code = (
            item.get("證券代號")
            or item.get("公司代號")
            or item.get("Code")
        )

        name = (
            item.get("證券名稱")
            or item.get("公司名稱")
            or item.get("Name")
        )

        code = clean_code(
            code
        )

        if not code:

            continue

        if not name:

            name = code

        etfs.append({

            "code":
                code,

            "name":
                str(name).strip(),

            "market":
                "TWSE",

            "type":
                "ETF"

        })

    print(
        f"TWSE ETF：{len(etfs)}"
    )

    return etfs


# ============================================================
# 取得 TPEx ETF
# ============================================================

def fetch_tpex_etf():

    print(
        ""
    )

    print(
        "取得 TPEx ETF 清單..."
    )

    params = {

        "l": "zh-tw",

        "d":
            datetime.now(
                TW_TZ
            ).strftime(
                "%Y%m%d"
            ),

        "s": "0,asc,0",

        "o": "json"

    }

    try:

        response = requests.get(
            TPEx_ETF_URL,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

    except Exception as error:

        print(
            f"TPEx ETF 清單取得失敗：{error}"
        )

        return []

    etfs = []

    rows = []

    if isinstance(
        data,
        dict
    ):

        if isinstance(
            data.get("aaData"),
            list
        ):

            rows = data.get(
                "aaData"
            )

        elif isinstance(
            data.get("tables"),
            list
        ):

            for table in data["tables"]:

                if not isinstance(
                    table,
                    dict
                ):

                    continue

                table_rows = (
                    table.get("data")
                    or []
                )

                if isinstance(
                    table_rows,
                    list
                ):

                    rows.extend(
                        table_rows
                    )

    for row in rows:

        code = None
        name = None

        if isinstance(
            row,
            dict
        ):

            code = (
                row.get("Code")
                or row.get("代號")
                or row.get("證券代號")
            )

            name = (
                row.get("Name")
                or row.get("名稱")
                or row.get("證券名稱")
            )

        elif isinstance(
            row,
            list
        ):

            if len(row) >= 2:

                code = row[0]
                name = row[1]

        code = clean_code(
            code
        )

        if not code:

            continue

        if not name:

            name = code

        etfs.append({

            "code":
                code,

            "name":
                str(name).strip(),

            "market":
                "TPEX",

            "type":
                "ETF"

        })

    unique = {}

    for item in etfs:

        unique[
            item["code"]
        ] = item

    etfs = list(
        unique.values()
    )

    print(
        f"TPEx ETF：{len(etfs)}"
    )

    return etfs


# ============================================================
# 建立全市場清單
# ============================================================

def build_market_list():

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "開始建立全市場股票清單"
    )

    print(
        "================================================"
    )


    twse_stocks = fetch_twse_stocks()

    tpex_stocks = fetch_tpex_stocks()

    twse_etfs = fetch_twse_etf()

    tpex_etfs = fetch_tpex_etf()


    all_items = []

    all_items.extend(
        twse_stocks
    )

    all_items.extend(
        tpex_stocks
    )

    all_items.extend(
        twse_etfs
    )

    all_items.extend(
        tpex_etfs
    )


    # --------------------------------------------------------
    # 去除重複代號
    # --------------------------------------------------------

    unique = {}

    for item in all_items:

        code = item.get(
            "code"
        )

        if not code:

            continue

        # ----------------------------------------------------
        # 股票與 ETF 若同代號
        # 優先 ETF
        # ----------------------------------------------------

        if code not in unique:

            unique[
                code
            ] = item

        else:

            if item.get(
                "type"
            ) == "ETF":

                unique[
                    code
                ] = item


    market_list = list(
        unique.values()
    )


    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    market_list = sorted(
        market_list,
        key=lambda x: (
            0
            if x.get("type") == "ETF"
            else 1,
            x.get("code", "")
        )
    )


    print(
        ""
    )

    print(
        "全市場清單建立完成"
    )

    print(
        f"上市股票："
        f"{sum(1 for x in market_list if x.get('market') == 'TWSE' and x.get('type') == 'STOCK')}"
    )

    print(
        f"上櫃股票："
        f"{sum(1 for x in market_list if x.get('market') == 'TPEX' and x.get('type') == 'STOCK')}"
    )

    print(
        f"ETF："
        f"{sum(1 for x in market_list if x.get('type') == 'ETF')}"
    )

    print(
        f"全部標的："
        f"{len(market_list)}"
    )


    return market_list


# ============================================================
# 儲存市場清單
# ============================================================

def save_market_list(
    market_list
):

    try:

        output = {

            "version":
                VERSION,

            "updated_at":
                datetime.now(
                    TW_TZ
                ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),

            "total":
                len(market_list),

            "stocks":
                [
                    item
                    for item in market_list
                    if item.get("type")
                    == "STOCK"
                ],

            "etfs":
                [
                    item
                    for item in market_list
                    if item.get("type")
                    == "ETF"
                ],

            "all":
                market_list

        }

        with open(
            MARKET_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"市場清單已寫入："
            f"{MARKET_FILE}"
        )

        return True

    except Exception as error:

        print(
            f"市場清單寫入失敗：{error}"
        )

        return False


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
        (
            100 /
            (1 + rs)
        )
    )

    # 沒有下跌
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
# 下載股票資料
# ============================================================

def download_stock(
    item,
    period="1y"
):

    code = item.get(
        "code"
    )

    market = item.get(
        "market",
        "TWSE"
    )

    symbol = yahoo_symbol(
        code,
        market
    )

    try:

        print(
            f"抓取 {code} "
            f"{item.get('name', '')} "
            f"{symbol}"
        )

        df = yf.download(
            symbol,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if df is None:

            return None

        if df.empty:

            return None


        # ----------------------------------------------------
        # MultiIndex
        # ----------------------------------------------------

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            new_columns = []

            for column in df.columns:

                if isinstance(
                    column,
                    tuple
                ):

                    new_columns.append(
                        str(
                            column[0]
                        )
                    )

                else:

                    new_columns.append(
                        str(column)
                    )

            df.columns = new_columns


        # ----------------------------------------------------
        # 欄位名稱
        # ----------------------------------------------------

        df.columns = [
            str(column)
            .strip()
            .lower()
            for column in df.columns
        ]


        required = [

            "open",

            "high",

            "low",

            "close",

            "volume"

        ]


        for column in required:

            if column not in df.columns:

                print(
                    f"{code}: "
                    f"缺少欄位 {column}"
                )

                return None


        # ----------------------------------------------------
        # 數字化
        # ----------------------------------------------------

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

            print(
                f"{code}: "
                f"歷史資料不足"
            )

            return None


        return df


    except Exception as error:

        print(
            f"{code}: "
            f"下載失敗：{error}"
        )

        return None


# ============================================================
# 分析股票
# ============================================================

def analyze_stock(
    item,
    df
):

    code = item.get(
        "code"
    )

    name = item.get(
        "name",
        code
    )

    market = item.get(
        "market",
        "TWSE"
    )

    stock_type = item.get(
        "type",
        "STOCK"
    )


    try:

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
            close
        )


        k, d = calculate_kd(
            high,
            low,
            close
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
        # 成交量比
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
                    previous_price *
                    100
                )

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
            current_macd is not None
            and
            current_macd_signal is not None
            and
            len(macd) >= 2
            and
            len(macd_signal) >= 2
        ):

            previous_macd = safe_float(
                macd.iloc[-2]
            )

            previous_signal = safe_float(
                macd_signal.iloc[-2]
            )


            if (
                previous_macd is not None
                and
                previous_signal is not None
            ):

                macd_golden_cross = (
                    previous_macd <=
                    previous_signal
                    and
                    current_macd >
                    current_macd_signal
                )


        # ====================================================
        # KD 黃金交叉
        # ====================================================

        kd_golden_cross = False


        if (
            current_k is not None
            and
            current_d is not None
            and
            previous_k is not None
            and
            previous_d is not None
        ):

            kd_golden_cross = (
                previous_k <=
                previous_d
                and
                current_k >
                current_d
            )


        # ====================================================
        # RSI > 50
        # ====================================================

        rsi_above_50 = (
            current_rsi is not None
            and
            current_rsi > 50
        )


        # ====================================================
        # 成交量 > 1.5 倍
        # ====================================================

        volume_over_1_5x = (
            volume_ratio is not None
            and
            volume_ratio >= 1.5
        )


        # ====================================================
        # 股價站上 MA20
        # ====================================================

        above_ma20 = (
            current_price is not None
            and
            current_ma20 is not None
            and
            current_price >
            current_ma20
        )


        # ====================================================
        # MA20 向上
        # ====================================================

        ma20_up = (
            current_ma20 is not None
            and
            previous_ma20 is not None
            and
            current_ma20 >
            previous_ma20
        )


        # ====================================================
        # MACD 柱體正值
        # ====================================================

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
        #
        # MACD 黃金交叉 20
        # KD 黃金交叉 15
        # RSI > 50 15
        # Volume 15
        # MA20 15
        # MA20 UP 10
        # MACD 正值 10
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

        elif (
            current_k is not None
            and
            current_d is not None
            and
            current_k >
            current_d
        ):

            score += 8


        if rsi_above_50:

            score += 15


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


        if above_ma20:

            score += 15


        if ma20_up:

            score += 10


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
        # 四段式 DCA
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


        # ====================================================
        # DCA 動作
        # ====================================================

        if (
            current_price is not None
            and
            current_ma20 is not None
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


        # ====================================================
        # 最終資料
        # ====================================================

        stock = {

            "id":
                str(code),

            "name":
                str(name),

            "symbol":
                str(code),

            "market":
                str(market),

            "type":
                str(stock_type),


            "yahoo_symbol":
                yahoo_symbol(
                    code,
                    market
                ),


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

                "macd_positive":
                    bool(
                        macd_positive
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
            f"{code}: "
            f"分析失敗：{error}"
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


    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

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
                    f"{field} 異常 {value}"
                )

                return False


    # --------------------------------------------------------
    # AI SCORE
    # --------------------------------------------------------

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
# 建立排名
# ============================================================

def build_rankings(
    stocks
):

    # --------------------------------------------------------
    # 全市場 AI 排名
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

        for stock in core_stocks

    ]


    # --------------------------------------------------------
    # DCA 排名
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

        for stock in dca_stocks

    ]


    # --------------------------------------------------------
    # 上市排名
    # --------------------------------------------------------

    twse_stocks = [

        stock

        for stock in ranking_data

        if stock.get(
            "market"
        ) == "TWSE"

        and stock.get(
            "type"
        ) == "STOCK"

    ]


    # --------------------------------------------------------
    # 上櫃排名
    # --------------------------------------------------------

    tpex_stocks = [

        stock

        for stock in ranking_data

        if stock.get(
            "market"
        ) == "TPEX"

        and stock.get(
            "type"
        ) == "STOCK"

    ]


    # --------------------------------------------------------
    # ETF 排名
    # --------------------------------------------------------

    etfs = [

        stock

        for stock in ranking_data

        if stock.get(
            "type"
        ) == "ETF"

    ]


    return {

        "short_term":
            short_term,

        "core":
            core,

        "dca":
            dca,

        "twse":
            [
                str(
                    stock["id"]
                )
                for stock in twse_stocks
            ],

        "tpex":
            [
                str(
                    stock["id"]
                )
                for stock in tpex_stocks
            ],

        "etf":
            [
                str(
                    stock["id"]
                )
                for stock in etfs
            ]

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


    stock_count = sum(

        1

        for stock in stocks

        if stock.get(
            "type"
        ) == "STOCK"

    )


    etf_count = sum(

        1

        for stock in stocks

        if stock.get(
            "type"
        ) == "ETF"

    )


    twse_count = sum(

        1

        for stock in stocks

        if stock.get(
            "market"
        ) == "TWSE"

        and stock.get(
            "type"
        ) == "STOCK"

    )


    tpex_count = sum(

        1

        for stock in stocks

        if stock.get(
            "market"
        ) == "TPEX"

        and stock.get(
            "type"
        ) == "STOCK"

    )


    core_stocks = sum(

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


    macd_golden = sum(

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


    rsi_above_50 = sum(

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


    kd_golden = sum(

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


    volume_over_1_5x = sum(

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


    strong = sum(

        1

        for stock in stocks

        if stock.get(
            "short_term",
            {}
        ).get(
            "score",
            0
        ) >= 70

    )


    return {

        "total_stocks":
            total_stocks,

        "stock_count":
            stock_count,

        "etf_count":
            etf_count,

        "twse_count":
            twse_count,

        "tpex_count":
            tpex_count,

        "core_stocks":
            core_stocks,

        "strong_stocks":
            strong,

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
            ma20_up

    }


# ============================================================
# 儲存 prices.json
# ============================================================

def save_prices_json(
    data
):

    try:

        with open(
            OUTPUT_FILE,
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
            ""
        )

        print(
            f"資料已寫入："
            f"{OUTPUT_FILE}"
        )


        return True


    except Exception as error:

        print(
            f"JSON 寫入失敗：{error}"
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
        f"台股 AI 全市場掃描系統 "
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
    # 第一階段：取得全市場清單
    # ========================================================

    market_list = build_market_list()


    if len(market_list) == 0:

        print(
            ""
        )

        print(
            "錯誤：無法取得全市場股票清單。"
        )

        sys.exit(1)


    save_market_list(
        market_list
    )


    # ========================================================
    # 第二階段：逐檔分析
    # ========================================================

    stocks = []

    failed = []


    total = len(
        market_list
    )


    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        f"開始 AI 掃描全市場 "
        f"共 {total} 檔"
    )

    print(
        "================================================"
    )


    for index, item in enumerate(
        market_list,
        start=1
    ):

        code = item.get(
            "code"
        )

        name = item.get(
            "name",
            code
        )


        print(
            ""
        )

        print(
            f"[{index}/{total}] "
            f"{code} {name}"
        )


        # ----------------------------------------------------
        # 下載
        # ----------------------------------------------------

        df = download_stock(
            item
        )


        if df is None:

            failed.append(
                code
            )

            continue


        # ----------------------------------------------------
        # 分析
        # ----------------------------------------------------

        stock = analyze_stock(
            item,
            df
        )


        if stock is None:

            failed.append(
                code
            )

            continue


        # ----------------------------------------------------
        # 驗證
        # ----------------------------------------------------

        if not validate_stock(
            stock
        ):

            failed.append(
                code
            )

            continue


        stocks.append(
            stock
        )


        print(
            f"OK | "
            f"價格={stock['price']['close']} | "
            f"RSI={stock['technical']['rsi']} | "
            f"AI={stock['short_term']['score']} | "
            f"{stock['short_term']['signal']}"
        )


        # ----------------------------------------------------
        # 降低請求速度
        # ----------------------------------------------------

        time.sleep(
            0.15
        )


    # ========================================================
    # 完全沒有資料
    # ========================================================

    if len(stocks) == 0:

        print(
            ""
        )

        print(
            "錯誤：沒有任何股票成功取得資料。"
        )

        sys.exit(1)


    # ========================================================
    # 排名
    # ========================================================

    rankings = build_rankings(
        stocks
    )


    # ========================================================
    # 統計
    # ========================================================

    statistics = build_statistics(
        stocks
    )


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

        "scan_mode":
            "FULL_MARKET",

        "source":
            "Yahoo Finance",

        "market_source":
            "TWSE / TPEx",

        "stocks":
            stocks,

        "rankings":
            rankings,

        "statistics":
            statistics,

        "scan": {

            "total_market":
                len(market_list),

            "success":
                len(stocks),

            "failed":
                len(failed),

            "failed_codes":
                failed

        }

    }


    # ========================================================
    # 儲存
    # ========================================================

    success = save_prices_json(
        output
    )


    if not success:

        sys.exit(1)


    # ========================================================
    # 最終統計
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
        "全市場 AI 掃描完成"
    )

    print(
        "================================================"
    )

    print(
        f"市場清單："
        f"{len(market_list)} 檔"
    )

    print(
        f"成功分析："
        f"{len(stocks)} 檔"
    )

    print(
        f"失敗："
        f"{len(failed)} 檔"
    )

    print(
        ""
    )

    print(
        f"上市股票："
        f"{statistics['twse_count']} 檔"
    )

    print(
        f"上櫃股票："
        f"{statistics['tpex_count']} 檔"
    )

    print(
        f"ETF："
        f"{statistics['etf_count']} 檔"
    )

    print(
        ""
    )

    print(
        f"AI ≥ 70："
        f"{statistics['strong_stocks']} 檔"
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
