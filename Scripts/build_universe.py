#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.2.3

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
5. 主 API 回應異常時使用備援 API
6. 所有資料通過驗證後才覆蓋 universe.json
7. 任一階段失敗都保留舊 universe.json
8. 不允許產生空 Universe
9. 不允許 total < MIN_TOTAL_STOCKS
10. TWSE / TPEx 均有最低股票數安全門檻
11. Yahoo ticker：
        TWSE -> .TW
        TPEx -> .TWO
12. 使用原子寫入，避免半成品 JSON
13. 支援 UTF-8 / Big5 / CP950
14. 支援 JSON / CSV 多種欄位名稱
15. main() 明確執行
16. 任何未預期錯誤都以 exit code 1 結束
17. 不會因為程式檔案只有函式定義而靜默成功
18. TPEx 驗證使用 MIN_TPEX_STOCKS
19. TWSE / TPEx 股票代號不可重複
20. 輸出寫入前後均進行資料驗證

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

VERSION = "V5.2.3"

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
# TPEx API
# ============================================================

TPEX_API = (
    "https://www.tpex.org.tw/"
    "openapi/v1/mopsfin_t187ap03_O"
)


# ============================================================
# TWSE 備援 API
# ============================================================

TWSE_FALLBACK_API = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/"
    "SecuritiesListing?response=json"
)


# ============================================================
# TPEx 備援 API
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
# HTTP GET - 備援
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

def get_value(record, keys):

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
# 找 JSON 裡所有候選 List
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

    candidates = find_lists(payload)

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
# Response Decode
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
# CSV Header Normalization
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
            [
                "證券代號",
                "股票代號",
                "代號",
                "Code",
                "SecuritiesCompanyCode",
                "有價證券代號",
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

            code_index = headers.index(
                code_header
            )

            if name_header is not None:

                name_index = headers.index(
                    name_header
                )

            break

    # --------------------------------------------------------
    # 沒找到 Header 時，嘗試固定欄位
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

            invalid.append(code)

            continue

        if item.get("code") != code:

            invalid.append(code)

            continue

        normalized_code = normalize_code(
            item.get("code")
        )

        if normalized_code != code:

            invalid.append(code)

            continue

        if not normalize_name(
            item.get("name")
        ):

            invalid.append(code)

            continue

        if item.get("market") != market:

            invalid.append(code)

            continue

        expected_symbol = make_symbol(
            code,
            market,
        )

        if item.get("symbol") != expected_symbol:

            invalid.append(code)

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

    # ========================================================
    # 主 API
    # ========================================================

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

        if len(records) >= MIN_TWSE_STOCKS:

            validate_records(
                records,
                "TWSE",
                MIN_TWSE_STOCKS,
            )

            return records

        log(
            "⚠️ TWSE 主 API 數量不足，"
            "進入備援"
        )

    except Exception as exc:

        log(
            f"⚠️ TWSE 主 API 失敗："
            f"{exc}"
        )

    # ========================================================
    # 備援 API
    # ========================================================

    log(
        f"TWSE 備援 API："
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

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        response_text = response.text

        stripped_text = (
            response_text.lstrip()
        )

        if (
            "json" in content_type
            or stripped_text.startswith("{")
            or stripped_text.startswith("[")
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
                    "⚠️ TWSE fallback JSON "
                    f"解析失敗：{exc}"
                )

        # ----------------------------------------------------
        # CSV
        # ----------------------------------------------------

        if len(records) < MIN_TWSE_STOCKS:

            records = parse_csv_records(
                decode_response(response),
                "TWSE",
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
# TPEx 取得
# ============================================================

def fetch_tpex():

    section(
        "取得 TPEx 上櫃股票"
    )

    log(
        f"主 API：{TPEX_API}"
    )

    # ========================================================
    # 主 API
    # ========================================================

    try:

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

        if len(records) >= MIN_TPEX_STOCKS:

            # ------------------------------------------------
            # 重要：
            # TPEx 必須使用 MIN_TPEX_STOCKS
            # 不可以使用 MIN_TWSE_STOCKS
            # ------------------------------------------------

            validate_records(
                records,
                "TPEx",
                MIN_TPEX_STOCKS,
            )

            return records

        log(
            "⚠️ TPEx 主 API 數量不足，"
            "進入備援"
        )

    except Exception as exc:

        log(
            f"⚠️ TPEx 主 API 失敗："
            f"{exc}"
        )

    # ========================================================
    # 備援 API
    # ========================================================

    log(
        f"TPEx 備援 API："
        f"{TPEX_FALLBACK_API}"
    )

    try:

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
            "Fallback Content-Type："
            f"{content_type}"
        )

        records = {}

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        response_text = response.text

        stripped_text = (
            response_text.lstrip()
        )

        if (
            "json" in content_type
            or stripped_text.startswith("{")
            or stripped_text.startswith("[")
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
                    "⚠️ TPEx fallback JSON "
                    f"解析失敗：{exc}"
                )

        # ----------------------------------------------------
        # CSV
        # ----------------------------------------------------

        if len(records) < MIN_TPEX_STOCKS:

            records = parse_csv_records(
                decode_response(response),
                "TPEx",
            )

        log(
            "TPEx fallback 解析："
            f"{len(records)} 筆"
        )

        # ----------------------------------------------------
        # 重要：
        # TPEx 必須使用 MIN_TPEX_STOCKS
        # ----------------------------------------------------

        validate_records(
            records,
            "TPEx",
            MIN_TPEX_STOCKS,
        )

        return records

    except Exception as exc:

        raise RuntimeError(
            "TPEx 主 API 與備援 API "
            f"均失敗：{exc}"
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

    # ========================================================
    # TWSE
    # ========================================================

    for code, item in twse_records.items():

        if code in combined:

            raise RuntimeError(
                f"股票代號重複：{code}"
            )

        combined[code] = item

    # ========================================================
    # TPEx
    # ========================================================

    for code, item in tpex_records.items():

        if code in combined:

            raise RuntimeError(
                "TWSE / TPEx 股票代號重複："
                f"{code}"
            )

        combined[code] = item

    log(
        f"TWSE：{len(twse_records)}"
    )

    log(
        f"TPEx：{len(tpex_records)}"
    )

    log(
        f"Total：{len(combined)}"
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

    # ========================================================
    # 數量安全門
    # ========================================================

    if listed_count < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE 最終數量不足："
            f"{listed_count} < "
            f"{MIN_TWSE_STOCKS}"
        )

    if otc_count < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "TPEx 最終數量不足："
            f"{otc_count} < "
            f"{MIN_TPEX_STOCKS}"
        )

    if total < MIN_TOTAL_STOCKS:

        raise RuntimeError(
            "全市場股票數量不足："
            f"{total} < "
            f"{MIN_TOTAL_STOCKS}"
        )

    # ========================================================
    # 內部一致性
    # ========================================================

    expected_total = (
        listed_count + otc_count
    )

    if total != expected_total:

        raise RuntimeError(
            "total 與上市/上櫃數量不一致"
        )

    if total != len(combined):

        raise RuntimeError(
            "total 與 combined 數量不一致"
        )

    # ========================================================
    # 每筆資料再次驗證
    # ========================================================

    for code, item in combined.items():

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"Universe item 格式錯誤："
                f"{code}"
            )

        if item.get("code") != code:

            raise RuntimeError(
                f"code 不一致：{code}"
            )

        if not normalize_code(
            item.get("code")
        ):

            raise RuntimeError(
                f"股票代號無效：{code}"
            )

        if not normalize_name(
            item.get("name")
        ):

            raise RuntimeError(
                f"股票名稱為空：{code}"
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
                f"{code} -> {market}"
            )

        expected_symbol = make_symbol(
            code,
            market,
        )

        if item.get("symbol") != expected_symbol:

            raise RuntimeError(
                f"symbol 錯誤："
                f"{code}"
            )

    log(
        f"✓ total = {total}"
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

    # ========================================================
    # 穩定排序
    # ========================================================

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

def validate_output(data):

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

    # ========================================================
    # 基本格式
    # ========================================================

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

    if total != len(items):

        raise RuntimeError(
            "total 與 items 數量不一致"
        )

    # ========================================================
    # 安全門檻
    # ========================================================

    if listed < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "listed_stocks 安全門檻失敗："
            f"{listed} < "
            f"{MIN_TWSE_STOCKS}"
        )

    if otc < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "otc_stocks 安全門檻失敗："
            f"{otc} < "
            f"{MIN_TPEX_STOCKS}"
        )

    if total < MIN_TOTAL_STOCKS:

        raise RuntimeError(
            "total 安全門檻失敗："
            f"{total} < "
            f"{MIN_TOTAL_STOCKS}"
        )

    if total != listed + otc:

        raise RuntimeError(
            "total != "
            "listed_stocks + otc_stocks"
        )

    # ========================================================
    # 每筆輸出資料驗證
    # ========================================================

    seen_codes = set()

    market_counts = {
        "TWSE": 0,
        "TPEx": 0,
    }

    for item in items:

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                "Universe item 不是 object"
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
                f"{code} 股票名稱為空"
            )

        if market not in (
            "TWSE",
            "TPEx",
        ):

            raise RuntimeError(
                f"{code} market 無效："
                f"{market}"
            )

        expected_symbol = make_symbol(
            code,
            market,
        )

        if symbol != expected_symbol:

            raise RuntimeError(
                f"{code} symbol 錯誤："
                f"{symbol}"
            )

        if code in seen_codes:

            raise RuntimeError(
                f"股票代號重複："
                f"{code}"
            )

        seen_codes.add(code)

        market_counts[market] += 1

    # ========================================================
    # 市場數量與 metadata 再次交叉驗證
    # ========================================================

    if market_counts["TWSE"] != listed:

        raise RuntimeError(
            "TWSE items 數量與 "
            "listed_stocks 不一致"
        )

    if market_counts["TPEx"] != otc:

        raise RuntimeError(
            "TPEx items 數量與 "
            "otc_stocks 不一致"
        )

    log(
        "✓ Output validation passed"
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

        # ----------------------------------------------------
        # 同目錄建立 temporary file
        # ----------------------------------------------------

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

            temp_file.write("\n")

            temp_file.flush()

        # ----------------------------------------------------
        # 原子 replace
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
# 寫入後重新讀取驗證
# ============================================================

def verify_written_file(path):

    path = Path(path)

    if not path.exists():

        raise RuntimeError(
            "Atomic Write 後找不到 "
            "universe.json"
        )

    if path.stat().st_size <= 0:

        raise RuntimeError(
            "Atomic Write 後 "
            "universe.json 為空"
        )

    try:

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            written_data = json.load(
                file
            )

    except Exception as exc:

        raise RuntimeError(
            "寫入後 JSON 重新讀取失敗："
            f"{exc}"
        ) from exc

    validate_output(
        written_data
    )

    return written_data


# ============================================================
# 主流程
# ============================================================

def main():

    section(
        "台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        f"BASE_DIR：{BASE_DIR}"
    )

    log(
        f"DATA_DIR：{DATA_DIR}"
    )

    log(
        f"OUTPUT：{UNIVERSE_FILE}"
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

    log("")

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
        # 2. Request Delay
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
        # 7. 輸出前驗證
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

        # ====================================================
        # 9. 寫入後重新讀取驗證
        # ====================================================

        section(
            "寫入後重新驗證"
        )

        written_data = (
            verify_written_file(
                UNIVERSE_FILE
            )
        )

        log(
            "✓ universe.json "
            "重新讀取成功"
        )

        # ====================================================
        # 10. 最終結果
        # ====================================================

        log("")

        log(
            f"✓ Version："
            f"{written_data['version']}"
        )

        log(
            f"✓ Generated at："
            f"{written_data['generated_at']}"
        )

        log(
            f"✓ TWSE："
            f"{written_data['listed_stocks']}"
        )

        log(
            f"✓ TPEx："
            f"{written_data['otc_stocks']}"
        )

        log(
            f"✓ Total："
            f"{written_data['total']}"
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

        # ====================================================
        # 最重要的安全機制
        #
        # 任一階段失敗：
        #
        # 1. 不刪除舊 universe.json
        # 2. 不清空舊 universe.json
        # 3. 不寫入不完整 Universe
        # 4. 回傳 exit code 1
        # ====================================================

        section(
            "BUILD UNIVERSE FAILED"
        )

        log(
            f"ERROR：{exc}"
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
                    f"  Existing file size："
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
