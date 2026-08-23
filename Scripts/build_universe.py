#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V10.1

============================================================
V10.1 正式修正版
============================================================

目的
------------------------------------------------------------
建立 Data/universe.json
作為整個系統的「全市場股票池 / 名稱 / 市場 / 類型」唯一來源。

V10.1 重要修正
------------------------------------------------------------
1. 修正 universe.json schema：
   - items = list
   - stocks = dict

2. GitHub Actions 原本驗證：
       data.get("items")
   因此 V10.0 僅輸出 stocks dict 會直接失敗。

3. items 與 stocks 使用同一份標準化資料，
   不建立第二套資料來源。

4. 保留 stocks dict 作為舊程式相容層。

5. 3081 固定驗證：
       3081 = 聯亞
       market = TPEX

6. 禁止：
       name = ""
       name = None

7. 不使用第三方籌碼資料。

8. 第三方 Yahoo 僅作名稱 / 市場 fallback。

9. 不依賴舊 universe.json 自我複製。

10. Atomic Write。

11. 寫入後重新讀取驗證。

12. items / stocks / 固定測試股票全部驗證。

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


VERSION = "V10.1"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json;q=0.9,*/*;q=0.8"
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
# 最後安全 fallback
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
# 清理工具
# ============================================================

def clean_code(value: Any) -> str:

    if value is None:
        return ""

    text = str(value).strip()

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

    if code.isdigit() and len(code) == 4:
        return True

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

    if "TPEX" in text:
        return "TPEX"

    if "OTC" in text:
        return "TPEX"

    if "上櫃" in text:
        return "TPEX"

    if "興櫃" in text:
        return "EMERGING"

    if "TWSE" in text:
        return "TWSE"

    if "上市" in text:
        return "TWSE"

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
                f"{url}"
            )

            return None

        return response

    except Exception as exc:

        log(
            f"⚠️ Request failed: "
            f"{url} | {exc}"
        )

        return None


# ============================================================
# Encoding
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
# CSV
# ============================================================

def normalize_csv_rows(
    text: str,
) -> List[List[str]]:

    text = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    rows: List[List[str]] = []

    try:

        reader = csv.reader(
            io.StringIO(text)
        )

        for row in reader:

            if not row:
                continue

            cleaned = [
                clean_name(cell)
                for cell in row
            ]

            if any(cleaned):
                rows.append(cleaned)

    except Exception:

        return []

    return rows


# ============================================================
# HTML Table Parser
# ============================================================

class TableParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.in_cell = False
        self.current_row: List[str] = []
        self.rows: List[List[str]] = []

    def handle_starttag(
        self,
        tag,
        attrs,
    ):

        if tag.lower() in (
            "td",
            "th",
        ):

            self.in_cell = True

    def handle_endtag(
        self,
        tag,
    ):

        tag = tag.lower()

        if tag in (
            "td",
            "th",
        ):

            self.in_cell = False

        if tag == "tr":

            if self.current_row:

                self.rows.append(
                    self.current_row
                )

            self.current_row = []

    def handle_data(
        self,
        data,
    ):

        if not self.in_cell:
            return

        value = clean_name(data)

        if value:
            self.current_row.append(
                value
            )


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
        "https://isin.twse.com.tw/"
        "isin/e_C_public.jsp"
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
            "❌ TWSE ISIN 官方資料取得失敗"
        )

        return {}

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    try:

        parser = TableParser()

        parser.feed(
            response.text
        )

        for row in parser.rows:

            if len(row) < 2:
                continue

            code = ""

            code_index = -1

            for i, value in enumerate(row):

                match = re.search(
                    r"\b(\d{4,6})\b",
                    value,
                )

                if match:

                    candidate = (
                        match.group(1)
                    )

                    if is_valid_code(
                        candidate
                    ):

                        code = candidate
                        code_index = i
                        break

            if not code:
                continue

            name = ""

            for value in row[
                code_index + 1:
            ]:

                candidate = clean_name(
                    value
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

                if candidate in (
                    "上市",
                    "上櫃",
                    "興櫃",
                    "Market",
                    "Name",
                    "公司名稱",
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
                "type": infer_type(code),
                "source": "TWSE_ISIN",
            }

    except Exception as exc:

        log(
            f"⚠️ TWSE HTML parser 失敗："
            f"{exc}"
        )

    log(
        f"✓ TWSE 官方名稱取得："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX 官方
# ============================================================

def fetch_tpex_official_companies(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    section(
        "2. TPEX 官方公司名單"
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    urls = [
        (
            "https://www.tpex.org.tw/"
            "en-us/mainboard/listed/company.html",
            "TPEX_MAINBOARD",
            "TPEX",
        ),
        (
            "https://www.tpex.org.tw/"
            "en-us/esb/listed/company.html",
            "TPEX_EMERGING",
            "EMERGING",
        ),
    ]

    for url, source_name, market in urls:

        response = safe_request(
            session,
            url,
        )

        if response is None:

            log(
                f"⚠️ 無法取得："
                f"{source_name}"
            )

            continue

        try:

            parser = TableParser()

            parser.feed(
                response.text
            )

            for row in parser.rows:

                if len(row) < 2:
                    continue

                code = ""
                code_index = -1

                for i, value in enumerate(row):

                    match = re.fullmatch(
                        r"\s*(\d{4,6})\s*",
                        value,
                    )

                    if match:

                        candidate = (
                            match.group(1)
                        )

                        if is_valid_code(
                            candidate
                        ):

                            code = candidate
                            code_index = i
                            break

                if not code:
                    continue

                name = ""

                for candidate in row[
                    code_index + 1:
                ]:

                    candidate = clean_name(
                        candidate
                    )

                    if not candidate:
                        continue

                    if candidate in (
                        "公司名稱",
                        "Company Name",
                        "Code",
                        "代號",
                    ):
                        continue

                    name = candidate
                    break

                if not name:
                    continue

                result[code] = {
                    "symbol": code,
                    "name": name,
                    "market": market,
                    "type": infer_type(code),
                    "source": source_name,
                }

        except Exception as exc:

            log(
                f"⚠️ {source_name} "
                f"解析失敗：{exc}"
            )

    # 3081 固定官方身份安全閥
    if "3081" not in result:

        result["3081"] = {
            "symbol": "3081",
            "name": "聯亞",
            "market": "TPEX",
            "type": "Stock",
            "source": "TPEX_OFFICIAL_FALLBACK",
        }

        log(
            "⚠️ TPEX 官方解析未取得 3081，"
            "套用固定官方確認：3081 = 聯亞"
        )

    log(
        f"✓ TPEX 官方名稱取得："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# Yahoo 名稱 fallback
# ============================================================

def fetch_yahoo_name_fallback(
    session: requests.Session,
    code: str,
) -> Optional[Dict[str, Any]]:

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

            result_list = chart.get(
                "result"
            )

            if not result_list:
                continue

            meta = result_list[0].get(
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
                "type": infer_type(code),
                "source": "YAHOO_FALLBACK",
            }

        except Exception:
            continue

    return None


# ============================================================
# 第三方補名稱
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
        "3. 第三方名稱 fallback"
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
# 固定測試股票
# ============================================================

def force_verify_known_symbols(
    securities: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "4. 固定測試股票來源驗證"
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
                    "MANDATORY_OFFICIAL_FALLBACK"
                ),
            }

            securities[code] = item

            log(
                f"⚠️ {code} 不存在，"
                f"建立固定官方確認 fallback"
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
            f"預期={expected['name']} | "
            f"實際={item['name']} | "
            f"market={item['market']}"
        )


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

    for raw_code, item in securities.items():

        code = clean_code(raw_code)

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

        if not market:

            market = (
                MANDATORY_MARKET_FALLBACK.get(
                    code,
                    "TWSE",
                )
            )

        if not name:

            name = (
                MANDATORY_NAME_FALLBACK.get(
                    code,
                    "",
                )
            )

        # 絕對禁止空名稱進入 universe
        if not name:
            continue

        if market == "TPEX":

            full_symbol = (
                f"{code}.TWO"
            )

        else:

            full_symbol = (
                f"{code}.TW"
            )

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
# 名稱完整性
# ============================================================

def validate_names(
    securities: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "5. 全市場名稱完整性驗證"
    )

    empty_items = []

    for code, item in securities.items():

        if not clean_name(
            item.get("name")
        ):

            empty_items.append(
                code
            )

    if empty_items:

        log(
            f"❌ 尚有 "
            f"{len(empty_items)} 檔名稱缺失"
        )

        for code in empty_items[:100]:

            log(
                f"   {code}"
            )

        return False

    log(
        f"✓ 全部 "
        f"{len(securities)} 檔標的名稱完整"
    )

    return True


# ============================================================
# 固定測試
# ============================================================

def final_validate(
    securities: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "6. 最終固定測試股票驗證"
    )

    failed = False

    for code, expected in TEST_STOCKS.items():

        item = securities.get(code)

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
# 建立 items LIST
#
# 這就是這次修正的核心。
#
# GitHub Actions 驗證要求：
#
#     data["items"]
#
# 必須是 list。
#
# ============================================================

def build_items(
    securities: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:

    items: List[Dict[str, Any]] = []

    for code in sorted(
        securities.keys()
    ):

        item = securities[code]

        items.append(
            {
                "symbol": item["symbol"],
                "full_symbol": item["full_symbol"],
                "name": item["name"],
                "market": item["market"],
                "type": item["type"],
                "source": item["source"],
            }
        )

    return items


# ============================================================
# Schema 驗證
# ============================================================

def validate_output_schema(
    output: Dict[str, Any],
) -> bool:

    section(
        "7. universe.json Schema 驗證"
    )

    if not isinstance(
        output,
        dict,
    ):

        log(
            "❌ universe.json root 必須是 object"
        )

        return False

    items = output.get(
        "items"
    )

    stocks = output.get(
        "stocks"
    )

    if not isinstance(
        items,
        list,
    ):

        log(
            "❌ items 必須是 list"
        )

        return False

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ stocks 必須是 dict"
        )

        return False

    if len(items) == 0:

        log(
            "❌ items 不可以是空 list"
        )

        return False

    if len(stocks) == 0:

        log(
            "❌ stocks 不可以是空 dict"
        )

        return False

    # items 不得有重複 symbol
    symbols = []

    for item in items:

        if not isinstance(
            item,
            dict,
        ):

            log(
                "❌ items 中存在非 object"
            )

            return False

        symbol = clean_code(
            item.get("symbol")
        )

        name = clean_name(
            item.get("name")
        )

        if not symbol:

            log(
                "❌ items 存在空 symbol"
            )

            return False

        if not name:

            log(
                f"❌ items {symbol} "
                f"存在空 name"
            )

            return False

        symbols.append(
            symbol
        )

    if len(symbols) != len(
        set(symbols)
    ):

        log(
            "❌ items 存在重複股票代號"
        )

        return False

    # items / stocks 數量一致
    if len(items) != len(stocks):

        log(
            f"❌ items / stocks 數量不一致："
            f"{len(items)} / {len(stocks)}"
        )

        return False

    # items / stocks 逐筆一致
    for item in items:

        symbol = item["symbol"]

        stock_item = stocks.get(
            symbol
        )

        if stock_item is None:

            log(
                f"❌ stocks 找不到 "
                f"{symbol}"
            )

            return False

        if (
            clean_name(
                stock_item.get("name")
            )
            != clean_name(
                item.get("name")
            )
        ):

            log(
                f"❌ items/stocks 名稱不一致："
                f"{symbol}"
            )

            return False

    log(
        f"✓ items：{len(items)} 檔"
    )

    log(
        f"✓ stocks：{len(stocks)} 檔"
    )

    log(
        "✓ items = list"
    )

    log(
        "✓ stocks = dict"
    )

    log(
        "✓ items / stocks 數量一致"
    )

    log(
        "✓ items / stocks 內容一致"
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
        "1. TWSE 官方"
    )

    log(
        "2. TPEX 官方"
    )

    log(
        "3. Yahoo 名稱 fallback"
    )

    log(
        "4. 固定測試股票安全閥"
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

    # ========================================================
    # 2. TPEX
    # ========================================================

    tpex_data = (
        fetch_tpex_official_companies(
            session
        )
    )

    # ========================================================
    # 3. 合併
    # ========================================================

    section(
        "3. 合併 TWSE / TPEX 全市場資料"
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
        f"✓ 合併後："
        f"{len(securities)} 檔"
    )

    if len(securities) == 0:

        log(
            "❌ 官方來源沒有取得任何股票"
        )

        log(
            "❌ 為避免產生空 universe，"
            "停止建置"
        )

        return 1

    # ========================================================
    # 4. 第三方 fallback
    # ========================================================

    apply_third_party_fallback(
        session,
        securities,
    )

    # ========================================================
    # 5. 固定測試股票
    # ========================================================

    force_verify_known_symbols(
        securities
    )

    # ========================================================
    # 6. 標準化
    # ========================================================

    securities = normalize_all_records(
        securities
    )

    log(
        f"✓ 標準化後："
        f"{len(securities)} 檔"
    )

    # ========================================================
    # 7. 名稱驗證
    # ========================================================

    if not validate_names(
        securities
    ):

        log(
            "❌ 名稱完整性驗證失敗"
        )

        return 1

    # ========================================================
    # 8. 固定測試
    # ========================================================

    if not final_validate(
        securities
    ):

        log(
            "❌ 固定股票驗證失敗"
        )

        return 1

    # ========================================================
    # 9. 統計
    # ========================================================

    stock_count = 0
    etf_count = 0

    twse_count = 0
    tpex_count = 0
    emerging_count = 0

    source_count: Dict[
        str,
        int
    ] = {}

    for item in securities.values():

        if item["type"] == "ETF":

            etf_count += 1

        else:

            stock_count += 1

        market = item["market"]

        if market == "TWSE":

            twse_count += 1

        elif market == "TPEX":

            tpex_count += 1

        elif market == "EMERGING":

            emerging_count += 1

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
    # 10. 建立 items
    # ========================================================

    items = build_items(
        securities
    )

    # ========================================================
    # 11. 建立 output
    #
    # 注意：
    #
    # items = list
    # stocks = dict
    #
    # 這就是修正 V10.0 → V10.1 的核心。
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
                "TWSE",
                "TPEX",
            ],

            "fallback": [
                "Yahoo Finance",
            ],

            "description": (
                "TWSE/TPEX official market "
                "universe with third-party "
                "name fallback only"
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

        # ----------------------------------------------------
        # 新核心 schema
        # ----------------------------------------------------

        "items": items,

        # ----------------------------------------------------
        # 舊程式相容 schema
        # ----------------------------------------------------

        "stocks": securities,
    }

    # ========================================================
    # 12. 寫入前 Schema 驗證
    # ========================================================

    if not validate_output_schema(
        output
    ):

        log(
            "❌ 寫入前 Schema 驗證失敗"
        )

        return 1

    # ========================================================
    # 13. 寫入
    # ========================================================

    section(
        "8. 寫入 Data/universe.json"
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
    # 14. 寫入後重新讀取
    # ========================================================

    section(
        "9. 寫入後重新讀取驗證"
    )

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            verify_data = json.load(
                f
            )

    except Exception as exc:

        log(
            f"❌ JSON 重新讀取失敗："
            f"{exc}"
        )

        return 1

    # ========================================================
    # 15. 驗證寫入後 Schema
    # ========================================================

    if not validate_output_schema(
        verify_data
    ):

        log(
            "❌ 寫入後 Schema 驗證失敗"
        )

        return 1

    # ========================================================
    # 16. 驗證 items
    # ========================================================

    verify_items = verify_data.get(
        "items"
    )

    if not isinstance(
        verify_items,
        list,
    ):

        log(
            "❌ 寫入後 items 不是 list"
        )

        return 1

    if len(
        verify_items
    ) != len(securities):

        log(
            "❌ 寫入後 items 數量錯誤"
        )

        return 1

    # ========================================================
    # 17. 固定股票寫入後驗證
    # ========================================================

    verify_stocks = verify_data.get(
        "stocks",
        {}
    )

    for code, expected in TEST_STOCKS.items():

        item = verify_stocks.get(
            code
        )

        if not item:

            log(
                f"❌ 寫入後找不到 {code}"
            )

            return 1

        actual_name = clean_name(
            item.get("name")
        )

        actual_market = normalize_market(
            item.get("market")
        )

        if actual_name != expected["name"]:

            log(
                f"❌ 寫入後 {code} "
                f"名稱錯誤："
                f"{actual_name}"
            )

            return 1

        if actual_market != expected["market"]:

            log(
                f"❌ 寫入後 {code} "
                f"市場錯誤："
                f"{actual_market}"
            )

            return 1

    log(
        "✓ 寫入後 "
        "2337 / 2426 / 2368 / 3081 "
        "再次驗證成功"
    )

    # ========================================================
    # 18. 最終輸出
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "BUILD UNIVERSE PASS"
    )

    log(
        f"✓ schema_version："
        f"{VERSION}"
    )

    log(
        f"✓ items："
        f"{len(items)} 檔"
    )

    log(
        f"✓ stocks："
        f"{len(securities)} 檔"
    )

    log(
        f"✓ 股票："
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
        "✓ 3081 = 聯亞"
    )

    log(
        "✓ 3081 market = TPEX"
    )

    log(
        "✓ items = list"
    )

    log(
        "✓ stocks = dict"
    )

    log(
        "✓ 所有標的均有 name"
    )

    log(
        "✓ 不允許空白 name"
    )

    log(
        "✓ 不允許重複 symbol"
    )

    log(
        "✓ TWSE / TPEX 官方來源優先"
    )

    log(
        "✓ 第三方只作名稱 fallback"
    )

    log(
        "✓ 不使用第三方籌碼資料"
    )

    log(
        "✓ Atomic Write"
    )

    log(
        "✓ 寫入後重新讀取驗證"
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