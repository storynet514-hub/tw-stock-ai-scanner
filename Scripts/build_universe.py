#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.2

============================================================
用途
============================================================

建立台股全市場 Universe：

    TWSE 上市
    TPEx 上櫃

輸出：

    Data/universe.json

============================================================
V5.2 核心修正
============================================================

1. 修正 V5.1 與 Workflow JSON 欄位不一致問題

固定輸出：

{
    "version": "V5.2",
    "generated_at": "...",
    "total": 1977,
    "listed_stocks": 1087,
    "otc_stocks": 890,
    "items": [
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

2. HTTP 200 不代表 API 正常

如果：

    HTTP 200
    Content-Length 只有幾百 bytes

直接判定失敗。

3. TWSE / TPEx 分開取得

4. TWSE / TPEx 分開驗證

5. API 自動 retry

6. 主 API 失敗後使用備援 API

7. 所有資料通過驗證後才覆蓋 universe.json

8. 任何失敗都保留舊 universe.json

9. 不允許產生：

    total = 0
    items = []

10. Yahoo ticker：

    TWSE -> .TW
    TPEx -> .TWO

============================================================
安全門檻
============================================================

TWSE 最低：

    700

TPEx 最低：

    300

全市場最低：

    1200

理論正常值約：

    TWSE 1087
    TPEx 890
    Total 1977

============================================================
"""

import csv
import io
import json
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

VERSION = "V5.2"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# 股票數量安全門檻
# ============================================================

MIN_TWSE_STOCKS = 700
MIN_TPEX_STOCKS = 300
MIN_TOTAL_STOCKS = 1200


# ============================================================
# HTTP 設定
# ============================================================

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 45

MAX_RETRIES = 5

RETRY_DELAY = 2.0

REQUEST_DELAY = 0.5


# ============================================================
# API 回應安全檢查
# ============================================================

MIN_RESPONSE_BYTES = 10_000


# ============================================================
# User Agent
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


# ============================================================
# TWSE API
# ============================================================

TWSE_API = (
    "https://openapi.twse.com.tw/"
    "v1/opendata/t187ap03_L"
)


# ============================================================
# TPEx API
# ============================================================

TPEX_API = (
    "https://www.tpex.org.tw/"
    "openapi/v1/mopsfin_t187ap03_O"
)


# ============================================================
# TWSE 備援
# ============================================================

TWSE_FALLBACK_API = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/"
    "SecuritiesListing?response=json"
)


# ============================================================
# TPEx 備援
# ============================================================

TPEX_FALLBACK_API = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_close_quotes/"
    "st43.php?l=zh-tw"
)


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
            "en-US;q=0.8,"
            "en;q=0.7"
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
# 文字清理
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\ufeff", "")
    text = text.replace("\xa0", " ")

    return text.strip()


# ============================================================
# 股票代號
# ============================================================

def normalize_code(value):
    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    text = text.upper()

    # Yahoo TWSE
    if text.endswith(".TW"):
        text = text[:-3]

    # Yahoo TPEx
    elif text.endswith(".TWO"):
        text = text[:-4]

    text = text.strip()

    # 去掉常見 CSV 雜訊
    text = text.replace(" ", "")

    if not text.isdigit():
        return None

    if len(text) < 4 or len(text) > 6:
        return None

    return text


# ============================================================
# 股票名稱
# ============================================================

def normalize_name(value):
    return clean_text(value)


# ============================================================
# Yahoo Symbol
# ============================================================

def make_symbol(code, market):
    if market == "TPEx":
        return code + ".TWO"

    return code + ".TW"


# ============================================================
# HTTP GET
# ============================================================

def http_get(url, label):
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
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT,
                ),
            )

            status = response.status_code

            content = response.content or b""

            size = len(content)

            log(
                f"  HTTP Status: {status}"
            )

            log(
                f"  Content-Length: "
                f"{size} bytes"
            )

            if status != 200:
                raise RuntimeError(
                    f"HTTP {status}"
                )

            if size < MIN_RESPONSE_BYTES:
                preview = (
                    content[:300]
                    .decode(
                        "utf-8",
                        errors="replace",
                    )
                    .replace("\r", " ")
                    .replace("\n", " ")
                )

                raise RuntimeError(
                    "API 回應異常過短："
                    f"{size} bytes；"
                    f"內容：{preview}"
                )

            return response

        except Exception as exc:
            last_error = exc

            log(
                f"  ⚠️ attempt "
                f"{attempt} 失敗：{exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"{label} 取得失敗："
        f"{last_error}"
    )


# ============================================================
# HTTP GET - 不使用大小門檻
#
# 備援 CSV 有些格式可能比較小。
# 但仍然不能接受完全空白。
# ============================================================

def http_get_fallback(url, label):
    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        log(
            f"  Fallback HTTP GET "
            f"attempt {attempt}/{MAX_RETRIES}"
        )

        try:
            response = SESSION.get(
                url,
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT,
                ),
            )

            status = response.status_code

            content = response.content or b""

            size = len(content)

            log(
                f"  HTTP Status: {status}"
            )

            log(
                f"  Content-Length: "
                f"{size} bytes"
            )

            if status != 200:
                raise RuntimeError(
                    f"HTTP {status}"
                )

            if size < 100:
                raise RuntimeError(
                    f"Fallback 回應過短："
                    f"{size} bytes"
                )

            return response

        except Exception as exc:
            last_error = exc

            log(
                f"  ⚠️ fallback attempt "
                f"{attempt} 失敗：{exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"{label} 取得失敗："
        f"{last_error}"
    )


# ============================================================
# JSON 解析
# ============================================================

def parse_json_response(response):
    try:
        return response.json()
    except Exception:
        pass

    text = response.text

    try:
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(
            f"JSON 解析失敗：{exc}"
        ) from exc


# ============================================================
# Dictionary 取值
# ============================================================

def get_value(record, keys):
    if not isinstance(record, dict):
        return None

    # 直接查找
    for key in keys:
        if key in record:
            value = record.get(key)

            if value is not None:
                return value

    # 忽略大小寫
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

        value = record.get(actual)

        if value is not None:
            return value

    return None


# ============================================================
# 找 JSON 裡的候選 List
# ============================================================

def find_lists(value):
    result = []

    if isinstance(value, list):
        result.append(value)

        for item in value:
            if isinstance(
                item,
                (list, dict),
            ):
                result.extend(
                    find_lists(item)
                )

    elif isinstance(value, dict):

        for child in value.values():

            if isinstance(
                child,
                (list, dict),
            ):
                result.extend(
                    find_lists(child)
                )

    return result


# ============================================================
# JSON 股票資料解析
# ============================================================

def parse_json_records(
    payload,
    market,
):
    candidates = find_lists(payload)

    best_records = []
    best_score = -1

    for records in candidates:

        score = 0

        sample = records[:100]

        for item in sample:

            if not isinstance(
                item,
                dict,
            ):
                continue

            code = get_value(
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

            name = get_value(
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

            if normalize_name(name):
                score += 1

        if score > best_score:
            best_score = score
            best_records = records

    output = {}

    for item in best_records:

        if not isinstance(
            item,
            dict,
        ):
            continue

        code = get_value(
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

        name = get_value(
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

        code = normalize_code(code)

        if not code:
            continue

        name = normalize_name(name)

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
# CSV 解析
# ============================================================

def decode_response(response):
    content = response.content

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5",
    ]

    for encoding in encodings:

        try:
            return content.decode(
                encoding
            )
        except Exception:
            continue

    return content.decode(
        "utf-8",
        errors="replace",
    )


# ============================================================
# CSV 欄位正規化
# ============================================================

def normalize_header(value):
    text = clean_text(value)

    text = (
        text.replace(" ", "")
        .replace("\t", "")
        .replace("\r", "")
        .replace("\n", "")
    )

    return text.lower()


# ============================================================
# CSV 找欄位
# ============================================================

def find_csv_column(
    headers,
    candidates,
):
    normalized = {}

    for header in headers:
        normalized[
            normalize_header(header)
        ] = header

    for candidate in candidates:

        key = normalize_header(
            candidate
        )

        if key in normalized:
            return normalized[key]

    return None


# ============================================================
# CSV 股票解析
# ============================================================

def parse_csv_records(
    text,
    market,
):
    lines = text.splitlines()

    if not lines:
        return {}

    # --------------------------------------------------------
    # 移除可能的 JSON callback / HTML
    # --------------------------------------------------------

    useful_lines = []

    for line in lines:

        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith(
            "<!DOCTYPE"
        ):
            continue

        if stripped.startswith(
            "<html"
        ):
            continue

        useful_lines.append(line)

    if not useful_lines:
        return {}

    # --------------------------------------------------------
    # 嘗試 CSV
    # --------------------------------------------------------

    output = {}

    reader = csv.reader(
        io.StringIO(
            "\n".join(
                useful_lines
            )
        )
    )

    rows = list(reader)

    if not rows:
        return {}

    # --------------------------------------------------------
    # 找 header
    # --------------------------------------------------------

    header_index = None

    code_index = None
    name_index = None

    for index, row in enumerate(rows[:20]):

        if not row:
            continue

        headers = [
            clean_text(x)
            for x in row
        ]

        code_header = find_csv_column(
            headers,
            [
                "證券代號",
                "股票代號",
                "代號",
                "Code",
                "SecuritiesCompanyCode",
            ],
        )

        name_header = find_csv_column(
            headers,
            [
                "證券名稱",
                "股票名稱",
                "名稱",
                "公司名稱",
                "Name",
                "SecuritiesCompanyName",
            ],
        )

        if code_header is not None:

            header_index = index

            code_index = headers.index(
                code_header
            )

            if name_header is not None:
                name_index = headers.index(
                    name_header
                )

            break

    # --------------------------------------------------------
    # 如果找不到 header
    # 嘗試固定欄位
    # --------------------------------------------------------

    if header_index is None:

        for index, row in enumerate(
            rows
        ):

            if len(row) < 2:
                continue

            code = normalize_code(
                row[0]
            )

            if code:

                header_index = index
                code_index = 0
                name_index = 1
                break

    if header_index is None:
        return {}

    # --------------------------------------------------------
    # 解析
    # --------------------------------------------------------

    for row in rows[
        header_index + 1:
    ]:

        if code_index is None:
            continue

        if len(row) <= code_index:
            continue

        code = normalize_code(
            row[code_index]
        )

        if not code:
            continue

        name = ""

        if (
            name_index is not None
            and len(row) > name_index
        ):
            name = normalize_name(
                row[name_index]
            )

        output[code] = {
            "code": code,
            "name": name,
            "market": market,
            "symbol": make_symbol(
                code,
                market,
            ),
        }

    return output


# ============================================================
# 取得 TWSE
# ============================================================

def fetch_twse():
    section("取得 TWSE 上市股票")

    log(
        f"API：{TWSE_API}"
    )

    # --------------------------------------------------------
    # 主 API
    # --------------------------------------------------------

    try:

        response = http_get(
            TWSE_API,
            "TWSE API",
        )

        payload = parse_json_response(
            response
        )

        records = parse_json_records(
            payload,
            "TWSE",
        )

        log(
            f"TWSE JSON 原始解析："
            f"{len(records)} 筆"
        )

        if len(records) >= MIN_TWSE_STOCKS:

            log(
                f"✓ TWSE 主 API 成功："
                f"{len(records)}"
            )

            return records

        log(
            "⚠️ TWSE JSON 數量不足，"
            "嘗試 CSV 備援"
        )

    except Except
