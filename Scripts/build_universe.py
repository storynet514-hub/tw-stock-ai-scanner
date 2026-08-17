#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.2.4

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
4. HTTP 200 不代表 API 正常
5. HTML / 安全頁 / 錯誤頁不得視為成功
6. Redirect 自動處理
7. 主 API 失敗時使用備援 API
8. TWSE 增加官方 ISIN 公開頁備援
9. TPEx 增加多種解析方式
10. 所有資料通過驗證後才覆蓋 universe.json
11. 任一階段失敗都保留舊 universe.json
12. 不允許產生空 Universe
13. 不允許 total < MIN_TOTAL_STOCKS
14. TWSE / TPEx 均有最低股票數安全門檻
15. Yahoo ticker：
        TWSE -> .TW
        TPEx -> .TWO
16. 使用原子寫入，避免半成品 JSON
17. 支援 UTF-8 / Big5 / CP950
18. 支援 JSON / CSV / HTML 多種資料格式
19. main() 明確執行
20. 任何未預期錯誤都以 exit code 1 結束
21. 不會因為程式檔案只有函式定義而靜默成功

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
from html.parser import HTMLParser
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V5.2.4"

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

MIN_RESPONSE_BYTES = 1000

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
# API
# ============================================================

TWSE_API = (
    "https://openapi.twse.com.tw/"
    "v1/opendata/t187ap03_L"
)

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
# TWSE 官方 ISIN 公開清單
#
# 這個頁面不是即時交易資料，
# 但可作為 Universe 股票主檔的備援來源。
# ============================================================

TWSE_ISIN_API = (
    "https://isin.twse.com.tw/"
    "isin/e_C_public.jsp?strMode=2"
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
            "text/html,"
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

    text = text.replace(" ", "")
    text = text.replace('"', "")
    text = text.replace("'", "")

    # 某些來源可能使用全形空白
    text = text.replace("　", "")

    if not text.isdigit():
        return None

    if len(text) < 4 or len(text) > 6:
        return None

    return text


# ============================================================
# 股票名稱正規化
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

def looks_like_html(content):

    if not content:
        return False

    sample = content[:2000]

    try:
        text = sample.decode(
            "utf-8",
            errors="ignore",
        ).lower()
    except Exception:
        return False

    html_markers = [
        "<html",
        "<!doctype",
        "<head",
        "<body",
        "<script",
        "for security reasons",
        "安全性考量",
        "cannot be accessed",
        "can not be accessed",
    ]

    return any(
        marker in text
        for marker in html_markers
    )


# ============================================================
# HTTP GET
#
# 重點：
# allow_redirects=True
#
# 避免 TWSE 307 redirect 被誤判為最終失敗。
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

            status = response.status_code

            content = (
                response.content or b""
            )

            size = len(content)

            final_url = (
                response.url
            )

            log(
                f"  HTTP Status: "
                f"{status}"
            )

            log(
                f"  Content-Length: "
                f"{size} bytes"
            )

            if final_url != url:

                log(
                    f"  Redirected URL: "
                    f"{final_url}"
                )

            if status != 200:

                raise RuntimeError(
                    f"HTTP {status}"
                )

            if size < min_bytes:

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
                    "API 回應過短："
                    f"{size} bytes；"
                    f"內容：{preview}"
                )

            if looks_like_html(content):

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
                    "API 回傳 HTML / "
                    "安全頁，而非預期資料；"
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
# HTTP GET
# 備援專用
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

            status = response.status_code

            content = (
                response.content or b""
            )

            size = len(content)

            final_url = (
                response.url
            )

            log(
                f"  HTTP Status: "
                f"{status}"
            )

            log(
                f"  Content-Length: "
                f"{size} bytes"
            )

            if final_url != url:

                log(
                    f"  Redirected URL: "
                    f"{final_url}"
                )

            if status != 200:

                raise RuntimeError(
                    f"HTTP {status}"
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
# 遞迴尋找 JSON List
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

    code_keys = [
        "Code",
        "code",
        "股票代號",
        "證券代號",
        "有價證券代號",
        "代號",
        "SecuritiesCompanyCode",
        "公司代號",
        "公司碼",
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
# Response Decode
# ============================================================

def decode_response(
    response,
):

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
# CSV Header Normalization
# ============================================================

def normalize_header(
    value,
):

    text = clean_text(
        value
    )

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
                "代號",
                "Code",
                "SecuritiesCompanyCode",
                "有價證券代號",
                "公司代號",
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
                "有價證券名稱",
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
    # 沒有 Header 時
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
# HTML Parser
#
# 用於 TWSE ISIN 官方公開頁。
# ============================================================

class TableParser(
    HTMLParser
):

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.in_td = False
        self.current_cell = []

        self.current_row = []

        self.rows = []

    def handle_starttag(
        self,
        tag,
        attrs,
    ):

        tag = tag.lower()

        if tag == "tr":

            self.current_row = []

        elif tag in (
            "td",
            "th",
        ):

            self.in_td = True
            self.current_cell = []

    def handle_endtag(
        self,
        tag,
    ):

        tag = tag.lower()

        if tag in (
            "td",
            "th",
        ):

            value = clean_text(
                "".join(
                    self.current_cell
                )
            )

            self.current_row.append(
                value
            )

            self.current_cell = []
            self.in_td = False

        elif tag == "tr":

            if self.current_row:

                self.rows.append(
                    self.current_row
                )

            self.current_row = []

    def handle_data(
        self,
        data,
    ):

        if self.in_td:

            self.current_cell.append(
                data
            )


# ============================================================
# 從文字中擷取股票代號
# ============================================================

def extract_code_from_text(
    text,
):

    if not text:
        return None

    text = clean_text(
        text
    )

    # 常見：
    # 1101　台泥
    # 1101 台泥
    # 1101 TCC
    match = re.match(
        r"^\s*(\d{4,6})\s*",
        text,
    )

    if match:

        return normalize_code(
            match.group(1)
        )

    return None


# ============================================================
# TWSE ISIN HTML 解析
# ============================================================

def parse_twse_isin_html(
    text,
):

    if not text:
        return {}

    parser = TableParser()

    try:

        parser.feed(
            text
        )

    except Exception as exc:

        raise RuntimeError(
            f"TWSE ISIN HTML "
            f"解析失敗：{exc}"
        ) from exc

    output = {}

    for row in parser.rows:

        if not row:
            continue

        row_text = " ".join(
            row
        )

        # 必須明確是 TWSE LISTED
        if (
            "TWSE LISTED"
            not in row_text.upper()
        ):
            continue

        code = None
        name = None

        # ----------------------------------------------------
        # 優先從第一欄尋找
        # ----------------------------------------------------

        for cell in row:

            code = extract_code_from_text(
                cell
            )

            if code:

                # 第一欄通常：
                # Security Code & Security Name
                remainder = re.sub(
                    r"^\s*\d{4,6}\s*",
                    "",
                    clean_text(cell),
                )

                name = clean_text(
                    remainder
                )

                break

        if not code:
            continue

        # ----------------------------------------------------
        # 如果第一欄沒有名稱，
        # 嘗試第二欄
        # ----------------------------------------------------

        if not name:

            for cell in row[1:]:

                candidate = clean_text(
                    cell
                )

                if candidate:

                    name = candidate
                    break

        if not name:
            continue

        output[code] = {
            "code": code,
            "name": name,
            "market": "TWSE",
            "symbol": make_symbol(
                code,
                "TWSE",
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

    count = len(
        records
    )

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

        if item.get("code") != code:

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

        if item.get("market") != market:

            invalid.append(
                code
            )

            continue

        expected_symbol = make_symbol(
            code,
            market,
        )

        if (
            item.get("symbol")
            != expected_symbol
        ):

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
            f"{len(invalid)} 筆資料驗證失敗；"
            f"範例：{preview}"
        )

    log(
        f"✓ {market} 驗證通過："
        f"{count} 檔"
    )

    return True


# ============================================================
# TWSE 主 API
# ============================================================

def fetch_twse_primary():

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

    if len(records) < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE 主 API 資料數量不足："
            f"{len(records)}"
        )

    validate_records(
        records,
        "TWSE",
        MIN_TWSE_STOCKS,
    )

    return records


# ============================================================
# TWSE 備援 API
# ============================================================

def fetch_twse_fallback_api():

    log(
        f"TWSE 備援 API："
        f"{TWSE_FALLBACK_API}"
    )

    response = http_get_fallback(
        TWSE_FALLBACK_API,
        "TWSE fallback",
    )

    records = {}

    content_type = (
        response.headers.get(
            "Content-Type",
            "",
        )
        .lower()
    )

    log(
        f"Fallback Content-Type："
        f"{content_type}"
    )

    text = decode_response(
        response
    )

    stripped = text.lstrip()

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    if (
        "json" in content_type
        or stripped.startswith("{")
        or stripped.startswith("[")
    ):

        try:

            payload = parse_json_response(
                response
            )

            records = parse_json_records(
                payload,
                "TWSE",
            )

        except Exception as exc:

            log(
                f"⚠️ TWSE fallback JSON "
                f"解析失敗：{exc}"
            )

    # --------------------------------------------------------
    # CSV
    # --------------------------------------------------------

    if len(records) < MIN_TWSE_STOCKS:

        try:

            csv_records = parse_csv_records(
                text,
                "TWSE",
            )

            if len(csv_records) > len(
                records
            ):

                records = csv_records

        except Exception as exc:

            log(
                f"⚠️ TWSE fallback CSV "
                f"解析失敗：{exc}"
            )

    log(
        "TWSE fallback API 解析："
        f"{len(records)} 筆"
    )

    if len(records) < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE fallback API "
            "股票數量不足："
            f"{len(records)}"
        )

    validate_records(
        records,
        "TWSE",
        MIN_TWSE_STOCKS,
    )

    return records


# ============================================================
# TWSE 官方 ISIN 備援
# ============================================================

def fetch_twse_isin():

    section(
        "TWSE 官方 ISIN 備援"
    )

    log(
        f"URL：{TWSE_ISIN_API}"
    )

    response = http_get_fallback(
        TWSE_ISIN_API,
        "TWSE ISIN",
    )

    text = decode_response(
        response
    )

    records = parse_twse_isin_html(
        text
    )

    log(
        "TWSE ISIN 解析："
        f"{len(records)} 筆"
    )

    if len(records) < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE ISIN 備援股票數量不足："
            f"{len(records)}"
        )

    validate_records(
        records,
        "TWSE",
        MIN_TWSE_STOCKS,
    )

    return records


# ============================================================
# TWSE 完整取得流程
# ============================================================

def fetch_twse():

    section(
        "取得 TWSE 上市股票"
    )

    log(
        f"主 API：{TWSE_API}"
    )

    # --------------------------------------------------------
    # 第一層：主 API
    # --------------------------------------------------------

    try:

        records = fetch_twse_primary()

        log(
            "✓ TWSE 主 API 成功"
        )

        return records

    except Exception as exc:

        log(
            f"⚠️ TWSE 主 API 失敗："
            f"{exc}"
        )

    # --------------------------------------------------------
    # 第二層：TWSE SecuritiesListing
    # --------------------------------------------------------

    try:

        records = fetch_twse_fallback_api()

        log(
            "✓ TWSE 第一備援成功"
        )

        return records

    except Exception as exc:

        log(
            f"⚠️ TWSE 第一備援失敗："
            f"{exc}"
        )

    # --------------------------------------------------------
    # 第三層：TWSE ISIN
    # --------------------------------------------------------

    try:

        records = fetch_twse_isin()

        log(
            "✓ TWSE 第二備援成功"
        )

        return records

    except Exception as exc:

        raise RuntimeError(
            "TWSE 主 API、"
            "第一備援、"
            "第二備援均失敗："
            f"{exc}"
        ) from exc


# ============================================================
# TPEx 主 API
# ============================================================

def fetch_tpex_primary():

    response = http_get(
        TPEX_API,
        "TPEx API",
    )

    payload = parse_json_response(
        response
    )

    records = parse_json_records(
        payload,
        "TPEx",
    )

    log(
        "TPEx JSON 原始解析："
        f"{len(records)} 筆"
    )

    if len(records) < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "TPEx 主 API 資料數量不足："
            f"{len(records)}"
        )

    validate_records(
        records,
        "TPEx",
        MIN_TPEX_STOCKS,
    )

    return records


# ============================================================
# TPEx 備援 API
# ============================================================

def fetch_tpex_fallback():

    log(
        f"TPEx 備援 API："
        f"{TPEX_FALLBACK_API}"
    )

    response = http_get_fallback(
        TPEX_FALLBACK_API,
        "TPEx fallback",
    )

    content_type = (
        response.headers.get(
            "Content-Type",
            "",
        )
        .lower()
    )

    log(
        f"Fallback Content-Type："
        f"{content_type}"
    )

    text = decode_response(
        response
    )

    records = {}

    stripped = text.lstrip()

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    if (
        "json" in content_type
        or stripped.startswith("{")
        or stripped.startswith("[")
    ):

        try:

            payload = parse_json_response(
                response
            )

            records = parse_json_records(
                payload,
                "TPEx",
            )

        except Exception as exc:

            log(
                f"⚠️ TPEx fallback JSON "
                f"解析失敗：{exc}"
            )

    # --------------------------------------------------------
    # CSV
    # --------------------------------------------------------

    if len(records) < MIN_TPEX_STOCKS:

        try:

            csv_records = parse_csv_records(
                text,
                "TPEx",
            )

            if len(csv_records) > len(
                records
            ):

                records = csv_records

        except Exception as exc:

            log(
                f"⚠️ TPEx fallback CSV "
                f"解析失敗：{exc}"
            )

    log(
        "TPEx fallback 解析："
        f"{len(records)} 筆"
    )

    if len(records) < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "TPEx fallback "
            "股票數量不足："
            f"{len(records)}"
        )

    validate_records(
        records,
        "TPEx",
        MIN_TPEX_STOCKS,
    )

    return records


# ============================================================
# TPEx 完整取得流程
# ============================================================

def fetch_tpex():

    section(
        "取得 TPEx 上櫃股票"
    )

    log(
        f"主 API：{TPEX_API}"
    )

    # --------------------------------------------------------
    # 主 API
    # --------------------------------------------------------

    try:

        records = fetch_tpex_primary()

        log(
            "✓ TPEx 主 API 成功"
        )

        return records

    except Exception as exc:

        log(
            f"⚠️ TPEx 主 API 失敗："
            f"{exc}"
        )

    # --------------------------------------------------------
    # 備援
    # --------------------------------------------------------

    try:

        records = fetch_tpex_fallback()

        log(
            "✓ TPEx 備援成功"
        )

        return records

    except Exception as exc:

        raise RuntimeError(
            "TPEx 主 API 與備援 API "
            "均失敗："
            f"{exc}"
        ) from exc


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
    # TWSE 安全門
    # --------------------------------------------------------

    if listed_count < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE 最終數量不足："
            f"{listed_count} < "
            f"{MIN_TWSE_STOCKS}"
        )

    # --------------------------------------------------------
    # TPEx 安全門
    # --------------------------------------------------------

    if otc_count < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "TPEx 最終數量不足："
            f"{otc_count} < "
            f"{MIN_TPEX_STOCKS}"
        )

    # --------------------------------------------------------
    # Total 安全門
    # --------------------------------------------------------

    if total < MIN_TOTAL_STOCKS:

        raise RuntimeError(
            "全市場股票數量不足："
            f"{total} < "
            f"{MIN_TOTAL_STOCKS}"
        )

    # --------------------------------------------------------
    # 數量一致性
    # --------------------------------------------------------

    if total != (
        listed_count
        + otc_count
    ):

        raise RuntimeError(
            "total 與 "
            "上市/上櫃數量不一致"
        )

    if total != len(
        combined
    ):

        raise RuntimeError(
            "total 與 combined "
            "數量不一致"
        )

    # --------------------------------------------------------
    # 每筆資料再次驗證
    # --------------------------------------------------------

    for code, item in (
        combined.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"Universe item "
                f"格式錯誤：{code}"
            )

        if item.get(
            "code"
        ) != code:

            raise RuntimeError(
                f"code 不一致："
                f"{code}"
            )

        if not normalize_code(
            item.get("code")
        ):

            raise RuntimeError(
                f"股票代號無效："
                f"{code}"
            )

        if not normalize_name(
            item.get("name")
        ):

            raise RuntimeError(
                f"股票名稱為空："
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
                f"market 錯誤："
                f"{code} -> "
                f"{market}"
            )

        expected_symbol = make_symbol(
            code,
            market,
        )

        if item.get(
            "symbol"
        ) != expected_symbol:

            raise RuntimeError(
                f"symbol 錯誤："
                f"{code}"
            )

    log(
        f"✓ total = "
        f"{total}"
    )

    log(
        f"✓ listed_stocks = "
        f"{listed_count}"
    )

    log(
        f"✓ otc_stocks = "
        f"{otc_count}"
    )

    log(
        f"✓ items = "
        f"{len(combined)}"
    )

    log(
        "✓ Universe validation passed"
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
        "generated_at": generated_at,
        "total": len(items),
        "listed_stocks": len(
            twse_records
        ),
        "otc_stocks": len(
            tpex_records
        ),
        "items": items,
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
            "輸出資料不是 "
            "JSON object"
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

    if total <= 0:

        raise RuntimeError(
            "禁止產生 total = 0"
        )

    if not items:

        raise RuntimeError(
            "禁止產生空 items"
        )

    if total != len(
        items
    ):

        raise RuntimeError(
            "total 與 items "
            "數量不一致"
        )

    if listed < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "listed_stocks "
            "安全門檻失敗"
        )

    if otc < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "otc_stocks "
            "安全門檻失敗"
        )

    if total < MIN_TOTAL_STOCKS:

        raise RuntimeError(
            "total "
            "安全門檻失敗"
        )

    if total != (
        listed + otc
    ):

        raise RuntimeError(
            "total != "
            "listed_stocks + "
            "otc_stocks"
        )

    # --------------------------------------------------------
    # 每筆輸出資料驗證
    # --------------------------------------------------------

    seen_codes = set()

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

        expected_symbol = make_symbol(
            code,
            market,
        )

        if symbol != expected_symbol:

            raise RuntimeError(
                f"{code} "
                "symbol 錯誤"
            )

        if code in seen_codes:

            raise RuntimeError(
                f"股票代號重複："
                f"{code}"
            )

        seen_codes.add(
            code
        )

    return True


# ============================================================
# Atomic Write
# ============================================================

def atomic_write_json(
    data,
    path,
):

    path = Path(
        path
    )

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
        # 原子替換
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

        twse_records = fetch_twse()

        # ====================================================
        # 2. REQUEST DELAY
        # ====================================================

        time.sleep(
            REQUEST_DELAY
        )

        # ====================================================
        # 3. TPEx
        # ====================================================

        tpex_records = fetch_tpex()

        # ====================================================
        # 4. 合併
        # ====================================================

        combined = merge_universe(
            twse_records,
            tpex_records,
        )

        # ====================================================
        # 5. Universe 最終驗證
        # ====================================================

        validate_universe(
            twse_records,
            tpex_records,
            combined,
        )

        # ====================================================
        # 6. 建立輸出
        # ====================================================

        data = build_output(
            twse_records,
            tpex_records,
            combined,
        )

        # ====================================================
        # 7. 輸出前再次驗證
        # ====================================================

        validate_output(
            data
        )

        # ====================================================
        # 8. Atomic Write
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
            f"✓ Output："
            f"{UNIVERSE_FILE}"
        )

        log(
            f"✓ Version："
            f"{data['version']}"
        )

        log(
            f"✓ Generated at："
            f"{data['generated_at']}"
        )

        log(
            f"✓ TWSE："
            f"{data['listed_stocks']}"
        )

        log(
            f"✓ TPEx："
            f"{data['otc_stocks']}"
        )

        log(
            f"✓ Total："
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
        # 最重要安全機制
        #
        # 任何錯誤：
        #
        # 不刪除
        # 不清空
        # 不覆蓋
        #
        # 原有 universe.json
        # ----------------------------------------------------

        section(
            "BUILD UNIVERSE FAILED"
        )

        log(
            f"ERROR："
            f"{exc}"
        )

        if UNIVERSE_FILE.exists():

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
                    f"  Existing "
                    f"file size："
                    f"{size} bytes"
                )

            except Exception:

                log(
                    "✓ 保留既有 "
                    "universe.json"
                )

        else:

            log(
                "ℹ️ 尚無既有 "
                "universe.json"
            )

        return 1


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
