#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.3.0

============================================================
用途
============================================================

建立台股全市場 Universe：

    TWSE 上市
    TPEx 上櫃

輸出：

    Data/universe.json

============================================================
核心規格
============================================================

1. TWSE / TPEx 分開取得
2. TWSE / TPEx 分開驗證
3. HTTP 自動 retry
4. 正確處理 HTTP redirect：
       301 / 302 / 303 / 307 / 308
5. HTTP 200 不代表 API 正常
6. 偵測 TWSE / TPEx 安全性阻擋 HTML
7. 主 API 異常時立即進入備援
8. 支援 JSON / CSV 多種資料格式
9. 所有資料通過驗證後才覆蓋 universe.json
10. 任一階段失敗都保留舊 universe.json
11. 不允許產生空 Universe
12. 不允許 total < MIN_TOTAL_STOCKS
13. TWSE / TPEx 均有最低股票數安全門檻
14. Yahoo ticker：
        TWSE -> .TW
        TPEx -> .TWO
15. 使用原子寫入
16. 支援 UTF-8 / Big5 / CP950
17. main() 明確執行
18. 未預期錯誤 exit code 1
19. 不會因為 API 回傳 HTTP 200 就誤判成功
20. 不會因為 redirect 就錯誤中止
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
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V5.3.0"

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

MAX_RETRIES = 3
RETRY_DELAY = 2.0

REQUEST_DELAY = 0.5

MIN_RESPONSE_BYTES = 100
MIN_FALLBACK_RESPONSE_BYTES = 100


# ============================================================
# Redirect
# ============================================================

REDIRECT_STATUS_CODES = {
    301,
    302,
    303,
    307,
    308,
}


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
# TWSE 備援 API
# ============================================================

TWSE_FALLBACK_APIS = [
    (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/"
        "SecuritiesListing?response=json"
    ),
    (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/"
        "SecuritiesListing?response=json&format=json"
    ),
]


# ============================================================
# TPEx 備援 API
# ============================================================

TPEX_FALLBACK_APIS = [
    (
        "https://www.tpex.org.tw/"
        "web/stock/aftertrading/"
        "daily_close_quotes/"
        "st43.php?l=zh-tw"
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

    if text.endswith(".TW"):
        text = text[:-3]

    elif text.endswith(".TWO"):
        text = text[:-4]

    text = text.strip()

    text = text.replace(" ", "")
    text = text.replace('"', "")
    text = text.replace("'", "")

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
# 判斷是否為 HTML
# ============================================================

def looks_like_html(response):

    content = response.content or b""

    preview = (
        content[:2000]
        .decode(
            "utf-8",
            errors="replace",
        )
        .lower()
        .strip()
    )

    content_type = (
        response.headers.get(
            "Content-Type",
            "",
        )
        .lower()
    )

    if "text/html" in content_type:
        return True

    html_markers = [
        "<html",
        "<!doctype",
        "<head",
        "<body",
        "for security reasons",
        "因為安全性考量",
        "無法呈現",
    ]

    for marker in html_markers:

        if marker in preview:
            return True

    return False


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    label,
    min_bytes=MIN_RESPONSE_BYTES,
):

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        log(
            f"  HTTP GET "
            f"attempt {attempt}/{MAX_RETRIES}"
        )

        try:

            response = SESSION.get(
                url,
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT,
                ),
                allow_redirects=True,
            )

            status = response.status_code

            content = response.content or b""

            size = len(content)

            log(
                f"  HTTP Status: {status}"
            )

            log(
                f"  Final URL: "
                f"{response.url}"
            )

            log(
                f"  Content-Length: "
                f"{size} bytes"
            )

            # ------------------------------------------------
            # Redirect
            # ------------------------------------------------

            if status in REDIRECT_STATUS_CODES:

                location = response.headers.get(
                    "Location",
                    "",
                )

                raise RuntimeError(
                    f"HTTP redirect {status}"
                    f" -> {location}"
                )

            # ------------------------------------------------
            # HTTP status
            # ------------------------------------------------

            if status != 200:

                raise RuntimeError(
                    f"HTTP {status}"
                )

            # ------------------------------------------------
            # 空內容
            # ------------------------------------------------

            if size < min_bytes:

                preview = (
                    content[:500]
                    .decode(
                        "utf-8",
                        errors="replace",
                    )
                    .replace("\r", " ")
                    .replace("\n", " ")
                )

                raise RuntimeError(
                    "API 回應過短："
                    f"{size} bytes；"
                    f"內容：{preview}"
                )

            # ------------------------------------------------
            # HTML 安全阻擋
            # ------------------------------------------------

            if looks_like_html(response):

                preview = (
                    content[:500]
                    .decode(
                        "utf-8",
                        errors="replace",
                    )
                    .replace("\r", " ")
                    .replace("\n", " ")
                )

                raise RuntimeError(
                    "API 回傳 HTML / "
                    "安全性阻擋頁面；"
                    f"內容：{preview}"
                )

            return response

        except Exception as exc:

            last_error = exc

            log(
                f"  ⚠️ attempt "
                f"{attempt} 失敗："
                f"{exc}"
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

def get_value(
    record,
    keys,
):

    if not isinstance(
        record,
        dict,
    ):
        return None

    # --------------------------------------------------------
    # 直接查找
    # --------------------------------------------------------

    for key in keys:

        if key in record:

            value = record.get(key)

            if value is not None:
                return value

    # --------------------------------------------------------
    # 忽略大小寫
    # --------------------------------------------------------

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
# 找 JSON 所有 List
# ============================================================

def find_lists(value):

    result = []

    if isinstance(
        value,
        list,
    ):

        result.append(value)

        for item in value:

            if isinstance(
                item,
                (list, dict),
            ):

                result.extend(
                    find_lists(item)
                )

    elif isinstance(
        value,
        dict,
    ):

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
# JSON 股票解析
# ============================================================

def parse_json_records(
    payload,
    market,
):

    candidates = find_lists(
        payload
    )

    best_records = []
    best_score = -1

    code_keys = [
        "Code",
        "code",
        "股票代號",
        "證券代號",
        "有價證券代號",
        "代號",
        "SecuritiesCompanyCode",
        "公司代號",
    ]

    name_keys = [
        "Name",
        "name",
        "股票名稱",
        "證券名稱",
        "公司名稱",
        "名稱",
        "SecuritiesCompanyName",
        "有價證券名稱",
    ]

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
                code_keys,
            )

            name = get_value(
                item,
                name_keys,
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
            code_keys,
        )

        name = get_value(
            item,
            name_keys,
        )

        code = normalize_code(
            code
        )

        if not code:
            continue

        name = normalize_name(
            name
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
# Decode Response
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
# CSV Header Normalize
# ============================================================

def normalize_header(value):

    text = clean_text(value)

    text = (
        text
        .replace(" ", "")
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

    if not text:
        return {}

    text = text.replace(
        "\ufeff",
        "",
    )

    lines = text.splitlines()

    if not lines:
        return {}

    useful_lines = []

    for line in lines:

        stripped = line.strip()

        if not stripped:
            continue

        if stripped.lower().startswith(
            "<!doctype"
        ):
            continue

        if stripped.lower().startswith(
            "<html"
        ):
            continue

        useful_lines.append(line)

    if not useful_lines:
        return {}

    try:

        reader = csv.reader(
            io.StringIO(
                "\n".join(
                    useful_lines
                )
            )
        )

        rows = list(reader)

    except Exception as exc:

        raise RuntimeError(
            f"CSV 解析失敗：{exc}"
        ) from exc

    if not rows:
        return {}

    header_index = None
    code_index = None
    name_index = None

    code_candidates = [
        "證券代號",
        "股票代號",
        "代號",
        "Code",
        "SecuritiesCompanyCode",
        "有價證券代號",
    ]

    name_candidates = [
        "證券名稱",
        "股票名稱",
        "名稱",
        "公司名稱",
        "Name",
        "SecuritiesCompanyName",
        "有價證券名稱",
    ]

    # --------------------------------------------------------
    # 找 Header
    # --------------------------------------------------------

    for index, row in enumerate(
        rows[:30]
    ):

        if not row:
            continue

        headers = [
            clean_text(x)
            for x in row
        ]

        code_header = find_csv_column(
            headers,
            code_candidates,
        )

        name_header = find_csv_column(
            headers,
            name_candidates,
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
    # Header 找不到
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

    output = {}

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
# 股票資料驗證
# ============================================================

def validate_records(
    records,
    market,
    minimum,
):

    if not isinstance(
        records,
        dict,
    ):

        raise RuntimeError(
            f"{market} 資料格式錯
