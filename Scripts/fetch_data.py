# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V6.1 正式修正版
#
# 功能：
# 1. 抓取台股上市 / 上櫃 / ETF
# 2. 計算 RSI / KD / MACD / MA20 / MA5
# 3. 計算成交量比
# 4. 計算短線 AI SCORE
# 5. 計算核心訊號
# 6. 計算四段式定投價格
# 7. 產生 rankings / statistics
# 8. 保持 index.html V6 所需 JSON 結構
# 9. RSI 強制限制 0~100
# 10. 單一股票抓取失敗不影響其他股票
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
import yfinance as yf


# ============================================================
# 基本設定
# ============================================================

VERSION = "V6.1"

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

os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# 台灣時區
# ============================================================

TW_TZ = timezone(
    timedelta(hours=8)
)


# ============================================================
# 股票清單
#
# 保留你的系統核心股票。
# 如果你之後要增加股票，只需要在這裡增加代號。
# ============================================================

STOCK_LIST = [

    # --------------------------------------------------------
    # 原有追蹤個股
    # --------------------------------------------------------

    ("2330", "台積電"),
    ("2454", "聯發科"),
    ("2303", "聯電"),
    ("2317", "鴻海"),
    ("2382", "廣達"),
    ("3231", "緯創"),
    ("2376", "技嘉"),
    ("2357", "華碩"),
    ("2344", "華邦電"),
    ("2337", "旺宏"),
    ("2426", "鼎元"),
    ("3490", "單井"),
    ("3680", "家登"),

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    ("00713", "元大台灣高息低波"),

]


# ============================================================
# Yahoo Finance ticker
# ============================================================

def yahoo_symbol(code):
    """
    台股 Yahoo Finance：
    2330 -> 2330.TW
    """

    code = str(code).strip()

    return code + ".TW"


# ============================================================
# 安全數字轉換
# ============================================================

def safe_float(value, default=None):

    try:

        if value is None:
            return default

        if isinstance(value, (list, tuple, dict)):
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

def round_value(value, digits=2):

    number = safe_float(value)

    if number is None:
        return None

    return round(number, digits)


# ============================================================
# RSI
# ============================================================

def calculate_rsi(close, period=14):

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

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    # 如果沒有跌幅，RSI 視為 100
    rsi = rsi.where(
        avg_loss != 0,
        100
    )

    # 強制限制 RSI 0~100
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
        (close - lowest_low) /
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
# 取得最新價格
# ============================================================

def get_last_value(series):

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
    period="1y"
):

    symbol = yahoo_symbol(code)

    try:

        print(
            f"抓取 {code} {symbol} ..."
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
        # 修正 Yahoo Finance MultiIndex
        # ----------------------------------------------------

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            try:

                df.columns = [
                    column[0]
                    for column in df.columns
                ]

            except Exception:

                df.columns = [
                    str(column[0])
                    for column in df.columns
                ]

        # ----------------------------------------------------
        # 統一欄位名稱
        # ----------------------------------------------------

        df.columns = [
            str(column).strip().lower()
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
                    f"{code}: 缺少欄位 {column}"
                )

                return None

        # ----------------------------------------------------
        # 強制數字化
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
                f"{code}: 歷史資料不足"
            )

            return None

        return df

    except Exception as error:

        print(
            f"{code}: 下載失敗：{error}"
        )

        return None


# ============================================================
# 分析股票
# ============================================================

def analyze_stock(
    code,
    name,
    df
):

    try:

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # ----------------------------------------------------
        # 技術指標
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

        rsi = calculate_rsi(
            close,
            14
        )

        k, d = calculate_kd(
            high,
            low,
            close
        )

        macd, macd_signal, macd_hist = (
            calculate_macd(
                close
            )
        )

        volume_ma5 = volume.rolling(
            5
        ).mean()

        # ----------------------------------------------------
        # 最新值
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

        # ----------------------------------------------------
        # RSI 防呆
        #
        # 這裡就是針對之前「RSI = 560」的核心修正。
        # RSI 絕對不能超過 100。
        # ----------------------------------------------------

        if current_rsi is not None:

            current_rsi = max(
                0,
                min(
                    100,
                    current_rsi
                )
            )

        # ----------------------------------------------------
        # KD 防呆
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 成交量比
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 漲跌
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 前一日指標
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

        previous_macd_hist = (
            safe_float(
                macd_hist.iloc[-2]
            )
            if len(macd_hist) >= 2
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
        # MACD 黃金交叉
        # ----------------------------------------------------

        macd_golden_cross = False

        if (
            current_macd is not None
            and current_macd_signal is not None
            and len(macd) >= 2
            and len(macd_signal) >= 2
        ):

            previous_macd = safe_float(
                macd.iloc[-2]
            )

            previous_signal = safe_float(
                macd_signal.iloc[-2]
            )

            if (
                previous_macd is not None
                and previous_signal is not None
            ):

                macd_golden_cross = (
                    previous_macd <=
                    previous_signal
                    and
                    current_macd >
                    current_macd_signal
                )

        # ----------------------------------------------------
        # KD 黃金交叉
        # ----------------------------------------------------

        kd_golden_cross = False

        if (
            current_k is not None
            and current_d is not None
            and previous_k is not None
            and previous_d is not None
        ):

            kd_golden_cross = (
                previous_k <= previous_d
                and
                current_k > current_d
            )

        # ----------------------------------------------------
        # RSI > 50
        # ----------------------------------------------------

        rsi_above_50 = (
            current_rsi is not None
            and current_rsi > 50
        )

        # ----------------------------------------------------
        # 成交量 > 5日均量 × 1.5
        # ----------------------------------------------------

        volume_over_1_5x = (
            volume_ratio is not None
            and volume_ratio >= 1.5
        )

        # ----------------------------------------------------
        # 股價站上 MA20
        # ----------------------------------------------------

        above_ma20 = (
            current_price is not None
            and current_ma20 is not None
            and current_price > current_ma20
        )

        # ----------------------------------------------------
        # MA20 向上
        # ----------------------------------------------------

        ma20_up = (
            current_ma20 is not None
            and previous_ma20 is not None
            and current_ma20 > previous_ma20
        )

        # ----------------------------------------------------
        # MACD 柱體正值
        # ----------------------------------------------------

        macd_positive = (
            current_macd_hist is not None
            and current_macd_hist > 0
        )

        # ----------------------------------------------------
        # 短線核心
        #
        # MACD + KD + RSI + Volume + MA20 + MA20 UP
        # ----------------------------------------------------

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
        # 100分制
        #
        # MACD 20
        # KD 15
        # RSI 15
        # Volume 15
        # MA20 15
        # MA20 UP 10
        # MACD hist 10
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
            and current_d is not None
            and current_k > current_d
        ):
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

        score = max(
            0,
            min(
                100,
                int(score)
            )
        )

        # ----------------------------------------------------
        # 訊號
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # DCA 建議
        # ----------------------------------------------------

        if (
            current_price is not None
            and current_ma20 is not None
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
        # ETF 判斷
        # ====================================================

        is_etf = (
            str(code).startswith("00")
        )

        stock_type = (
            "ETF"
            if is_etf
            else "STOCK"
        )

        # ====================================================
        # 最終 JSON
        # ====================================================

        stock = {

            "id": str(code),

            "name": str(name),

            "symbol": str(code),

            "type": stock_type,

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
                    bool(macd_golden_cross),

                "kd_golden_cross":
                    bool(kd_golden_cross),

                "rsi_above_50":
                    bool(rsi_above_50),

                "volume_over_1_5x":
                    bool(volume_over_1_5x),

                "above_ma20":
                    bool(above_ma20),

                "ma20_up":
                    bool(ma20_up),

                "short_term_core":
                    bool(short_term_core)

            },

            "short_term": {

                "score": int(score),

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
            f"{code}: 分析失敗：{error}"
        )

        traceback.print_exc()

        return None


# ============================================================
# 建立排名
# ============================================================

def build_rankings(stocks):

    # --------------------------------------------------------
    # 短線排名
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
        str(stock["id"])
        for stock in ranking_data
    ]

    # --------------------------------------------------------
    # 核心訊號優先
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
        key=lambda stock: stock.get(
            "short_term",
            {}
        ).get(
            "score",
            0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # DCA 排名
    #
    # MA20 有效才進入排名
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

    def dca_score(stock):

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
                price / ma20 - 1
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
        str(stock["id"])
        for stock in dca_stocks
    ]

    return {

        "short_term":
            short_term,

        "core":
            [
                str(stock["id"])
                for stock in core_stocks
            ],

        "dca":
            dca

    }


# ============================================================
# 統計
# ============================================================

def build_statistics(stocks):

    total_stocks = len(
        stocks
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
            above_ma20

    }


# ============================================================
# 驗證資料
# ============================================================

def validate_stock(stock):

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
    # RSI 必須 0~100
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
            or rsi < 0
            or rsi > 100
        ):

            print(
                f"{stock_id}: RSI 異常 {rsi}"
            )

            return False

    # --------------------------------------------------------
    # KD 必須 0~100
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
                or value < 0
                or value > 100
            ):

                print(
                    f"{stock_id}: "
                    f"{field} 異常 {value}"
                )

                return False

    return True


# ============================================================
# 儲存 JSON
# ============================================================

def save_json(data):

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

    print(
        "================================================"
    )

    print(
        f"台股 AI 選股系統 fetch_data.py {VERSION}"
    )

    print(
        f"開始時間："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        "================================================"
    )

    stocks = []

    failed = []

    # --------------------------------------------------------
    # 逐檔抓取
    # --------------------------------------------------------

    for code, name in STOCK_LIST:

        print(
            ""
        )

        df = download_stock(
            code
        )

        if df is None:

            failed.append(
                code
            )

            continue

        stock = analyze_stock(
            code,
            name,
            df
        )

        if stock is None:

            failed.append(
                code
            )

            continue

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
            f"{code} {name} "
            f"OK | "
            f"價格={stock['price']['close']} | "
            f"RSI={stock['technical']['rsi']} | "
            f"AI={stock['short_term']['score']}"
        )

        # 避免 API 請求過快
        time.sleep(
            0.3
        )

    # --------------------------------------------------------
    # 如果完全沒有資料
    # --------------------------------------------------------

    if len(stocks) == 0:

        print(
            ""
        )

        print(
            "錯誤：沒有任何股票成功取得資料。"
        )

        sys.exit(1)

    # --------------------------------------------------------
    # 排名
    # --------------------------------------------------------

    rankings = build_rankings(
        stocks
    )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    statistics = build_statistics(
        stocks
    )

    # --------------------------------------------------------
    # 最終 JSON
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 儲存
    # --------------------------------------------------------

    success = save_json(
        output
    )

    if not success:

        sys.exit(1)

    # --------------------------------------------------------
    # 統計輸出
    # --------------------------------------------------------

    elapsed = (
        time.time() -
        start_time
    )

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "抓取完成"
    )

    print(
        f"成功：{len(stocks)} 檔"
    )

    print(
        f"失敗：{len(failed)} 檔"
    )

    if failed:

        print(
            "失敗代號："
            +
            ", ".join(failed)
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
        f"耗時：{elapsed:.2f} 秒"
    )

    print(
        "================================================"
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    main()
