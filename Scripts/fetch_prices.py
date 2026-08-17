#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V6.0

用途：
    建立台股全市場 Universe

市場：
    TWSE 上市
    TPEx 上櫃

主要來源：
    TWSE OpenAPI
    TPEx OpenAPI

設計目標：
    1. GitHub Actions 可穩定執行
    2. API 失敗自動重試
    3. TPEx 520 / 連線中斷時使用備援
    4. 不因單次 API 異常破壞既有 universe.json
    5. 成功驗證後才正式覆蓋
    6. 保留完整市場資訊
    7. 輸出供 fetch_prices.py 使用

輸出：

Data/
└── universe.json

Universe 格式：

{
    "schema_version": "universe-v6.0",
    "version": "V6.0",
    "generated_at": "...",
    "count": 1977,
    "twse_count": 1087,
    "tpex_count": 890,
    "stocks": {
        "1101": {
            "code": "1101",
            "name": "台泥",
            "market": "TWSE",
            "symbol": "1101.TW"
        }
    }
}
"""

import json
import math
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V6.0"
SCHEMA_VERSION = "universe-v6.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

TWSE_URL = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
)

TPEX_URL = (
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
)

# 備援來源
TPEX_BACKUP_URLS = [
    (
        "https://www.tpex.org.tw/openapi/"
        "v1/tpex_mainboard_peratio_analysis"
    ),
    (
        "https://www.tpex.org.tw/openapi/"
        "v1/mopsfin_t187ap03_O"
    ),
]

# API 重試
MAX_RETRIES = 5

RETRY_DELAY = 2.0

CONNECT_TIMEOUT = 20

READ_TIMEOUT = 60

# 合理數量安全門檻
MIN_TWSE_COUNT = 900
MIN_TPEX_COUNT = 300

# 最大合理數量
MAX_TWSE_COUNT = 1500
MAX_TPEX_COUNT = 1200

# HTTP session
SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Connection": "keep-alive",
    }
)


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# JSON
# ============================================================

def load_json(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# HTTP
# ============================================================

def http_get_json(
    url,
    label,
    retries=MAX_RETRIES,
):
    last_error = None

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            log(
                f"  HTTP GET attempt "
                f"{attempt}/{retries}"
            )

            response = SESSION.get(
                url,
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT,
                ),
            )

            log(
                "  HTTP Status: "
                + str(response.status_code)
            )

            content_length = response.headers.get(
                "Content-Length"
            )

            if content_length:
                log(
                    "  Content-Length: "
                    + str(content_length)
                    + " bytes"
                )

            response.raise_for_status()

            if not response.content:
                raise RuntimeError(
                    f"{label} 回應內容為空"
                )

            try:
                data = response.json()

            except Exception as json_error:

                preview = (
                    response.text[:300]
                    .replace("\n", " ")
                    .replace("\r", " ")
                )

                raise RuntimeError(
                    f"{label} JSON 解析失敗："
                    f"{json_error}; "
                    f"response={preview}"
                )

            return data

        except Exception as error:

            last_error = error

            log(
                f"  ⚠️ attempt {attempt} 失敗："
                f"{error}"
            )

            if attempt < retries:

                delay = (
                    RETRY_DELAY
                    * attempt
                )

                time.sleep(delay)

    raise RuntimeError(
        f"{label} 取得失敗："
        f"{last_error}"
    )


# ============================================================
# 數值
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def is_valid_code(value):
    text = clean_text(value)

    if not text:
        return False

    return (
        len(text) == 4
        and text.isdigit()
    )


# ============================================================
# TWSE 欄位
# ============================================================

def parse_twse_rows(data):
    if not isinstance(data, list):
        raise RuntimeError(
            "TWSE API 回傳不是 list"
        )

    log(
        "TWSE JSON 原始資料："
        + str(len(data))
        + " 筆"
    )

    stocks = {}

    for row in data:

        if not isinstance(row, dict):
            continue

        code = ""

        name = ""

        for key in (
            "證券代號",
            "股票代號",
            "代號",
            "code",
        ):
            if key in row:
                value = clean_text(
                    row.get(key)
                )

                if is_valid_code(value):
                    code = value
                    break

        for key in (
            "證券名稱",
            "股票名稱",
            "名稱",
            "name",
        ):
            if key in row:
                value = clean_text(
                    row.get(key)
                )

                if value:
                    name = value
                    break

        if not is_valid_code(code):
            continue

        if not name:
            continue

        stocks[code] = {
            "code": code,
            "name": name,
            "market": "TWSE",
            "symbol": code + ".TW",
        }

    log(
        "TWSE JSON 合法普通股票："
        + str(len(stocks))
    )

    return stocks


# ============================================================
# TPEx 欄位偵測
# ============================================================

def find_value(
    row,
    possible_keys,
):
    for key in possible_keys:

        if key not in row:
            continue

        value = clean_text(
            row.get(key)
        )

        if value:
            return value

    return ""


def parse_tpex_rows(data):
    """
    TPEx API 欄位名稱可能因 API 版本變化，
    因此採用多組欄位名稱自動辨識。
    """

    rows = None

    if isinstance(data, list):

        rows = data

    elif isinstance(data, dict):

        possible_keys = [
            "aaData",
            "data",
            "items",
            "rows",
            "result",
            "records",
        ]

        for key in possible_keys:

            value = data.get(key)

            if isinstance(value, list):
                rows = value
                break

        if rows is None:

            for value in data.values():

                if isinstance(value, list):

                    if value and isinstance(
                        value[0],
                        dict,
                    ):
                        rows = value
                        break

    if rows is None:
        raise RuntimeError(
            "TPEx API 無法解析資料陣列"
        )

    log(
        "TPEx API 原始資料："
        + str(len(rows))
        + " 筆"
    )

    stocks = {}

    for row in rows:

        if not isinstance(row, dict):
            continue

        code = find_value(
            row,
            [
                "SecuritiesCompanyCode",
                "SecuritiesCompanyCode",
                "公司代號",
                "證券代號",
                "股票代號",
                "代號",
                "code",
                "symbol",
            ],
        )

        name = find_value(
            row,
            [
                "CompanyName",
                "公司名稱",
                "證券名稱",
                "股票名稱",
                "名稱",
                "name",
            ],
        )

        if not is_valid_code(code):
            continue

        if not name:
            continue

        stocks[code] = {
            "code": code,
            "name": name,
            "market": "TPEx",
            "symbol": code + ".TWO",
        }

    log(
        "TPEx 合法普通股票："
        + str(len(stocks))
    )

    return stocks


# ============================================================
# 取得 TWSE
# ============================================================

def fetch_twse():
    section("取得 TWSE 上市股票")

    log(
        "API："
        + TWSE_URL
    )

    data = http_get_json(
        TWSE_URL,
        "TWSE",
    )

    stocks = parse_twse_rows(
        data
    )

    if len(stocks) < MIN_TWSE_COUNT:
        raise RuntimeError(
            "TWSE 合法股票數量異常："
            + str(len(stocks))
            + "，低於最低門檻 "
            + str(MIN_TWSE_COUNT)
        )

    if len(stocks) > MAX_TWSE_COUNT:
        raise RuntimeError(
            "TWSE 合法股票數量異常："
            + str(len(stocks))
            + "，高於合理上限 "
            + str(MAX_TWSE_COUNT)
        )

    return stocks


# ============================================================
# 取得 TPEx 主來源
# ============================================================

def fetch_tpex_primary():
    section("取得 TPEx 上櫃股票")

    log(
        "API："
        + TPEX_URL
    )

    data = http_get_json(
        TPEX_URL,
        "TPEx",
    )

    stocks = parse_tpex_rows(
        data
    )

    if len(stocks) < MIN_TPEX_COUNT:
        raise RuntimeError(
            "TPEx 合法股票數量異常："
            + str(len(stocks))
            + "，低於最低門檻 "
            + str(MIN_TPEX_COUNT)
        )

    if len(stocks) > MAX_TPEX_COUNT:
        raise RuntimeError(
            "TPEx 合法股票數量異常："
            + str(len(stocks))
            + "，高於合理上限 "
            + str(MAX_TPEX_COUNT)
        )

    return stocks


# ============================================================
# TPEx 備援
# ============================================================

def fetch_tpex_backup():
    section("TPEx 主 API 失敗，啟動備援")

    errors = []

    for index, url in enumerate(
        TPEX_BACKUP_URLS,
        start=1,
    ):

        log("")
        log(
            f"TPEx 備援來源 {index}"
        )

        log(
            "API："
            + url
        )

        try:

            data = http_get_json(
                url,
                f"TPEx 備援 {index}",
                retries=3,
            )

            stocks = parse_tpex_rows(
                data
            )

            if (
                len(stocks)
                >= MIN_TPEX_COUNT
            ):

                log(
                    "✓ TPEx 備援成功："
                    + str(len(stocks))
                    + " 檔"
                )

                return stocks

            errors.append(
                "來源 "
                + str(index)
                + " 僅解析 "
                + str(len(stocks))
                + " 檔"
            )

        except Exception as error:

            errors.append(
                "來源 "
                + str(index)
                + "："
                + str(error)
            )

            log(
                "⚠️ 備援失敗："
                + str(error)
            )

    raise RuntimeError(
        "所有 TPEx 備援來源皆失敗："
        + " | ".join(errors)
    )


# ============================================================
# 舊 Universe
# ============================================================

def load_existing_universe():
    if not UNIVERSE_FILE.exists():
        return {}

    try:

        data = load_json(
            UNIVERSE_FILE
        )

    except Exception as error:

        log(
            "⚠️ 舊 universe.json "
            "讀取失敗："
            + str(error)
        )

        return {}

    if not isinstance(data, dict):
        return {}

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result = {}

    for code, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        code_text = clean_text(
            item.get(
                "code",
                code,
            )
        )

        if not is_valid_code(
            code_text
        ):
            continue

        market = clean_text(
            item.get(
                "market",
                "",
            )
        )

        if market not in (
            "TWSE",
            "TPEx",
        ):
            continue

        name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        if not name:
            continue

        symbol = clean_text(
            item.get(
                "symbol",
                "",
            )
        )

        if not symbol:

            if market == "TPEx":
                symbol = (
                    code_text
                    + ".TWO"
                )
            else:
                symbol = (
                    code_text
                    + ".TW"
                )

        result[code_text] = {
            "code": code_text,
            "name": name,
            "market": market,
            "symbol": symbol,
        }

    return result


# ============================================================
# Universe 驗證
# ============================================================

def validate_market_counts(
    twse,
    tpex,
):
    section("Universe 市場資料驗證")

    twse_count = len(twse)
    tpex_count = len(tpex)

    total = (
        twse_count
        + tpex_count
    )

    log(
        "TWSE："
        + str(twse_count)
    )

    log(
        "TPEx："
        + str(tpex_count)
    )

    log(
        "總數："
        + str(total)
    )

    if twse_count < MIN_TWSE_COUNT:
        raise RuntimeError(
            "TWSE 數量不足"
        )

    if tpex_count < MIN_TPEX_COUNT:
        raise RuntimeError(
            "TPEx 數量不足"
        )

    if twse_count > MAX_TWSE_COUNT:
        raise RuntimeError(
            "TWSE 數量異常過高"
        )

    if tpex_count > MAX_TPEX_COUNT:
        raise RuntimeError(
            "TPEx 數量異常過高"
        )

    if total < (
        MIN_TWSE_COUNT
        + MIN_TPEX_COUNT
    ):
        raise RuntimeError(
            "全市場股票數量不足"
        )

    log("")
    log(
        "✓ Universe 市場數量驗證通過"
    )


# ============================================================
# 合併 Universe
# ============================================================

def build_universe(
    twse,
    tpex,
):
    section("合併 TWSE + TPEx")

    stocks = {}

    duplicate_count = 0

    for code, item in twse.items():

        stocks[code] = item

    for code, item in tpex.items():

        if code in stocks:
            duplicate_count += 1
            continue

        stocks[code] = item

    if duplicate_count:
        log(
            "⚠️ 重複股票代號："
            + str(duplicate_count)
        )

    log(
        "TWSE："
        + str(len(twse))
    )

    log(
        "TPEx："
        + str(len(tpex))
    )

    log(
        "Universe 總數："
        + str(len(stocks))
    )

    if len(stocks) < 1200:
        raise RuntimeError(
            "Universe 總數異常過低："
            + str(len(stocks))
        )

    return stocks


# ============================================================
# 建立輸出
# ============================================================

def create_output(
    stocks,
):
    generated_at = datetime.now(
        timezone.utc
    ).isoformat()

    twse_count = sum(
        1
        for item in stocks.values()
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item["market"] == "TPEx"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "version": VERSION,
        "generated_at": generated_at,
        "market": "TW",
        "count": len(stocks),
        "twse_count": twse_count,
        "tpex_count": tpex_count,
        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda item: item[0],
            )
        ),
    }


# ============================================================
# 驗證輸出
# ============================================================

def validate_output(
    output,
):
    section("驗證 Universe 輸出")

    stocks = output.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "Universe stocks 格式錯誤"
        )

    count = len(stocks)

    twse_count = 0
    tpex_count = 0

    for code, item in stocks.items():

        if not is_valid_code(code):
            raise RuntimeError(
                "無效股票代號："
                + str(code)
            )

        if not isinstance(
            item,
            dict,
        ):
            raise RuntimeError(
                f"{code} 資料格式錯誤"
            )

        market = item.get(
            "market"
        )

        name = item.get(
            "name"
        )

        symbol = item.get(
            "symbol"
        )

        if market == "TWSE":
            twse_count += 1

        elif market == "TPEx":
            tpex_count += 1

        else:
            raise RuntimeError(
                f"{code} 市場別錯誤："
                + str(market)
            )

        if not name:
            raise RuntimeError(
                f"{code} 缺少名稱"
            )

        if not symbol:
            raise RuntimeError(
                f"{code} 缺少 Yahoo symbol"
            )

    if count != (
        twse_count
        + tpex_count
    ):
        raise RuntimeError(
            "市場數量與總數不一致"
        )

    if twse_count < MIN_TWSE_COUNT:
        raise RuntimeError(
            "輸出 TWSE 數量不足"
        )

    if tpex_count < MIN_TPEX_COUNT:
        raise RuntimeError(
            "輸出 TPEx 數量不足"
        )

    log(
        "Universe："
        + str(count)
    )

    log(
        "TWSE："
        + str(twse_count)
    )

    log(
        "TPEx："
        + str(tpex_count)
    )

    log("")
    log(
        "✓ Universe 輸出驗證通過"
    )


# ============================================================
# 安全寫入
# ============================================================

def safe_replace(
    output,
):
    section("正式更新 universe.json")

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="universe_",
            dir=str(DATA_DIR),
            delete=False,
        ) as file:

            temp_file = Path(
                file.name
            )

            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=2,
            )

