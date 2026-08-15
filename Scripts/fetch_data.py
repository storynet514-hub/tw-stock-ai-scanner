# ================================================================
# 台股 AI 選股系統
# fetch_data.py V9.0 FINAL
#
# V9.0 INCREMENTAL DATA ENGINE
#
# 架構：
#
#   第一次：
#       TWSE / TPEx Universe
#              ↓
#       Yahoo 批次建立歷史資料
#              ↓
#       SQLite market.db
#
#   每日：
#       TWSE / TPEx 當日收盤
#              ↓
#       SQLite 增量更新
#              ↓
#       技術指標
#              ↓
#       6/6
#              ↓
#       AI Score
#              ↓
#       Top30
#              ↓
#       A/B Backtest
#              ↓
#       prices.json
#
# 重要：
#   Yahoo 不再每天逐檔下載 2 年資料。
#
# ================================================================

import os
import json
import math
import time
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf


# ================================================================
# 基本設定
# ================================================================

VERSION = "V9.0"
SCHEMA_VERSION = "prices.v9"

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

DATABASE_FILE = os.path.join(
    DATA_DIR,
    "market.db"
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
# 歷史資料設定
# ================================================================

HISTORY_PERIOD = "2y"

MIN_HISTORY = 120

BACKTEST_MIN_HISTORY = 120

BACKTEST_HORIZONS = [
    5,
    10,
    20
]

TOP30 = 30


# ================================================================
# 批次下載設定
# ================================================================

YF_BATCH_SIZE = 80

YF_BATCH_DELAY = 0.5

MAX_RETRY = 3

RETRY_DELAY = 2


# ================================================================
# 官方 API
# ================================================================

TWSE_UNIVERSE_URL = (
    "https://openapi.twse.com.tw/"
    "v1/exchangeReport/STOCK_DAY_ALL"
)

TPEx_UNIVERSE_URL = (
    "https://www.tpex.org.tw/"
    "openapi/v1/"
    "tpex_mainboard_daily_close_quotes"
)

TWSE_DAILY_URL = (
    "https://openapi.twse.com.tw/"
    "v1/exchangeReport/STOCK_DAY_ALL"
)

TPEx_DAILY_URL = (
    "https://www.tpex.org.tw/"
    "openapi/v1/"
    "tpex_mainboard_daily_close_quotes"
)


# ================================================================
# 六項正式條件
# ================================================================

CORE_CONDITIONS = [
    "MACD 多方",
    "RSI > 50",
    "KD 多方",
    "成交量 ≥ MA5 × 1.5",
    "股價 > MA20",
    "MA20 今日 > 昨日"
]


# ================================================================
# 特殊商品排除
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
    "債券",
    "公司債"
]


# ================================================================
# SQLite
# ================================================================

def get_connection():

    conn = sqlite3.connect(
        DATABASE_FILE,
        timeout=60
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA synchronous=NORMAL"
    )

    conn.execute(
        "PRAGMA temp_store=MEMORY"
    )

    return conn


def init_database():

    conn = get_connection()

    try:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS securities (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                type TEXT,
                is_etf INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                updated_at TEXT,
                PRIMARY KEY (market, code)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_prices (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                PRIMARY KEY (market, code, date)
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_daily_code_date
            ON daily_prices (market, code, date)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_daily_date
            ON daily_prices (date)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )

        conn.commit()

    finally:

        conn.close()


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

    if value.endswith(".TWO"):
        value = value[:-4]

    if value.endswith(".TW"):
        value = value[:-3]

    return value


def safe_float(value):

    try:

        value = float(value)

        if not math.isfinite(value):
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
        for keyword in INVALID_SECURITY_KEYWORDS
    )


# ================================================================
# ETF 判斷
# ================================================================

def is_etf_code(code):

    code = clean_code(
        code
    )

    if not code:
        return False

    if code.startswith("00"):

        if len(code) in (
            4,
            5,
            6
        ):
            return True

    return False


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

        text = clean_text(
            security_type
        ).upper()

        if "ETF" in text:
            return True

    if "ETF" in name.upper():
        return True

    if is_etf_code(code):
        return True

    return False


# ================================================================
# Yahoo Symbol
# ================================================================

def yahoo_symbol(
    code,
    market
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

    response = requests.get(
        TWSE_UNIVERSE_URL,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(
        data,
        list
    ):

        raise RuntimeError(
            "TWSE Universe API 格式錯誤"
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

        if not code.isalnum():
            continue

        if is_invalid_security(
            name
        ):
            continue

        etf = is_etf(
            code,
            name
        )

        universe.append({

            "market": "TW",

            "code": code,

            "name": name,

            "type":
                "etf"
                if etf
                else "stock",

            "is_etf":
                etf

        })

    return universe


# ================================================================
# TPEx Universe
# ================================================================

def fetch_tpex_universe():

    response = requests.get(
        TPEx_UNIVERSE_URL,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(
        data,
        list
    ):

        raise RuntimeError(
            "TPEx Universe API 格式錯誤"
        )

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

        if not code.isalnum():
            continue

        if is_invalid_security(
            name
        ):
            continue

        etf = is_etf(
            code,
            name
        )

        universe.append({

            "market": "TWO",

            "code": code,

            "name": name,

            "type":
                "etf"
                if etf
                else "stock",

            "is_etf":
                etf

        })

    return universe


# ================================================================
# 建立 Universe
# ================================================================

def build_universe():

    print(
        "================================================"
    )

    print(
        "V9.0 建立完整 Universe"
    )

    print(
        "================================================"
    )

    twse = fetch_twse_universe()

    print(
        f"[TWSE] {len(twse)}"
    )

    tpex = fetch_tpex_universe()

    print(
        f"[TPEx] {len(tpex)}"
    )

    combined = {}

    for item in (
        twse +
        tpex
    ):

        key = (
            item["market"],
            item["code"]
        )

        combined[key] = item

    universe = list(
        combined.values()
    )

    statistics = {

        "twse_stock_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TW"
                and x["type"] == "stock"
            ),

        "tpex_stock_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TWO"
                and x["type"] == "stock"
            ),

        "twse_etf_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TW"
                and x["type"] == "etf"
            ),

        "tpex_etf_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TWO"
                and x["type"] == "etf"
            )

    }

    statistics["stock_universe"] = (
        statistics["twse_stock_universe"]
        +
        statistics["tpex_stock_universe"]
    )

    statistics["etf_universe"] = (
        statistics["twse_etf_universe"]
        +
        statistics["tpex_etf_universe"]
    )

    statistics["total_universe"] = (
        len(universe)
    )

    print(
        "------------------------------------------------"
    )

    print(
        f"上市股票："
        f"{statistics['twse_stock_universe']}"
    )

    print(
        f"上櫃股票："
        f"{statistics['tpex_stock_universe']}"
    )

    print(
        f"上市 ETF："
        f"{statistics['twse_etf_universe']}"
    )

    print(
        f"上櫃 ETF："
        f"{statistics['tpex_etf_universe']}"
    )

    print(
        f"Universe："
        f"{statistics['total_universe']}"
    )

    print(
        "------------------------------------------------"
    )

    if statistics[
        "twse_stock_universe"
    ] < 500:

        raise RuntimeError(
            "TWSE 股票 Universe 異常"
        )

    if statistics[
        "tpex_stock_universe"
    ] < 300:

        raise RuntimeError(
            "TPEx 股票 Universe 異常"
        )

    return (
        universe,
        statistics
    )


# ================================================================
# 儲存 Universe
# ================================================================

def save_universe(
    universe
):

    conn = get_connection()

    now = datetime.now(
        TW_TZ
    ).isoformat()

    try:

        conn.execute(
            "UPDATE securities SET active = 0"
        )

        rows = []

        for item in universe:

            rows.append((

                item["market"],

                item["code"],

                item["name"],

                item["type"],

                1
                if item["is_etf"]
                else 0,

                1,

                now

            ))

        conn.executemany(
            """
            INSERT INTO securities (
                market,
                code,
                name,
                type,
                is_etf,
                active,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market, code)
            DO UPDATE SET
                name=excluded.name,
                type=excluded.type,
                is_etf=excluded.is_etf,
                active=1,
                updated_at=excluded.updated_at
            """,
            rows
        )

        conn.commit()

    finally:

        conn.close()


# ================================================================
# DB 是否已有歷史資料
# ================================================================

def database_has_history():

    conn = get_connection()

    try:

        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM daily_prices
            """
        ).fetchone()

        return (
            row[0] > 0
        )

    finally:

        conn.close()


# ================================================================
# 取得 DB 最新日期
# ================================================================

def database_latest_date():

    conn = get_connection()

    try:

        row = conn.execute(
            """
            SELECT MAX(date)
            FROM daily_prices
            """
        ).fetchone()

        if not row:
            return None

        return row[0]

    finally:

        conn.close()


# ================================================================
# Yahoo 批次建立歷史資料
# ================================================================

def bootstrap_history(
    universe
):

    print(
        "================================================"
    )

    print(
        "第一次建立歷史資料庫"
    )

    print(
        "使用 Yahoo Finance 批次下載"
    )

    print(
        "================================================"
    )

    total = len(
        universe
    )

    conn = get_connection()

    try:

        for start in range(
            0,
            total,
            YF_BATCH_SIZE
        ):

            batch = universe[
                start:
                start +
                YF_BATCH_SIZE
            ]

            symbols = [
                yahoo_symbol(
                    x["code"],
                    x["market"]
                )
                for x in batch
            ]

            print(
                f"[Bootstrap] "
                f"{min(start + len(batch), total)}/{total}"
            )

            downloaded = None

            for attempt in range(
                1,
                MAX_RETRY + 1
            ):

                try:

                    downloaded = yf.download(
                        tickers=symbols,
                        period=HISTORY_PERIOD,
                        interval="1d",
                        auto_adjust=False,
                        actions=False,
                        progress=False,
                        group_by="ticker",
                        threads=True
                    )

                    if (
                        downloaded is not None
                        and
                        not downloaded.empty
                    ):
                        break

                except Exception as exc:

                    print(
                        f"[Yahoo retry {attempt}] "
                        f"{exc}"
                    )

                    time.sleep(
                        RETRY_DELAY
                    )

            if (
                downloaded is None
                or
                downloaded.empty
            ):

                print(
                    "[WARN] "
                    "批次下載失敗，跳過"
                )

                continue

            rows = []

            for item, symbol in zip(
                batch,
                symbols
            ):

                try:

                    if len(symbols) == 1:

                        df = downloaded.copy()

                    else:

                        if symbol not in downloaded.columns.get_level_values(0):

                            continue

                        df = downloaded[
                            symbol
                        ].copy()

                    if df.empty:
                        continue

                    df = df.reset_index()

                    date_col = "Date"

                    if date_col not in df.columns:

                        continue

                    for _, row in df.iterrows():

                        date_value = row[
                            date_col
                        ]

                        if pd.isna(
                            date_value
                        ):
                            continue

                        date_text = (
                            pd.Timestamp(
                                date_value
                            )
                            .strftime(
                                "%Y-%m-%d"
                            )
                        )

                        close = safe_float(
                            row.get("Close")
                        )

                        if close is None:
                            continue

                        open_price = safe_float(
                            row.get("Open")
                        )

                        high = safe_float(
                            row.get("High")
                        )

                        low = safe_float(
                            row.get("Low")
                        )

                        volume = safe_int(
                            row.get("Volume")
                        )

                        rows.append((

                            item["market"],

                            item["code"],

                            date_text,

                            open_price,

                            high,

                            low,

                            close,

                            volume

                        ))

                except Exception:

                    continue

            if rows:

                conn.executemany(
                    """
                    INSERT OR REPLACE INTO daily_prices (
                        market,
                        code,
                        date,
                        open,
                        high,
                        low,
                        close,
                        volume
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows
                )

                conn.commit()

            time.sleep(
                YF_BATCH_DELAY
            )

    finally:

        conn.close()

    count = count_history_records()

    print(
        "------------------------------------------------"
    )

    print(
        f"歷史資料筆數：{count}"
    )

    print(
        "------------------------------------------------"
    )


# ================================================================
# 歷史資料筆數
# ================================================================

def count_history_records():

    conn = get_connection()

    try:

        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM daily_prices
            """
        ).fetchone()

        return int(
            row[0]
        )

    finally:

        conn.close()


# ================================================================
# 官方當日資料解析
# ================================================================

def parse_daily_item(
    item,
    market
):

    if market == "TW":

        code = clean_code(
            item.get("Code")
        )

        name = clean_text(
            item.get("Name")
        )

        open_price = (
            item.get("OpeningPrice")
            or
            item.get("Open")
        )

        high = (
            item.get("HighestPrice")
            or
            item.get("High")
        )

        low = (
            item.get("LowestPrice")
            or
            item.get("Low")
        )

        close = (
            item.get("ClosingPrice")
            or
            item.get("Close")
        )

        volume = (
            item.get("TradeVolume")
            or
            item.get("Volume")
        )

    else:

        code = clean_code(
            item.get(
                "SecuritiesCompanyCode"
            )
            or
            item.get("Code")
            or
            item.get("股票代號")
        )

        name = clean_text(
            item.get(
                "CompanyName"
            )
            or
            item.get("Name")
            or
            item.get("公司名稱")
        )

        open_price = (
            item.get("Open")
            or
            item.get("OpeningPrice")
            or
            item.get("開盤價")
        )

        high = (
            item.get("High")
            or
            item.get("HighestPrice")
            or
            item.get("最高價")
        )

        low = (
            item.get("Low")
            or
            item.get("LowestPrice")
            or
            item.get("最低價")
        )

        close = (
            item.get("Close")
            or
            item.get("ClosingPrice")
            or
            item.get("收盤價")
        )

        volume = (
            item.get("Volume")
            or
            item.get("TradeVolume")
            or
            item.get("成交股數")
        )

    return {

        "code": code,

        "name": name,

        "open": safe_float(
            open_price
        ),

        "high": safe_float(
            high
        ),

        "low": safe_float(
            low
        ),

        "close": safe_float(
            close
        ),

        "volume": safe_int(
            volume
        )

    }


# ================================================================
# 官方每日更新
# ================================================================

def fetch_official_daily(
    universe
):

    print(
        "================================================"
    )

    print(
        "更新官方當日收盤資料"
    )

    print(
        "================================================"
    )

    now = datetime.now(
        TW_TZ
    )

    today = now.strftime(
        "%Y-%m-%d"
    )

    universe_map = {

        (
            x["market"],
            x["code"]
        ): x

        for x in universe

    }

    records = []

    # ------------------------------------------------------------
    # TWSE
    # ------------------------------------------------------------

    try:

        response = requests.get(
            TWSE_DAILY_URL,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        for item in data:

            parsed = parse_daily_item(
                item,
                "TW"
            )

            key = (
                "TW",
                parsed["code"]
            )

            if key not in universe_map:
                continue

            if parsed["close"] is None:
                continue

            records.append((

                "TW",

                parsed["code"],

                today,

                parsed["open"],

                parsed["high"],

                parsed["low"],

                parsed["close"],

                parsed["volume"]

            ))

        print(
            f"[TWSE] "
            f"{sum(1 for x in records if x[0] == 'TW')}"
        )

    except Exception as exc:

        print(
            f"[ERROR] TWSE daily：{exc}"
        )

    # ------------------------------------------------------------
    # TPEx
    # ------------------------------------------------------------

    try:

        response = requests.get(
            TPEx_DAILY_URL,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        for item in data:

            parsed = parse_daily_item(
                item,
                "TWO"
            )

            key = (
                "TWO",
                parsed["code"]
            )

            if key not in universe_map:
                continue

            if parsed["close"] is None:
                continue

            records.append((

                "TWO",

                parsed["code"],

                today,

                parsed["open"],

                parsed["high"],

                parsed["low"],

                parsed["close"],

                parsed["volume"]

            ))

        print(
            f"[TPEx] "
            f"{sum(1 for x in records if x[0] == 'TWO')}"
        )

    except Exception as exc:

        print(
            f"[ERROR] TPEx daily：{exc}"
        )

    if not records:

        raise RuntimeError(
            "官方每日資料為空"
        )

    conn = get_connection()

    try:

        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_prices (
                market,
                code,
                date,
                open,
                high,
                low,
                close,
                volume
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records
        )

        conn.commit()

    finally:

        conn.close()

    print(
        f"官方資料寫入：{len(records)}"
    )

    return len(records)


# ================================================================
# 取得股票歷史
# ================================================================

def load_history(
    market,
    code
):

    conn = get_connection()

    try:

        df = pd.read_sql_query(
            """
            SELECT
                date,
                open,
                high,
                low,
                close,
                volume
            FROM daily_prices
            WHERE market = ?
              AND code = ?
            ORDER BY date ASC
            """,
            conn,
            params=(
                market,
                code
            )
        )

    finally:

        conn.close()

    if df.empty:
        return None

    df["date"] = pd.to_datetime(
        df["date"]
    )

    df = df.set_index(
        "date"
    )

    df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        },
        inplace=True
    )

    return df


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

    result = (
        100 -
        100 /
        (1 + rs)
    )

    # 全程上漲時 RSI = 100
    result = result.where(
        ~(
            avg_loss.eq(0)
            &
            avg_gain.gt(0)
        ),
        100
    )

    return result


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

    hist = (
        macd -
        signal
    )

    return (
        macd,
        signal,
        hist
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
# 技術指標
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
# 核心條件
# ================================================================

def evaluate_core(
    df
):

    if len(df) < 21:
        return None

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    values = {

        "macd_golden_cross":
            bool(
                latest["MACD"]
                >
                latest["MACD_SIGNAL"]
            ),

        "rsi_over_50":
            bool(
                latest["RSI"]
                >
                50
            ),

        "kd_golden_cross":
            bool(
                latest["K"]
                >
                latest["D"]
            ),

        "volume_expand":
            bool(
                latest["Volume"]
                >=
                latest["VOL_MA5"]
                *
                1.5
            ),

        "price_over_ma20":
            bool(
                latest["Close"]
                >
                latest["MA20"]
            ),

        "ma20_up":
            bool(
                latest["MA20"]
                >
                previous["MA20"]
            )

    }

    score = sum(
        1
        for value in values.values()
        if value
    )

    values["core_score"] = score

    values["core_total"] = 6

    values["core_pass"] = (
        score == 6
    )

    return values


# ================================================================
# Strength Score
# ================================================================

def calculate_strength_score(
    df
):

    latest = df.iloc[-1]

    score = 0.0

    rsi = latest["RSI"]

    if pd.notna(rsi):

        rsi = float(rsi)

        if rsi >= 70:
            score += 20

        elif rsi >= 60:
            score += 16

        elif rsi >= 50:
            score += 12

    if (
        latest["MACD"]
        >
        latest["MACD_SIGNAL"]
    ):
        score += 20

    if (
        latest["K"]
        >
        latest["D"]
    ):
        score += 15

    if (
        latest["Close"]
        >
        latest["MA20"]
    ):
        score += 20

    if len(df) >= 2:

        if (
            latest["MA20"]
            >
            df.iloc[-2]["MA20"]
        ):
            score += 15

    if (
        pd.notna(
            latest["VOL_MA5"]
        )
        and
        latest["VOL_MA5"] > 0
        and
        latest["Volume"]
        >=
        latest["VOL_MA5"]
        *
        1.5
    ):
        score += 10

    return round(
        min(
            score,
            100
        ),
        2
    )


# ================================================================
# AI Score
# ================================================================

def calculate_ai_score(
    strength_score,
    core_score,
    change_pct
):

    strength = float(
        strength_score
        or 0
    )

    core = float(
        core_score
        or 0
    )

    change = float(
        change_pct
        or 0
    )

    momentum_bonus = min(
        max(
            change,
            -10
        ),
        10
    )

    score = (
        strength * 0.65
        +
        (core / 6) * 30
        +
        momentum_bonus * 0.5
    )

    return round(
        max(
            0,
            min(
                score,
                100
            )
        ),
        2
    )


# ================================================================
# Signal
# ================================================================

def make_signal(
    core_pass,
    core_score,
    ai_score
):

    if core_pass:
        return "今日精選"

    if core_score >= 4:
        return "強勢觀察"

    if ai_score >= 70:
        return "多方觀察"

    if ai_score >= 50:
        return "中性"

    return "弱勢"


# ================================================================
# 建立單一 Record
# ================================================================

def build_security_record(
    item,
    df
):

    if df is None:
        return None

    if len(df) < MIN_HISTORY:
        return None

    df = calculate_indicators(
        df
    )

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    core = evaluate_core(
        df
    )

    if core is None:
        return None

    close = safe_float(
        latest["Close"]
    )

    previous_close = safe_float(
        previous["Close"]
    )

    if (
        close is None
        or
        previous_close is None
    ):
        return None

    change_pct = 0

    if previous_close != 0:

        change_pct = (
            (
                close -
                previous_close
            )
            /
            previous_close
            *
            100
        )

    strength_score = (
        calculate_strength_score(
            df
        )
    )

    ai_score = (
        calculate_ai_score(
            strength_score,
            core["core_score"],
            change_pct
        )
    )

    volume_ma5 = safe_float(
        latest["VOL_MA5"]
    )

    volume = safe_int(
        latest["Volume"]
    )

    volume_ratio = None

    if (
        volume_ma5
        and
        volume_ma5 > 0
    ):

        volume_ratio = (
            volume /
            volume_ma5
        )

    return {

        "code":
            item["code"],

        "symbol":
            yahoo_symbol(
                item["code"],
                item["market"]
            ),

        "name":
            item["name"],

        "type":
            item["type"],

        "market":
            item["market"],

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
            volume,

        "volume_ma5":
            volume_ma5,

        "volume_ratio":
            safe_float(
                volume_ratio
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

        "macd_golden_cross":
            core[
                "macd_golden_cross"
            ],

        "rsi_over_50":
            core[
                "rsi_over_50"
            ],

        "kd_golden_cross":
            core[
                "kd_golden_cross"
            ],

        "volume_expand":
            core[
                "volume_expand"
            ],

        "price_over_ma20":
            core[
                "price_over_ma20"
            ],

        "ma20_up":
            core[
                "ma20_up"
            ],

        "core_score":
            core[
                "core_score"
            ],

        "core_total":
            6,

        "core_pass":
            core[
                "core_pass"
            ],

        "strength_score":
            strength_score,

        "ai_score":
            ai_score,

        "signal":
            make_signal(
                core["core_pass"],
                core["core_score"],
                ai_score
            )

    }


# ================================================================
# 從 DB 讀取 Universe
# ================================================================

def load_active_universe():

    conn = get_connection()

    try:

        rows = conn.execute(
            """
            SELECT
                market,
                code,
                name,
                type,
                is_etf
            FROM securities
            WHERE active = 1
            ORDER BY market, code
            """
        ).fetchall()

    finally:

        conn.close()

    universe = []

    for row in rows:

        universe.append({

            "market": row[0],

            "code": row[1],

            "name": row[2],

            "type": row[3],

            "is_etf":
                bool(row[4])

        })

    return universe


# ================================================================
# 掃描
# ================================================================

def scan_universe(
    universe
):

    stocks = []

    etfs = []

    failed = []

    total = len(
        universe
    )

    print(
        "================================================"
    )

    print(
        f"V9.0 本地資料庫掃描：{total}"
    )

    print(
        "不重新下載歷史行情"
    )

    print(
        "================================================"
    )

    for index, item in enumerate(
        universe,
        start=1
    ):

        try:

            df = load_history(
                item["market"],
                item["code"]
            )

            if df is None:

                failed.append({

                    "code":
                        item["code"],

                    "symbol":
                        yahoo_symbol(
                            item["code"],
                            item["market"]
                        ),

                    "name":
                        item["name"],

                    "market":
                        item["market"],

                    "reason":
                        "no_history"

                })

                continue

            record = (
                build_security_record(
                    item,
                    df
                )
            )

            if record is None:

                failed.append({

                    "code":
                        item["code"],

                    "symbol":
                        yahoo_symbol(
                            item["code"],
                            item["market"]
                        ),

                    "name":
                        item["name"],

                    "market":
                        item["market"],

                    "reason":
                        "insufficient_history"

                })

                continue

            if item["type"] == "etf":

                etfs.append(
                    record
                )

            else:

                stocks.append(
                    record
                )

        except Exception as exc:

            failed.append({

                "code":
                    item["code"],

                "symbol":
                    yahoo_symbol(
                        item["code"],
                        item["market"]
                    ),

                "name":
                    item["name"],

                "market":
                    item["market"],

                "reason":
                    str(exc)

            })

        if (
            index % 250 == 0
            or
            index == total
        ):

            print(
                f"[掃描進度] "
                f"{index}/{total}"
            )

    return (
        stocks,
        etfs,
        failed
    )


# ================================================================
# 排序
# ================================================================

def sort_by_ai_score(
    items
):

    return sorted(
        items,
        key=lambda x:
            float(
                x.get(
                    "ai_score",
                    0
                )
                or 0
            ),
        reverse=True
    )


# ================================================================
# Top30
# ================================================================

def build_top30(
    stocks
):

    return sort_by_ai_score(
        stocks
    )[:TOP30]


# ================================================================
# 今日 6/6
# ================================================================

def build_today_selected(
    stocks
):

    selected = [

        item

        for item in stocks

        if item.get(
            "core_pass"
        )
        is True

    ]

    return sort_by_ai_score(
        selected
    )


# ================================================================
# ETF
# ================================================================

def build_etf_result(
    etfs
):

    return sort_by_ai_score(
        etfs
    )


# ================================================================
# A/B 回測
# ================================================================

def backtest_stock(
    df
):

    if (
        df is None
        or
        len(df) < BACKTEST_MIN_HISTORY
    ):
        return None

    df = calculate_indicators(
        df
    )

    results = {

        "A_today_cross": {},

        "B_current_bullish": {}

    }

    for horizon in BACKTEST_HORIZONS:

        a_returns = []

        b_returns = []

        for i in range(
            1,
            len(df) - horizon
        ):

            today = df.iloc[i]

            yesterday = df.iloc[i - 1]

            future = df.iloc[
                i + horizon
            ]

            today_close = (
                today["Close"]
            )

            future_close = (
                future["Close"]
            )

            if (
                pd.isna(
                    today_close
                )
                or
                pd.isna(
                    future_close
                )
            ):
                continue

            if today_close == 0:
                continue

            future_return = (

                (
                    future_close -
                    today_close
                )
                /
                today_close
                *
                100

            )

            # A
            macd_cross = (

                today["MACD"]
                >
                today["MACD_SIGNAL"]

                and

                yesterday["MACD"]
                <=
                yesterday["MACD_SIGNAL"]

            )

            if macd_cross:

                a_returns.append(
                    future_return
                )

            # B
            current_bullish = (

                today["MACD"]
                >
                today["MACD_SIGNAL"]

                and

                today["RSI"]
                >
                50

                and

                today["K"]
                >
                today["D"]

                and

                today["Close"]
                >
                today["MA20"]

            )

            if current_bullish:

                b_returns.append(
                    future_return
                )

        def summarize(
            returns
        ):

            if not returns:

                return {

                    "signals": 0,

                    "wins": 0,

                    "losses": 0,

                    "win_rate": 0,

                    "average_return": 0

                }

            wins = sum(
                1
                for value in returns
                if value > 0
            )

            losses = (
                len(returns) -
                wins
            )

            return {

                "signals":
                    len(returns),

                "wins":
                    wins,

                "losses":
                    losses,

                "win_rate":
                    round(
                        wins /
                        len(returns)
                        *
                        100,
                        2
                    ),

                "average_return":
                    round(
                        float(
                            np.mean(
                                returns
                            )
                        ),
                        4
                    )

            }

        results[
            "A_today_cross"
        ][
            f"{horizon}d"
        ] = summarize(
            a_returns
        )

        results[
            "B_current_bullish"
        ][
            f"{horizon}d"
        ] = summarize(
            b_returns
        )

    return results


# ================================================================
# 整體回測
# ================================================================

def build_backtest(
    stocks
):

    aggregate = {

        "A_today_cross": {
            h: []
            for h in BACKTEST_HORIZONS
        },

        "B_current_bullish": {
            h: []
            for h in BACKTEST_HORIZONS
        }

    }

    count = 0

    total = len(
        stocks
    )

    for index, item in enumerate(
        stocks,
        start=1
    ):

        try:

            df = load_history(
                item["market"],
                item["code"]
            )

            if (
                df is None
                or
                len(df)
                <
                BACKTEST_MIN_HISTORY
            ):
                continue

            result = backtest_stock(
                df
            )

            if result is None:
                continue

            count += 1

            for strategy in (
                "A_today_cross",
                "B_current_bullish"
            ):

                for horizon in (
                    BACKTEST_HORIZONS
                ):

                    aggregate[
                        strategy
                    ][
                        horizon
                    ].append(
                        result[
                            strategy
                        ][
                            f"{horizon}d"
                        ]
                    )

        except Exception:

            continue

        if (
            index % 250 == 0
            or
            index == total
        ):

            print(
                f"[回測進度] "
                f"{index}/{total}"
            )

    def merge(
        values
    ):

        if not values:

            return {

                "signals": 0,

                "wins": 0,

                "losses": 0,

                "win_rate": 0,

                "average_return": 0

            }

        signals = sum(
            x["signals"]
            for x in values
        )

        wins = sum(
            x["wins"]
            for x in values
        )

        losses = sum(
            x["losses"]
            for x in values
        )

        weighted = []

        for x in values:

            if x["signals"] > 0:

                weighted.extend(

                    [x["average_return"]]
                    *
                    x["signals"]

                )

        average_return = (

            float(
                np.mean(
                    weighted
                )
            )

            if weighted

            else 0

        )

        return {

            "signals":
                signals,

            "wins":
                wins,

            "losses":
                losses,

            "win_rate":
                round(
                    wins /
                    signals *
                    100,
                    2
                )
                if signals
                else 0,

            "average_return":
                round(
                    average_return,
                    4
                )

        }

    final = {

        "status":
            "completed",

        "comparison_horizon":
            10,

        "strategy_A":
            "當日黃金交叉",

        "strategy_B":
            "目前維持多方",

        "strategies": {

            "A_today_cross": {},

            "B_current_bullish": {}

        }

    }

    for strategy in (
        "A_today_cross",
        "B_current_bullish"
    ):

        for horizon in (
            BACKTEST_HORIZONS
        ):

            final[
                "strategies"
            ][
                strategy
            ][
                f"{horizon}d"
            ] = merge(
                aggregate[
                    strategy
                ][
                    horizon
                ]
            )

    a10 = final[
        "strategies"
    ][
        "A_today_cross"
    ][
        "10d"
    ][
        "win_rate"
    ]

    b10 = final[
        "strategies"
    ][
        "B_current_bullish"
    ][
        "10d"
    ][
        "win_rate"
    ]

    if b10 > a10:

        final[
            "better_by_win_rate"
        ] = "B_current_bullish"

    elif a10 > b10:

        final[
            "better_by_win_rate"
        ] = "A_today_cross"

    else:

        final[
            "better_by_win_rate"
        ] = "tie"

    final[
        "backtest_stock_count"
    ] = count

    return final


# ================================================================
# Record 驗證
# ================================================================

def validate_record(
    item,
    is_etf_record=False
):

    required = {

        "code",
        "symbol",
        "name",
        "type",
        "market",

        "price",
        "close",
        "previous_close",
        "change_pct",

        "volume",
        "volume_ma5",
        "volume_ratio",

        "rsi",
        "k",
        "d",

        "macd",
        "macd_signal",
        "macd_hist",

        "ma5",
        "ma20",
        "ma60",

        "strength_score",
        "ai_score",
        "signal"

    }

    if not is_etf_record:

        required.update({

            "macd_golden_cross",

            "rsi_over_50",

            "kd_golden_cross",

            "volume_expand",

            "price_over_ma20",

            "ma20_up",

            "core_score",

            "core_total",

            "core_pass"

        })

    missing = [

        field

        for field in required

        if field not in item

    ]

    if missing:

        raise RuntimeError(
            "資料欄位缺失："
            +
            ",".join(
                missing
            )
        )

    if not item["symbol"]:

        raise RuntimeError(
            "symbol 空白"
        )

    if item["price"] is None:

        raise RuntimeError(
            f"{item['symbol']} price 空白"
        )


# ================================================================
# Atomic JSON
# ================================================================

def atomic_write_json(
    path,
    data
):

    directory = os.path.dirname(
        path
    )

    fd, temp_path = tempfile.mkstemp(
        prefix=".tmp_",
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
            os.remove(
                temp_path
            )
        except Exception:
            pass

        raise


# ================================================================
# 建立 prices.json
# ================================================================

def build_prices_json(
    stocks,
    etfs,
    failed,
    universe_statistics,
    backtest,
    history_mode,
    official_update_count
):

    now = datetime.now(
        TW_TZ
    )

    today = now.strftime(
        "%Y-%m-%d"
    )

    updated_at_tw = (
        now.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    for item in stocks:

        validate_record(
            item,
            False
        )

    for item in etfs:

        validate_record(
            item,
            True
        )

    today_selected = (
        build_today_selected(
            stocks
        )
    )

    top30 = build_top30(
        stocks
    )

    etfs_sorted = (
        build_etf_result(
            etfs
        )
    )

    stock_tw = sum(
        1
        for x in stocks
        if x["market"] == "TW"
    )

    stock_two = sum(
        1
        for x in stocks
        if x["market"] == "TWO"
    )

    etf_tw = sum(
        1
        for x in etfs
        if x["market"] == "TW"
    )

    etf_two = sum(
        1
        for x in etfs
        if x["market"] == "TWO"
    )

    return {

        "version":
            VERSION,

        "schema_version":
            SCHEMA_VERSION,

        "status":
            "success",

        "updated_at":
            now.isoformat(),

        "updated_at_tw":
            updated_at_tw,

        "date":
            today,

        "data_engine":
            "V9.0_INCREMENTAL_SQLITE",

        "history_mode":
            history_mode,

        "official_update_count":
            official_update_count,

        "database":
            "Data/market.db",

        "stocks":
            stocks,

        "etfs":
            etfs_sorted,

        "today_selected":
            today_selected,

        "top30":
            top30,

        "backtest_summary":
            backtest,

        "statistics": {

            "stock_count":
                len(stocks),

            "stock_twse_count":
                stock_tw,

            "stock_tpex_count":
                stock_two,

            "etf_count":
                len(etfs),

            "etf_twse_count":
                etf_tw,

            "etf_tpex_count":
                etf_two,

            "top30_count":
                len(top30),

            "today_selected_count":
                len(today_selected),

            "core_6_count":
                len(today_selected),

            "backtest_stock_count":
                backtest.get(
                    "backtest_stock_count",
                    0
                ),

            "failed_count":
                len(failed),

            "twse_stock_universe":
                universe_statistics[
                    "twse_stock_universe"
                ],

            "tpex_stock_universe":
                universe_statistics[
                    "tpex_stock_universe"
                ],

            "twse_etf_universe":
                universe_statistics[
                    "twse_etf_universe"
                ],

            "tpex_etf_universe":
                universe_statistics[
                    "tpex_etf_universe"
                ],

            "stock_universe":
                universe_statistics[
                    "stock_universe"
                ],

            "etf_universe":
                universe_statistics[
                    "etf_universe"
                ],

            "total_universe":
                universe_statistics[
                    "total_universe"
                ]

        },

        "core_conditions": {

            "total":
                6,

            "names":
                CORE_CONDITIONS,

            "definition":
                "目前維持多方",

            "rules": {

                "macd_golden_cross":
                    "MACD > MACD Signal",

                "rsi_over_50":
                    "RSI > 50",

                "kd_golden_cross":
                    "K > D",

                "volume_expand":
                    "Volume >= MA5 Volume × 1.5",

                "price_over_ma20":
                    "Close > MA20",

                "ma20_up":
                    "MA20 今日 > MA20 昨日"

            }

        },

        "failed":
            failed

    }


# ================================================================
# 主程式
# ================================================================

def main():

    start_time = datetime.now(
        TW_TZ
    )

    print(
        "================================================"
    )

    print(
        "台股 AI 選股系統 fetch_data.py V9.0"
    )

    print(
        "增量 SQLite Data Engine"
    )

    print(
        "================================================"
    )

    print(
        "開始時間："
        +
        start_time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print(
        "================================================"
    )

    # ------------------------------------------------------------
    # 1. 初始化 DB
    # ------------------------------------------------------------

    init_database()

    # ------------------------------------------------------------
    # 2. Universe
    # ------------------------------------------------------------

    (
        universe,
        universe_statistics
    ) = build_universe()

    if not universe:

        raise RuntimeError(
            "Universe 為空"
        )

    save_universe(
        universe
    )

    # ------------------------------------------------------------
    # 3. 歷史資料模式
    # ------------------------------------------------------------

    has_history = (
        database_has_history()
    )

    if not has_history:

        history_mode = (
            "BOOTSTRAP_YAHOO_BATCH"
        )

        bootstrap_history(
            universe
        )

    else:

        history_mode = (
            "INCREMENTAL_OFFICIAL"
        )

        print(
            "================================================"
        )

        print(
            "已有歷史資料庫"
        )

        print(
            "跳過 Yahoo 歷史下載"
        )

        print(
            "改用官方每日增量更新"
        )

        print(
            "================================================"
        )

    # ------------------------------------------------------------
    # 4. 官方每日更新
    # ------------------------------------------------------------

    official_update_count = 0

    if history_mode == (
        "INCREMENTAL_OFFICIAL"
    ):

        official_update_count = (
            fetch_official_daily(
                universe
            )
        )

    else:

        # Bootstrap 後仍嘗試用官方當日資料
        # 覆蓋最新一天
        try:

            official_update_count = (
                fetch_official_daily(
                    universe
                )
            )

            history_mode = (
                "BOOTSTRAP_YAHOO_BATCH"
                "+"
                "OFFICIAL_DAILY"
            )

        except Exception as exc:

            print(
                f"[WARN] "
                f"官方當日更新失敗：{exc}"
            )

    # ------------------------------------------------------------
    # 5. 確認資料庫
    # ------------------------------------------------------------

    history_count = (
        count_history_records()
    )

    if history_count == 0:

        raise RuntimeError(
            "market.db 沒有任何歷史資料"
        )

    latest_date = (
        database_latest_date()
    )

    print(
        "------------------------------------------------"
    )

    print(
        f"歷史資料筆數：{history_count}"
    )

    print(
        f"最新交易日期：{latest_date}"
    )

    print(
        "------------------------------------------------"
    )

    # ------------------------------------------------------------
    # 6. 掃描
    # ------------------------------------------------------------

    active_universe = (
        load_active_universe()
    )

    (
        stocks,
        etfs,
        failed
    ) = scan_universe(
        active_universe
    )

    print(
        "================================================"
    )

    print(
        "掃描結果"
    )

    print(
        "================================================"
    )

    print(
        f"股票成功：{len(stocks)}"
    )

    print(
        f"ETF 成功：{len(etfs)}"
    )

    print(
        f"失敗：{len(failed)}"
    )

    print(
        "------------------------------------------------"
    )

    print(
        f"上市股票成功："
        f"{sum(1 for x in stocks if x['market'] == 'TW')}"
    )

    print(
        f"上櫃股票成功："
        f"{sum(1 for x in stocks if x['market'] == 'TWO')}"
    )

    print(
        f"上市 ETF 成功："
        f"{sum(1 for x in etfs if x['market'] == 'TW')}"
    )

    print(
        f"上櫃 ETF 成功："
        f"{sum(1 for x in etfs if x['market'] == 'TWO')}"
    )

    # ------------------------------------------------------------
    # 7. 強制檢查
    # ------------------------------------------------------------

    if len(stocks) < 500:

        raise RuntimeError(
            "股票成功數量異常過低："
            f"{len(stocks)}"
        )

    if len(etfs) == 0:

        raise RuntimeError(
            "ETF 掃描結果為 0"
        )

    # ------------------------------------------------------------
    # 8. 回測
    # ------------------------------------------------------------

    print(
        "================================================"
    )

    print(
        "開始 A/B 歷史回測"
    )

    print(
        "使用 SQLite 既有歷史資料"
    )

    print(
        "不重新下載"
    )

    print(
        "================================================"
    )

    backtest = build_backtest(
        stocks
    )

    print(
        "------------------------------------------------"
    )

    print(
        f"回測股票數："
        f"{backtest['backtest_stock_count']}"
    )

    print(
        f"A 10日勝率："
        f"{backtest['strategies']['A_today_cross']['10d']['win_rate']}%"
    )

    print(
        f"B 10日勝率："
        f"{backtest['strategies']['B_current_bullish']['10d']['win_rate']}%"
    )

    print(
        f"勝率較佳："
        f"{backtest['better_by_win_rate']}"
    )

    # ------------------------------------------------------------
    # 9. 建立 prices.json
    # ------------------------------------------------------------

    prices = build_prices_json(

        stocks,

        etfs,

        failed,

        universe_statistics,

        backtest,

        history_mode,

        official_update_count

    )

    # ------------------------------------------------------------
    # 10. Data Contract 驗證
    # ------------------------------------------------------------

    if prices[
        "version"
    ] != VERSION:

        raise RuntimeError(
            "version 驗證失敗"
        )

    if prices[
        "schema_version"
    ] != SCHEMA_VERSION:

        raise RuntimeError(
            "schema_version 驗證失敗"
        )

    if prices[
        "status"
    ] != "success":

        raise RuntimeError(
            "status 驗證失敗"
        )

    if not prices[
        "stocks"
    ]:

        raise RuntimeError(
            "stocks 為空"
        )

    if not prices[
        "etfs"
    ]:

        raise RuntimeError(
            "etfs 為空"
        )

    expected_top30 = min(
        TOP30,
        len(
            prices["stocks"]
        )
    )

    if len(
        prices["top30"]
    ) != expected_top30:

        raise RuntimeError(
            "Top30 數量驗證失敗"
        )

    # ------------------------------------------------------------
    # 11. 寫 prices.json
    # ------------------------------------------------------------

    atomic_write_json(
        OUTPUT_FILE,
        prices
    )

    # ------------------------------------------------------------
    # 12. backtest.json
    # ------------------------------------------------------------

    atomic_write_json(
        BACKTEST_FILE,
        backtest
    )

    # ------------------------------------------------------------
    # 13. 完成
    # ------------------------------------------------------------

    end_time = datetime.now(
        TW_TZ
    )

    elapsed = (
        end_time -
        start_time
    ).total_seconds()

    print(
        "================================================"
    )

    print(
        "V9.0 完成"
    )

    print(
        "================================================"
    )

    print(
        f"Universe："
        f"{len(universe)}"
    )

    print(
        f"歷史資料："
        f"{history_count}"
    )

    print(
        f"上市股票："
        f"{sum(1 for x in stocks if x['market'] == 'TW')}"
    )

    print(
        f"上櫃股票："
        f"{sum(1 for x in stocks if x['market'] == 'TWO')}"
    )

    print(
        f"上市 ETF："
        f"{sum(1 for x in etfs if x['market'] == 'TW')}"
    )

    print(
        f"上櫃 ETF："
        f"{sum(1 for x in etfs if x['market'] == 'TWO')}"
    )

    print(
        f"股票總成功："
        f"{len(stocks)}"
    )

    print(
        f"ETF 總成功："
        f"{len(etfs)}"
    )

    print(
        f"今日 6/6："
        f"{len(prices['today_selected'])}"
    )

    print(
        f"Top30："
        f"{len(prices['top30'])}"
    )

    print(
        f"失敗："
        f"{len(failed)}"
    )

    print(
        f"回測股票："
        f"{backtest['backtest_stock_count']}"
    )

    print(
        f"耗時："
        f"{elapsed:.1f} 秒"
    )

    print(
        "------------------------------------------------"
    )

    print(
        f"prices.json："
        f"{OUTPUT_FILE}"
    )

    print(
        f"backtest.json："
        f"{BACKTEST_FILE}"
    )

    print(
        f"market.db："
        f"{DATABASE_FILE}"
    )

    print(
        "------------------------------------------------"
    )

    print(
        "V9.0 Data Contract：OK"
    )

    print(
        f"schema_version："
        f"{SCHEMA_VERSION}"
    )

    print(
        f"history_mode："
        f"{history_mode}"
    )

    print(
        "status：success"
    )

    print(
        "================================================"
    )


# ================================================================
# Entry
# ================================================================

if __name__ == "__main__":

    main()
