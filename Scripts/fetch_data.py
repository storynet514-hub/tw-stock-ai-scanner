# ================================================================
# 台股 AI 選股系統
# fetch_data.py V10.0 FINAL
#
# ================================================================
# V10.0 DATA ENGINE
#
# 核心架構：
#
#   TWSE / TPEx Universe
#              ↓
#       SQLite market.db
#              ↓
#   ┌─────────────────────────────┐
#   │ 股票                         │
#   │ 6/6 核心條件                │
#   └─────────────────────────────┘
#              ↓
#        AI / Strength Score
#              ↓
#          Top10 / 精選
#
#   ┌─────────────────────────────┐
#   │ ETF                          │
#   │ ETF 專用趨勢 / 動能模型      │
#   └─────────────────────────────┘
#
#   ┌─────────────────────────────┐
#   │ Bond ETF                     │
#   │ 固定收益型 ETF 獨立分類      │
#   └─────────────────────────────┘
#
#              ↓
#       ┌──────────────────┐
#       │ prices.json      │ ← 完整後台結果
#       │ ui_data.json     │ ← 前台專用小資料
#       │ securities.json  │ ← 搜尋 / 加入清單
#       │ backtest.json    │ ← 回測
#       └──────────────────┘
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

UI_OUTPUT_FILE = os.path.join(
    DATA_DIR,
    "ui_data.json"
)

SECURITIES_OUTPUT_FILE = os.path.join(
    DATA_DIR,
    "securities.json"
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
# 歷史資料
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
# 前台輸出數量
# ================================================================

TOP10 = 10

TODAY_RECOMMENDATION_LIMIT = 5

ETF_RECOMMENDATION_LIMIT = 5


# ================================================================
# Yahoo 批次
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
# 股票六項核心條件
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
# 特殊商品
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
    "公司債"
]


# ================================================================
# ETF / 債券 ETF 關鍵字
# ================================================================

BOND_ETF_KEYWORDS = [
    "債",
    "債券",
    "公債",
    "公司債",
    "投資級",
    "高收益債",
    "金融債",
    "非投資級債",
    "短天期債",
    "短期債",
    "長天期債",
    "長期債",
    "美債",
    "美國債",
    "國債",
    "IG債",
    "高收債"
]


ETF_KEYWORDS = [
    "ETF",
    "指數",
    "台灣50",
    "高股息",
    "高息",
    "科技",
    "半導體",
    "金融",
    "ESG",
    "REIT"
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
                asset_class TEXT DEFAULT 'stock',
                is_etf INTEGER DEFAULT 0,
                is_bond_etf INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                updated_at TEXT,
                PRIMARY KEY (market, code)
            )
            """
        )

        # --------------------------------------------------------
        # V9 舊資料庫升級
        # --------------------------------------------------------

        columns = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(securities)"
            ).fetchall()
        ]

        if "asset_class" not in columns:

            conn.execute(
                """
                ALTER TABLE securities
                ADD COLUMN asset_class TEXT
                DEFAULT 'stock'
                """
            )

        if "is_bond_etf" not in columns:

            conn.execute(
                """
                ALTER TABLE securities
                ADD COLUMN is_bond_etf INTEGER
                DEFAULT 0
                """
            )

        # --------------------------------------------------------
        # Daily prices
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

        # --------------------------------------------------------
        # Metadata
        # --------------------------------------------------------

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

        if isinstance(
            value,
            str
        ):

            value = (
                value
                .replace(",", "")
                .replace("--", "")
                .strip()
            )

            if value == "":
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

        if isinstance(
            value,
            str
        ):

            value = (
                value
                .replace(",", "")
                .strip()
            )

            if value in (
                "",
                "--"
            ):
                return 0

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


def is_bond_etf_name(name):

    name = clean_text(
        name
    )

    if not name:
        return False

    return any(
        keyword in name
        for keyword in BOND_ETF_KEYWORDS
    )


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


def determine_asset_class(
    code,
    name,
    security_type=None
):

    if is_bond_etf_name(
        name
    ):

        return (
            "bond_etf"
        )

    if is_etf(
        code,
        name,
        security_type
    ):

        return (
            "etf"
        )

    return "stock"


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

        asset_class = (
            determine_asset_class(
                code,
                name
            )
        )

        universe.append({

            "market": "TW",

            "code": code,

            "name": name,

            "type":
                asset_class,

            "asset_class":
                asset_class,

            "is_etf":
                asset_class in (
                    "etf",
                    "bond_etf"
                ),

            "is_bond_etf":
                asset_class == "bond_etf"

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

        asset_class = (
            determine_asset_class(
                code,
                name
            )
        )

        universe.append({

            "market": "TWO",

            "code": code,

            "name": name,

            "type":
                asset_class,

            "asset_class":
                asset_class,

            "is_etf":
                asset_class in (
                    "etf",
                    "bond_etf"
                ),

            "is_bond_etf":
                asset_class == "bond_etf"

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
                and x["asset_class"] == "stock"
            ),

        "tpex_stock_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TWO"
                and x["asset_class"] == "stock"
            ),

        "twse_etf_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TW"
                and x["asset_class"] == "etf"
            ),

        "tpex_etf_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TWO"
                and x["asset_class"] == "etf"
            ),

        "twse_bond_etf_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TW"
                and x["asset_class"] == "bond_etf"
            ),

        "tpex_bond_etf_universe":
            sum(
                1
                for x in universe
                if x["market"] == "TWO"
                and x["asset_class"] == "bond_etf"
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

    statistics["bond_etf_universe"] = (
        statistics["twse_bond_etf_universe"]
        +
        statistics["tpex_bond_etf_universe"]
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
        f"上市債券 ETF："
        f"{statistics['twse_bond_etf_universe']}"
    )

    print(
        f"上櫃債券 ETF："
        f"{statistics['tpex_bond_etf_universe']}"
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
            """
            UPDATE securities
            SET active = 0
            """
        )

        rows = []

        for item in universe:

            rows.append((

                item["market"],

                item["code"],

                item["name"],

                item["type"],

                item["asset_class"],

                1
                if item["is_etf"]
                else 0,

                1
                if item["is_bond_etf"]
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
                asset_class,
                is_etf,
                is_bond_etf,
                active,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market, code)
            DO UPDATE SET
                name=excluded.name,
                type=excluded.type,
                asset_class=excluded.asset_class,
                is_etf=excluded.is_etf,
                is_bond_etf=excluded.is_bond_etf,
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
# Bootstrap Yahoo
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
        "Yahoo Finance 批次下載"
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
                    "[WARN] 批次下載失敗"
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

                        if (
                            not isinstance(
                                downloaded.columns,
                                pd.MultiIndex
                            )
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

                        rows.append((

                            item["market"],

                            item["code"],

                            date_text,

                            safe_float(
                                row.get("Open")
                            ),

                            safe_float(
                                row.get("High")
                            ),

                            safe_float(
                                row.get("Low")
                            ),

                            close,

                            safe_int(
                                row.get("Volume")
                            )

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
# 讀取歷史
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
# 股票核心條件
# ================================================================

def evaluate_core(
    df
):

    if len(df) < 21:
        return None

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    conditions = {

        "macd_golden_cross":
            bool(
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
            ),

        "rsi_over_50":
            bool(
                pd.notna(
                    latest["RSI"]
                )
                and
                latest["RSI"]
                >
                50
            ),

        "kd_golden_cross":
            bool(
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
            ),

        "volume_expand":
            bool(
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
            ),

        "price_over_ma20":
            bool(
                pd.notna(
                    latest["MA20"]
                )
                and
                latest["Close"]
                >
                latest["MA20"]
            ),

        "ma20_up":
            bool(
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

    }

    score = sum(
        1
        for value in conditions.values()
        if value
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
# 股票 Strength Score
# ================================================================

def calculate_stock_strength_score(
    df
):

    latest = df.iloc[-1]

    score = 0.0

    rsi = safe_float(
        latest["RSI"]
    )

    if rsi is not None:

        if rsi >= 70:
            score += 20

        elif rsi >= 60:
            score += 16

        elif rsi >= 50:
            score += 12

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
    ):
        score += 20

    if len(df) >= 2:

        if (
            pd.notna(
                latest["MA20"]
            )
            and
            pd.notna(
                df.iloc[-2]["MA20"]
            )
            and
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
# ETF 專用評分
# ================================================================
#
# ETF 不使用股票 6/6。
#
# 核心：
#   1. 價格 > MA20
#   2. MA20 上升
#   3. MACD 多方
#   4. RSI > 50
#   5. K > D
#
# 量能只作為輔助，不作為 ETF 的硬性 6/6。
#
# ================================================================

def calculate_etf_score(
    df
):

    if df is None or len(df) < 60:

        return {

            "etf_score":
                0,

            "trend_score":
                0,

            "momentum_score":
                0,

            "volume_score":
                0,

            "etf_pass":
                False

        }

    latest = df.iloc[-1]

    score = 0.0

    trend_score = 0.0

    momentum_score = 0.0

    volume_score = 0.0

    # ------------------------------------------------------------
    # 趨勢
    # ------------------------------------------------------------

    if (
        pd.notna(latest["Close"])
        and
        pd.notna(latest["MA20"])
        and
        latest["Close"]
        >
        latest["MA20"]
    ):

        trend_score += 25

    if len(df) >= 2:

        if (
            pd.notna(latest["MA20"])
            and
            pd.notna(df.iloc[-2]["MA20"])
            and
            latest["MA20"]
            >
            df.iloc[-2]["MA20"]
        ):

            trend_score += 20

    # ------------------------------------------------------------
    # 動能
    # ------------------------------------------------------------

    if (
        pd.notna(latest["MACD"])
        and
        pd.notna(latest["MACD_SIGNAL"])
        and
        latest["MACD"]
        >
        latest["MACD_SIGNAL"]
    ):

        momentum_score += 20

    if (
        pd.notna(latest["RSI"])
        and
        latest["RSI"] > 50
    ):

        momentum_score += 15

    if (
        pd.notna(latest["K"])
        and
        pd.notna(latest["D"])
        and
        latest["K"]
        >
        latest["D"]
    ):

        momentum_score += 10

    # ------------------------------------------------------------
    # 量能
    # ------------------------------------------------------------

    if (
        pd.notna(latest["VOL_MA5"])
        and
        latest["VOL_MA5"] > 0
    ):

        volume_ratio = (
            latest["Volume"]
            /
            latest["VOL_MA5"]
        )

        if volume_ratio >= 1.5:

            volume_score = 10

        elif volume_ratio >= 1.0:

            volume_score = 5

    score = (
        trend_score
        +
        momentum_score
        +
        volume_score
    )

    return {

        "etf_score":
            round(
                min(score, 100),
                2
            ),

        "trend_score":
            round(
                trend_score,
                2
            ),

        "momentum_score":
            round(
                momentum_score,
                2
            ),

        "volume_score":
            round(
                volume_score,
                2
            ),

        "etf_pass":
            score >= 60

    }


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
# 股票評級
# ================================================================

def make_stock_rating(
    core_score,
    ai_score
):

    if core_score == 6:

        if ai_score >= 85:
            return "A+"

        if ai_score >= 75:
            return "A"

        return "A-"

    if core_score >= 5:

        if ai_score >= 75:
            return "B+"

        return "B"

    if core_score >= 4:

        return "B-"

    if core_score >= 3:

        return "C"

    return "D"


# ================================================================
# 股票系統訊號
# ================================================================

def make_stock_signal(
    core_score
):

    if core_score == 6:

        return "6/6 多方成立"

    if core_score >= 5:

        return "5/6 強勢"

    if core_score >= 4:

        return "4/6 偏多"

    if core_score >= 3:

        return "3/6 中性偏多"

    return "多方條件不足"


# ================================================================
# 股票建議
# ================================================================

def make_stock_recommendation(
    core_score,
    ai_score
):

    if core_score == 6:

        if ai_score >= 80:
            return "可分批布局"

        if ai_score >= 70:
            return "列入買進觀察"

        return "符合條件，控制部位"

    if core_score >= 5:

        return "等待條件完整"

    if core_score >= 4:

        return "觀察後續突破"

    return "暫不建議進場"


# ================================================================
# ETF 評級
# ================================================================

def make_etf_rating(
    score
):

    if score >= 85:
        return "A+"

    if score >= 75:
        return "A"

    if score >= 65:
        return "B+"

    if score >= 55:
        return "B"

    if score >= 45:
        return "C"

    return "D"


# ================================================================
# ETF 系統訊號
# ================================================================

def make_etf_signal(
    score
):

    if score >= 80:
        return "強勢多方"

    if score >= 70:
        return "多方趨勢"

    if score >= 60:
        return "偏多觀察"

    if score >= 50:
        return "中性"

    return "趨勢不足"


# ================================================================
# ETF 建議
# ================================================================

def make_etf_recommendation(
    score,
    is_bond_etf=False
):

    if is_bond_etf:

        if score >= 75:
            return "可作防禦型配置"

        if score >= 60:
            return "列入配置觀察"

        return "暫不建議增加部位"

    if score >= 80:
        return "可分批布局"

    if score >= 70:
        return "列入配置觀察"

    if score >= 60:
        return "等待趨勢確認"

    return "暫不建議進場"


# ================================================================
# 建立股票 Record
# ================================================================

def build_stock_record(
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

    change_pct = 0.0

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
        calculate_stock_strength_score(
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
        volume_ma5 is not None
        and
        volume_ma5 > 0
    ):

        volume_ratio = (
            volume /
            volume_ma5
        )

    core_score = (
        core["core_score"]
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
            "stock",

        "asset_class":
            "stock",

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
            core_score,

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
            make_stock_signal(
                core_score
            ),

        "rating":
            make_stock_rating(
                core_score,
                ai_score
            ),

        "recommendation":
            make_stock_recommendation(
                core_score,
                ai_score
            )

    }


# ================================================================
# 建立 ETF Record
# ================================================================

def build_etf_record(
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

    etf_result = calculate_etf_score(
        df
    )

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

    change_pct = 0.0

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

    volume_ma5 = safe_float(
        latest["VOL_MA5"]
    )

    volume = safe_int(
        latest["Volume"]
    )

    volume_ratio = None

    if (
        volume_ma5 is not None
        and
        volume_ma5 > 0
    ):

        volume_ratio = (
            volume /
            volume_ma5
        )

    score = (
        etf_result[
            "etf_score"
        ]
    )

    is_bond_etf = (
        item["asset_class"]
        ==
        "bond_etf"
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

        "asset_class":
            item["asset_class"],

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

        "trend_score":
            etf_result[
                "trend_score"
            ],

        "momentum_score":
            etf_result[
                "momentum_score"
            ],

        "volume_score":
            etf_result[
                "volume_score"
            ],

        "etf_score":
            score,

        "etf_pass":
            etf_result[
                "etf_pass"
            ],

        "is_bond_etf":
            is_bond_etf,

        "ai_score":
            score,

        "signal":
            make_etf_signal(
                score
            ),

        "rating":
            make_etf_rating(
                score
            ),

        "recommendation":
            make_etf_recommendation(
                score,
                is_bond_etf
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
                asset_class,
                is_etf,
                is_bond_etf
            FROM securities
            WHERE active = 1
            ORDER BY market, code
            """
        ).fetchall()

    finally:

        conn.close()

    universe = []

    for row in rows:

        asset_class = (
            row[4]
            or
            (
                "bond_etf"
                if row[6]
                else
                (
                    "etf"
                    if row[5]
                    else "stock"
                )
            )
        )

        universe.append({

            "market":
                row[0],

            "code":
                row[1],

            "name":
                row[2],

            "type":
                row[3],

            "asset_class":
                asset_class,

            "is_etf":
                bool(row[5]),

            "is_bond_etf":
                bool(row[6])

        })

    return universe


# ================================================================
# 掃描 Universe
# ================================================================

def scan_universe(
    universe
):

    stocks = []

    etfs = []

    bond_etfs = []

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

            if item["asset_class"] == "stock":

                record = (
                    build_stock_record(
                        item,
                        df
                    )
                )

            else:

                record = (
                    build_etf_record(
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

            if item["asset_class"] == "stock":

                stocks.append(
                    record
                )

            elif item["asset_class"] == "bond_etf":

                bond_etfs.append(
                    record
                )

            else:

                etfs.append(
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
        bond_etfs,
        failed
    )


# ================================================================
# 排序
# ================================================================

def sort_by_score(
    items,
    field="ai_score"
):

    return sorted(
        items,
        key=lambda x:
            float(
                x.get(
                    field,
                    0
                )
                or 0
            ),
        reverse=True
    )


# ================================================================
# 股票 Top10
# ================================================================

def build_top10(
    stocks
):

    return sort_by_score(
        stocks
    )[:TOP10]


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

    return sort_by_score(
        selected
    )


# ================================================================
# 今日精選
#
# 重要：
#
# 後台可以有很多 6/6。
#
# 但前台只展示前 5 名。
#
# ================================================================

def build_today_recommendations(
    stocks
):

    selected = (
        build_today_selected(
            stocks
        )
    )

    return selected[
        :TODAY_RECOMMENDATION_LIMIT
    ]


# ================================================================
# ETF 精選
# ================================================================

def build_etf_recommendations(
    etfs,
    bond_etfs
):

    normal_etf = sort_by_score(
        etfs,
        "etf_score"
    )[
        :ETF_RECOMMENDATION_LIMIT
    ]

    bond_etf = sort_by_score(
        bond_etfs,
        "etf_score"
    )[
        :ETF_RECOMMENDATION_LIMIT
    ]

    return (
        normal_etf,
        bond_etf
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

            # ----------------------------------------------------
            # A：當日 MACD 黃金交叉
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
            # B：目前維持多方
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
    asset_class
):

    required = {

        "code",
        "symbol",
        "name",
        "type",
        "asset_class",
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

        "ai_score",
        "signal",
        "rating",
        "recommendation"

    }

    if asset_class == "stock":

        required.update({

            "macd_golden_cross",

            "rsi_over_50",

            "kd_golden_cross",

            "volume_expand",

            "price_over_ma20",

            "ma20_up",

            "core_score",

            "core_total",

            "core_pass",

            "strength_score"

        })

    else:

        required.update({

            "trend_score",

            "momentum_score",

            "volume_score",

            "etf_score",

            "etf_pass",

            "is_bond_etf"

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
# 前台 Record
# ================================================================
#
# index.html 不需要知道後台所有技術指標。
#
# ================================================================

def make_ui_record(
    item
):

    return {

        "code":
            item["code"],

        "symbol":
            item["symbol"],

        "name":
            item["name"],

        "market":
            item["market"],

        "asset_class":
            item["asset_class"],

        "type":
            item["type"],

        "price":
            item["price"],

        "change_pct":
            item["change_pct"],

        "signal":
            item["signal"],

        "rating":
            item["rating"],

        "recommendation":
            item["recommendation"],

        "ai_score":
            item["ai_score"]

    }


# ================================================================
# 搜尋索引
# ================================================================

def build_securities_index(
    stocks,
    etfs,
    bond_etfs
):

    all_items = (
        stocks
        +
        etfs
        +
        bond_etfs
    )

    result = []

    seen = set()

    for item in all_items:

        key = (
            item["market"],
            item["code"]
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        result.append({

            "code":
                item["code"],

            "symbol":
                item["symbol"],

            "name":
                item["name"],

            "market":
                item["market"],

            "asset_class":
                item["asset_class"],

            "type":
                item["type"],

            "price":
                item["price"],

            "change_pct":
                item["change_pct"]

        })

    result.sort(
        key=lambda x: (
            x["code"],
            x["name"]
        )
    )

    return result


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
    bond_etfs,
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
            "stock"
        )

    for item in etfs:

        validate_record(
            item,
            "etf"
        )

    for item in bond_etfs:

        validate_record(
            item,
            "bond_etf"
        )

    today_selected = (
        build_today_selected(
            stocks
        )
    )

    today_recommendations = (
        build_today_recommendations(
            stocks
        )
    )

    top10 = build_top10(
        stocks
    )

    etf_recommendations, bond_etf_recommendations = (
        build_etf_recommendations(
            etfs,
            bond_etfs
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

    bond_etf_tw = sum(
        1
        for x in bond_etfs
        if x["market"] == "TW"
    )

    bond_etf_two = sum(
        1
        for x in bond_etfs
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
            etfs,

        "bond_etfs":
            bond_etfs,

        "today_selected":
            today_selected,

        "today_recommendations":
            today_recommendations,

        "top10":
            top10,

        "etf_recommendations":
            etf_recommendations,

        "bond_etf_recommendations":
            bond_etf_recommendations,

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

            "bond_etf_count":
                len(bond_etfs),

            "bond_etf_twse_count":
                bond_etf_tw,

            "bond_etf_tpex_count":
                bond_etf_two,

            "top10_count":
                len(top10),

            "today_selected_count":
                len(today_selected),

            "today_recommendation_count":
                len(today_recommendations),

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

            "twse_bond_etf_universe":
                universe_statistics[
                    "twse_bond_etf_universe"
                ],

            "tpex_bond_etf_universe":
                universe_statistics[
                    "tpex_bond_etf_universe"
                ],

            "stock_universe":
                universe_statistics[
                    "stock_universe"
                ],

            "etf_universe":
                universe_statistics[
                    "etf_universe"
                ],

            "bond_etf_universe":
                universe_statistics[
                    "bond_etf_universe"
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
                "股票目前維持多方",

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

        "etf_model": {

            "definition":
                "ETF 趨勢與動能模型",

            "rules": {

                "trend":
                    "Close > MA20",

                "ma20":
                    "MA20 今日 > MA20 昨日",

                "macd":
                    "MACD > MACD Signal",

                "rsi":
                    "RSI > 50",

                "kd":
                    "K > D",

                "volume":
                    "量能作為輔助條件，不作為硬性 6/6"

            }

        },

        "failed":
            failed

    }


# ================================================================
# 建立 ui_data.json
# ================================================================

def build_ui_data(
    stocks,
    etfs,
    bond_etfs,
    universe_statistics,
    history_mode,
    official_update_count
):

    now = datetime.now(
        TW_TZ
    )

    today = now.strftime(
        "%Y-%m-%d"
    )

    today_recommendations = (
        build_today_recommendations(
            stocks
        )
    )

    top10 = build_top10(
        stocks
    )

    etf_recommendations, bond_etf_recommendations = (
        build_etf_recommendations(
            etfs,
            bond_etfs
        )
    )

    # ------------------------------------------------------------
    # 前台真正使用的資料
    # ------------------------------------------------------------

    return {

        "version":
            VERSION,

        "schema_version":
            "ui.v10",

        "status":
            "success",

        "date":
            today,

        "updated_at":
            now.isoformat(),

        "history_mode":
            history_mode,

        "official_update_count":
            official_update_count,

        # --------------------------------------------------------
        # 今日推薦
        # --------------------------------------------------------

        "today_recommendations":

            [
                make_ui_record(
                    item
                )

                for item
                in today_recommendations
            ],

        # --------------------------------------------------------
        # Top10
        # --------------------------------------------------------

        "top10":

            [
                make_ui_record(
                    item
                )

                for item
                in top10
            ],

        # --------------------------------------------------------
        # ETF
        # --------------------------------------------------------

        "etf_recommendations":

            [
                make_ui_record(
                    item
                )

                for item
                in etf_recommendations
            ],

        # --------------------------------------------------------
        # Bond ETF
        # --------------------------------------------------------

        "bond_etf_recommendations":

            [
                make_ui_record(
                    item
                )

                for item
                in bond_etf_recommendations
            ],

        # --------------------------------------------------------
        # 統計
        # --------------------------------------------------------

        "statistics": {

            "stock_universe":
                universe_statistics[
                    "stock_universe"
                ],

            "etf_universe":
                universe_statistics[
                    "etf_universe"
                ],

            "bond_etf_universe":
                universe_statistics[
                    "bond_etf_universe"
                ],

            "today_core6_count":
                sum(
                    1
                    for item
                    in stocks
                    if item.get(
                        "core_pass"
                    )
                ),

            "today_recommendation_count":
                len(
                    today_recommendations
                ),

            "top10_count":
                len(
                    top10
                ),

            "etf_recommendation_count":
                len(
                    etf_recommendations
                ),

            "bond_etf_recommendation_count":
                len(
                    bond_etf_recommendations
                )

        },

        # --------------------------------------------------------
        # UI 顯示規則
        # --------------------------------------------------------

        "display_rules": {

            "today_recommendation_limit":
                TODAY_RECOMMENDATION_LIMIT,

            "top10_limit":
                TOP10,

            "show_all_core6":
                False,

            "show_failed":
                False,

            "source":
                "Data/ui_data.json"

        }

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
        "台股 AI 選股系統 fetch_data.py V10.0"
    )

    print(
        "Incremental SQLite + UI Data Engine"
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
    # 1. 初始化 DB
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
    # 3. 歷史資料
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
            "使用官方每日增量更新"
        )

        print(
            "================================================"
        )

    # ============================================================
    # 4. 官方每日更新
    # ============================================================

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

    # ============================================================
    # 5. DB 檢查
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
        f"歷史資料筆數：{history_count}"
    )

    print(
        f"最新交易日期：{latest_date}"
    )

    print(
        "------------------------------------------------"
    )

    # ============================================================
    # 6. 掃描
    # ============================================================

    active_universe = (
        load_active_universe()
    )

    (
        stocks,
        etfs,
        bond_etfs,
        failed
    ) = scan_universe(
        active_universe
    )

    print(
        "================================================"
    )

    print(
        "V10.0 掃描結果"
    )

    print(
        "================================================"
    )

    print(
        f"股票成功：{len(stocks)}"
    )

    print(
        f"一般 ETF：{len(etfs)}"
    )

    print(
        f"債券 ETF：{len(bond_etfs)}"
    )

    print(
        f"失敗：{len(failed)}"
    )

    print(
        "------------------------------------------------"
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
        f"債券 ETF："
        f"{len(bond_etfs)}"
    )

    # ============================================================
    # 7. 強制檢查
    # ============================================================

    if len(stocks) < 500:

        raise RuntimeError(
            "股票成功數量異常過低："
            f"{len(stocks)}"
        )

    if (
        len(etfs) == 0
        and
        len(bond_etfs) == 0
    ):

        raise RuntimeError(
            "ETF 掃描結果為 0"
        )

    # ============================================================
    # 8. 回測
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
    # 9. prices.json
    # ============================================================

    prices = build_prices_json(

        stocks,

        etfs,

        bond_etfs,

        failed,

        universe_statistics,

        backtest,

        history_mode,

        official_update_count

    )

    # ============================================================
    # 10. ui_data.json
    # ============================================================

    ui_data = build_ui_data(

        stocks,

        etfs,

        bond_etfs,

        universe_statistics,

        history_mode,

        official_update_count

    )

    # ============================================================
    # 11. securities.json
    # ============================================================

    securities_index = (
        build_securities_index(

            stocks,

            etfs,

            bond_etfs

        )
    )

    securities_data = {

        "version":
            VERSION,

        "schema_version":
            "securities.v10",

        "status":
            "success",

        "updated_at":
            datetime.now(
                TW_TZ
            ).isoformat(),

        "count":
            len(
                securities_index
            ),

        "securities":
            securities_index

    }

    # ============================================================
    # 12. Data Contract
    # ============================================================

    if prices[
        "version"
    ] != VERSION:

        raise RuntimeError(
            "prices version 驗證失敗"
        )

    if prices[
        "schema_version"
    ] != SCHEMA_VERSION:

        raise RuntimeError(
            "prices schema_version 驗證失敗"
        )

    if prices[
        "status"
    ] != "success":

        raise RuntimeError(
            "prices status 驗證失敗"
        )

    if not prices[
        "stocks"
    ]:

        raise RuntimeError(
            "stocks 為空"
        )

    if (
        not prices["etfs"]
        and
        not prices["bond_etfs"]
    ):

        raise RuntimeError(
            "ETF 為空"
        )

    if ui_data[
        "status"
    ] != "success":

        raise RuntimeError(
            "ui_data status 驗證失敗"
        )

    if securities_data[
        "status"
    ] != "success":

        raise RuntimeError(
            "securities status 驗證失敗"
        )

    # ============================================================
    # 13. 寫入 prices.json
    # ============================================================

    atomic_write_json(
        OUTPUT_FILE,
        prices
    )

    # ============================================================
    # 14. 寫入 ui_data.json
    # ============================================================

    atomic_write_json(
        UI_OUTPUT_FILE,
        ui_data
    )

    # ============================================================
    # 15. 寫入 securities.json
    # ============================================================

    atomic_write_json(
        SECURITIES_OUTPUT_FILE,
        securities_data
    )

    # ============================================================
    # 16. backtest.json
    # ============================================================

    atomic_write_json(
        BACKTEST_FILE,
        backtest
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

    today_core6 = (
        build_today_selected(
            stocks
        )
    )

    today_recommendations = (
        build_today_recommendations(
            stocks
        )
    )

    top10 = (
        build_top10(
            stocks
        )
    )

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
        f"股票總成功："
        f"{len(stocks)}"
    )

    print(
        f"一般 ETF："
        f"{len(etfs)}"
    )

    print(
        f"債券 ETF："
        f"{len(bond_etfs)}"
    )

    print(
        "------------------------------------------------"
    )

    print(
        f"今日 6/6 完整結果："
        f"{len(today_core6)}"
    )

    print(
        f"今日前台精選："
        f"{len(today_recommendations)}"
    )

    print(
        f"Top10："
        f"{len(top10)}"
    )

    print(
        f"搜尋索引："
        f"{len(securities_index)}"
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
        f"ui_data.json："
        f"{UI_OUTPUT_FILE}"
    )

    print(
        f"securities.json："
        f"{SECURITIES_OUTPUT_FILE}"
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
        "V10.0 Data Contract：OK"
    )

    print(
        f"prices schema："
        f"{SCHEMA_VERSION}"
    )

    print(
        "UI schema：ui.v10"
    )

    print(
        "Search schema：securities.v10"
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
