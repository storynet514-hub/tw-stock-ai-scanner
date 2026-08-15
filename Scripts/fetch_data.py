# ================================================================
# 台股 AI 選股系統
# fetch_data.py V7.6
#
# 原則：
# 1. 不手動指定任何個股或 ETF
# 2. 不手動塞入 00720B 或任何特定代號
# 3. Universe 只來自官方市場資料
# 4. ETF 不因「公司債／債券／金融債」名稱被排除
# 5. 權證、牛熊證、ETN、存託憑證、可轉債等非目標商品排除
# 6. 股票 6 項核心條件由後台計算
# 7. 前台只讀取後台結果
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


VERSION = "V7.6"

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

DATA_DIR = os.path.join(BASE_DIR, "Data")
OUTPUT_FILE = os.path.join(DATA_DIR, "prices.json")

os.makedirs(DATA_DIR, exist_ok=True)

TW_TZ = timezone(timedelta(hours=8))

YF_PERIOD = "1y"
YF_INTERVAL = "1d"

MAX_RETRY = 3
RETRY_DELAY = 2

BATCH_SIZE = 40
BATCH_DELAY = 1.0

TOP_N = 10


# ================================================================
# 股票核心條件
# ================================================================

CORE_CONDITIONS = [
    "MACD 黃金交叉",
    "RSI > 50",
    "KD 黃金交叉",
    "成交量放大",
    "股價 > MA20",
    "MA20 向上",
]


# ================================================================
# 只排除「商品類型」
#
# 重要：
# 不得放入：
# 公司債
# 債券
# 金融債
#
# 因為合法 ETF 名稱可以包含這些文字。
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


# ================================================================
# ETF 判斷
#
# 不靠人工代號清單。
#
# 官方商品名稱如果被官方資料標示為 ETF，
# 或名稱本身包含 ETF，才分類為 ETF。
# ================================================================

def is_etf(code, name, security_type=None):
    name = clean_text(name)

    if security_type:
        security_type = clean_text(
            security_type
        ).upper()

        if "ETF" in security_type:
            return True

    if "ETF" in name.upper():
        return True

    return False


# ================================================================
# Yahoo symbol
# ================================================================

def yahoo_symbol(code, market="TW"):
    code = clean_code(code)

    if market == "TWO":
        return f"{code}.TWO"

    return f"{code}.TW"


# ================================================================
# 官方 TWSE 商品清單
#
# 這裡不手動指定任何股票。
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
            ),
        })

    return universe


# ================================================================
# 官方 TPEx 商品清單
#
# 仍然不手動指定任何股票。
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

            if response.status_code != 200:
                continue

            data = response.json()

            if not isinstance(data, list):
                continue

            universe = []

            for item in data:

                code = clean_code(
                    item.get("SecuritiesCompanyCode")
                    or item.get("Code")
                    or item.get("股票代號")
                )

                name = clean_text(
                    item.get("CompanyName")
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
                    ),
                })

            if universe:
                return universe

        except Exception:
            continue

    return []


# ================================================================
# 建立完整 Universe
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
            f"[WARN] TWSE Universe 取得失敗：{exc}"
        )

    try:
        sources.extend(
            fetch_tpex_universe()
        )
    except Exception as exc:
        print(
            f"[WARN] TPEx Universe 取得失敗：{exc}"
        )

    for item in sources:

        code = item["code"]

        if code not in combined:

            combined[code] = item

    return list(
        combined.values()
    )


# ================================================================
# Yahoo 行情
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

            df = yf.Ticker(
                symbol
            ).history(
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
                "Volume",
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
                subset=[
                    "Close"
                ]
            )

            if len(df) < 30:
                raise RuntimeError(
                    "歷史資料不足 30 日"
                )

            return df

        except Exception as exc:

            if attempt == MAX_RETRY:

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

    low9 = low.rolling(
        9
    ).min()

    high9 = high.rolling(
        9
    ).max()

    denominator = (
        high9 -
        low9
    ).replace(
        0,
        np.nan
    )

    rsv = (
        (close - low9) /
        denominator *
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
# 股票 6 項核心條件
# ================================================================

def evaluate_core(
    df
):

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    macd_cross = (
        pd.notna(
            latest["MACD"]
        )
        and pd.notna(
            previous["MACD"]
        )
        and pd.notna(
            latest["MACD_SIGNAL"]
        )
        and pd.notna(
            previous["MACD_SIGNAL"]
        )
        and
        latest["MACD"]
        >
        latest["MACD_SIGNAL"]
        and
        previous["MACD"]
        <=
        previous["MACD_SIGNAL"]
    )

    rsi_pass = (
        pd.notna(
            latest["RSI"]
        )
        and
        latest["RSI"] > 50
    )

    kd_cross = (
        pd.notna(
            latest["K"]
        )
        and pd.notna(
            previous["K"]
        )
        and pd.notna(
            latest["D"]
        )
        and pd.notna(
            previous["D"]
        )
        and
        latest["K"]
        >
        latest["D"]
        and
        previous["K"]
        <=
        previous["D"]
    )

    volume_pass = (
        pd.notna(
            latest["Volume"]
        )
        and
        pd.notna(
            latest["VOL_MA5"]
        )
        and
        latest["VOL_MA5"] > 0
        and
        latest["Volume"]
        >=
        latest["VOL_MA5"] * 1.5
    )

    price_ma20 = (
        pd.notna(
            latest["Close"]
        )
        and
        pd.notna(
            latest["MA20"]
        )
        and
        latest["Close"]
        >
        latest["MA20"]
    )

    ma20_up = (
        pd.notna(
            latest["MA20"]
        )
        and
        pd.notna(
            previous["MA20"]
        )
        and
        latest["MA20"]
        >
        previous["MA20"]
    )

    conditions = {
        "macd_golden_cross": bool(
            macd_cross
        ),

        "rsi_over_50": bool(
            rsi_pass
        ),

        "kd_golden_cross": bool(
            kd_cross
        ),

        "volume_expand": bool(
            volume_pass
        ),

        "price_over_ma20": bool(
            price_ma20
        ),

        "ma20_up": bool(
            ma20_up
        ),
    }

    score = sum(
        conditions.values()
    )

    conditions[
        "core_score"
    ] = score

    conditions[
        "core_total"
    ] = 6

    conditions[
        "core_pass"
    ] = (
        score == 6
    )

    return conditions


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
                (float(rsi) - 50)
                * 0.8
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
        pd.notna(
            latest["MACD"]
        )
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
        pd.notna(
            latest["K"]
        )
        and
        pd.notna(
            latest["D"]
        )
        and
        latest["K"]
        >
        latest["D"]
    ):
        score += 15

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
# 單一商品分析
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
            (
                close /
                previous_close
            ) - 1
        ) * 100

    result = {
        "code": code,
        "symbol": yahoo_symbol(
            code,
            market
        ),
        "name": name,

        "type": security_type,

        "market": market,

        "price": close,

        "close": close,

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
# 今日精選
#
# 只有後台真正 6 / 6 才進入。
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
# JSON
# ================================================================

def build_output(
    stocks,
    etfs
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

    strong_stocks = stocks[
        :TOP_N
    ]

    strong_etfs = etfs[
        :TOP_N
    ]

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

        "statistics": {

            "stock_count":
                len(stocks),

            "etf_count":
                len(etfs),

            "today_selected_count":
                len(
                    today_selected
                ),

            "core_6_count":
                len(
                    today_selected
                ),
        },

        "core_conditions": {

            "total":
                6,

            "names":
                CORE_CONDITIONS,
        },
    }


# ================================================================
# 原子寫入
# ================================================================

def write_json(
    data
):

    fd, temp_path = tempfile.mkstemp(
        prefix="prices_",
        suffix=".json",
        dir=DATA_DIR
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

            file.write(
                "\n"
            )

        os.replace(
            temp_path,
            OUTPUT_FILE
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
# 驗證
# ================================================================

def validate_output(
    output
):

    if not isinstance(
        output.get(
            "stocks"
        ),
        list
    ):
        raise RuntimeError(
            "stocks 資料格式錯誤"
        )

    if not isinstance(
        output.get(
            "etfs"
        ),
        list
    ):
        raise RuntimeError(
            "etfs 資料格式錯誤"
        )

    # ------------------------------------------------------------
    # 注意：
    # 這裡「不」檢查 00720B。
    #
    # 因為我們禁止任何特定股票被手動指定。
    # 如果官方 Universe 有 00720B，
    # 它就應該自然出現在結果。
    # ------------------------------------------------------------

    return True


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
        "模式：官方 Universe 自動掃描"
    )

    print(
        "規則：禁止手動指定個股 / ETF"
    )

    print(
        f"開始："
        f"{datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print("=" * 64)

    # ------------------------------------------------------------
    # 1. Universe
    # ------------------------------------------------------------

    print(
        "\n[1/4] 建立官方市場 Universe..."
    )

    universe = build_universe()

    if not universe:

        raise RuntimeError(
            "官方市場 Universe 為空，"
            "停止寫入 prices.json"
        )

    print(
        f"Universe："
        f"{len(universe)} 檔"
    )

    # ------------------------------------------------------------
    # 2. 分類
    # ------------------------------------------------------------

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
    # 3. 分析
    # ------------------------------------------------------------

    print(
        "\n[2/4] 分析股票..."
    )

    stocks = process_universe(
        stocks_universe
    )

    print(
        f"股票完成："
        f"{len(stocks)}"
    )

    print(
        "\n[3/4] 分析 ETF..."
    )

    etfs = process_universe(
        etfs_universe
    )

    print(
        f"ETF 完成："
        f"{len(etfs)}"
    )

    # ------------------------------------------------------------
    # 4. JSON
    # ------------------------------------------------------------

    print(
        "\n[4/4] 建立 prices.json..."
    )

    output = build_output(
        stocks,
        etfs
    )

    validate_output(
        output
    )

    write_json(
        output
    )

    elapsed = (
        time.time() -
        start
    )

    # ------------------------------------------------------------
    # 最終統計
    # ------------------------------------------------------------

    six_of_six = [
        item
        for item in stocks
        if item.get(
            "core_score"
        ) == 6
    ]

    print(
        "\n" + "=" * 64
    )

    print(
        "✓ 後台更新完成"
    )

    print(
        f"股票資料："
        f"{len(stocks)}"
    )

    print(
        f"ETF 資料："
        f"{len(etfs)}"
    )

    print(
        f"6 / 6 個股："
        f"{len(six_of_six)}"
    )

    print(
        f"今日精選："
        f"{len(output['today_selected'])}"
    )

    print(
        f"輸出："
        f"{OUTPUT_FILE}"
    )

    print(
        f"耗時："
        f"{elapsed:.1f} 秒"
    )

    print("=" * 64)


if __name__ == "__main__":
    main()
