# ============================================================
# 台股 AI 選股・全市場掃描・零股定投・動態風控
# fetch_data.py V6.2 FULL MARKET FINAL
#
# 功能：
# 1. 自動取得台股上市股票
# 2. 自動取得台股上櫃股票
# 3. 自動取得台股 ETF
# 4. 不再使用固定 STOCK_LIST
# 5. 全市場自動掃描
# 6. 計算 RSI / KD / MACD / MA5 / MA20 / MA60
# 7. 計算成交量比
# 8. 計算 AI SCORE
# 9. 計算核心訊號
# 10. 計算四段式定投價格
# 11. 建立全市場排名
# 12. 建立統計資料
# 13. RSI 強制限制 0~100
# 14. KD 強制限制 0~100
# 15. 單一股票失敗不影響其他股票
# 16. 保留 index.html V6.2 JSON 結構
#
# 資料來源：
# - TWSE
# - TPEx
# - Yahoo Finance
#
# 輸出：
# Data/prices.json
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
        "Chrome/120.0 Safari/537.36"
})


# ============================================================
# Yahoo Finance ticker
# ============================================================

def yahoo_symbol(
    code,
    market="TW"
):

    code = str(code).strip()

    if market == "TWO":
        return code + ".TWO"

    return code + ".TW"


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

    number = safe_float(value)

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

    code = str(code).strip()

    if not code:
        return None

    # 台股一般股票 / ETF
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

    code = str(code)

    # 台灣 ETF 大多為 00 開頭
    return code.startswith("00")


# ============================================================
# 取得上市股票清單
#
# TWSE 公開資訊
# ============================================================

def get_twse_stock_list():

    print("")
    print("================================================")
    print("取得上市股票清單")
    print("================================================")

    url = (
        "https://openapi.twse.com.tw/"
        "v1/opendata/t187ap03_L"
    )

    try:

        response = SESSION.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        stocks = []

        if not isinstance(
            data,
            list
        ):
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
            )

            name = (
                item.get("公司簡稱")
                or item.get("CompanyName")
                or item.get("公司名稱")
            )

            code = clean_code(
                code
            )

            if not code:
                continue

            if not name:
                name = code

            stocks.append({

                "id": code,

                "name": str(name).strip(),

                "market": "TWSE",

                "type": (
                    "ETF"
                    if is_etf_code(code)
                    else "STOCK"
                )

            })

        print(
            f"上市市場取得：{len(stocks)} 檔"
        )

        return stocks

    except Exception as error:

        print(
            f"上市清單取得失敗：{error}"
        )

        return []


# ============================================================
# 取得上櫃股票清單
#
# TPEx 公開資訊
# ============================================================

def get_tpex_stock_list():

    print("")
    print("================================================")
    print("取得上櫃股票清單")
    print("================================================")

    url = (
        "https://www.tpex.org.tw/"
        "web/stock/aftertrading/"
        "daily_close_quotes/"
        "st43.php?l=zh-tw&o=json"
    )

    try:

        response = SESSION.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        stocks = []

        # ----------------------------------------------------
        # TPEx JSON 結構可能不同
        # ----------------------------------------------------

        tables = []

        if isinstance(
            data,
            dict
        ):

            tables = data.get(
                "tables",
                []
            )

        for table in tables:

            if not isinstance(
                table,
                dict
            ):
                continue

            fields = table.get(
                "fields",
                []
            )

            rows = table.get(
                "data",
                []
            )

            if not fields:
                continue

            for row in rows:

                if not row:
                    continue

                row_data = dict(
                    zip(
                        fields,
                        row
                    )
                )

                code = (
                    row_data.get("代號")
                    or row_data.get("證券代號")
                )

                name = (
                    row_data.get("名稱")
                    or row_data.get("證券名稱")
                )

                code = clean_code(
                    code
                )

                if not code:
                    continue

                if not name:
                    name = code

                stocks.append({

                    "id": code,

                    "name": str(name).strip(),

                    "market": "TPEX",

                    "type": (
                        "ETF"
                        if is_etf_code(code)
                        else "STOCK"
                    )

                })

        # ----------------------------------------------------
        # 去除重複
        # ----------------------------------------------------

        unique = {}

        for stock in stocks:

            unique[
                stock["id"]
            ] = stock

        stocks = list(
            unique.values()
        )

        print(
            f"上櫃市場取得：{len(stocks)} 檔"
        )

        return stocks

    except Exception as error:

        print(
            f"上櫃清單取得失敗：{error}"
        )

        return []


# ============================================================
# 取得 ETF 清單
#
# ETF 主要透過 TWSE / TPEx 清單中
# 00 開頭代號自動分類。
# ============================================================

def split_market_list(
    twse,
    tpex
):

    stocks = []
    etfs = []

    for item in (
        twse + tpex
    ):

        code = item.get(
            "id"
        )

        if not code:
            continue

        if is_etf_code(code):

            etfs.append(item)

        else:

            stocks.append(item)

    return stocks, etfs


# ============================================================
# 建立全市場清單
# ============================================================

def build_market_list():

    twse = get_twse_stock_list()

    tpex = get_tpex_stock_list()

    stocks, etfs = split_market_list(
        twse,
        tpex
    )

    # --------------------------------------------------------
    # 所有清單
    # --------------------------------------------------------

    all_items = (
        stocks +
        etfs
    )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in all_items:

        code = item.get(
            "id"
        )

        if not code:
            continue

        unique[code] = item

    all_items = list(
        unique.values()
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    all_items.sort(
        key=lambda x: x["id"]
    )

    # --------------------------------------------------------
    # 顯示
    # --------------------------------------------------------

    twse_count = sum(
        1
        for x in all_items
        if x.get("market") == "TWSE"
    )

    tpex_count = sum(
        1
        for x in all_items
        if x.get("market") == "TPEX"
    )

    etf_count = sum(
        1
        for x in all_items
        if x.get("type") == "ETF"
    )

    stock_count = sum(
        1
        for x in all_items
        if x.get("type") == "STOCK"
    )

    print("")
    print("================================================")
    print("全市場清單完成")
    print("================================================")

    print(
        f"市場清單：{len(all_items)} 檔"
    )

    print(
        f"上市市場：{twse_count} 檔"
    )

    print(
        f"上櫃市場：{tpex_count} 檔"
    )

    print(
        f"一般股票：{stock_count} 檔"
    )

    print(
        f"ETF：{etf_count} 檔"
    )

    return all_items


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
    # Wilder RSI
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
        100 / (1 + rs)
    )

    # 沒有跌幅
    rsi = rsi.where(
        avg_loss != 0,
        100
    )

    # --------------------------------------------------------
    # 最重要防呆
    # RSI 絕對只能 0~100
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
        ) /
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
    code,
    market
):

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
        # 小寫欄位
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

        # ----------------------------------------------------
        # 技術指標至少需要歷史資料
        # ----------------------------------------------------

        if len(df) < 35:

            return None

        return df

    except Exception:

        return None


# ============================================================
# 分析單一股票
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

        close = df["close"]

        high = df["high"]

        low = df["low"]

        volume = df["volume"]

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
            close
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
        # 防呆
        # ====================================================

        if current_rsi is not None:

            current_rsi = max(
                0,
                min(
                    100,
                    current_rsi
                )
            )

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
            and current_volume_ma5 is not None
            and current_volume_ma5 > 0
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
            and previous_price is not None
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
        # 前一天
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

        previous_signal = (
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
            and previous_signal is not None
            and current_macd is not None
            and current_macd_signal is not None
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

        # ====================================================
        # RSI > 50
        # ====================================================

        rsi_above_50 = (
            current_rsi is not None
            and current_rsi > 50
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
            and current_ma20 is not None
            and
            current_price >
            current_ma20
        )

        # ====================================================
        # MA20 向上
        # ====================================================

        ma20_up = (
            current_ma20 is not None
            and previous_ma20 is not None
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
        # MACD 黃金交叉：20
        # KD 黃金交叉：15
        # RSI > 50：15
        # Volume：15
        # MA20：15
        # MA20 UP：10
        # MACD 正柱：10
        #
        # 總分：100
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
        # DCA 行動
        # ====================================================

        if (
            current_price is not None
            and current_ma20 is not None
            and current_ma20 > 0
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
        # 最終 JSON
        # ====================================================

        stock = {

            "id": str(code),

            "name": str(name),

            "symbol": str(code),

            "type": str(stock_type),

            "market": str(market),

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
            f"{code}: 分析失敗：{error}"
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
                f"{stock_id}: RSI 異常"
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
                    f"{field} 異常"
                )

                return False

    # --------------------------------------------------------
    # AI Score
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
    # 核心
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

    core_stocks.sort(

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

    dca_stocks.sort(
        key=dca_score,
        reverse=True
    )

    dca = [

        str(
            stock["id"]
        )

        for stock in dca_stocks

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
# 統計
# ============================================================

def build_statistics(
    stocks,
    market_list
):

    total_market = len(
        market_list
    )

    total_success = len(
        stocks
    )

    failed = (
        total_market -
        total_success
    )

    twse_count = sum(

        1

        for stock in stocks

        if stock.get(
            "market"
        ) == "TWSE"

    )

    tpex_count = sum(

        1

        for stock in stocks

        if stock.get(
            "market"
        ) == "TPEX"

    )

    etf_count = sum(

        1

        for stock in stocks

        if stock.get(
            "type"
        ) == "ETF"

    )

    ai_70 = sum(

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

    return {

        "total_market":
            total_market,

        "total_stocks":
            total_success,

        "successful":
            total_success,

        "failed":
            failed,

        "listed_stocks":
            twse_count,

        "otc_stocks":
            tpex_count,

        "etf":
            etf_count,

        "ai_70":
            ai_70,

        "core_stocks":
            core,

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
                indent=2
            )

        os.replace(
            temp_file,
            OUTPUT_FILE
        )

        print("")
        print(
            f"資料已寫入：{OUTPUT_FILE}"
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

    print("")
    print("================================================")
    print(
        "台股 AI 全市場掃描系統"
    )
    print(
        f"fetch_data.py {VERSION}"
    )
    print(
        f"開始時間："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    print("================================================")

    # ========================================================
    # 1. 自動取得全市場清單
    # ========================================================

    market_list = build_market_list()

    if not market_list:

        print(
            "錯誤：無法取得台股市場清單"
        )

        sys.exit(1)

    # ========================================================
    # 2. 開始逐檔分析
    # ========================================================

    stocks = []

    failed = []

    total = len(
        market_list
    )

    print("")
    print("================================================")
    print(
        f"開始分析全市場：{total} 檔"
    )
    print("================================================")

    for index, item in enumerate(
        market_list,
        start=1
    ):

        code = item["id"]

        name = item["name"]

        market = item["market"]

        print(
            f"[{index}/{total}] "
            f"{code} {name}",
            end=" "
        )

        try:

            df = download_stock(
                code,
                market
            )

            if df is None:

                failed.append(
                    code
                )

                print(
                    "FAIL"
                )

                continue

            stock = analyze_stock(
                item,
                df
            )

            if stock is None:

                failed.append(
                    code
                )

                print(
                    "FAIL"
                )

                continue

            if not validate_stock(
                stock
            ):

                failed.append(
                    code
                )

                print(
                    "INVALID"
                )

                continue

            stocks.append(
                stock
            )

            print(
                f"OK "
                f"價格={stock['price']['close']} "
                f"RSI={stock['technical']['rsi']} "
                f"AI={stock['short_term']['score']}"
            )

        except Exception as error:

            failed.append(
                code
            )

            print(
                f"ERROR: {error}"
            )

        # ----------------------------------------------------
        # API 節流
        # ----------------------------------------------------

        time.sleep(
            0.20
        )

    # ========================================================
    # 3. 完全沒有資料
    # ========================================================

    if not stocks:

        print("")
        print(
            "錯誤：沒有任何股票成功分析"
        )

        sys.exit(1)

    # ========================================================
    # 4. 排名
    # ========================================================

    rankings = build_rankings(
        stocks
    )

    # ========================================================
    # 5. 統計
    # ========================================================

    statistics = build_statistics(
        stocks,
        market_list
    )

    # ========================================================
    # 6. 最終 JSON
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
            "TWSE + TPEx + Yahoo Finance",

        "scan_mode":
            "FULL_MARKET",

        "market_list_count":
            len(market_list),

        "failed_count":
            len(failed),

        "failed_symbols":
            failed,

        "stocks":
            stocks,

        "rankings":
            rankings,

        "statistics":
            statistics

    }

    # ========================================================
    # 7. 寫入
    # ========================================================

    success = save_json(
        output
    )

    if not success:

        sys.exit(1)

    # ========================================================
    # 8. 最終報告
    # ========================================================

    elapsed = (
        time.time() -
        start_time
    )

    print("")
    print("================================================")
    print("全市場 AI 掃描完成")
    print("================================================")

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

    print("")

    print(
        f"上市股票："
        f"{statistics['listed_stocks']} 檔"
    )

    print(
        f"上櫃股票："
        f"{statistics['otc_stocks']} 檔"
    )

    print(
        f"ETF："
        f"{statistics['etf']} 檔"
    )

    print("")

    print(
        f"AI ≥ 70："
        f"{statistics['ai_70']} 檔"
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

    print("================================================")


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    main()
