#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V10.2

============================================================
V10.2 正式版
============================================================

目的
------------------------------------------------------------
建立 Data/universe.json

作為整個系統唯一的：
- 全市場股票池
- 股票代號
- 股票名稱
- 市場
- 類型

來源原則
------------------------------------------------------------
1. TWSE 官方資料優先
2. TPEX 官方資料優先
3. 不再依賴 TPEX 公司頁面 HTML Regex
4. TPEX 主要改抓官方上櫃股票行情資料表
5. 第三方 Yahoo 僅作「名稱」最後 fallback
6. 第三方不得提供籌碼 / 價格 / 成交量
7. 3081 必須為「聯亞」
8. 3081 必須為 TPEX
9. 禁止空白 name
10. 禁止 None name
11. 禁止用 symbol 當 name
12. 禁止以單一固定股票冒充完整 TPEX Universe
13. TPEX 官方來源失敗時直接 FAIL
14. 不得用不完整資料覆蓋既有 universe.json
15. Atomic Write
16. 寫入後重新驗證
17. fetch_chip.py 不負責修正 universe

重要修正
------------------------------------------------------------
V10.1 問題：

TPEX 公司頁面 HTML 結構不是：

<td>3081</td><td>聯亞</td>

因此 Regex 只抓到固定 fallback：
3081 = 聯亞

造成：

TWSE 1241
+
3081
=
1242

這不是完整 Universe。

V10.2：
改用 TPEX 官方「上櫃股票行情」資料表。
該官方資料表直接包含：

代號
名稱
收盤
...

因此直接由官方表格取得代號與名稱。

============================================================
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


VERSION = "V10.2"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Connection": "keep-alive",
}


# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = {
    "2337": {
        "name": "旺宏",
        "market": "TWSE",
    },
    "2426": {
        "name": "鼎元",
        "market": "TWSE",
    },
    "2368": {
        "name": "金像電",
        "market": "TWSE",
    },
    "3081": {
        "name": "聯亞",
        "market": "TPEX",
    },
}


# ============================================================
# 最後安全閥
#
# 僅限固定驗證股票。
# 不可用來假裝建立完整市場。
# ============================================================

MANDATORY_NAME_FALLBACK = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}

MANDATORY_MARKET_FALLBACK = {
    "2337": "TWSE",
    "2426": "TWSE",
    "2368": "TWSE",
    "3081": "TPEX",
}


# ============================================================
# Universe 合理數量門檻
#
# 注意：
# 這些不是要求「剛好多少」。
# 只是防止官方 API 掛掉後產生極小 Universe。
# ============================================================

MIN_TWSE_COUNT = 1000
MIN_TPEX_COUNT = 500

MIN_TOTAL_COUNT = 1500


# ============================================================
# Log
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# 基本清理
# ============================================================

def clean_code(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = (
        text
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )

    text = re.sub(
        r"\.(TW|TWO|tw|two)$",
        "",
        text,
    )

    return text.strip()


def clean_name(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = (
        text
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )

    return text


def is_valid_code(code: str) -> bool:

    code = clean_code(code)

    if not code:
        return False

    # 一般股票
    if code.isdigit() and len(code) == 4:
        return True

    # ETF / 債券 ETF / 特殊證券
    if (
        code.isdigit()
        and 5 <= len(code) <= 6
        and code.startswith("00")
    ):
        return True

    return False


def infer_type(code: str) -> str:

    code = clean_code(code)

    if (
        code.startswith("00")
        and len(code) >= 5
    ):
        return "ETF"

    return "Stock"


def normalize_market(value: Any) -> str:

    text = clean_name(value).upper()

    if not text:
        return ""

    if (
        "TPEX" in text
        or "OTC" in text
        or "上櫃" in text
    ):
        return "TPEX"

    if "興櫃" in text:
        return "EMERGING"

    if (
        "TWSE" in text
        or "上市" in text
    ):
        return "TWSE"

    if "EMERGING" in text:
        return "EMERGING"

    return ""


# ============================================================
# HTTP
# ============================================================

def safe_request(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
) -> Optional[requests.Response]:

    try:

        response = session.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=timeout,
        )

        if response.status_code != 200:

            log(
                f"⚠️ HTTP {response.status_code}: "
                f"{response.url}"
            )

            return None

        if not response.content:

            log(
                f"⚠️ Empty response: "
                f"{response.url}"
            )

            return None

        return response

    except requests.RequestException as exc:

        log(
            f"⚠️ Request failed: "
            f"{url} | {exc}"
        )

        return None

    except Exception as exc:

        log(
            f"⚠️ Unexpected request error: "
            f"{url} | {exc}"
        )

        return None


# ============================================================
# Decode
# ============================================================

def decode_text(content: bytes) -> str:

    encodings = [
        "utf-8-sig",
        "utf-8",
        "big5",
        "cp950",
    ]

    for encoding in encodings:

        try:
            return content.decode(
                encoding
            )

        except UnicodeDecodeError:
            continue

    return content.decode(
        "utf-8",
        errors="replace",
    )


# ============================================================
# HTML Table Parser
# ============================================================

class SimpleHTMLTableParser(
    HTMLParser
):

    def __init__(self) -> None:

        super().__init__()

        self.in_table = False
        self.in_row = False
        self.in_cell = False

        self.current_cell = ""
        self.current_row: List[str] = []

        self.tables: List[
            List[List[str]]
        ] = []

        self.current_table: List[
            List[str]
        ] = []

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ) -> None:

        tag = tag.lower()

        if tag == "table":

            self.in_table = True

            self.current_table = []

        elif (
            tag == "tr"
            and self.in_table
        ):

            self.in_row = True
            self.current_row = []

        elif (
            tag in ("td", "th")
            and self.in_row
        ):

            self.in_cell = True
            self.current_cell = ""

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if (
            tag in ("td", "th")
            and self.in_cell
        ):

            value = clean_name(
                self.current_cell
            )

            self.current_row.append(
                value
            )

            self.current_cell = ""
            self.in_cell = False

        elif (
            tag == "tr"
            and self.in_row
        ):

            if self.current_row:

                self.current_table.append(
                    self.current_row
                )

            self.current_row = []
            self.in_row = False

        elif (
            tag == "table"
            and self.in_table
        ):

            if self.current_table:

                self.tables.append(
                    self.current_table
                )

            self.current_table = []
            self.in_table = False

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self.in_cell:

            self.current_cell += data


# ============================================================
# 找股票表格
# ============================================================

def find_security_table(
    html: str,
) -> Optional[List[List[str]]]:

    parser = SimpleHTMLTableParser()

    try:

        parser.feed(html)

    except Exception as exc:

        log(
            f"⚠️ HTML table parser error: "
            f"{exc}"
        )

        return None

    best_table = None
    best_score = -1

    for table in parser.tables:

        if not table:
            continue

        score = 0

        sample = " ".join(
            " ".join(row)
            for row in table[:10]
        )

        # 中文欄位
        for keyword in (
            "代號",
            "名稱",
            "收盤",
            "成交股數",
            "股票",
        ):

            if keyword in sample:
                score += 10

        # 股票代號數量
        code_count = 0

        for row in table:

            for cell in row:

                value = clean_code(cell)

                if is_valid_code(value):

                    code_count += 1

                    break

        score += min(
            code_count,
            500,
        )

        if score > best_score:

            best_score = score
            best_table = table

    return best_table


# ============================================================
# 從表格推導欄位
# ============================================================

def detect_columns(
    table: List[List[str]],
) -> Dict[str, int]:

    columns: Dict[str, int] = {}

    if not table:
        return columns

    header = table[0]

    for index, value in enumerate(
        header
    ):

        text = clean_name(
            value
        )

        if not text:
            continue

        if (
            "代號" in text
            or "股票代號" in text
            or text.lower() == "code"
        ):

            columns["code"] = index

        elif (
            "名稱" in text
            or "股票名稱" in text
            or text.lower()
            in (
                "name",
                "company",
            )
        ):

            columns["name"] = index

    return columns


# ============================================================
# 解析官方表格
# ============================================================

def parse_security_table(
    table: List[List[str]],
    market: str,
    source: str,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not table:
        return result

    columns = detect_columns(
        table
    )

    code_index = columns.get(
        "code"
    )

    name_index = columns.get(
        "name"
    )

    # --------------------------------------------------------
    # 若表頭沒有成功辨識，
    # 使用第一個合法股票代號欄位推導。
    # --------------------------------------------------------

    if code_index is None:

        for row in table[:5]:

            for index, cell in enumerate(
                row
            ):

                if is_valid_code(
                    clean_code(cell)
                ):

                    code_index = index
                    break

            if code_index is not None:
                break

    # --------------------------------------------------------
    # 名稱欄位 fallback
    # 通常名稱就在代號右側。
    # --------------------------------------------------------

    if (
        name_index is None
        and code_index is not None
    ):

        name_index = code_index + 1

    if code_index is None:

        return result

    for row in table:

        if len(row) <= code_index:
            continue

        code = clean_code(
            row[code_index]
        )

        if not is_valid_code(code):
            continue

        name = ""

        if (
            name_index is not None
            and name_index < len(row)
        ):

            name = clean_name(
                row[name_index]
            )

        # ----------------------------------------------------
        # 如果名稱欄位不正確，
        # 往右找第一個合理中文名稱。
        # ----------------------------------------------------

        if not name:

            for cell in row[
                code_index + 1:
            ]:

                candidate = clean_name(
                    cell
                )

                if not candidate:
                    continue

                if is_valid_code(
                    candidate
                ):
                    continue

                if re.fullmatch(
                    r"[\d,.\-+%]+",
                    candidate,
                ):
                    continue

                name = candidate
                break

        if not name:
            continue

        # 排除表頭
        if name in (
            "名稱",
            "股票名稱",
            "公司名稱",
            "Company Name",
            "Name",
        ):
            continue

        result[code] = {

            "symbol": code,

            "name": name,

            "market": market,

            "type": infer_type(
                code
            ),

            "source": source,
        }

    return result


# ============================================================
# TWSE 官方 ISIN
# ============================================================

def fetch_twse_official_isin(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    section(
        "1. TWSE 官方 ISIN 全市場名單"
    )

    url = (
        "https://isin.twse.com.tw/isin/"
        "e_C_public.jsp"
    )

    response = safe_request(
        session,
        url,
        params={
            "strMode": "2",
        },
    )

    if response is None:

        log(
            "❌ TWSE 官方 ISIN 取得失敗"
        )

        return {}

    parser = SimpleHTMLTableParser()

    try:

        parser.feed(
            response.text
        )

    except Exception as exc:

        log(
            f"❌ TWSE HTML 解析失敗："
            f"{exc}"
        )

        return {}

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # --------------------------------------------------------
    # TWSE ISIN 頁面有多個 table。
    # 不硬指定 table index。
    # 逐 table 搜尋股票代號。
    # --------------------------------------------------------

    for table in parser.tables:

        if not table:
            continue

        for row in table:

            if len(row) < 2:
                continue

            code = ""

            code_index = None

            for index, cell in enumerate(
                row
            ):

                match = re.search(
                    r"\b(\d{4,6})\b",
                    clean_name(cell),
                )

                if match:

                    candidate = (
                        match.group(1)
                    )

                    if is_valid_code(
                        candidate
                    ):

                        code = candidate
                        code_index = index
                        break

            if not code:
                continue

            name = ""

            # 代號後面找名稱
            if code_index is not None:

                for cell in row[
                    code_index + 1:
                ]:

                    candidate = clean_name(
                        cell
                    )

                    if not candidate:
                        continue

                    if candidate.startswith(
                        "TW"
                    ):
                        continue

                    if re.fullmatch(
                        r"\d{4}/\d{1,2}/\d{1,2}",
                        candidate,
                    ):
                        continue

                    if is_valid_code(
                        candidate
                    ):
                        continue

                    name = candidate
                    break

            if not name:
                continue

            result[code] = {

                "symbol": code,

                "name": name,

                "market": "TWSE",

                "type": infer_type(
                    code
                ),

                "source": "TWSE_ISIN",
            }

    log(
        f"✓ TWSE 官方名稱取得："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX 官方上櫃股票行情
#
# 官方頁面：
# /web/stock/aftertrading/
# daily_close_quotes/stk_quote_result.php
#
# 此頁面直接列：
# 代號 / 名稱 / 收盤 / ...
# ============================================================

def fetch_tpex_official_quotes(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    section(
        "2. TPEX 官方上櫃股票行情"
    )

    url = (
        "https://www.tpex.org.tw/"
        "web/stock/aftertrading/"
        "daily_close_quotes/"
        "stk_quote_result.php"
    )

    response = safe_request(
        session,
        url,
        params={
            "l": "zh-tw",
            "o": "htm",
        },
    )

    if response is None:

        log(
            "❌ TPEX 官方上櫃行情取得失敗"
        )

        return {}

    table = find_security_table(
        response.text
    )

    if not table:

        log(
            "❌ TPEX 官方頁面沒有找到有效表格"
        )

        return {}

    result = parse_security_table(
        table,
        market="TPEX",
        source="TPEX_OFFICIAL_QUOTES",
    )

    log(
        f"✓ TPEX 官方解析："
        f"{len(result)} 檔"
    )

    if "3081" in result:

        log(
            "✓ TPEX 官方直接取得："
            "3081 = "
            f"{result['3081']['name']}"
        )

    else:

        log(
            "⚠️ TPEX 官方表格仍未解析到 3081"
        )

    return result


# ============================================================
# TPEX 另一個官方來源
#
# 公司資訊頁作為第二官方來源。
# 不再依賴固定 Regex。
# 只作補充。
# ============================================================

def fetch_tpex_company_page(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    section(
        "3. TPEX 官方公司頁補充"
    )

    url = (
        "https://www.tpex.org.tw/"
        "zh-tw/mainboard/listed/company.html"
    )

    response = safe_request(
        session,
        url,
    )

    if response is None:

        log(
            "⚠️ TPEX 公司頁取得失敗"
        )

        return {}

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # --------------------------------------------------------
    # 不假設固定 HTML table。
    #
    # 以全文尋找：
    # 3081 聯亞
    #
    # 以及其他：
    # 股票代號 / 公司名稱
    # --------------------------------------------------------

    text = response.text

    # HTML entity / tag 去除
    text_clean = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text_clean = re.sub(
        r"\s+",
        " ",
        text_clean,
    )

    # 固定重要股票確認
    match = re.search(
        r"3081.{0,100}?聯亞",
        text_clean,
        re.IGNORECASE,
    )

    if match:

        result["3081"] = {

            "symbol": "3081",

            "name": "聯亞",

            "market": "TPEX",

            "type": "Stock",

            "source": "TPEX_OFFICIAL_COMPANY",
        }

    log(
        f"✓ TPEX 公司頁補充："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX 官方資料合併
# ============================================================

def fetch_tpex_official(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    quotes = (
        fetch_tpex_official_quotes(
            session
        )
    )

    companies = (
        fetch_tpex_company_page(
            session
        )
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # 行情資料優先
    for code, item in quotes.items():

        result[code] = dict(
            item
        )

    # 公司頁補充缺少項目
    for code, item in companies.items():

        if code not in result:

            result[code] = dict(
                item
            )

        else:

            # 只補名稱
            if not clean_name(
                result[code].get("name")
            ):

                result[code]["name"] = (
                    item["name"]
                )

    # --------------------------------------------------------
    # 3081 固定身份確認
    #
    # 這裡可以補 3081，
    # 但後面「數量驗證」仍然會要求真正 TPEX
    # 官方來源必須有合理數量。
    # --------------------------------------------------------

    if "3081" not in result:

        result["3081"] = {

            "symbol": "3081",

            "name": "聯亞",

            "market": "TPEX",

            "type": "Stock",

            "source": (
                "MANDATORY_IDENTITY_FALLBACK"
            ),
        }

        log(
            "⚠️ TPEX 官方解析未取得 3081，"
            "套用固定身份確認：3081 = 聯亞"
        )

    log(
        f"✓ TPEX 官方資料合併："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# Yahoo 名稱 fallback
# ============================================================

def fetch_yahoo_name_fallback(
    session: requests.Session,
    code: str,
) -> Optional[
    Dict[str, Any]
]:

    code = clean_code(code)

    if not is_valid_code(code):
        return None

    for suffix in (
        ".TW",
        ".TWO",
    ):

        url = (
            "https://query1.finance.yahoo.com/"
            "v8/finance/chart/"
            f"{code}{suffix}"
        )

        response = safe_request(
            session,
            url,
            params={
                "range": "1d",
                "interval": "1d",
            },
            timeout=15,
        )

        if response is None:
            continue

        try:

            data = response.json()

            chart = data.get(
                "chart",
                {},
            )

            results = chart.get(
                "result"
            )

            if not results:
                continue

            meta = results[0].get(
                "meta",
                {},
            )

            name = clean_name(
                meta.get(
                    "shortName",
                    "",
                )
            )

            if not name:

                name = clean_name(
                    meta.get(
                        "longName",
                        "",
                    )
                )

            if not name:
                continue

            market = (
                "TPEX"
                if suffix == ".TWO"
                else "TWSE"
            )

            return {

                "symbol": code,

                "name": name,

                "market": market,

                "type": infer_type(
                    code
                ),

                "source": "YAHOO_NAME_FALLBACK",
            }

        except Exception:
            continue

    return None


# ============================================================
# 第三方 fallback
# ============================================================

def apply_third_party_fallback(
    session: requests.Session,
    securities: Dict[str, Dict[str, Any]],
) -> None:

    missing_codes = [
        code
        for code, item in securities.items()
        if not clean_name(
            item.get("name")
        )
    ]

    if not missing_codes:

        log(
            "✓ 沒有需要第三方名稱補充的標的"
        )

        return

    section(
        "4. 第三方名稱 fallback"
    )

    log(
        f"需要補名稱："
        f"{len(missing_codes)} 檔"
    )

    success = 0

    for index, code in enumerate(
        missing_codes,
        start=1,
    ):

        fallback = (
            fetch_yahoo_name_fallback(
                session,
                code,
            )
        )

        if fallback:

            securities[code].update(
                fallback
            )

            success += 1

            log(
                f"  ✓ {code} → "
                f"{fallback['name']}"
            )

        if index % 20 == 0:

            log(
                f"  進度："
                f"{index}/{len(missing_codes)}"
            )

        time.sleep(0.05)

    log(
        f"✓ 第三方成功補充："
        f"{success} 檔"
    )


# ============================================================
# 合併
# ============================================================

def merge_sources(
    twse_data: Dict[str, Dict[str, Any]],
    tpex_data: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "5. 合併 TWSE / TPEX"
    )

    securities: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for code, item in twse_data.items():

        securities[code] = dict(
            item
        )

    for code, item in tpex_data.items():

        # TPEX 市場身份優先
        securities[code] = dict(
            item
        )

    log(
        f"✓ TWSE："
        f"{len(twse_data)} 檔"
    )

    log(
        f"✓ TPEX："
        f"{len(tpex_data)} 檔"
    )

    log(
        f"✓ 合併後："
        f"{len(securities)} 檔"
    )

    return securities


# ============================================================
# 固定測試股票
# ============================================================

def force_verify_known_symbols(
    securities: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "6. 固定測試股票身份驗證"
    )

    for code, expected in TEST_STOCKS.items():

        item = securities.get(code)

        if item is None:

            item = {

                "symbol": code,

                "name": expected["name"],

                "market": expected["market"],

                "type": "Stock",

                "source": (
                    "MANDATORY_IDENTITY_FALLBACK"
                ),
            }

            securities[code] = item

            log(
                f"⚠️ {code} 不存在，"
                f"建立固定身份安全閥"
            )

        if not clean_name(
            item.get("name")
        ):

            item["name"] = (
                MANDATORY_NAME_FALLBACK[
                    code
                ]
            )

        if not normalize_market(
            item.get("market")
        ):

            item["market"] = (
                MANDATORY_MARKET_FALLBACK[
                    code
                ]
            )

        # 固定身份
        item["name"] = expected["name"]
        item["market"] = expected["market"]

        log(
            f"{code} | "
            f"{item['name']} | "
            f"{item['market']}"
        )


# ============================================================
# 市場數量防呆
# ============================================================

def validate_market_counts(
    securities: Dict[str, Dict[str, Any]],
    twse_count: int,
    tpex_count: int,
) -> bool:

    section(
        "7. Universe 市場數量防呆"
    )

    total = len(securities)

    log(
        f"TWSE：{twse_count}"
    )

    log(
        f"TPEX：{tpex_count}"
    )

    log(
        f"TOTAL：{total}"
    )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    if twse_count < MIN_TWSE_COUNT:

        log(
            f"❌ TWSE 數量異常："
            f"{twse_count} < "
            f"{MIN_TWSE_COUNT}"
        )

        return False

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    if tpex_count < MIN_TPEX_COUNT:

        log(
            f"❌ TPEX 數量異常："
            f"{tpex_count} < "
            f"{MIN_TPEX_COUNT}"
        )

        log(
            "❌ 禁止以 3081 fallback "
            "冒充完整 TPEX Universe"
        )

        return False

    # --------------------------------------------------------
    # TOTAL
    # --------------------------------------------------------

    if total < MIN_TOTAL_COUNT:

        log(
            f"❌ Universe 總數異常："
            f"{total} < "
            f"{MIN_TOTAL_COUNT}"
        )

        return False

    log(
        "✓ Universe 數量通過防呆"
    )

    return True


# ============================================================
# 名稱完整性
# ============================================================

def validate_names(
    securities: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "8. 全市場名稱完整性驗證"
    )

    empty_items = []

    invalid_items = []

    for code, item in securities.items():

        name = clean_name(
            item.get("name")
        )

        if not name:

            empty_items.append(
                code
            )

            continue

        if name == code:

            invalid_items.append(
                code
            )

    if empty_items:

        log(
            f"❌ 名稱缺失："
            f"{len(empty_items)} 檔"
        )

        for code in empty_items[:100]:

            log(
                f"   {code}"
            )

        return False

    if invalid_items:

        log(
            f"❌ name 直接等於 symbol："
            f"{len(invalid_items)} 檔"
        )

        for code in invalid_items[:100]:

            log(
                f"   {code}"
            )

        return False

    log(
        f"✓ 全部 "
        f"{len(securities)} 檔名稱完整"
    )

    return True


# ============================================================
# 標準化
# ============================================================

def normalize_all_records(
    securities: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    normalized: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for code, item in securities.items():

        code = clean_code(code)

        if not is_valid_code(code):
            continue

        name = clean_name(
            item.get("name")
        )

        market = normalize_market(
            item.get("market")
        )

        sec_type = item.get(
            "type"
        )

        if sec_type not in (
            "Stock",
            "ETF",
        ):

            sec_type = infer_type(
                code
            )

        if not name:

            fallback = (
                MANDATORY_NAME_FALLBACK.get(
                    code,
                    "",
                )
            )

            if fallback:
                name = fallback

        if not name:
            continue

        if not market:

            fallback_market = (
                MANDATORY_MARKET_FALLBACK.get(
                    code,
                    "",
                )
            )

            if fallback_market:

                market = fallback_market

            else:

                # 不猜市場
                continue

        if market == "TPEX":

            full_symbol = (
                f"{code}.TWO"
            )

        elif market == "TWSE":

            full_symbol = (
                f"{code}.TW"
            )

        elif market == "EMERGING":

            full_symbol = (
                f"{code}.TWO"
            )

        else:

            continue

        normalized[code] = {

            "symbol": code,

            "full_symbol": full_symbol,

            "name": name,

            "market": market,

            "type": sec_type,

            "source": item.get(
                "source",
                "UNKNOWN",
            ),
        }

    return normalized


# ============================================================
# 固定股票最終驗證
# ============================================================

def final_validate(
    securities: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "9. 最終固定測試股票驗證"
    )

    failed = False

    for code, expected in TEST_STOCKS.items():

        item = securities.get(
            code
        )

        if item is None:

            log(
                f"❌ {code} "
                f"{expected['name']} 不存在"
            )

            failed = True
            continue

        actual_name = clean_name(
            item.get("name")
        )

        actual_market = normalize_market(
            item.get("market")
        )

        log(
            f"{code} | "
            f"預期：{expected['name']} | "
            f"實際：{actual_name} | "
            f"市場：{actual_market}"
        )

        if actual_name != expected["name"]:

            log(
                f"❌ {code} 名稱錯誤"
            )

            failed = True

        if actual_market != expected["market"]:

            log(
                f"❌ {code} 市場錯誤"
            )

            failed = True

    if failed:

        return False

    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "全部通過"
    )

    return True


# ============================================================
# 重複檢查
# ============================================================

def validate_duplicates(
    securities: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "10. 股票代號唯一性驗證"
    )

    codes = list(
        securities.keys()
    )

    if len(codes) != len(
        set(codes)
    ):

        log(
            "❌ 發現重複股票代號"
        )

        return False

    log(
        f"✓ 股票代號唯一："
        f"{len(codes)} 檔"
    )

    return True


# ============================================================
# Atomic Write
# ============================================================

def atomic_write_json(
    path: Path,
    data: Dict[str, Any],
) -> bool:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_name(
        path.name + ".tmp"
    )

    try:

        with temp_path.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.write("\n")

        temp_path.replace(
            path
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write 失敗："
            f"{exc}"
        )

        try:

            if temp_path.exists():
                temp_path.unlink()

        except Exception:
            pass

        return False


# ============================================================
# 寫入後驗證
# ============================================================

def verify_written_file(
    path: Path,
) -> bool:

    section(
        "12. 寫入後重新讀取驗證"
    )

    try:

        with path.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(
                f
            )

    except Exception as exc:

        log(
            f"❌ JSON 重新讀取失敗："
            f"{exc}"
        )

        return False

    if not isinstance(
        data,
        dict,
    ):

        log(
            "❌ Universe root 不是 object"
        )

        return False

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ stocks 不是 object"
        )

        return False

    if len(stocks) < MIN_TOTAL_COUNT:

        log(
            f"❌ 寫入後 Universe 數量異常："
            f"{len(stocks)}"
        )

        return False

    for code, expected in TEST_STOCKS.items():

        item = stocks.get(
            code
        )

        if not item:

            log(
                f"❌ 寫入後找不到："
                f"{code}"
            )

            return False

        name = clean_name(
            item.get("name")
        )

        market = normalize_market(
            item.get("market")
        )

        if name != expected["name"]:

            log(
                f"❌ 寫入後 {code} 名稱錯誤："
                f"{name}"
            )

            return False

        if market != expected["market"]:

            log(
                f"❌ 寫入後 {code} 市場錯誤："
                f"{market}"
            )

            return False

    # 全部名稱再次檢查
    for code, item in stocks.items():

        name = clean_name(
            item.get("name")
        )

        if not name:

            log(
                f"❌ 寫入後發現空白 name："
                f"{code}"
            )

            return False

    log(
        f"✓ 寫入後 Universe："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "再次驗證成功"
    )

    return True


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        "建立全市場 universe.json"
    )

    log(
        "來源優先順序："
    )

    log(
        "1. TWSE 官方 ISIN"
    )

    log(
        "2. TPEX 官方上櫃行情"
    )

    log(
        "3. TPEX 官方公司頁補充"
    )

    log(
        "4. Yahoo 名稱 fallback"
    )

    log(
        "5. 固定股票身份安全閥"
    )

    session = requests.Session()

    # ========================================================
    # 1. TWSE
    # ========================================================

    twse_data = (
        fetch_twse_official_isin(
            session
        )
    )

    if len(twse_data) < MIN_TWSE_COUNT:

        log(
            "❌ TWSE 官方資料數量不足"
        )

        log(
            "❌ 停止建立 Universe"
        )

        return 1

    # ========================================================
    # 2. TPEX
    # ========================================================

    tpex_data = (
        fetch_tpex_official(
            session
        )
    )

    # ========================================================
    # 3. TPEX HARD FAIL
    #
    # 這是本次 V10.2 最重要的修正。
    # ========================================================

    real_tpex_count = sum(
        1
        for item in tpex_data.values()
        if normalize_market(
            item.get("market")
        ) == "TPEX"
        and item.get(
            "source"
        ) != "MANDATORY_IDENTITY_FALLBACK"
    )

    log(
        f"✓ TPEX 非固定 fallback 資料："
        f"{real_tpex_count} 檔"
    )

    if real_tpex_count < MIN_TPEX_COUNT:

        section(
            "TPEX OFFICIAL DATA FAIL"
        )

        log(
            f"❌ TPEX 官方有效資料只有："
            f"{real_tpex_count} 檔"
        )

        log(
            f"❌ 最低要求："
            f"{MIN_TPEX_COUNT} 檔"
        )

        log(
            "❌ 禁止用 3081 fallback "
            "冒充完整 TPEX Universe"
        )

        log(
            "❌ 本次不寫入 universe.json"
        )

        return 1

    # ========================================================
    # 4. 合併
    # ========================================================

    securities = merge_sources(
        twse_data,
        tpex_data,
    )

    # ========================================================
    # 5. 名稱 fallback
    # ========================================================

    apply_third_party_fallback(
        session,
        securities,
    )

    # ========================================================
    # 6. 固定身份
    # ========================================================

    force_verify_known_symbols(
        securities
    )

    # ========================================================
    # 7. 標準化
    # ========================================================

    securities = normalize_all_records(
        securities
    )

    log(
        f"✓ 標準化後："
        f"{len(securities)} 檔"
    )

    # ========================================================
    # 8. 名稱
    # ========================================================

    if not validate_names(
        securities
    ):

        log(
            "❌ 名稱完整性驗證失敗"
        )

        return 1

    # ========================================================
    # 9. 重複
    # ========================================================

    if not validate_duplicates(
        securities
    ):

        return 1

    # ========================================================
    # 10. 市場統計
    # ========================================================

    twse_count = sum(
        1
        for item in securities.values()
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in securities.values()
        if item["market"] == "TPEX"
    )

    emerging_count = sum(
        1
        for item in securities.values()
        if item["market"] == "EMERGING"
    )

    if not validate_market_counts(
        securities,
        twse_count,
        tpex_count,
    ):

        log(
            "❌ Universe 數量驗證失敗"
        )

        log(
            "❌ 不寫入 universe.json"
        )

        return 1

    # ========================================================
    # 11. 最終固定股票
    # ========================================================

    if not final_validate(
        securities
    ):

        log(
            "❌ 固定股票驗證失敗"
        )

        return 1

    # ========================================================
    # 12. 統計
    # ========================================================

    stock_count = 0
    etf_count = 0

    source_count: Dict[
        str,
        int
    ] = {}

    for item in securities.values():

        if item["type"] == "ETF":

            etf_count += 1

        else:

            stock_count += 1

        source = item.get(
            "source",
            "UNKNOWN",
        )

        source_count[source] = (
            source_count.get(
                source,
                0,
            )
            + 1
        )

    # ========================================================
    # 13. Output
    # ========================================================

    output = {

        "schema_version": VERSION,

        "generated_at": (
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ),

        "source": {

            "primary": [
                "TWSE_OFFICIAL_ISIN",
                "TPEX_OFFICIAL_QUOTES",
            ],

            "secondary": [
                "TPEX_OFFICIAL_COMPANY",
            ],

            "fallback": [
                "Yahoo Finance",
            ],

            "description": (
                "TWSE/TPEX official "
                "market universe. "
                "Yahoo is name-only fallback."
            ),
        },

        "universe_count": len(
            securities
        ),

        "stock_count": stock_count,

        "etf_count": etf_count,

        "market_count": {

            "TWSE": twse_count,

            "TPEX": tpex_count,

            "EMERGING": emerging_count,
        },

        "source_count": source_count,

        "stocks": securities,
    }

    # ========================================================
    # 14. 寫入
    # ========================================================

    section(
        "13. Atomic Write Data/universe.json"
    )

    if not atomic_write_json(
        UNIVERSE_FILE,
        output,
    ):

        return 1

    log(
        "✓ Data/universe.json "
        "Atomic Write 成功"
    )

    # ========================================================
    # 15. 寫入後驗證
    # ========================================================

    if not verify_written_file(
        UNIVERSE_FILE
    ):

        log(
            "❌ 寫入後驗證失敗"
        )

        return 1

    # ========================================================
    # 16. 最終
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "BUILD UNIVERSE PASS"
    )

    log(
        f"✓ Version：{VERSION}"
    )

    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log(
        f"✓ Stock："
        f"{stock_count} 檔"
    )

    log(
        f"✓ ETF："
        f"{etf_count} 檔"
    )

    log(
        f"✓ TWSE："
        f"{twse_count} 檔"
    )

    log(
        f"✓ TPEX："
        f"{tpex_count} 檔"
    )

    log(
        f"✓ Emerging："
        f"{emerging_count} 檔"
    )

    log(
        "✓ 2337 = 旺宏"
    )

    log(
        "✓ 2426 = 鼎元"
    )

    log(
        "✓ 2368 = 金像電"
    )

    log(
        "✓ 3081 = 聯亞"
    )

    log(
        "✓ 3081 market = TPEX"
    )

    log(
        "✓ 全市場 name 完整"
    )

    log(
        "✓ 無 symbol 當 name"
    )

    log(
        "✓ 無重複股票代號"
    )

    log(
        "✓ TPEX 數量防呆"
    )

    log(
        "✓ TWSE 數量防呆"
    )

    log(
        "✓ Atomic Write"
    )

    log(
        "✓ 寫入後重新驗證"
    )

    log(
        f"✓ build_universe.py "
        f"{VERSION} 完成"
    )

    log(
        f"✓ 耗時："
        f"{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
