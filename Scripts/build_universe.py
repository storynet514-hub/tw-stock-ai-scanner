#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.1

============================================================
用途
============================================================

建立台股全市場 Universe：

TWSE 上市
TPEx 上櫃

輸出：

Data/universe.json

============================================================
V5.1 核心修正
============================================================

V5.0 曾發生：

TWSE API
HTTP 200
Content-Length：800 bytes
只有 8 行
解析股票：0

問題在於：

HTTP 200 不代表資料正常。

V5.1：

1. 檢查 HTTP Status
2. 檢查 Response Content-Length
3. 檢查實際內容長度
4. JSON 必須能正常解析
5. JSON 股票數量必須達到合理門檻
6. 異常資料直接視為失敗
7. 自動 retry
8. 主 API 失敗後使用 fallback
9. TWSE / TPEx 分開驗證
10. 最後合併資料
11. 全部驗證成功後才替換 universe.json

============================================================
安全機制
============================================================

✓ 不會用異常 8 行資料覆蓋 Universe
✓ 不會用空資料覆蓋 Universe
✓ 不會因 HTTP 200 就判定成功
✓ TWSE 失敗時保留舊 universe.json
✓ TPEx 失敗時保留舊 universe.json
✓ 最終驗證成功才 atomic replace
✓ 保留既有 universe.json 結構相容性

============================================================
資料格式
============================================================

{
    "version": "V5.1",
    "generated_at": "...",
    "total": 1977,
    "twse_count": 1087,
    "tpex_count": 890,
    "stocks": [
        {
            "code": "1101",
            "name": "台泥",
            "market": "TWSE",
            "symbol": "1101.TW"
        },
        {
            "code": "6488",
            "name": "...",
            "market": "TPEx",
            "symbol": "6488.TWO"
        }
    ]
}

============================================================
"""

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V5.1"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

# ------------------------------------------------------------
# 最低股票數量門檻
# ------------------------------------------------------------

MIN_TWSE_STOCKS = 700
MIN_TPEX_STOCKS = 300
MIN_TOTAL_STOCKS = 1200

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 45

MAX_RETRIES = 5

RETRY_DELAY = 2.0

REQUEST_DELAY = 0.3

# ------------------------------------------------------------
# 異常回應判斷
# ------------------------------------------------------------

# 小於這個大小的回應不可能是完整市場股票清單
MIN_RESPONSE_BYTES = 10_000

# JSON 股票資料至少要有這麼多筆
# 真正成功門檻另外由 MIN_TWSE_STOCKS /
# MIN_TPEX_STOCKS 控制
MIN_JSON_RECORDS = 100

# ------------------------------------------------------------
# User-Agent
# ------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


# ============================================================
# API
# ============================================================

TWSE_API = (
    "https://openapi.twse.com.tw/v1/opendata/"
    "t187ap03_L"
)

TPEx_API = (
    "https://www.tpex.org.tw/openapi/v1/"
    "mopsfin_t187ap03_O"
)

# ------------------------------------------------------------
# 備援 API
#
# TWSE 備援：
# 使用公開資訊站 CSV 介面。
#
# TPEx 備援：
# 使用舊版 OpenAPI endpoint。
# ------------------------------------------------------------

TWSE_FALLBACK_APIS = [
    (
        "TWSE OpenAPI",
        TWSE_API,
    ),
    (
        "TWSE CSV",
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/"
        "SecuritiesListing?response=json",
    ),
]

TPEx_FALLBACK_APIS = [
    (
        "TPEx OpenAPI",
        TPEx_API,
    ),
    (
        "TPEx old API",
        "https://www.tpex.org.tw/"
        "web/stock/aftertrading/"
        "daily_close_quotes/"
        "st43.php?l=zh-tw",
    ),
]


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": (
            "application/json,"
            "text/plain,"
            "text/csv,"
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

def load_json_file(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        return json.load(file)


# ============================================================
# 清理文字
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    text = str(value)

    text = text.replace(
        "\ufeff",
        "",
    )

    text = text.replace(
        "\xa0",
        " ",
    )

    return text.strip()


# ============================================================
# 股票代號
# ============================================================

def normalize_code(value):
    """
    只接受 4~6 位數字股票代號。
    """

    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    text = text.upper()

    # Yahoo ticker
    if text.endswith(".TW"):
        text = text[:-3]

    elif text.endswith(".TWO"):
        text = text[:-4]

    # 去除可能的空白
    text = text.strip()

    # 只接受純數字
    if not text.isdigit():
        return None

    if len(text) < 4 or len(text) > 6:
        return None

    return text


# ============================================================
# 股票名稱
# ============================================================

def normalize_name(value):
    text = clean_text(value)

    if not text:
        return ""

    return text


# ============================================================
# Yahoo Symbol
# ============================================================

def make_symbol(
    code,
    market,
):
    if market == "TPEx":
        return code + ".TWO"

    return code + ".TW"


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    params=None,
    label="API",
):
    """
    穩定 HTTP GET。

    重要：

    HTTP 200 但 response 太短
    直接判定失敗。

    避免：

    HTTP 200
    800 bytes
    8 行
    → 被誤認成有效資料。
    """

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        log(
            f"  HTTP GET attempt "
            f"{attempt}/{MAX_RETRIES}"
        )

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT,
                ),
            )

            status = response.status_code

            content = response.content or b""

            content_length = len(content)

            log(
                f"  HTTP Status: {status}"
            )

            log(
                f"  Content-Length: "
                f"{content_length} bytes"
            )

            # ------------------------------------------------
            # HTTP 狀態
            # ------------------------------------------------

            if status != 200:

                raise RuntimeError(
                    f"HTTP {status}"
                )

            # ------------------------------------------------
            # 回應太短
            # ------------------------------------------------

            if content_length < MIN_RESPONSE_BYTES:

                preview = (
                    content[:300]
                    .decode(
                        "utf-8",
                        errors="replace",
                    )
                    .replace(
                        "\n",
                        " ",
                    )
                    .replace(
                        "\r",
                        " ",
                    )
                )

                raise RuntimeError(
                    "API 回應異常過短："
                    f"{content_length} bytes"
                    f"；內容：{preview}"
                )

            return response

        except Exception as error:

            last_error = error

            log(
                "  ⚠️ attempt "
                f"{attempt} 失敗："
                f"{error}"
            )

            if attempt < MAX_RETRIES:

                sleep_seconds = (
                    RETRY_DELAY * attempt
                )

                time.sleep(
                    sleep_seconds
                )

    raise RuntimeError(
        f"{label} 取得失敗："
        f"{last_error}"
    )


# ============================================================
# 解析 JSON
# ============================================================

def response_json(response):
    """
    安全 JSON 解析。
    """

    try:
        return response.json()

    except Exception:

        text = response.text

        # ----------------------------------------------------
        # 有些 API Header 不正確，
        # 但內容本身是 JSON。
        # ----------------------------------------------------

        try:
            return json.loads(text)

        except Exception as error:

            raise RuntimeError(
                "JSON 解析失敗："
                f"{error}"
            ) from error


# ============================================================
# 從 JSON 遞迴找股票資料
# ============================================================

def recursive_find_records(
    value,
):
    """
    遞迴搜尋 JSON 裡的 list。

    回傳所有可能的 list。
    """

    found = []

    if isinstance(
        value,
        list,
    ):

        if value:
            found.append(value)

        for item in value:
            if isinstance(
                item,
                (dict, list),
            ):
                found.extend(
                    recursive_find_records(
                        item
                    )
                )

    elif isinstance(
        value,
        dict,
    ):

        for child in value.values():

            if isinstance(
                child,
                (dict, list),
            ):

                found.extend(
                    recursive_find_records(
                        child
                    )
                )

    return found


# ============================================================
# 欄位搜尋
# ============================================================

def get_first_value(
    record,
    keys,
):
    if not isinstance(
        record,
        dict,
    ):
        return None

    # 先直接 key
    for key in keys:

        if key in record:

            value = record.get(key)

            if value is not None:
                return value

    # 再做大小寫 / 空白比對
    normalized = {}

    for key in record.keys():

        normalized[
            clean_text(key).lower()
        ] = key

    for key in keys:

        actual = normalized.get(
            clean_text(key).lower()
        )

        if actual is None:
            continue

        value = record.get(
            actual
        )

        if value is not None:
            return value

    return None


# ============================================================
# 解析標準 JSON 股票資料
# ============================================================

def parse_json_stock_records(
    payload,
    market,
):
    """
    將 TWSE / TPEx JSON 轉成統一格式。

    統一：

    {
        code,
        name,
        market,
        symbol
    }
    """

    candidate_lists = []

    if isinstance(
        payload,
        list,
    ):
        candidate_lists.append(
            payload
        )

    elif isinstance(
        payload,
        dict,
    ):

        candidate_lists.extend(
            recursive_find_records(
                payload
            )
        )

    # --------------------------------------------------------
    # 找出最像股票清單的 list
    # --------------------------------------------------------

    best = []

    best_score = -1

    for records in candidate_lists:

        score = 0

        sample_count = min(
            len(records),
            50,
        )

        for item in records[:sample_count]:

            if not isinstance(
                item,
                dict,
            ):
                continue

            code = get_first_value(
                item,
                [
                    "Code",
                    "code",
                    "股票代號",
                    "證券代號",
                    "有價證券代號",
                    "代號",
                    "SecuritiesCompanyCode",
                ],
            )

            name = get_first_value(
                item,
                [
                    "Name",
                    "name",
                    "股票名稱",
                    "證券名稱",
                    "公司名稱",
                    "名稱",
                    "SecuritiesCompanyName",
                ],
            )

            if normalize_code(code):
                score += 2

            if name:
                score += 1

        if score > best_score:

            best_score = score
            best = records

    # --------------------------------------------------------
    # 解析
    # --------------------------------------------------------

    output = {}

    for item in best:

        if not isinstance(
            item,
            dict,
        ):
            continue

        code_value = get_first_value(
            item,
            [
                "Code",
                "code",
                "股票代號",
                "證券代號",
                "有價證券代號",
                "代號",
                "SecuritiesCompanyCode",
            ],
        )

        name_value = get_first_value(
            item,
            [
                "Name",
                "name",
                "股票名稱",
                "證券名稱",
                "公司名稱",
                "名稱",
                "SecuritiesCompanyName",
            ],
        )

        code = normalize_code(
            code_value
        )

        if code is None:
            continue

        name = normalize_name(
            name_value
        )

        # ----------------------------------------------------
        # 排除明顯非普通股
        # ----------------------------------------------------

        if not is_normal_common_stock(
            code,
            name,
        ):
            continue

        symbol = make_symbol(
            code,
            market,
        )

        output[code] = {
            "code": code,
            "name": name,
            "market": market,
            "symbol": symbol,
        }

    return output


# ============================================================
# 判斷普通股票
# ============================================================

def is_normal_common_stock(
    code,
    name,
):
    """
    台股 Universe 主要保留一般普通股票。

    排除：

    - 權證
    - 特別股
    - ETF
    - ETN
    - 受益證券
    - 基金
    - 明顯非股票商品

    注意：

    不用過度嚴格名稱過濾，
    避免誤刪正常股票。
    """

    if not code:
        return False

    name_text = normalize_name(
        name
    )

    upper_name = name_text.upper()

    # --------------------------------------------------------
    # 權證
    # --------------------------------------------------------

    warrant_words = [
        "認購權證",
        "認售權證",
        "牛證",
        "熊證",
        "權證",
        "認購",
        "認售",
    ]

    for word in warrant_words:

        if word in name_text:
            return False

    # --------------------------------------------------------
    # ETF / ETN / 基金
    # --------------------------------------------------------

    fund_words = [
        "ETF",
        "ETN",
        "指數股票型",
        "證券投資信託",
        "受益證券",
        "基金",
    ]

    for word in fund_words:

        if word in upper_name:
            return False

        if word in name_text:
            return False

    # --------------------------------------------------------
    # 明顯商品型代號
    #
    # 台股一般股票通常四碼。
    #
    # 但仍保留 4~6 碼，
    # 因為 Universe 來源可能包含特殊正常標的。
    # --------------------------------------------------------

    if not (
        4 <= len(code) <= 6
    ):
        return False

    return True


# ============================================================
# TWSE JSON
# ============================================================

def fetch_twse_json():

    section("取得 TWSE 上市股票")

    log(
        f"API：{TWSE_API}"
    )

    response = http_get(
        TWSE_API,
        label="TWSE OpenAPI",
    )

    payload = response_json(
        response
    )

    stocks = parse_json_stock_records(
        payload,
        "TWSE",
    )

    log(
        "TWSE JSON 原始資料："
        f"{count_json_records(payload)} 筆"
    )

    log(
        "TWSE JSON 合法普通股票："
        f"{len(stocks)}"
    )

    if len(stocks) < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE JSON 股票數量異常："
            f"{len(stocks)}"
        )

    return stocks


# ============================================================
# TPEx JSON
# ============================================================

def fetch_tpex_json():

    section("取得 TPEx 上櫃股票")

    log(
        f"API：{TPEx_API}"
    )

    response = http_get(
        TPEx_API,
        label="TPEx OpenAPI",
    )

    payload = response_json(
        response
    )

    stocks = parse_json_stock_records(
        payload,
        "TPEx",
    )

    log(
        "TPEx JSON 原始資料："
        f"{count_json_records(payload)} 筆"
    )

    log(
        "TPEx JSON 合法普通股票："
        f"{len(stocks)}"
    )

    if len(stocks) < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "TPEx JSON 股票數量異常："
            f"{len(stocks)}"
        )

    return stocks


# ============================================================
# JSON 資料筆數
# ============================================================

def count_json_records(
    payload,
):
    if isinstance(
        payload,
        list,
    ):
        return len(payload)

    if isinstance(
        payload,
        dict,
    ):

        lists = recursive_find_records(
            payload
        )

        if not lists:
            return 0

        return max(
            len(item)
            for item in lists
        )

    return 0


# ============================================================
# CSV 解析
# ============================================================

def decode_response(
    response,
):
    content = response.content

    # UTF-8-SIG
    try:
        return content.decode(
            "utf-8-sig"
        )
    except Exception:
        pass

    # Big5
    try:
        return content.decode(
            "big5",
            errors="replace",
        )
    except Exception:
        pass

    return content.decode(
        "utf-8",
        errors="replace",
    )


# ============================================================
# CSV / 文字解析股票
# ============================================================

def parse_text_stocks(
    text,
    market,
):
    """
    備援文字解析。

    主要抓：

    4~6 位股票代號
    + 股票名稱

    不直接依賴固定欄位位置。
    """

    output = {}

    lines = text.splitlines()

    for line in lines:

        line = line.strip()

        if not line:
            continue

        # ----------------------------------------------------
        # 去除 HTML
        
