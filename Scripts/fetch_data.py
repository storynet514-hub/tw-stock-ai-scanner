# ================================================================
# 台股 AI 選股系統
# fetch_data.py V10.0 FINAL
#
# ================================================================
#
# V10.0 DATA ENGINE
#
# V10.0 核心架構：
#
#   TWSE / TPEx Universe
#             ↓
#       securities table
#             ↓
#   第一次：Yahoo 批次建立歷史資料
#   每日：官方收盤資料增量更新
#             ↓
#          SQLite
#       Data/market.db
#             ↓
#       技術指標計算
#             ↓
#       核心 6/6
#             ↓
#       AI Score
#             ↓
#       今日精選 / Top10
#             ↓
#   ┌─────────────────────────────┐
#   │ prices.json                 │ ← 完整後台資料
#   │ backtest.json               │ ← 回測
#   │ ui_data.json                │ ← 前台 UI 專用
#   │ securities.json             │ ← 個股搜尋專用
#   └─────────────────────────────┘
#
# ================================================================
#
# V10.0 重要原則
#
# 1. index.html 不讀巨大 prices.json
#
# 2. index.html 主要讀：
#
#       Data/ui_data.json
#       Data/securities.json
#
# 3. securities.json 由本程式自動生成
#
# 4. ui_data.json 由本程式自動生成
#
# 5. 今日精選 = 核心 6/6
#
# 6. Top10 = 股票 AI Score 前 10
#
# 7. ETF 與債券 ETF 分開
#
# 8. 不把「今日精選」當成系統訊號文字
#
# 9. 原始 prices.json 保留，不破壞後台資料
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

VERSION = "V10.0"

SCHEMA_VERSION = "prices.v10"

UI_SCHEMA_VERSION = "ui.v10"

SECURITIES_SCHEMA_VERSION = "securities.v10"


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


UI_DATA_FILE = os.path.join(
    DATA_DIR,
    "ui_data.json"
)


SECURITIES_FILE = os.path.join(
    DATA_DIR,
    "securities.json"
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


# ================================================================
# 前台數量設定
# ================================================================

TOP10 = 10

TOP30 = 30

ETF_TOP10 = 10

BOND_TOP10 = 10


# ================================================================
# Yahoo 批次下載設定
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
#
# 注意：
#
# 「債券 ETF」不能在這裡直接全部排除。
#
# V10.0 會先判斷 ETF，再判斷 bond ETF。
#
# ================================================================

INVALID_SECURITY_KEYWORDS = [

    "權證",
    "認購權證",
    "認售權證",
    "牛熊證",

    "ETN",

    "海外存託憑證",
    "存託憑證",

    "認購證",
    "認售證"

]


# ================================================================
# 債券 ETF 關鍵字
# ================================================================

BOND_KEYWORDS = [

    "債券",
    "公司債",
    "公債",
    "國債",
    "政府債",
    "美債",
    "國庫券",
    "投資級債",
    "高收益債",
    "非投資等級債",
    "短天期債",
    "短期債",
    "中期債",
    "長期債",
    "金融債",
    "美元債",
    "人民幣債",
    "新興市場債",
    "全球債",
    "全球高收益",
    "收益債",
    "Treasury",
    "Bond",
    "Bonds",
    "Corporate Bond",
    "Government Bond",
    "High Yield"

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


# ================================================================
# 初始化 SQLite
# ================================================================

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
                is_bond INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                updated_at TEXT,
                PRIMARY KEY (market, code)
            )
            """
        )

        # --------------------------------------------------------
        # V9 → V10 舊 DB 相容
        # --------------------------------------------------------

        columns = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(securities)"
            ).fetchall()
        ]

        if "is_bond" not in columns:

            conn.execute(
                """
                ALTER TABLE securities
                ADD COLUMN is_bond INTEGER DEFAULT 0
                """
            )

        # --------------------------------------------------------

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
                PRIMARY KEY (
                    market,
                    code,
                    date
                )
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_daily_code_date
            ON daily_prices (
                market,
                code,
                date
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_daily_date
            ON daily_prices (
                date
            )
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

        if value is None:
            return None

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

        if value is None:
            return 0

        if pd.isna(value):
            return 0

        return int(
            float(value)
        )

    except Exception:

        return 0


# ================================================================
# 商品名稱是否為特殊商品
# ================================================================

def is_invalid_security(name):

    text = clean_text(
        name
    )

    return any(
        keyword.lower()
        in text.lower()
        for keyword
        in INVALID_SECURITY_KEYWORDS
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
# 債券 ETF 判斷
# ================================================================

def is_bond_etf(
    code,
    name,
    security_type=None
):

    if not is_etf(
        code,
        name,
        security_type
    ):
        return False

    text = clean_text(
        name
    )

    text_lower = text.lower()

    return any(
        keyword.lower()
        in text_lower
        for keyword
        in BOND_KEYWORDS
    )


# ================================================================
# 商品類型
# ================================================================

def classify_security(
    code,
    name,
    security_type=None
):

    if is_bond_etf(
        code,
        name,
        security_type
    ):

        return (
            "bond",
            True,
            True
        )

    if is_etf(
        code,
        name,
        security_type
    ):

        return (
            "etf",
            True,
            False
        )

    return (
        "stock",
        False,
        False
    )


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

        (
            security_type,
            etf,
            bond
        ) = classify_security(
            code,
            name
        )

        universe.append({

            "market": "TW",

            "code": code,

            "name": name,

            "type":
                security_type,

            "is_etf":
                etf,

            "is_bond":
                bond

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

            or

            item.get(
                "Code"
            )

            or

            item.get(
                "股票代號"
            )

        )

        name = clean_text(

            item.get(
                "CompanyName"
            )

            or

            item.get(
                "Name"
            )

            or

            item.get(
                "公司名稱"
            )

        )

        if not code:
            continue

        if not code.isalnum():
            continue

        if is_invalid_security(
            name
        ):
            continue

        (
            security_type,
            etf,
            bond
        ) = classify_security(
            code,
            name
        )

        universe.append({

            "market": "TWO",

            "code": code,

            "name": name,

            "type":
                security_type,

            "is_etf":
                etf,

            "is_bond":
                bond

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
        "V10.0 建立完整 Universe"
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
            ),

        "twse_bond_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TW"
                and x["type"] == "bond"
            ),

        "tpex_bond_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TWO"
                and x["type"] == "bond"
            )

    }

    statistics["stock_universe"] = (

        statistics[
            "twse_stock_universe"
        ]

        +

        statistics[
            "tpex_stock_universe"
        ]

    )

    statistics["etf_universe"] = (

        statistics[
            "twse_etf_universe"
        ]

        +

        statistics[
            "tpex_etf_universe"
        ]

    )

    statistics["bond_universe"] = (

        statistics[
            "twse_bond_universe"
        ]

        +

        statistics[
            "tpex_bond_universe"
        ]

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
        f"債券商品："
        f"{statistics['bond_universe']}"
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

                1
                if item["is_bond"]
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
                is_bond,
                active,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?
            )

            ON CONFLICT(
                market,
                code
            )

            DO UPDATE SET

                name=excluded.name,

                type=excluded.type,

                is_etf=excluded.is_etf,

                is_bond=excluded.is_bond,

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
# DB 最新日期
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
# Yahoo Bootstrap
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
        "Yahoo Finance 批次下載 2 年資料"
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
                f"{min(start + len(batch), total)}"
                f"/{total}"
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

                        if not isinstance(
                            downloaded.columns,
                            pd.MultiIndex
                        ):

                            continue

                        first_level = (
                            downloaded
                            .columns
                            .get_level_values(0)
                        )

                        if symbol not in first_level:

                            continue

                        df = downloaded[
                            symbol
                        ].copy()

                    if df.empty:
                        continue

                    df = df.reset_index()

                    if "Date" not in df.columns:

                        continue

                    for _, row in df.iterrows():

                        date_value = row[
                            "Date"
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
                    INSERT OR REPLACE INTO
                    daily_prices (

                        market,
                        code,
                        date,
                        open,
                        high,
                        low,
                        close,
                        volume

                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
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
# 官方資料解析
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

            item.get(
                "OpeningPrice"
            )

            or

            item.get(
                "Open"
            )

        )

        high = (

            item.get(
                "HighestPrice"
            )

            or

            item.get(
                "High"
            )

        )

        low = (

            item.get(
                "LowestPrice"
            )

            or

            item.get(
                "Low"
            )

        )

        close = (

            item.get(
                "ClosingPrice"
            )

            or

            item.get(
                "Close"
            )

        )

        volume = (

            item.get(
                "TradeVolume"
            )

            or

            item.get(
                "Volume"
            )

        )

    else:

        code = clean_code(

            item.get(
                "SecuritiesCompanyCode"
            )

            or

            item.get(
                "Code"
            )

            or

            item.get(
                "股票代號"
            )

        )

        name = clean_text(

            item.get(
                "CompanyName"
            )

            or

            item.get(
                "Name"
            )

            or

            item.get(
                "公司名稱"
            )

        )

        open_price = (

            item.get(
                "Open"
            )

            or

            item.get(
                "OpeningPrice"
            )

            or

            item.get(
                "開盤價"
            )

        )

        high = (

            item.get(
                "High"
            )

            or

            item.get(
                "HighestPrice"
            )

            or

            item.get(
                "最高價"
            )

        )

        low = (

            item.get(
                "Low"
            )

            or

            item.get(
                "LowestPrice"
            )

            or

            item.get(
                "最低價"
            )

        )

        close = (

            item.get(
                "Close"
            )

            or

            item.get(
                "ClosingPrice"
            )

            or

            item.get(
                "收盤價"
            )

        )

        volume = (

            item.get(
                "Volume"
            )

            or

            item.get(
                "TradeVolume"
            )

            or

            item.get(
                "成交股數"
            )

        )

    return {

        "code":
            code,

        "name":
            name,

        "open":
            safe_float(
                open_price
            ),

        "high":
            safe_float(
                high
            ),

        "low":
            safe_float(
                low
            ),

        "close":
            safe_float(
                close
            ),

        "volume":
            safe_int(
                volume
            )

    }


# ================================================================
# 官方每日更新
# ================================================================

# ================================================================
# TWSE Universe
# ================================================================

def fetch_twse_universe():

    import time

    print("")
    print("=" * 64)
    print("TWSE Universe")
    print("=" * 64)

    url = TWSE_UNIVERSE_URL

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }

    max_attempts = 5

    last_error = None

    # ------------------------------------------------------------
    # 主要來源：TWSE OpenAPI
    # ------------------------------------------------------------

    for attempt in range(
        1,
        max_attempts + 1
    ):

        try:

            print(
                f"TWSE Universe API "
                f"第 {attempt}/{max_attempts} 次嘗試"
            )

            response = requests.get(
                url,
                headers=headers,
                timeout=30
            )

            print(
                f"HTTP Status："
                f"{response.status_code}"
            )

            content_type = response.headers.get(
                "Content-Type",
                ""
            )

            print(
                f"Content-Type："
                f"{content_type}"
            )

            # ----------------------------------------------------
            # HTTP 狀態檢查
            # ----------------------------------------------------

            response.raise_for_status()

            # ----------------------------------------------------
            # 空內容檢查
            # ----------------------------------------------------

            raw_text = response.text.strip()

            if not raw_text:

                raise RuntimeError(
                    "TWSE API 回傳空內容"
                )

            print(
                f"TWSE 回傳資料長度："
                f"{len(raw_text)} bytes"
            )

            # ----------------------------------------------------
            # JSON 解析
            #
            # 不直接使用 response.json()
            # 避免 API 回傳 HTML / 空內容時直接
            # JSONDecodeError 導致整個 Actions 終止
            # ----------------------------------------------------

            try:

                data = response.json()

            except ValueError as exc:

                preview = raw_text[:300]

                raise RuntimeError(
                    "TWSE API 回傳內容不是有效 JSON。"
                    f"內容前 300 字：{preview}"
                ) from exc

            # ----------------------------------------------------
            # 資料格式檢查
            # ----------------------------------------------------

            if not isinstance(
                data,
                list
            ):

                raise RuntimeError(
                    "TWSE Universe API 格式錯誤："
                    f"預期 list，實際為 "
                    f"{type(data).__name__}"
                )

            if len(data) == 0:

                raise RuntimeError(
                    "TWSE Universe API 回傳空 list"
                )

            print(
                f"TWSE Universe 原始資料："
                f"{len(data)} 筆"
            )

            # ----------------------------------------------------
            # 建立 Universe
            # ----------------------------------------------------

            universe = []

            for item in data:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

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

                (
                    security_type,
                    etf,
                    bond
                ) = classify_security(
                    code,
                    name
                )

                universe.append({

                    "market": "TW",

                    "code": code,

                    "name": name,

                    "type":
                        security_type,

                    "is_etf":
                        etf,

                    "is_bond":
                        bond

                })

            # ----------------------------------------------------
            # 最終資料量檢查
            # ----------------------------------------------------

            if len(universe) < 50:

                raise RuntimeError(
                    "TWSE Universe 過濾後資料異常："
                    f"{len(universe)} 筆"
                )

            print(
                f"TWSE Universe 建立成功："
                f"{len(universe)} 筆"
            )

            print("=" * 64)

            return universe

        except Exception as exc:

            last_error = exc

            print(
                f"TWSE Universe 第 "
                f"{attempt} 次失敗："
                f"{exc}"
            )

            if attempt < max_attempts:

                wait_seconds = attempt * 3

                print(
                    f"{wait_seconds} 秒後重試..."
                )

                time.sleep(
                    wait_seconds
                )

    # ============================================================
    # TWSE API 五次都失敗
    #
    # 不在這裡假裝有資料。
    #
    # 直接回報明確錯誤，讓後續 fallback 機制
    # （如果 V10.0 已經存在）接手。
    # ============================================================

    print("")
    print("=" * 64)
    print("ERROR：TWSE Universe API 無法取得")
    print("=" * 64)
    print(
        f"最後錯誤：{last_error}"
    )
    print("=" * 64)

    raise RuntimeError(
        "TWSE Universe API 在 "
        f"{max_attempts} 次嘗試後仍無法取得有效 JSON。"
        f"最後錯誤：{last_error}"
    )

# ================================================================
# 掃描
# ================================================================

def scan_universe(
    universe
):

    stocks = []

    etfs = []

    bonds = []

    failed = []

    total = len(
        universe
    )

    print(
        "================================================"
    )

    print(
        f"V10.0 本地資料庫掃描：{total}"
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

            if item["type"] == "bond":

                bonds.append(
                    record
                )

            elif item["type"] == "etf":

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

        bonds,

        failed

    )


# ================================================================
# AI Score 排序
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

                or

                0

            ),

        reverse=True

    )


# ================================================================
# Top10
# ================================================================

def build_top10(
    stocks
):

    return sort_by_ai_score(
        stocks
    )[:TOP10]


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
# 今日精選
#
# 唯一條件：
#
# core_score == 6
#
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
# 債券
# ================================================================

def build_bond_result(
    bonds
):

    return sort_by_ai_score(
        bonds
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

        len(df) <
        BACKTEST_MIN_HISTORY

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

            yesterday = df.iloc[
                i - 1
            ]

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

            # ----------------------------------------------------
            # A
            # 當日 MACD 黃金交叉
            # ----------------------------------------------------

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

            # ----------------------------------------------------
            # B
            # 目前維持多方
            # ----------------------------------------------------

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

                for value
                in returns

                if value > 0

            )

            losses = (

                len(returns)

                -

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

            for h
            in BACKTEST_HORIZONS

        },

        "B_current_bullish": {

            h: []

            for h
            in BACKTEST_HORIZONS

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

            else

            0

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

                    signals

                    *

                    100,

                    2

                )

                if signals

                else

                0,

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

        "signal",
        "rating",
        "recommendation"

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

        for field
        in required

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

            f"{item['symbol']} "
            f"price 空白"

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
# 精簡 UI Record
#
# 這裡故意不把所有技術指標都塞進 ui_data.json。
#
# 前台首頁只需要：
#
#   股票名稱
#   現價
#   漲跌
#   核心分數
#   AI Score
#   訊號
#   評級
#   建議
#
# 詳細資料仍保留在 prices.json。
#
# ================================================================

def make_ui_record(
    item,
    include_core=True
):

    record = {

        "code":
            item["code"],

        "symbol":
            item["symbol"],

        "name":
            item["name"],

        "market":
            item["market"],

        "type":
            item["type"],

        "price":
            item["price"],

        "change_pct":
            item["change_pct"],

        "ai_score":
            item["ai_score"],

        "strength_score":
            item["strength_score"],

        "signal":
            item["signal"],

        "rating":
            item["rating"],

        "recommendation":
            item["recommendation"]

    }

    if include_core:

        record.update({

            "core_score":
                item["core_score"],

            "core_total":
                item["core_total"],

            "core_pass":
                item["core_pass"]

        })

    return record


# ================================================================
# UI Summary
# ================================================================

def build_ui_summary(
    stocks,
    etfs,
    bonds,
    today_selected,
    top10
):

    all_stock_change = [

        x["change_pct"]

        for x in stocks

        if x.get(
            "change_pct"
        ) is not None

    ]

    rising = sum(

        1

        for value
        in all_stock_change

        if value > 0

    )

    falling = sum(

        1

        for value
        in all_stock_change

        if value < 0

    )

    unchanged = (

        len(all_stock_change)

        -

        rising

        -

        falling

    )

    return {

        "stock_count":
            len(stocks),

        "etf_count":
            len(etfs),

        "bond_count":
            len(bonds),

        "today_selected_count":
            len(today_selected),

        "top10_count":
            len(top10),

        "market_breadth": {

            "rising":
                rising,

            "falling":
                falling,

            "unchanged":
                unchanged

        },

        "core_condition_count":
            6

    }


# ================================================================
# 建立 ui_data.json
# ================================================================

def build_ui_data(
    stocks,
    etfs,
    bonds,
    today_selected,
    top10,
    backtest,
    universe_statistics,
    latest_date
):

    now = datetime.now(
        TW_TZ
    )

    today = now.strftime(
        "%Y-%m-%d"
    )

    updated_at_tw = now.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    ui_today_selected = [

        make_ui_record(
            item,
            True
        )

        for item
        in today_selected

    ]

    ui_top10 = [

        make_ui_record(
            item,
            True
        )

        for item
        in top10

    ]

    ui_etfs = [

        make_ui_record(
            item,
            False
        )

        for item
        in build_etf_result(
            etfs
        )[
            :ETF_TOP10
        ]

    ]

    ui_bonds = [

        make_ui_record(
            item,
            False
        )

        for item
        in build_bond_result(
            bonds
        )[
            :BOND_TOP10
        ]

    ]

    return {

        "version":
            VERSION,

        "schema_version":
            UI_SCHEMA_VERSION,

        "status":
            "success",

        "date":
            today,

        "latest_market_date":
            latest_date,

        "updated_at":
            now.isoformat(),

        "updated_at_tw":
            updated_at_tw,

        "source":
            "fetch_data.py V10.0",

        "summary":
            build_ui_summary(

                stocks,

                etfs,

                bonds,

                today_selected,

                top10

            ),

        "core_conditions": {

            "total":
                6,

            "names":
                CORE_CONDITIONS

        },

        "today_selected":
            ui_today_selected,

        "top10":
            ui_top10,

        "etfs":
            ui_etfs,

        "bonds":
            ui_bonds,

        "backtest_summary": {

            "comparison_horizon":
                backtest.get(
                    "comparison_horizon",
                    10
                ),

            "better_by_win_rate":
                backtest.get(
                    "better_by_win_rate"
                ),

            "stock_count":
                backtest.get(
                    "backtest_stock_count",
                    0
                ),

            "A_10d_win_rate":
                backtest.get(
                    "strategies",
                    {}
                )
                .get(
                    "A_today_cross",
                    {}
                )
                .get(
                    "10d",
                    {}
                )
                .get(
                    "win_rate",
                    0
                ),

            "B_10d_win_rate":
                backtest.get(
                    "strategies",
                    {}
                )
                .get(
                    "B_current_bullish",
                    {}
                )
                .get(
                    "10d",
                    {}
                )
                .get(
                    "win_rate",
                    0
                )

        },

        "universe": {

            "stock_count":
                universe_statistics[
                    "stock_universe"
                ],

            "etf_count":
                universe_statistics[
                    "etf_universe"
                ],

            "bond_count":
                universe_statistics[
                    "bond_universe"
                ],

            "total_count":
                universe_statistics[
                    "total_universe"
                ]

        }

    }


# ================================================================
# 建立 securities.json
#
# 這個檔案不是 prices.json 的縮小版。
#
# 它是：
#
#   「整個可搜尋 Universe」
#
# 即使今天沒有 6/6，
# 使用者仍然可以搜尋該股票。
#
# ================================================================

def build_securities_json(
    universe,
    stocks,
    etfs,
    bonds,
    latest_date
):

    now = datetime.now(
        TW_TZ
    )

    # ------------------------------------------------------------
    # 建立最新行情 Map
    # ------------------------------------------------------------

    latest_map = {}

    for item in (

        stocks
        +
        etfs
        +
        bonds

    ):

        key = (

            item["market"],

            item["code"]

        )

        latest_map[key] = item

    # ------------------------------------------------------------
    # 建立完整搜尋 Universe
    # ------------------------------------------------------------

    securities = []

    for item in universe:

        key = (

            item["market"],

            item["code"]

        )

        latest = latest_map.get(
            key
        )

        security = {

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

            "type":
                item["type"],

            "is_etf":
                bool(
                    item["is_etf"]
                ),

            "is_bond":
                bool(
                    item["is_bond"]
                )

        }

        # --------------------------------------------------------
        # 有行情則加入最新價格
        # --------------------------------------------------------

        if latest is not None:

            security.update({

                "price":
                    latest.get(
                        "price"
                    ),

                "change_pct":
                    latest.get(
                        "change_pct"
                    ),

                "ai_score":
                    latest.get(
                        "ai_score"
                    ),

                "core_score":
                    latest.get(
                        "core_score"
                    ),

                "core_pass":
                    latest.get(
                        "core_pass"
                    )

            })

        else:

            security.update({

                "price":
                    None,

                "change_pct":
                    None,

                "ai_score":
                    None,

                "core_score":
                    None,

                "core_pass":
                    False

            })

        securities.append(
            security
        )

    securities.sort(

        key=lambda x: (

            x["market"],

            x["code"]

        )

    )

    return {

        "version":
            VERSION,

        "schema_version":
            SECURITIES_SCHEMA_VERSION,

        "status":
            "success",

        "updated_at":
            now.isoformat(),

        "updated_at_tw":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "latest_market_date":
            latest_date,

        "count":
            len(securities),

        "securities":
            securities

    }


# ================================================================
# 建立 prices.json
# ================================================================

def build_prices_json(

    stocks,

    etfs,

    bonds,

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

    updated_at_tw = now.strftime(
        "%Y-%m-%d %H:%M:%S"
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

    for item in bonds:

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

    top10 = build_top10(
        stocks
    )

    etfs_sorted = (
        build_etf_result(
            etfs
        )
    )

    bonds_sorted = (
        build_bond_result(
            bonds
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

    bond_tw = sum(

        1

        for x in bonds

        if x["market"] == "TW"

    )

    bond_two = sum(

        1

        for x in bonds

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
            "V10.0_INCREMENTAL_SQLITE",

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

        "bonds":
            bonds_sorted,

        "today_selected":
            today_selected,

        "top10":
            top10,

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

            "bond_count":
                len(bonds),

            "bond_twse_count":
                bond_tw,

            "bond_tpex_count":
                bond_two,

            "top10_count":
                len(top10),

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

            "twse_bond_universe":
                universe_statistics[
                    "twse_bond_universe"
                ],

            "tpex_bond_universe":
                universe_statistics[
                    "tpex_bond_universe"
                ],

            "stock_universe":
                universe_statistics[
                    "stock_universe"
                ],

            "etf_universe":
                universe_statistics[
                    "etf_universe"
                ],

            "bond_universe":
                universe_statistics[
                    "bond_universe"
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
                "六項條件全部成立才列入今日精選",

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
# Data Contract 驗證
# ================================================================

def validate_output_contract(
    prices,
    ui_data,
    securities
):

    # ------------------------------------------------------------
    # prices
    # ------------------------------------------------------------

    if prices.get(
        "version"
    ) != VERSION:

        raise RuntimeError(
            "prices version 驗證失敗"
        )

    if prices.get(
        "schema_version"
    ) != SCHEMA_VERSION:

        raise RuntimeError(
            "prices schema 驗證失敗"
        )

    if prices.get(
        "status"
    ) != "success":

        raise RuntimeError(
            "prices status 驗證失敗"
        )

    if not prices.get(
        "stocks"
    ):

        raise RuntimeError(
            "prices stocks 為空"
        )

    # ------------------------------------------------------------
    # ui_data
    # ------------------------------------------------------------

    if ui_data.get(
        "version"
    ) != VERSION:

        raise RuntimeError(
            "ui_data version 驗證失敗"
        )

    if ui_data.get(
        "schema_version"
    ) != UI_SCHEMA_VERSION:

        raise RuntimeError(
            "ui_data schema 驗證失敗"
        )

    if ui_data.get(
        "status"
    ) != "success":

        raise RuntimeError(
            "ui_data status 驗證失敗"
        )

    if "today_selected" not in ui_data:

        raise RuntimeError(
            "ui_data 缺少 today_selected"
        )

    if "top10" not in ui_data:

        raise RuntimeError(
            "ui_data 缺少 top10"
        )

    if "etfs" not in ui_data:

        raise RuntimeError(
            "ui_data 缺少 etfs"
        )

    if "bonds" not in ui_data:

        raise RuntimeError(
            "ui_data 缺少 bonds"
        )

    # ------------------------------------------------------------
    # securities
    # ------------------------------------------------------------

    if securities.get(
        "version"
    ) != VERSION:

        raise RuntimeError(
            "securities version 驗證失敗"
        )

    if securities.get(
        "schema_version"
    ) != SECURITIES_SCHEMA_VERSION:

        raise RuntimeError(
            "securities schema 驗證失敗"
        )

    if securities.get(
        "status"
    ) != "success":

        raise RuntimeError(
            "securities status 驗證失敗"
        )

    if not isinstance(
        securities.get(
            "securities"
        ),
        list
    ):

        raise RuntimeError(
            "securities.securities 格式錯誤"
        )

    if len(
        securities["securities"]
    ) == 0:

        raise RuntimeError(
            "securities.json 沒有任何商品"
        )


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
        "台股 AI 選股系統 fetch_data.py V10.0"
    )

    print(
        "================================================"
    )

    print(
        "V10.0 Incremental SQLite Data Engine"
    )

    print(
        "自動產生："
    )

    print(
        "  prices.json"
    )

    print(
        "  backtest.json"
    )

    print(
        "  ui_data.json"
    )

    print(
        "  securities.json"
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

    # ============================================================
    # 1. SQLite
    # ============================================================

    init_database()

    # ============================================================
    # 2. Universe
    # ============================================================

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

    # ============================================================
    # 3. 歷史資料模式
    # ============================================================

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
            "使用官方每日資料增量更新"
        )

        print(
            "================================================"
        )

    # ============================================================
    # 4. 官方每日更新
    # ============================================================

    official_update_count = 0

    try:

        official_update_count = (
            fetch_official_daily(
                universe
            )
        )

        if history_mode == (
            "BOOTSTRAP_YAHOO_BATCH"
        ):

            history_mode = (
                "BOOTSTRAP_YAHOO_BATCH"
                "+"
                "OFFICIAL_DAILY"
            )

    except Exception as exc:

        if history_mode == (
            "INCREMENTAL_OFFICIAL"
        ):

            raise

        print(
            f"[WARN] "
            f"官方當日更新失敗：{exc}"
        )

    # ============================================================
    # 5. 確認 DB
    # ============================================================

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
        f"歷史資料筆數："
        f"{history_count}"
    )

    print(
        f"最新交易日期："
        f"{latest_date}"
    )

    print(
        "------------------------------------------------"
    )

    # ============================================================
    # 6. Active Universe
    # ============================================================

    active_universe = (
        load_active_universe()
    )

    if not active_universe:

        raise RuntimeError(
            "Active Universe 為空"
        )

    # ============================================================
    # 7. 掃描
    # ============================================================

    (
        stocks,
        etfs,
        bonds,
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
        f"股票成功："
        f"{len(stocks)}"
    )

    print(
        f"ETF 成功："
        f"{len(etfs)}"
    )

    print(
        f"債券 ETF 成功："
        f"{len(bonds)}"
    )

    print(
        f"失敗："
        f"{len(failed)}"
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

    print(
        f"債券商品成功："
        f"{len(bonds)}"
    )

    # ============================================================
    # 8. 強制檢查
    # ============================================================

    if len(stocks) < 500:

        raise RuntimeError(

            "股票成功數量異常過低："

            f"{len(stocks)}"

        )

    if len(etfs) == 0:

        print(
            "[WARN] ETF 掃描結果為 0"
        )

    # ============================================================
    # 9. A/B 回測
    # ============================================================

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

    # ============================================================
    # 10. 建立今日精選
    # ============================================================

    today_selected = (
        build_today_selected(
            stocks
        )
    )

    top10 = build_top10(
        stocks
    )

    print(
        "================================================"
    )

    print(
        "今日選股結果"
    )

    print(
        "================================================"
    )

    print(
        f"今日 6/6："
        f"{len(today_selected)}"
    )

    print(
        f"Top10："
        f"{len(top10)}"
    )

    if today_selected:

        print(
            "------------------------------------------------"
        )

        print(
            "今日精選："
        )

        for item in today_selected:

            print(

                f"{item['code']} "
                f"{item['name']} "
                f"AI={item['ai_score']} "
                f"6/6"

            )

    else:

        print(
            "今日精選：0 檔"
        )

    # ============================================================
    # 11. 建立 prices.json
    # ============================================================

    prices = build_prices_json(

        stocks,

        etfs,

        bonds,

        failed,

        universe_statistics,

        backtest,

        history_mode,

        official_update_count

    )

    # ============================================================
    # 12. 建立 ui_data.json
    # ============================================================

    ui_data = build_ui_data(

        stocks,

        etfs,

        bonds,

        today_selected,

        top10,

        backtest,

        universe_statistics,

        latest_date

    )

    # ============================================================
    # 13. 建立 securities.json
    # ============================================================

    securities = build_securities_json(

        active_universe,

        stocks,

        etfs,

        bonds,

        latest_date

    )

    # ============================================================
    # 14. Data Contract
    # ============================================================

    validate_output_contract(

        prices,

        ui_data,

        securities

    )

    # ============================================================
    # 15. 寫入 JSON
    # ============================================================

    print(
        "================================================"
    )

    print(
        "寫入 V10.0 資料檔"
    )

    print(
        "================================================"
    )

    atomic_write_json(

        OUTPUT_FILE,

        prices

    )

    print(
        f"[OK] prices.json"
    )

    atomic_write_json(

        BACKTEST_FILE,

        backtest

    )

    print(
        f"[OK] backtest.json"
    )

    atomic_write_json(

        UI_DATA_FILE,

        ui_data

    )

    print(
        f"[OK] ui_data.json"
    )

    atomic_write_json(

        SECURITIES_FILE,

        securities

    )

    print(
        f"[OK] securities.json"
    )

    # ============================================================
    # 16. 最終驗證
    # ============================================================

    required_files = [

        OUTPUT_FILE,

        BACKTEST_FILE,

        UI_DATA_FILE,

        SECURITIES_FILE,

        DATABASE_FILE

    ]

    for path in required_files:

        if not os.path.exists(path):

            raise RuntimeError(

                "輸出檔不存在："

                f"{path}"

            )

        if os.path.getsize(path) <= 0:

            raise RuntimeError(

                "輸出檔為空："

                f"{path}"

            )

    # ============================================================
    # 17. 完成
    # ============================================================

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
        "V10.0 完成"
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
        f"股票："
        f"{len(stocks)}"
    )

    print(
        f"ETF："
        f"{len(etfs)}"
    )

    print(
        f"債券 ETF："
        f"{len(bonds)}"
    )

    print(
        f"今日 6/6："
        f"{len(today_selected)}"
    )

    print(
        f"Top10："
        f"{len(top10)}"
    )

    print(
        f"搜尋 Universe："
        f"{len(securities['securities'])}"
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
        "輸出檔案："
    )

    print(
        f"prices.json"
        f" → {OUTPUT_FILE}"
    )

    print(
        f"backtest.json"
        f" → {BACKTEST_FILE}"
    )

    print(
        f"ui_data.json"
        f" → {UI_DATA_FILE}"
    )

    print(
        f"securities.json"
        f" → {SECURITIES_FILE}"
    )

    print(
        f"market.db"
        f" → {DATABASE_FILE}"
    )

    print(
        "------------------------------------------------"
    )

    print(
        "V10.0 Data Contract：OK"
    )

    print(
        f"prices schema："
        f"{SCHEMA_VERSION}"
    )

    print(
        f"UI schema："
        f"{UI_SCHEMA_VERSION}"
    )

    print(
        f"Securities schema："
        f"{SECURITIES_SCHEMA_VERSION}"
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
