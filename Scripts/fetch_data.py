# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V6 正式修正版
#
# V6 修正重點
# ------------------------------------------------------------
# 1. 修正 RSI 統計錯誤
#    statistics.rsi_above_50 僅代表：
#    「RSI > 50 的股票數量」
#
# 2. RSI 數值獨立儲存在：
#    stock["technical"]["rsi"]
#
# 3. 不再把 RSI 數值、資料筆數、其他欄位
#    誤寫到 statistics.rsi_above_50
#
# 4. 保留 index.html V5/V6 使用的 JSON 結構
#
# 5. 支援個股 + ETF
#
# 6. 技術指標：
#    RSI
#    KD
#    MACD
#    MA5
#    MA20
#    MA60
#    成交量
#    5日均量
#    Volume Ratio
#
# 7. 核心條件：
#    MACD 黃金交叉
#    KD 黃金交叉
#    RSI > 50
#    成交量 > 5日均量 × 1.5
#    股價站上20MA
#    20MA向上
#
# 8. AI SCORE
#    0～100
#
# 9. DCA 四段式：
#    MA20
#    MA20 - 3%
#    MA20 - 6%
#    MA20 - 10%
#
# 10. GitHub Actions 可直接執行
# ============================================================

import os
import json
import math
import time
import warnings
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import yfinance as yf


warnings.filterwarnings("ignore")


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


# ============================================================
# 台灣時區
# ============================================================

TAIPEI_TZ = timezone(
    timedelta(hours=8)
)


# ============================================================
# 股票清單
#
# 這裡保留你目前系統的核心追蹤標的。
# 未來可以直接增加。
# ============================================================

STOCKS = [

    # --------------------------------------------------------
    # 台股個股
    # --------------------------------------------------------

    {
        "id": "2303",
        "name": "聯電",
        "type": "STOCK"
    },

    {
        "id": "2330",
        "name": "台積電",
        "type": "STOCK"
    },

    {
        "id": "2344",
        "name": "華邦電",
        "type": "STOCK"
    },

    {
        "id": "2408",
        "name": "南亞科",
        "type": "STOCK"
    },

    {
        "id": "2337",
        "name": "旺宏",
        "type": "STOCK"
    },

    {
        "id": "2426",
        "name": "鼎元",
        "type": "STOCK"
    },

    {
        "id": "2498",
        "name": "宏達電",
        "type": "STOCK"
    },

    {
        "id": "3006",
        "name": "晶豪科",
        "type": "STOCK"
    },

    {
        "id": "3035",
        "name": "智原",
        "type": "STOCK"
    },

    {
        "id": "3481",
        "name": "群創",
        "type": "STOCK"
    },

    {
        "id": "3545",
        "name": "敦泰",
        "type": "STOCK"
    },

    {
        "id": "4906",
        "name": "正文",
        "type": "STOCK"
    },

    {
        "id": "5388",
        "name": "中磊",
        "type": "STOCK"
    },

    {
        "id": "6147",
        "name": "頎邦",
        "type": "STOCK"
    },

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    {
        "id": "00713",
        "name": "元大台灣高息低波",
        "type": "ETF"
    }

]


# ============================================================
# yfinance ticker
# ============================================================

def get_ticker(stock_id):

    return f"{stock_id}.TW"


# ============================================================
# 安全數字
# ============================================================

def safe_float(value, digits=4):

    try:

        if value is None:
            return None

        value = float(value)

        if not math.isfinite(value):
            return None

        return round(
            value,
            digits
        )

    except Exception:

        return None


# ============================================================
# RSI
# Wilder RSI
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

    rs = avg_gain / avg_loss

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ============================================================
# MACD
# ============================================================

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

    macd = ema12 - ema26

    signal = macd.ewm(
        span=9,
        adjust=False
    ).mean()

    histogram = macd - signal

    return (
        macd,
        signal,
        histogram
    )


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

    k = rsv.ewm(
        com=2,
        adjust=False
    ).mean()

    d = k.ewm(
        com=2,
        adjust=False
    ).mean()

    return (
        k,
        d
    )


# ============================================================
# 取得歷史資料
# ============================================================

def download_data(
    ticker
):

    try:

        df = yf.download(
            ticker,
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
        # yfinance 新版可能回傳 MultiIndex
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

        required = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        for column in required:

            if column not in df.columns:
                return None

        df = df[
            required
        ].copy()

        df = df.dropna(
            subset=["Close"]
        )

        if len(df) < 70:

            return None

        return df

    except Exception as error:

        print(
            f"[ERROR] 下載資料失敗 {ticker}: {error}"
        )

        return None


# ============================================================
# 技術指標
# ============================================================

def calculate_indicators(
    df
):

    data = df.copy()

    close = data["Close"]
    high = data["High"]
    low = data["Low"]
    volume = data["Volume"]

    # --------------------------------------------------------
    # MA
    # --------------------------------------------------------

    data["MA5"] = close.rolling(
        5
    ).mean()

    data["MA20"] = close.rolling(
        20
    ).mean()

    data["MA60"] = close.rolling(
        60
    ).mean()

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    data["RSI"] = calculate_rsi(
        close,
        14
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    (
        data["MACD"],
        data["MACD_SIGNAL"],
        data["MACD_HIST"]
    ) = calculate_macd(
        close
    )

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    (
        data["K"],
        data["D"]
    ) = calculate_kd(
        high,
        low,
        close
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    data["VOL_MA5"] = volume.rolling(
        5
    ).mean()

    data["VOLUME_RATIO"] = (
        volume /
        data["VOL_MA5"]
    )

    return data


# ============================================================
# 判斷 MACD 黃金交叉
# ============================================================

def macd_golden_cross(
    data
):

    if len(data) < 2:
        return False

    previous = data.iloc[-2]
    current = data.iloc[-1]

    macd_prev = safe_float(
        previous["MACD"]
    )

    signal_prev = safe_float(
        previous["MACD_SIGNAL"]
    )

    macd_now = safe_float(
        current["MACD"]
    )

    signal_now = safe_float(
        current["MACD_SIGNAL"]
    )

    if any(
        value is None
        for value in [
            macd_prev,
            signal_prev,
            macd_now,
            signal_now
        ]
    ):

        return False

    return (
        macd_prev <= signal_prev
        and
        macd_now > signal_now
    )


# ============================================================
# KD 黃金交叉
# ============================================================

def kd_golden_cross(
    data
):

    if len(data) < 2:
        return False

    previous = data.iloc[-2]
    current = data.iloc[-1]

    k_prev = safe_float(
        previous["K"]
    )

    d_prev = safe_float(
        previous["D"]
    )

    k_now = safe_float(
        current["K"]
    )

    d_now = safe_float(
        current["D"]
    )

    if any(
        value is None
        for value in [
            k_prev,
            d_prev,
            k_now,
            d_now
        ]
    ):

        return False

    return (
        k_prev <= d_prev
        and
        k_now > d_now
    )


# ============================================================
# MA20 向上
# ============================================================

def ma20_is_up(
    data
):

    if len(data) < 2:
        return False

    current = safe_float(
        data.iloc[-1]["MA20"]
    )

    previous = safe_float(
        data.iloc[-2]["MA20"]
    )

    if (
        current is None
        or
        previous is None
    ):

        return False

    return current > previous


# ============================================================
# 建立 DCA
# ============================================================

def calculate_dca(
    price,
    ma20
):

    if (
        price is None
        or
        ma20 is None
        or
        ma20 <= 0
    ):

        return {

            "buy_1": None,
            "buy_2": None,
            "buy_3": None,
            "buy_4": None,
            "action": "資料不足"

        }

    buy_1 = ma20

    buy_2 = ma20 * 0.97

    buy_3 = ma20 * 0.94

    buy_4 = ma20 * 0.90

    # --------------------------------------------------------
    # DCA 建議
    # --------------------------------------------------------

    if price <= buy_4:

        action = "第四批區域"

    elif price <= buy_3:

        action = "第三批區域"

    elif price <= buy_2:

        action = "第二批區域"

    elif price <= buy_1:

        action = "第一批區域"

    else:

        action = "等待回檔"

    return {

        "buy_1": safe_float(
            buy_1,
            2
        ),

        "buy_2": safe_float(
            buy_2,
            2
        ),

        "buy_3": safe_float(
            buy_3,
            2
        ),

        "buy_4": safe_float(
            buy_4,
            2
        ),

        "action": action

    }


# ============================================================
# AI Score
# ============================================================

def calculate_score(
    conditions
):

    score = 0

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    if conditions[
        "macd_golden_cross"
    ]:

        score += 20

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    if conditions[
        "kd_golden_cross"
    ]:

        score += 15

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if conditions[
        "rsi_above_50"
    ]:

        score += 15

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    if conditions[
        "volume_over_1_5x"
    ]:

        score += 20

    # --------------------------------------------------------
    # Above MA20
    # --------------------------------------------------------

    if conditions[
        "above_ma20"
    ]:

        score += 15

    # --------------------------------------------------------
    # MA20 up
    # --------------------------------------------------------

    if conditions[
        "ma20_up"
    ]:

        score += 15

    return min(
        100,
        max(
            0,
            score
        )
    )


# ============================================================
# 訊號文字
# ============================================================

def get_signal(
    score,
    core
):

    if core:

        return "核心訊號"

    if score >= 80:

        return "強勢"

    if score >= 60:

        return "偏多"

    if score >= 40:

        return "觀察"

    if score >= 20:

        return "偏弱"

    return "弱勢"


# ============================================================
# 建立單一股票資料
# ============================================================

def build_stock(
    stock_info
):

    stock_id = str(
        stock_info["id"]
    )

    name = stock_info["name"]

    stock_type = stock_info.get(
        "type",
        "STOCK"
    )

    ticker = get_ticker(
        stock_id
    )

    print(
        f"[INFO] 取得 {stock_id} {name} ..."
    )

    df = download_data(
        ticker
    )

    if df is None:

        print(
            f"[WARN] {stock_id} 無法取得資料"
        )

        return None

    data = calculate_indicators(
        df
    )

    if len(data) < 2:

        return None

    current = data.iloc[-1]
    previous = data.iloc[-2]

    # --------------------------------------------------------
    # Price
    # --------------------------------------------------------

    close = safe_float(
        current["Close"],
        2
    )

    previous_close = safe_float(
        previous["Close"],
        2
    )

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

    else:

        change = None
        change_percent = None

    # --------------------------------------------------------
    # 技術指標
    # --------------------------------------------------------

    rsi = safe_float(
        current["RSI"],
        2
    )

    k = safe_float(
        current["K"],
        2
    )

    d = safe_float(
        current["D"],
        2
    )

    macd = safe_float(
        current["MACD"],
        4
    )

    macd_signal = safe_float(
        current["MACD_SIGNAL"],
        4
    )

    macd_hist = safe_float(
        current["MACD_HIST"],
        4
    )

    ma5 = safe_float(
        current["MA5"],
        2
    )

    ma20 = safe_float(
        current["MA20"],
        2
    )

    ma60 = safe_float(
        current["MA60"],
        2
    )

    volume = safe_float(
        current["Volume"],
        0
    )

    volume_ma5 = safe_float(
        current["VOL_MA5"],
        0
    )

    volume_ratio = safe_float(
        current["VOLUME_RATIO"],
        2
    )

    # --------------------------------------------------------
    # 條件
    # --------------------------------------------------------

    condition_macd = macd_golden_cross(
        data
    )

    condition_kd = kd_golden_cross(
        data
    )

    condition_rsi = (
        rsi is not None
        and
        rsi > 50
    )

    condition_volume = (
        volume_ratio is not None
        and
        volume_ratio >= 1.5
    )

    condition_ma20 = (
        close is not None
        and
        ma20 is not None
        and
        close > ma20
    )

    condition_ma20_up = ma20_is_up(
        data
    )

    # --------------------------------------------------------
    # 核心條件
    # --------------------------------------------------------

    short_term_core = all(
        [
            condition_macd,
            condition_kd,
            condition_rsi,
            condition_volume,
            condition_ma20,
            condition_ma20_up
        ]
    )

    conditions = {

        "macd_golden_cross":
            bool(condition_macd),

        "kd_golden_cross":
            bool(condition_kd),

        "rsi_above_50":
            bool(condition_rsi),

        "volume_over_1_5x":
            bool(condition_volume),

        "above_ma20":
            bool(condition_ma20),

        "ma20_up":
            bool(condition_ma20_up),

        "short_term_core":
            bool(short_term_core)

    }

    # --------------------------------------------------------
    # AI Score
    # --------------------------------------------------------

    score = calculate_score(
        conditions
    )

    signal = get_signal(
        score,
        short_term_core
    )

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

    dca = calculate_dca(
        close,
        ma20
    )

    # --------------------------------------------------------
    # 最後交易日期
    # --------------------------------------------------------

    last_date = data.index[-1]

    try:

        if hasattr(
            last_date,
            "strftime"
        ):

            last_date_text = (
                last_date.strftime(
                    "%Y-%m-%d"
                )
            )

        else:

            last_date_text = str(
                last_date
            )

    except Exception:

        last_date_text = ""

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    result = {

        "id": stock_id,

        "symbol": stock_id,

        "name": name,

        "type": stock_type,

        "ticker": ticker,

        "updated_at": datetime.now(
            TAIPEI_TZ
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "data_date":
            last_date_text,

        "price": {

            "close":
                close,

            "previous_close":
                previous_close,

            "change":
                safe_float(
                    change,
                    2
                ),

            "change_percent":
                safe_float(
                    change_percent,
                    2
                )

        },

        "technical": {

            # ------------------------------------------------
            # RSI 數值
            # ------------------------------------------------

            "rsi":
                rsi,

            # ------------------------------------------------
            # KD
            # ------------------------------------------------

            "k":
                k,

            "d":
                d,

            # ------------------------------------------------
            # MACD
            # ------------------------------------------------

            "macd":
                macd,

            "macd_signal":
                macd_signal,

            "macd_hist":
                macd_hist,

            # ------------------------------------------------
            # MA
            # ------------------------------------------------

            "ma5":
                ma5,

            "ma20":
                ma20,

            "ma60":
                ma60,

            # ------------------------------------------------
            # Volume
            # ------------------------------------------------

            "volume":
                volume,

            "volume_ma5":
                volume_ma5,

            "volume_ratio":
                volume_ratio

        },

        "conditions":
            conditions,

        "short_term": {

            "score":
                score,

            "signal":
                signal,

            "core":
                short_term_core

        },

        "dca":
            dca

    }

    print(
        f"[OK] {stock_id} "
        f"RSI={rsi} "
        f"Score={score} "
        f"Core={short_term_core}"
    )

    return result


# ============================================================
# 排名
# ============================================================

def build_rankings(
    stocks
):

    # --------------------------------------------------------
    # 短線排名
    # --------------------------------------------------------

    short_term = sorted(
        stocks,
        key=lambda stock:
            (
                stock.get(
                    "short_term",
                    {}
                ).get(
                    "score",
                    0
                )
                or 0
            ),
        reverse=True
    )

    short_term_ids = [
        stock["id"]
        for stock in short_term
    ]

    # --------------------------------------------------------
    # DCA 排名
    #
    # 優先：
    # 1. 股價接近 MA20
    # 2. AI 分數
    # --------------------------------------------------------

    def dca_key(stock):

        price = (
            stock.get(
                "price",
                {}
            ).get(
                "close"
            )
        )

        ma20 = (
            stock.get(
                "technical",
                {}
            ).get(
                "ma20"
            )
        )

        score = (
            stock.get(
                "short_term",
                {}
            ).get(
                "score",
                0
            )
            or 0
        )

        if (
            price is None
            or
            ma20 is None
            or
            ma20 == 0
        ):

            distance = 999

        else:

            distance = abs(
                (
                    price -
                    ma20
                ) /
                ma20
            )

        return (
            distance,
            -score
        )

    dca_sorted = sorted(
        stocks,
        key=dca_key
    )

    dca_ids = [
        stock["id"]
        for stock in dca_sorted
    ]

    return {

        "short_term":
            short_term_ids,

        "dca":
            dca_ids

    }


# ============================================================
# 統計
#
# ★★★ V6 最重要修正區 ★★★
#
# rsi_above_50：
# 「符合 RSI > 50 的股票數量」
#
# 絕對不是：
# RSI 數值
# 股票資料數
# 任何其他欄位
#
# 因此首頁：
# RSI ＞ 50
# 顯示的必定是「幾檔」
# ============================================================

def build_statistics(
    stocks
):

    total_stocks = len(
        stocks
    )

    # --------------------------------------------------------
    # MACD 黃金交叉數量
    # --------------------------------------------------------

    macd_golden = sum(
        1
        for stock in stocks
        if bool(
            stock.get(
                "conditions",
                {}
            ).get(
                "macd_golden_cross",
                False
            )
        )
    )

    # --------------------------------------------------------
    # RSI > 50 數量
    #
    # ★ 只計算 True
    # ★ 不取 RSI 數值
    # ★ 不取資料筆數
    # ★ 不取任何其他欄位
    # --------------------------------------------------------

    rsi_above_50 = sum(
        1
        for stock in stocks
        if bool(
            stock.get(
                "conditions",
                {}
            ).get(
                "rsi_above_50",
                False
            )
        )
    )

    # --------------------------------------------------------
    # 核心訊號數量
    # --------------------------------------------------------

    core_stocks = sum(
        1
        for stock in stocks
        if bool(
            stock.get(
                "conditions",
                {}
            ).get(
                "short_term_core",
                False
            )
        )
    )

    # --------------------------------------------------------
    # KD 黃金交叉
    # --------------------------------------------------------

    kd_golden = sum(
        1
        for stock in stocks
        if bool(
            stock.get(
                "conditions",
                {}
            ).get(
                "kd_golden_cross",
                False
            )
        )
    )

    # --------------------------------------------------------
    # 量能條件
    # --------------------------------------------------------

    volume_strong = sum(
        1
        for stock in stocks
        if bool(
            stock.get(
                "conditions",
                {}
            ).get(
                "volume_over_1_5x",
                False
            )
        )
    )

    # --------------------------------------------------------
    # 站上 MA20
    # --------------------------------------------------------

    above_ma20 = sum(
        1
        for stock in stocks
        if bool(
            stock.get(
                "conditions",
                {}
            ).get(
                "above_ma20",
                False
            )
        )
    )

    # --------------------------------------------------------
    # MA20 向上
    # --------------------------------------------------------

    ma20_up = sum(
        1
        for stock in stocks
        if bool(
            stock.get(
                "conditions",
                {}
            ).get(
                "ma20_up",
                False
            )
        )
    )

    # --------------------------------------------------------
    # 統計結果
    # --------------------------------------------------------

    statistics = {

        "total_stocks":
            int(total_stocks),

        "core_stocks":
            int(core_stocks),

        "macd_golden":
            int(macd_golden),

        "kd_golden":
            int(kd_golden),

        # ★★★ RSI 修正 ★★★
        "rsi_above_50":
            int(rsi_above_50),

        "volume_strong":
            int(volume_strong),

        "above_ma20":
            int(above_ma20),

        "ma20_up":
            int(ma20_up)

    }

    # --------------------------------------------------------
    # 安全檢查
    # --------------------------------------------------------

    for key, value in statistics.items():

        if not isinstance(
            value,
            int
        ):

            statistics[key] = int(
                value or 0
            )

    return statistics


# ============================================================
# 建立完整 JSON
# ============================================================

def build_output(
    stocks
):

    statistics = build_statistics(
        stocks
    )

    rankings = build_rankings(
        stocks
    )

    now = datetime.now(
        TAIPEI_TZ
    )

    output = {

        "version":
            "V6",

        "system":
            "台股 AI 選股・零股定投・動態風控",

        "updated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "timezone":
            "Asia/Taipei",

        "statistics":
            statistics,

        "rankings":
            rankings,

        "stocks":
            stocks

    }

    return output


# ============================================================
# JSON 儲存
# ============================================================

def save_json(
    data
):

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    with open(
        OUTPUT_FILE,
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

    print(
        f"[SUCCESS] 已寫入：{OUTPUT_FILE}"
    )


# ============================================================
# 最終資料驗證
# ============================================================

def validate_output(
    output
):

    print(
        "\n"
        "============================================================"
    )

    print(
        "V6 資料驗證"
    )

    print(
        "============================================================"
    )

    statistics = output.get(
        "statistics",
        {}
    )

    stocks = output.get(
        "stocks",
        []
    )

    # --------------------------------------------------------
    # 基本數量
    # --------------------------------------------------------

    print(
        f"股票數量：{len(stocks)}"
    )

    print(
        f"核心訊號："
        f"{statistics.get('core_stocks', 0)}"
    )

    print(
        f"MACD 黃金交叉："
        f"{statistics.get('macd_golden', 0)}"
    )

    print(
        f"RSI > 50："
        f"{statistics.get('rsi_above_50', 0)}"
    )

    print(
        f"KD 黃金交叉："
        f"{statistics.get('kd_golden', 0)}"
    )

    print(
        f"量能 > 1.5x："
        f"{statistics.get('volume_strong', 0)}"
    )

    print(
        f"站上 MA20："
        f"{statistics.get('above_ma20', 0)}"
    )

    print(
        f"MA20 向上："
        f"{statistics.get('ma20_up', 0)}"
    )

    # --------------------------------------------------------
    # RSI 再次獨立驗證
    # --------------------------------------------------------

    calculated_rsi_count = sum(

        1

        for stock in stocks

        if (
            stock
            .get(
                "technical",
                {}
            )
            .get(
                "rsi"
            )
            is not None
            and
            float(
                stock
                .get(
                    "technical",
                    {}
                )
                .get(
                    "rsi"
                )
            ) > 50
        )

    )

    stored_rsi_count = int(
        statistics.get(
            "rsi_above_50",
            0
        )
    )

    print(
        "\n"
        f"RSI 實際計算數量："
        f"{calculated_rsi_count}"
    )

    print(
        f"JSON 統計數量："
        f"{stored_rsi_count}"
    )

    if (
        calculated_rsi_count
        !=
        stored_rsi_count
    ):

        raise ValueError(
            "RSI 統計驗證失敗："
            "statistics.rsi_above_50 "
            "與實際 RSI > 50 數量不一致。"
        )

    # --------------------------------------------------------
    # 確認 RSI 統計不是異常值
    # --------------------------------------------------------

    if (
        stored_rsi_count < 0
        or
        stored_rsi_count > len(stocks)
    ):

        raise ValueError(
            "RSI 統計異常："
            "rsi_above_50 不可能大於股票總數。"
        )

    print(
        "\n"
        "✓ RSI 統計驗證通過"
    )

    print(
        "✓ V6 JSON 結構驗證通過"
    )

    print(
        "============================================================\n"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        "============================================================"
    )

    print(
        "台股 AI 選股・零股定投・動態風控"
    )

    print(
        "fetch_data.py V6 正式修正版"
    )

    print(
        "============================================================"
    )

    print(
        f"開始時間："
        f"{datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        f"股票清單：{len(STOCKS)} 檔"
    )

    print(
        "============================================================\n"
    )

    stocks = []

    # --------------------------------------------------------
    # 逐檔抓取
    # --------------------------------------------------------

    for stock_info in STOCKS:

        try:

            result = build_stock(
                stock_info
            )

            if result is not None:

                stocks.append(
                    result
                )

        except Exception as error:

            print(
                f"[ERROR] "
                f"{stock_info['id']} "
                f"{stock_info['name']}："
                f"{error}"
            )

        # ----------------------------------------------------
        # 避免 API 請求過快
        # ----------------------------------------------------

        time.sleep(
            0.5
        )

    # --------------------------------------------------------
    # 如果全部失敗
    # --------------------------------------------------------

    if len(stocks) == 0:

        raise RuntimeError(
            "沒有任何股票取得成功，"
            "停止寫入 prices.json。"
        )

    # --------------------------------------------------------
    # 建立輸出
    # --------------------------------------------------------

    output = build_output(
        stocks
    )

    # --------------------------------------------------------
    # 驗證
    # --------------------------------------------------------

    validate_output(
        output
    )

    # --------------------------------------------------------
    # 儲存
    # --------------------------------------------------------

    save_json(
        output
    )

    print(
        "============================================================"
    )

    print(
        "V6 更新完成"
    )

    print(
        "============================================================"
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    main()
