#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.2.5

============================================================
用途
============================================================

建立台股全市場 Universe：

    TWSE 上市
    TPEx 上櫃

輸出：

    Data/universe.json

============================================================
V5.2.5 修正重點
============================================================

1. TWSE 主 API 保留
2. TPEx 主 API 保留
3. 強化 TPEx JSON 欄位辨識
4. 支援 TPEx 不同 API 欄位命名
5. TPEx 主 API 解析失敗時：
       -> tpex_mainboard_quotes
       -> 舊版網頁 fallback
6. HTTP 200 不代表 API 正常
7. HTML error page 不視為有效資料
8. Redirect 到 /errors 不視為有效資料
9. TWSE / TPEx 分開取得
10. TWSE / TPEx 分開驗證
11. TWSE / TPEx 均有最低股票數安全門檻
12. Total 有最低安全門檻
13. 不允許空 Universe
14. 不允許產生半成品 JSON
15. 任一階段失敗都保留舊 universe.json
16. Atomic Write
17. UTF-8 / Big5 / CP950
18. JSON / CSV
19. 多組股票代號欄位
20. 多組股票名稱欄位
21. 明確 main()
22. 未預期錯誤 exit code 1
23. 成功 exit code 0
24. KeyboardInterrupt exit code 130

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
from urllib.parse import urlparse

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V5.2.5"

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

MIN_FALLBACK_RESPONSE_BYTES = 100


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
# TPEx 主 API
# ============================================================

TPEX_API = (
    "https://www.tpex.org.tw/"
    "openapi/v1/mopsfin_t187ap03_O"
)


# ============================================================
# TPEx 行情型備援 API
#
# 此 endpoint 可提供上櫃股票代號與公司名稱，
# 因此適合作為 Universe 建立的備援資料源。
# ============================================================

TPEX_QUOTES_API = (
    "https://www.tpex.org.tw/"
    "openapi/v1/tpex_mainboard_quotes"
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
# TPEx 舊版備援
#
# 注意：
# 這個 endpoint 如果被導向 /errors，
# 必須判定為失敗。
# ============================================================

TPEX_LEGACY_FALLBACK_API = (
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
# 股票代號正規化
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

    text = text.replace(
        " ",
        "",
    )

    text = text.replace(
        '"',
        "",
    )

    text = text.replace(
        "'",
        "",
    )

    if not text.isdigit():
        return None

    if len(text) < 4 or len(text) > 6:
        return None

    return text


# ============================================================
# 股票名稱正規化
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
# 判斷是否為 HTML
# ============================================================

def looks_like_html(
    response,
):

    content = response.content or b""

    if not content:
        return False

    preview = (
        content[:1000]
        .decode(
            "utf-8",
            errors="ignore",
        )
        .lstrip()
        .lower()
    )

    if preview.startswith(
        "<!doctype"
    ):
        return True

    if preview.startswith(
        "<html"
    ):
        return True

    if "<html" in preview:
        return True

    if "<body" in preview:
        return True

    if "page cannot be accessed" in preview:
        return True

    if "for security reasons" in preview:
        return True

    return False


# ============================================================
# 判斷是否為 TPEx error page
# ============================================================

def looks_like_error_page(
    response,
):

    final_url = clean_text(
        getattr(
            response,
            "url",
            "",
        )
    )

    parsed = urlparse(
        final_url
    )

    path = (
        parsed.path or ""
    ).lower()

    if path.rstrip("/") == "/errors":
        return True

    text = (
        response.text[:3000]
        .lower()
    )

    error_keywords = [
        "page cannot be accessed",
        "for security reasons",
        "error",
        "access denied",
        "403",
        "404",
    ]

    for keyword in error_keywords:

        if keyword in text:
            return True

    return False


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    label,
):

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
                allow_redirects=True,
            )

            status = (
                response.status_code
            )

            content = (
                response.content or b""
            )

            size = len(content)

            log(
                f"  HTTP Status: {status}"
            )

            log(
                f"  Content-Length: "
                f"{size} bytes"
            )

            final_url = clean_text(
                getattr(
                    response,
                    "url",
                    "",
                )
            )

            if final_url:
                log(
                    "  Final URL："
                    f"{final_url}"
                )

            if status != 200:

                raise RuntimeError(
                    f"HTTP {status}"
                )

            if looks_like_html(
                response
            ):

                preview = (
                    content[:500]
                    .decode(
                        "utf-8",
                        errors="replace",
                    )
                    .replace(
                        "\r",
                        " ",
                    )
                    .replace(
                        "\n",
                        " ",
                    )
                )

                raise RuntimeError(
                    "API 回傳 HTML "
                    "而非有效資料："
                    f"{preview}"
                )

            if looks_like_error_page(
                response
            ):

                raise RuntimeError(
                    "API 被導向錯誤頁面："
                    f"{final_url}"
                )

            if size < MIN_RESPONSE_BYTES:

                preview = (
                    content[:300]
                    .decode(
                        "utf-8",
                        errors="replace",
                    )
                    .replace(
                        "\r",
                        " ",
                    )
                    .replace(
                        "\n",
                        " ",
                    )
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
# HTTP GET - 備援
# ============================================================

def http_get_fallback(
    url,
    label,
):

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        log(
            f"  Fallback HTTP GET "
            f"attempt "
            f"{attempt}/{MAX_RETRIES}"
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

            status = (
                response.status_code
            )

            content = (
                response.content or b""
            )

            size = len(content)

            log(
                f"  HTTP Status: "
                f"{status}"
            )

            log(
                f"  Content-Length: "
                f"{size} bytes"
            )

            final_url = clean_text(
                getattr(
                    response,
                    "url",
                    "",
                )
            )

            if final_url:

                log(
                    "  Redirected URL："
                    f"{final_url}"
                )

            if status != 200:

                raise RuntimeError(
                    f"HTTP {status}"
                )

            if looks_like_html(
                response
            ):

                raise RuntimeError(
                    "Fallback 回傳 HTML"
                )

            if looks_like_error_page(
                response
            ):

                raise RuntimeError(
                    "Fallback 被導向錯誤頁面："
                    f"{final_url}"
                )

            if size < MIN_FALLBACK_RESPONSE_BYTES:

                raise RuntimeError(
                    "Fallback 回應過短："
                    f"{size} bytes"
                )

            return response

        except Exception as exc:

            last_error = exc

            log(
                f"  ⚠️ fallback attempt "
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

def parse_json_response(
    response,
):

    try:

        return response.json()

    except Exception:
        pass

    text = response.text

    try:

        return json.loads(
            text
        )

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

            value = record.get(
                key
            )

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

        value = record.get(
            actual
        )

        if value is not None:

            return value

    return None


# ============================================================
# 遞迴找 JSON 所有 List
# ============================================================

def find_lists(
    value,
):

    result = []

    if isinstance(
        value,
        list,
    ):

        result.append(
            value
        )

        for item in value:

            if isinstance(
                item,
                (
                    list,
                    dict,
                ),
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
                (
                    list,
                    dict,
                ),
            ):

                result.extend(
                    find_lists(child)
                )

    return result


# ============================================================
# 股票代號欄位
# ============================================================

CODE_KEYS = [

    "Code",

    "code",

    "StockCode",

    "stockCode",

    "StockNo",

    "stockNo",

    "StockID",

    "stockID",

    "SecurityCode",

    "securityCode",

    "SecuritiesCompanyCode",

    "CompanyCode",

    "companyCode",

    "CompanyID",

    "companyID",

    "證券代號",

    "股票代號",

    "有價證券代號",

    "代號",

    "公司代號",

]


# ============================================================
# 股票名稱欄位
# ============================================================

NAME_KEYS = [

    "Name",

    "name",

    "StockName",

    "stockName",

    "SecurityName",

    "securityName",

    "CompanyName",

    "companyName",

    "CompanyShortName",

    "companyShortName",

    "SecuritiesCompanyName",

    "證券名稱",

    "股票名稱",

    "有價證券名稱",

    "名稱",

    "公司名稱",

    "公司簡稱",

]


# ============================================================
# JSON 股票資料解析
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

    best_code_count = 0

    # --------------------------------------------------------
    # 搜尋最像股票清單的 List
    # --------------------------------------------------------

    for records in candidates:

        score = 0

        code_count = 0

        name_count = 0

        sample = records[:200]

        for item in sample:

            if not isinstance(
                item,
                dict,
            ):
                continue

            code = get_value(
                item,
                CODE_KEYS,
            )

            name = get_value(
                item,
                NAME_KEYS,
            )

            normalized_code = (
                normalize_code(code)
            )

            normalized_name = (
                normalize_name(name)
            )

            if normalized_code:

                score += 3

                code_count += 1

            if normalized_name:

                score += 1

                name_count += 1

        # ----------------------------------------------------
        # 股票資料應同時有 code / name
        # ----------------------------------------------------

        if code_count > 0:

            score += min(
                code_count,
                50,
            )

        if name_count > 0:

            score += min(
                name_count,
                25,
            )

        if score > best_score:

            best_score = score

            best_records = records

            best_code_count = (
                code_count
            )

    log(
        "JSON candidate："
        f"{len(candidates)} 組"
    )

    log(
        "Best JSON candidate："
        f"{len(best_records)} 筆"
    )

    log(
        "Best candidate code count："
        f"{best_code_count}"
    )

    output = {}

    # --------------------------------------------------------
    # 解析最佳 List
    # --------------------------------------------------------

    for item in best_records:

        if not isinstance(
            item,
            dict,
        ):

            continue

        code = get_value(
            item,
            CODE_KEYS,
        )

        name = get_value(
            item,
            NAME_KEYS,
        )

        code = normalize_code(
            code
        )

        if not code:

            continue

        name = normalize_name(
            name
        )

        # ----------------------------------------------------
        # 沒有名稱的資料先不加入
        # ----------------------------------------------------

        if not name:

            continue

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
# TPEx 專用 JSON 解析
#
# 目的：
# 不完全依賴通用欄位搜尋。
# ============================================================

def parse_tpex_json_records(
    payload,
):

    section(
        "TPEx 專用 JSON 解析"
    )

    # --------------------------------------------------------
    # 第一階段：通用解析
    # --------------------------------------------------------

    records = parse_json_records(
        payload,
        "TPEx",
    )

    if len(records) >= MIN_TPEX_STOCKS:

        log(
            "✓ TPEx 通用 JSON 解析成功："
            f"{len(records)} 筆"
        )

        return records

    log(
        "⚠️ TPEx 通用解析不足："
        f"{len(records)} 筆"
    )

    # --------------------------------------------------------
    # 第二階段：直接掃描所有 dict
    #
    # 某些 API 的資料巢狀結構可能讓候選 List 判斷
    # 選錯，因此再次全樹搜尋。
    # --------------------------------------------------------

    output = {}

    def walk(value):

        if isinstance(
            value,
            dict,
        ):

            code = get_value(
                value,
                CODE_KEYS,
            )

            name = get_value(
                value,
                NAME_KEYS,
            )

            code = normalize_code(
                code
            )

            name = normalize_name(
                name
            )

            if code and name:

                output[code] = {

                    "code": code,

                    "name": name,

                    "market": "TPEx",

                    "symbol": make_symbol(
                        code,
                        "TPEx",
                    ),
                }

            for child in value.values():

                if isinstance(
                    child,
                    (
                        dict,
                        list,
                    ),
                ):

                    walk(child)

        elif isinstance(
            value,
            list,
        ):

            for item in value:

                if isinstance(
                    item,
                    (
                        dict,
                        list,
                    ),
                ):

                    walk(item)

    walk(payload)

    log(
        "TPEx recursive JSON 解析："
        f"{len(output)} 筆"
    )

    if len(output) >= MIN_TPEX_STOCKS:

        return output

    return {}


# ============================================================
# Response Decode
# ============================================================

def decode_response(
    response,
):

    content = (
        response.content
    )

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
# CSV Header Normalization
# ============================================================

def normalize_header(
    value,
):

    text = clean_text(
        value
    )

    text = (
        text
        .replace(
            " ",
            "",
        )
        .replace(
            "\t",
            "",
        )
        .replace(
            "\r",
            "",
        )
        .replace(
            "\n",
            "",
        )
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

            return normalized[
                key
            ]

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

        lower = stripped.lower()

        if lower.startswith(
            "<!doctype"
        ):

            continue

        if lower.startswith(
            "<html"
        ):

            continue

        useful_lines.append(
            line
        )

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

        rows = list(
            reader
        )

    except Exception as exc:

        raise RuntimeError(
            f"CSV 解析失敗：{exc}"
        ) from exc

    if not rows:

        return {}

    header_index = None

    code_index = None

    name_index = None

    # --------------------------------------------------------
    # 搜尋 Header
    # --------------------------------------------------------

    for index, row in enumerate(
        rows[:50]
    ):

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
                "有價證券代號",
                "代號",
                "Code",
                "code",
                "StockCode",
                "SecurityCode",
                "SecuritiesCompanyCode",
                "CompanyCode",
                "公司代號",
            ],
        )

        name_header = find_csv_column(
            headers,
            [
                "證券名稱",
                "股票名稱",
                "有價證券名稱",
                "名稱",
                "公司名稱",
                "公司簡稱",
                "Name",
                "name",
                "StockName",
                "SecurityName",
                "CompanyName",
                "SecuritiesCompanyName",
            ],
        )

        if code_header is not None:

            header_index = index

            code_index = (
                headers.index(
                    code_header
                )
            )

            if name_header is not None:

                name_index = (
                    headers.index(
                        name_header
                    )
                )

            break

    # --------------------------------------------------------
    # Header 找不到：
    # 嘗試第一個可辨識股票代號
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

    # --------------------------------------------------------
    # 解析資料
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

        if not name:

            continue

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
# 股票資料基本驗證
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
            f"{market} 資料格式錯誤"
        )

    count = len(records)

    if count < minimum:

        raise RuntimeError(
            f"{market} 股票數量不足："
            f"{count} < {minimum}"
        )

    invalid = []

    for code, item in records.items():

        if not isinstance(
            item,
            dict,
        ):

            invalid.append(
                code
            )

            continue

        if item.get(
            "code"
        ) != code:

            invalid.append(
                code
            )

            continue

        if not normalize_code(
            item.get("code")
        ):

            invalid.append(
                code
            )

            continue

        if not normalize_name(
            item.get("name")
        ):

            invalid.append(
                code
            )

            continue

        if item.get(
            "market"
        ) != market:

            invalid.append(
                code
            )

            continue

        expected_symbol = make_symbol(
            code,
            market,
        )

        if item.get(
            "symbol"
        ) != expected_symbol:

            invalid.append(
                code
            )

            continue

    if invalid:

        preview = ", ".join(
            invalid[:10]
        )

        raise RuntimeError(
            f"{market} 有 "
            f"{len(invalid)} 筆資料驗證失敗"
            f"；範例：{preview}"
        )

    log(
        f"✓ {market} 驗證通過："
        f"{count} 檔"
    )

    return True


# ============================================================
# TWSE 取得
# ============================================================

def fetch_twse():

    section(
        "取得 TWSE 上市股票"
    )

    log(
        f"主 API：{TWSE_API}"
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
            "TWSE JSON 原始解析："
            f"{len(records)} 筆"
        )

        if (
            len(records)
            >= MIN_TWSE_STOCKS
        ):

            validate_records(
                records,
                "TWSE",
                MIN_TWSE_STOCKS,
            )

            log(
                "✓ TWSE 主 API 成功"
            )

            return records

        log(
            "⚠️ TWSE 主 API 數量不足，"
            "進入備援"
        )

    except Exception as exc:

        log(
            "⚠️ TWSE 主 API 失敗："
            f"{exc}"
        )

    # --------------------------------------------------------
    # 備援 API
    # --------------------------------------------------------

    log(
        "TWSE 備援 API："
        f"{TWSE_FALLBACK_API}"
    )

    try:

        response = http_get_fallback(
            TWSE_FALLBACK_API,
            "TWSE fallback",
        )

        content_type = (
            response.headers.get(
                "Content-Type",
                "",
            )
            .lower()
        )

        log(
            "Fallback Content-Type："
            f"{content_type}"
        )

        records = {}

        text = decode_response(
            response
        )

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        if (
            "json" in content_type
            or text.lstrip().startswith(
                "{"
            )
            or text.lstrip().startswith(
                "["
            )
        ):

            try:

                payload = (
                    parse_json_response(
                        response
                    )
                )

                records = (
                    parse_json_records(
                        payload,
                        "TWSE",
                    )
                )

            except Exception as exc:

                log(
                    "⚠️ TWSE fallback "
                    "JSON 解析失敗："
                    f"{exc}"
                )

        # ----------------------------------------------------
        # CSV
        # ----------------------------------------------------

        if (
            len(records)
            < MIN_TWSE_STOCKS
        ):

            records = (
                parse_csv_records(
                    text,
                    "TWSE",
                )
            )

        log(
            "TWSE fallback 解析："
            f"{len(records)} 筆"
        )

        validate_records(
            records,
            "TWSE",
            MIN_TWSE_STOCKS,
        )

        return records

    except Exception as exc:

        raise RuntimeError(
            "TWSE 主 API 與備援 API "
            f"均失敗：{exc}"
        ) from exc


# ============================================================
# TPEx 主 API
# ============================================================

def fetch_tpex_primary():

    log(
        f"主 API：{TPEX_API}"
    )

    response = http_get(
        TPEX_API,
        "TPEx API",
    )

    payload = parse_json_response(
        response
    )

    records = parse_tpex_json_records(
        payload
    )

    log(
        "TPEx JSON 原始解析："
        f"{len(records)} 筆"
    )

    validate_records(
        records,
        "TPEx",
        MIN_TPEX_STOCKS,
    )

    return records


# ============================================================
# TPEx Quotes API
#
# 作為主要備援。
# ============================================================

def fetch_tpex_quotes():

    section(
        "TPEx 備援 A：主板股票行情 API"
    )

    log(
        f"API：{TPEX_QUOTES_API}"
    )

    response = http_get_fallback(
        TPEX_QUOTES_API,
        "TPEx quotes fallback",
    )

    payload = parse_json_response(
        response
    )

    # --------------------------------------------------------
    # 這個 endpoint 常見欄位：
    #
    # SecuritiesCompanyCode
    # CompanyName
    #
    # 但仍使用通用 parser，
    # 同時 CODE_KEYS / NAME_KEYS 已包含。
    # --------------------------------------------------------

    records = parse_json_records(
        payload,
        "TPEx",
    )

    log(
        "TPEx quotes JSON 解析："
        f"{len(records)} 筆"
    )

    if (
        len(records)
        < MIN_TPEX_STOCKS
    ):

        records = (
            parse_tpex_json_records(
                payload
            )
        )

    validate_records(
        records,
        "TPEx",
        MIN_TPEX_STOCKS,
    )

    return records


# ============================================================
# TPEx Legacy Fallback
# ============================================================

def fetch_tpex_legacy():

    section(
        "TPEx 備援 B：Legacy API"
    )

    log(
        "API："
        f"{TPEX_LEGACY_FALLBACK_API}"
    )

    response = http_get_fallback(
        TPEX_LEGACY_FALLBACK_API,
        "TPEx legacy fallback",
    )

    text = decode_response(
        response
    )

    records = {}

    # --------------------------------------------------------
    # 嘗試 JSON
    # --------------------------------------------------------

    stripped = text.lstrip()

    if (
        stripped.startswith(
            "{"
        )
        or stripped.startswith(
            "["
        )
    ):

        try:

            payload = (
                parse_json_response(
                    response
                )
            )

            records = (
                parse_json_records(
                    payload,
                    "TPEx",
                )
            )

        except Exception as exc:

            log(
                "⚠️ TPEx legacy "
                "JSON 解析失敗："
                f"{exc}"
            )

    # --------------------------------------------------------
    # CSV
    # --------------------------------------------------------

    if (
        len(records)
        < MIN_TPEX_STOCKS
    ):

        records = (
            parse_csv_records(
                text,
                "TPEx",
            )
        )

    log(
        "TPEx legacy 解析："
        f"{len(records)} 筆"
    )

    validate_records(
        records,
        "TPEx",
        MIN_TPEX_STOCKS,
    )

    return records


# ============================================================
# TPEx 取得總流程
# ============================================================

def fetch_tpex():

    section(
        "取得 TPEx 上櫃股票"
    )

    # --------------------------------------------------------
    # 1. TPEx 官方基本資料 API
    # --------------------------------------------------------

    try:

        records = fetch_tpex_primary()

        log(
            "✓ TPEx 主 API 成功"
        )

        return records

    except Exception as exc:

        log(
            "⚠️ TPEx 主 API 失敗："
            f"{exc}"
        )

    time.sleep(
        REQUEST_DELAY
    )

    # --------------------------------------------------------
    # 2. TPEx mainboard quotes
    # --------------------------------------------------------

    try:

        records = fetch_tpex_quotes()

        log(
            "✓ TPEx 備援 A 成功"
        )

        return records

    except Exception as exc:

        log(
            "⚠️ TPEx 備援 A 失敗："
            f"{exc}"
        )

    time.sleep(
        REQUEST_DELAY
    )

    # --------------------------------------------------------
    # 3. Legacy fallback
    # --------------------------------------------------------

    try:

        records = fetch_tpex_legacy()

        log(
            "✓ TPEx 備援 B 成功"
        )

        return records

    except Exception as exc:

        log(
            "⚠️ TPEx 備援 B 失敗："
            f"{exc}"
        )

    raise RuntimeError(
        "TPEx 主 API、"
        "備援 A、"
        "備援 B 均失敗"
    )


# ============================================================
# Universe 合併
# ============================================================

def merge_universe(
    twse_records,
    tpex_records,
):

    section(
        "合併全市場 Universe"
    )

    if not twse_records:

        raise RuntimeError(
            "TWSE 資料為空"
        )

    if not tpex_records:

        raise RuntimeError(
            "TPEx 資料為空"
        )

    combined = {}

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    for code, item in (
        twse_records.items()
    ):

        if code in combined:

            raise RuntimeError(
                f"股票代號重複："
                f"{code}"
            )

        combined[code] = item

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    for code, item in (
        tpex_records.items()
    ):

        if code in combined:

            raise RuntimeError(
                "TWSE / TPEx "
                "股票代號重複："
                f"{code}"
            )

        combined[code] = item

    log(
        f"TWSE："
        f"{len(twse_records)}"
    )

    log(
        f"TPEx："
        f"{len(tpex_records)}"
    )

    log(
        f"Total："
        f"{len(combined)}"
    )

    return combined


# ============================================================
# Universe 最終驗證
# ============================================================

def validate_universe(
    twse_records,
    tpex_records,
    combined,
):

    section(
        "Universe 最終驗證"
    )

    listed_count = len(
        twse_records
    )

    otc_count = len(
        tpex_records
    )

    total = len(
        combined
    )

    # --------------------------------------------------------
    # 數量安全門
    # --------------------------------------------------------

    if (
        listed_count
        < MIN_TWSE_STOCKS
    ):

        raise RuntimeError(
            "TWSE 最終數量不足："
            f"{listed_count}"
        )

    if (
        otc_count
        < MIN_TPEX_STOCKS
    ):

        raise RuntimeError(
            "TPEx 最終數量不足："
            f"{otc_count}"
        )

    if (
        total
        < MIN_TOTAL_STOCKS
    ):

        raise RuntimeError(
            "全市場股票數量不足："
            f"{total} < "
            f"{MIN_TOTAL_STOCKS}"
        )

    # --------------------------------------------------------
    # 一致性
    # --------------------------------------------------------

    if total != (
        listed_count
        + otc_count
    ):

        raise RuntimeError(
            "total 與上市/上櫃"
            "數量不一致"
        )

    # --------------------------------------------------------
    # 每筆再次驗證
    # --------------------------------------------------------

    for code, item in (
        combined.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                "Universe item "
                "格式錯誤："
                f"{code}"
            )

        if item.get(
            "code"
        ) != code:

            raise RuntimeError(
                "code 不一致："
                f"{code}"
            )

        if not normalize_code(
            item.get("code")
        ):

            raise RuntimeError(
                "股票代號無效："
                f"{code}"
            )

        if not normalize_name(
            item.get("name")
        ):

            raise RuntimeError(
                "股票名稱為空："
                f"{code}"
            )

        market = item.get(
            "market"
        )

        if market not in (
            "TWSE",
            "TPEx",
        ):

            raise RuntimeError(
                "market 錯誤："
                f"{code} -> "
                f"{market}"
            )

        expected_symbol = (
            make_symbol(
                code,
                market,
            )
        )

        if item.get(
            "symbol"
        ) != expected_symbol:

            raise RuntimeError(
                "symbol 錯誤："
                f"{code}"
            )

    log(
        f"✓ total = {total}"
    )

    log(
        "✓ listed_stocks = "
        f"{listed_count}"
    )

    log(
        "✓ otc_stocks = "
        f"{otc_count}"
    )

    log(
        f"✓ items = "
        f"{len(combined)}"
    )

    log(
        "✓ Universe validation "
        "passed"
    )

    return True


# ============================================================
# 建立輸出資料
# ============================================================

def build_output(
    twse_records,
    tpex_records,
    combined,
):

    generated_at = (
        datetime.now(
            timezone.utc
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )

    items = list(
        combined.values()
    )

    # --------------------------------------------------------
    # 穩定排序
    # --------------------------------------------------------

    items.sort(
        key=lambda item: (
            item.get(
                "market",
                "",
            ),
            item.get(
                "code",
                "",
            ),
        )
    )

    data = {

        "version": VERSION,

        "generated_at":
            generated_at,

        "total":
            len(items),

        "listed_stocks":
            len(twse_records),

        "otc_stocks":
            len(tpex_records),

        "items":
            items,
    }

    return data


# ============================================================
# 輸出資料最後驗證
# ============================================================

def validate_output(
    data,
):

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "輸出資料不是 JSON object"
        )

    items = data.get(
        "items",
        [],
    )

    total = data.get(
        "total",
        0,
    )

    listed = data.get(
        "listed_stocks",
        0,
    )

    otc = data.get(
        "otc_stocks",
        0,
    )

    if not isinstance(
        items,
        list,
    ):

        raise RuntimeError(
            "items 不是 list"
        )

    if not items:

        raise RuntimeError(
            "禁止產生空 items"
        )

    if total <= 0:

        raise RuntimeError(
            "禁止產生 total = 0"
        )

    if total != len(
        items
    ):

        raise RuntimeError(
            "total 與 items "
            "數量不一致"
        )

    if (
        listed
        < MIN_TWSE_STOCKS
    ):

        raise RuntimeError(
            "listed_stocks "
            "安全門檻失敗"
        )

    if (
        otc
        < MIN_TPEX_STOCKS
    ):

        raise RuntimeError(
            "otc_stocks "
            "安全門檻失敗"
        )

    if (
        total
        < MIN_TOTAL_STOCKS
    ):

        raise RuntimeError(
            "total 安全門檻失敗"
        )

    if total != (
        listed + otc
    ):

        raise RuntimeError(
            "total != "
            "listed_stocks + "
            "otc_stocks"
        )

    seen_codes = set()

    seen_symbols = set()

    market_counts = {
        "TWSE": 0,
        "TPEx": 0,
    }

    # --------------------------------------------------------
    # 每筆輸出資料驗證
    # --------------------------------------------------------

    for item in items:

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                "Universe item "
                "不是 object"
            )

        code = normalize_code(
            item.get("code")
        )

        name = normalize_name(
            item.get("name")
        )

        market = item.get(
            "market"
        )

        symbol = item.get(
            "symbol"
        )

        if not code:

            raise RuntimeError(
                "存在無效股票代號"
            )

        if not name:

            raise RuntimeError(
                f"{code} "
                "股票名稱為空"
            )

        if market not in (
            "TWSE",
            "TPEx",
        ):

            raise RuntimeError(
                f"{code} "
                "market 無效"
            )

        expected_symbol = (
            make_symbol(
                code,
                market,
            )
        )

        if symbol != expected_symbol:

            raise RuntimeError(
                f"{code} "
                "symbol 錯誤"
            )

        if code in seen_codes:

            raise RuntimeError(
                "股票代號重複："
                f"{code}"
            )

        if symbol in seen_symbols:

            raise RuntimeError(
                "Yahoo symbol 重複："
                f"{symbol}"
            )

        seen_codes.add(
            code
        )

        seen_symbols.add(
            symbol
        )

        market_counts[
            market
        ] += 1

    # --------------------------------------------------------
    # 市場數量再次驗證
    # --------------------------------------------------------

    if (
        market_counts["TWSE"]
        != listed
    ):

        raise RuntimeError(
            "TWSE item 數量與 "
            "listed_stocks 不一致"
        )

    if (
        market_counts["TPEx"]
        != otc
    ):

        raise RuntimeError(
            "TPEx item 數量與 "
            "otc_stocks 不一致"
        )

    return True


# ============================================================
# Atomic Write
# ============================================================

def atomic_write_json(
    data,
    path,
):

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".universe.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:

            temp_path = Path(
                temp_file.name
            )

            json.dump(
                data,
                temp_file,
                ensure_ascii=False,
                indent=2,
            )

            temp_file.write(
                "\n"
            )

            temp_file.flush()

        # ----------------------------------------------------
        # Atomic replace
        # ----------------------------------------------------

        temp_path.replace(
            path
        )

    except Exception:

        if (
            temp_path is not None
            and temp_path.exists()
        ):

            try:

                temp_path.unlink()

            except Exception:

                pass

        raise


# ============================================================
# 既有 Universe 資訊
# ============================================================

def log_existing_universe():

    if not UNIVERSE_FILE.exists():

        log(
            "ℹ️ 尚無既有 "
            "universe.json"
        )

        return

    try:

        size = (
            UNIVERSE_FILE.stat()
            .st_size
        )

        log(
            "✓ 保留既有 "
            "universe.json"
        )

        log(
            "  Existing file size："
            f"{size} bytes"
        )

        # ----------------------------------------------------
        # 如果可以讀，顯示目前 total
        # ----------------------------------------------------

        try:

            with open(
                UNIVERSE_FILE,
                "r",
                encoding="utf-8",
            ) as file:

                old_data = json.load(
                    file
                )

            old_total = old_data.get(
                "total"
            )

            if old_total is not None:

                log(
                    "  Existing total："
                    f"{old_total}"
                )

        except Exception:

            pass

    except Exception:

        log(
            "✓ 保留既有 "
            "universe.json"
        )


# ============================================================
# 主流程
# ============================================================

def main():

    section(
        "台股 AI 選股系統 "
        f"build_universe.py "
        f"{VERSION}"
    )

    log(
        f"BASE_DIR："
        f"{BASE_DIR}"
    )

    log(
        f"DATA_DIR："
        f"{DATA_DIR}"
    )

    log(
        f"OUTPUT："
        f"{UNIVERSE_FILE}"
    )

    log("")

    log(
        "安全門檻："
    )

    log(
        f"  TWSE >= "
        f"{MIN_TWSE_STOCKS}"
    )

    log(
        f"  TPEx >= "
        f"{MIN_TPEX_STOCKS}"
    )

    log(
        f"  Total >= "
        f"{MIN_TOTAL_STOCKS}"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        # ====================================================
        # 1. TWSE
        # ====================================================

        twse_records = (
            fetch_twse()
        )

        time.sleep(
            REQUEST_DELAY
        )

        # ====================================================
        # 2. TPEx
        # ====================================================

        tpex_records = (
            fetch_tpex()
        )

        # ====================================================
        # 3. 合併
        # ====================================================

        combined = (
            merge_universe(
                twse_records,
                tpex_records,
            )
        )

        # ====================================================
        # 4. Universe 最終驗證
        # ====================================================

        validate_universe(
            twse_records,
            tpex_records,
            combined,
        )

        # ====================================================
        # 5. 建立輸出
        # ====================================================

        data = build_output(
            twse_records,
            tpex_records,
            combined,
        )

        # ====================================================
        # 6. 輸出前再次驗證
        # ====================================================

        validate_output(
            data
        )

        # ====================================================
        # 7. Atomic Write
        # ====================================================

        section(
            "寫入 Data/universe.json"
        )

        atomic_write_json(
            data,
            UNIVERSE_FILE,
        )

        log(
            "✓ Atomic write completed"
        )

        log(
            "✓ Output："
            f"{UNIVERSE_FILE}"
        )

        log(
            "✓ Version："
            f"{data['version']}"
        )

        log(
            "✓ Generated at："
            f"{data['generated_at']}"
        )

        log(
            "✓ TWSE："
            f"{data['listed_stocks']}"
        )

        log(
            "✓ TPEx："
            f"{data['otc_stocks']}"
        )

        log(
            "✓ Total："
            f"{data['total']}"
        )

        section(
            "BUILD UNIVERSE SUCCESS"
        )

        return 0

    except KeyboardInterrupt:

        log("")

        log(
            "⚠️ 使用者中止執行"
        )

        return 130

    except Exception as exc:

        # ----------------------------------------------------
        # 安全機制
        #
        # 任何錯誤：
        # 不刪除
        # 不清空
        # 不覆蓋
        # 原有 universe.json
        # ----------------------------------------------------

        section(
            "BUILD UNIVERSE FAILED"
        )

        log(
            f"ERROR："
            f"{exc}"
        )

        log_existing_universe()

        return 1


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
