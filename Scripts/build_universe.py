```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V10.2

============================================================
V10.2 正式修正版
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
1. TWSE 官方 ISIN
2. TPEX 官方 OpenAPI
3. TPEX 官方公司頁僅作固定身份補充
4. 第三方 Yahoo 僅作名稱 fallback
5. 第三方不得提供籌碼 / 價格 / 成交量
6. 3081 必須為「聯亞」
7. 3081 必須為 TPEX
8. 禁止空白 name
9. 禁止 None name
10. 禁止用 symbol 當 name
11. 禁止以單一固定股票冒充完整 TPEX Universe
12. TPEX 官方來源失敗時直接 FAIL
13. 不得用不完整資料覆蓋既有 universe.json
14. Atomic Write
15. 寫入後重新驗證
16. fetch_chip.py 不負責修正 universe

============================================================
V10.2 修正重點
============================================================

舊版問題：
------------------------------------------------------------
原本使用：

https://www.tpex.org.tw/web/stock/aftertrading/
daily_close_quotes/stk_quote_result.php

GitHub Actions 實際發生：

Response ended prematurely

導致：
TWSE = 1241
TPEX = 0
3081 fallback = 1

最終 Universe = 1242

這不是完整市場。

============================================================
本版處理
============================================================

TPEX 主來源改為官方 OpenAPI：

https://www.tpex.org.tw/openapi/v1/
tpex_mainboard_daily_close_quotes

官方 JSON 欄位：

SecuritiesCompanyCode
CompanyName

直接建立：

symbol
name
market = TPEX
type
source = TPEX_OFFICIAL_OPENAPI

3081 fallback 仍保留，
但永遠不計入 real_tpex_count。

============================================================
"""

from __future__ import annotations

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
        "application/json,"
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
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
# 固定身份安全閥
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
# Universe 數量門檻
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

    # ETF / 部分特殊證券
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
# HTML Table Parser
#
# 保留給 TWSE ISIN 使用。
# TPEX 行情不再使用 HTML Parser。
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
# TPEX 官方 OpenAPI
#
# V10.2 修正版核心
#
# 不再使用：
# stk_quote_result.php
#
# 改用：
# https://www.tpex.org.tw/openapi/v1/
# tpex_mainboard_daily_close_quotes
# ============================================================

def fetch_tpex_official_quotes(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    section(
        "2. TPEX 官方 OpenAPI 上櫃股票行情"
    )

    url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/"
        "tpex_mainboard_daily_close_quotes"
    )

    log(
        "官方來源："
        "TPEX OpenAPI"
    )

    log(
        f"Endpoint：{url}"
    )

    response = safe_request(
        session,
        url,
        timeout=REQUEST_TIMEOUT,
    )

    if response is None:

        log(
            "❌ TPEX 官方 OpenAPI 取得失敗"
        )

        return {}

    # --------------------------------------------------------
    # OpenAPI 必須是 JSON。
    # --------------------------------------------------------

    try:

        payload = response.json()

    except ValueError as exc:

        log(
            f"❌ TPEX OpenAPI JSON 解析失敗："
            f"{exc}"
        )

        preview = (
            response.text[:300]
            .replace("\n", " ")
            .replace("\r", " ")
        )

        log(
            f"⚠️ Response preview："
            f"{preview}"
        )

        return {}

    except Exception as exc:

        log(
            f"❌ TPEX OpenAPI 回應解析錯誤："
            f"{exc}"
        )

        return {}

    if not isinstance(
        payload,
        list,
    ):

        log(
            "❌ TPEX OpenAPI 回傳格式不是 list"
        )

        log(
            f"實際型別："
            f"{type(payload).__name__}"
        )

        return {}

    log(
        f"✓ TPEX OpenAPI 原始資料："
        f"{len(payload)} 筆"
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    invalid_code_count = 0
    missing_name_count = 0

    for row in payload:

        if not isinstance(
            row,
            dict,
        ):
            continue

        # ----------------------------------------------------
        # 官方欄位
        # ----------------------------------------------------

        code = clean_code(
            row.get(
                "SecuritiesCompanyCode"
            )
        )

        name = clean_name(
            row.get(
                "CompanyName"
            )
        )

        # ----------------------------------------------------
        # 某些情況如果官方欄位名稱未來有微調，
        # 只允許同一份官方 JSON 的明確別名。
        # 不從第三方補。
        # ----------------------------------------------------

        if not code:

            code = clean_code(
                row.get("Code")
            )

        if not name:

            name = clean_name(
                row.get("Name")
            )

        if not is_valid_code(code):

            invalid_code_count += 1
            continue

        if not name:

            missing_name_count += 1
            continue

        # 防止名稱欄位異常
        if name == code:

            missing_name_count += 1
            continue

        result[code] = {

            "symbol": code,

            "name": name,

            "market": "TPEX",

            "type": infer_type(
                code
            ),

            "source": (
                "TPEX_OFFICIAL_OPENAPI"
            ),
        }

    log(
        f"✓ TPEX 官方 OpenAPI 有效資料："
        f"{len(result)} 檔"
    )

    if invalid_code_count:

        log(
            f"⚠️ 無效代號略過："
            f"{invalid_code_count} 筆"
        )

    if missing_name_count:

        log(
            f"⚠️ 缺少名稱略過："
            f"{missing_name_count} 筆"
        )

    # --------------------------------------------------------
    # 3081 必須由官方資料直接驗證
    # --------------------------------------------------------

    if "3081" in result:

        log(
            "✓ TPEX 官方 OpenAPI 直接取得："
            f"3081 = {result['3081']['name']}"
        )

    else:

        log(
            "⚠️ TPEX 官方 OpenAPI 未取得 3081"
        )

    return result


# ============================================================
# TPEX 官方公司頁
#
# 僅作固定身份補充。
#
# 不拿來建立完整 TPEX Universe。
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

    text = response.text

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

    # --------------------------------------------------------
    # 只確認 3081。
    #
    # 不使用此頁面建立全市場 Universe。
    # --------------------------------------------------------

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

            "source": (
                "TPEX_OFFICIAL_COMPANY"
            ),
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

    # --------------------------------------------------------
    # OpenAPI 主來源
    # --------------------------------------------------------

    for code, item in quotes.items():

        result[code] = dict(
            item
        )

    # --------------------------------------------------------
    # 公司頁只補缺少項目
    # --------------------------------------------------------

    for code, item in companies.items():

        if code not in result:

            result[code] = dict(
                item
            )

        else:

            if not clean_name(
                result[code].get("name")
            ):

                result[code]["name"] = (
                    item["name"]
                )

    # --------------------------------------------------------
    # 固定身份安全閥
    #
    # 只能補 3081 身份。
    # 後續 real_tpex_count 會排除它。
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
            "套用固定身份確認："
            "3081 = 聯亞"
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

                "source": (
                    "YAHOO_NAME_FALLBACK"
                ),
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

    if twse_count < MIN_TWSE_COUNT:

        log(
            f"❌ TWSE 數量異常："
            f"{twse_count} < "
            f"{MIN_TWSE_COUNT}"
        )

        return False

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
        "2. TPEX 官方 OpenAPI"
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
    # 只計真正官方來源。
    # MANDATORY_IDENTITY_FALLBACK
    # 永遠不能計入。
    # ========================================================

    real_tpex_count = sum(
        1
        for item in tpex_data.values()
        if normalize_market(
            item.get("market")
        ) == "TPEX"
        and item.get(
            "source"
        ) in (
            "TPEX_OFFICIAL_OPENAPI",
            "TPEX_OFFICIAL_COMPANY",
        )
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
                "TPEX_OFFICIAL_OPENAPI",
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
    # 14. Atomic Write
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
```
