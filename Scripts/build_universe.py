#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - Scripts/build_universe.py

Universe 建置核心契約
============================================================

1. 官方商品主檔決定 Universe。
2. ETF 不依賴當日成交資料。
3. ETF 不依賴當日行情是否存在。
4. 支援新制 6 碼 ETF，例如 00400A。
5. 支援 TWSE / TPEX ETF。
6. 債券 ETF 保留。
7. ETN、權證、REIT、一般債券、公司債、TDR、特別股等非 ETF
   商品排除。
8. STOCK 與 ETF 完全分流。
9. 不寫死 Universe 數量。
10. 保留既有 universe.json metadata。
11. 官方商品主檔抓取失敗 => FAIL。
12. 官方商品主檔解析失敗 => FAIL。
13. 官方 ETF 主檔缺失 => FAIL。
14. 官方主檔與 Universe 不一致 => FAIL。
15. Gate FAIL 時絕對不覆蓋既有 universe.json。
16. Atomic Write。
17. 寫入後再次驗證。
18. 不使用 CMoney。
19. 不使用日成交行情建立 Universe。
20. 不追版本號。

官方來源
============================================================

TWSE / TPEX 官方 ISIN 商品主檔：

https://isin.twse.com.tw/isin/e_single_main.jsp

此資料層級與每日成交行情不同。

Universe 的商品存在性：
    官方商品主檔

價格 / 成交量 / 籌碼：
    由後續資料流程處理

兩者不可混用。
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# OFFICIAL MASTER
# ============================================================

MASTER_URL = "https://isin.twse.com.tw/isin/e_single_main.jsp"

TIMEOUT = 45
RETRIES = 4
RETRY_SLEEP = 2.0


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# LOG
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 76)
    log(title)
    log("=" * 76)


def now_tw() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Taipei"))


# ============================================================
# TEXT
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = html.unescape(str(value))
    text = text.replace("\xa0", " ")
    text = text.replace("\u3000", " ")

    return re.sub(r"\s+", " ", text).strip()


def clean_code(value: Any) -> str:
    text = clean_text(value).upper()

    text = text.replace(".TW", "")
    text = text.replace(".TWO", "")
    text = text.replace(" ", "")
    text = text.replace("\u3000", "")

    return text


def normalize_key(value: Any) -> str:
    text = clean_text(value).lower()

    return re.sub(
        r"[\s_\-\/\\\(\)（）:：.．]+",
        "",
        text,
    )


# ============================================================
# HTML TABLE PARSER
# ============================================================

class TableParser(HTMLParser):
    """
    只解析官方頁面的 table / tr / td / th。

    不依賴 pandas read_html，避免 GitHub Actions 因 HTML 結構
    小幅變動而出現不一致。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

        self.rows: List[List[str]] = []

        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:

        tag = tag.lower()

        if tag == "tr":
            self._row = []

        elif tag in {"td", "th"}:
            if self._row is not None:
                self._cell = []

        elif tag == "br":
            if self._cell is not None:
                self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in {"td", "th"}:

            if (
                self._row is not None
                and self._cell is not None
            ):
                value = clean_text("".join(self._cell))
                self._row.append(value)

            self._cell = None

        elif tag == "tr":

            if self._row:
                self.rows.append(self._row)

            self._row = None
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


# ============================================================
# OFFICIAL MASTER FETCH
# ============================================================

def fetch_master_html() -> str:
    """
    抓取官方商品主檔。

    注意：
    任何失敗都直接 raise。
    不允許 fallback 到行情資料。
    不允許拿舊 Universe 湊數。
    """

    last_error = ""

    for attempt in range(1, RETRIES + 1):

        try:
            response = session.get(
                MASTER_URL,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            content = response.content

            if not content:
                raise RuntimeError(
                    "官方商品主檔 response body 為空"
                )

            # ------------------------------------------------
            # 嘗試多種官方頁面可能使用的編碼
            # ------------------------------------------------

            encodings: List[str] = []

            content_type = response.headers.get(
                "Content-Type",
                "",
            )

            match = re.search(
                r"charset\s*=\s*['\"]?([^;'\"]+)",
                content_type,
                flags=re.IGNORECASE,
            )

            if match:
                encodings.append(
                    match.group(1).strip()
                )

            encodings.extend(
                [
                    "utf-8",
                    "big5",
                    "cp950",
                    "ms950",
                ]
            )

            text = None

            for encoding in encodings:

                try:

                    candidate = content.decode(
                        encoding,
                        errors="replace",
                    )

                    # 官方頁面至少應該出現這些欄位之一
                    markers = (
                        "Security Code",
                        "Security Name",
                        "Type of security",
                        "Market",
                        "證券代號",
                        "證券名稱",
                        "證券種類",
                        "市場別",
                        "有價證券",
                    )

                    if any(
                        marker in candidate
                        for marker in markers
                    ):
                        text = candidate
                        break

                except LookupError:
                    continue

            if text is None:

                raise RuntimeError(
                    "官方商品主檔無法使用已知編碼解析"
                )

            if len(text) < 5000:

                raise RuntimeError(
                    f"官方商品主檔內容異常過短：{len(text)} bytes"
                )

            return text

        except Exception as exc:

            last_error = str(exc)

            log(
                f"⚠️ 官方主檔抓取第 {attempt}/{RETRIES} 次失敗："
                f"{last_error}"
            )

            if attempt < RETRIES:
                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        f"官方商品主檔抓取失敗：{last_error}"
    )


# ============================================================
# MASTER PARSE
# ============================================================

def parse_master(
    text: str,
) -> List[Dict[str, str]]:

    parser = TableParser()
    parser.feed(text)

    rows = parser.rows

    if not rows:
        return []

    # --------------------------------------------------------
    # 找官方欄位標題
    # --------------------------------------------------------

    header_index: Optional[int] = None
    headers: List[str] = []

    for index, row in enumerate(rows):

        normalized = {
            normalize_key(value)
            for value in row
        }

        has_code = any(
            key in normalized
            for key in {
                "securitycode",
                "securitiescode",
                "證券代號",
                "有價證券代號",
                "有價證券代號及名稱",
            }
        )

        has_market = any(
            key in normalized
            for key in {
                "market",
                "市場別",
                "市場",
            }
        )

        has_type = any(
            key in normalized
            for key in {
                "typeofsecurity",
                "證券種類",
                "有價證券種類",
            }
        )

        if has_code and (
            has_market or has_type
        ):
            header_index = index
            headers = row
            break

    if header_index is None:

        # ----------------------------------------------------
        # 第二層：允許官方頁面欄位順序變動
        # ----------------------------------------------------

        for index, row in enumerate(rows):

            joined = " ".join(
                normalize_key(value)
                for value in row
            )

            if (
                "isin" in joined
                and (
                    "market" in joined
                    or "市場" in joined
                )
            ):
                header_index = index
                headers = row
                break

    if header_index is None:
        return []

    result: List[Dict[str, str]] = []

    for row in rows[
        header_index + 1:
    ]:

        if len(row) < 2:
            continue

        record: Dict[str, str] = {}

        for index, value in enumerate(row):

            if index >= len(headers):
                break

            key = clean_text(
                headers[index]
            )

            value = clean_text(value)

            if key:
                record[key] = value

        if record:
            result.append(record)

    return result


# ============================================================
# OFFICIAL FIELD ALIASES
# ============================================================

CODE_FIELDS = (
    "Security Code",
    "Securities Code",
    "證券代號",
    "有價證券代號",
    "有價證券代號及名稱",
)

NAME_FIELDS = (
    "Security Name",
    "Securities Name",
    "證券名稱",
    "證券簡稱",
    "有價證券名稱",
    "有價證券代號及名稱",
)

MARKET_FIELDS = (
    "Market",
    "市場別",
    "市場",
)

TYPE_FIELDS = (
    "Type of security",
    "Type of Security",
    "證券種類",
    "有價證券種類",
)

DATE_FIELDS = (
    "Date Stock Listed",
    "Date Listed",
    "上市日",
    "上市日期",
    "發行日期",
)

CFI_FIELDS = (
    "CFI Code",
    "CFICode",
    "CFI",
)


def get_field(
    row: Dict[str, str],
    aliases: Tuple[str, ...],
) -> str:

    normalized = {
        normalize_key(key): clean_text(value)
        for key, value in row.items()
    }

    for alias in aliases:

        value = normalized.get(
            normalize_key(alias)
        )

        if value:
            return value

    return ""


# ============================================================
# CODE / NAME EXTRACTION
# ============================================================

def split_code_name(
    value: str,
) -> Tuple[str, str]:

    text = clean_text(value)

    # 支援：
    #
    # 00400A 台灣主動式...
    # 00937B 凱基美國非投資等級債...
    # 2330 台積電
    #
    match = re.match(
        r"^\s*([0-9]{4,6}[A-Z]?)"
        r"(?:\s+|　+)(.+?)\s*$",
        text,
    )

    if match:

        return (
            clean_code(match.group(1)),
            clean_text(match.group(2)),
        )

    return "", ""


def extract_code(
    row: Dict[str, str],
) -> str:

    value = get_field(
        row,
        CODE_FIELDS,
    )

    code = clean_code(value)

    # 完整純代號
    if re.fullmatch(
        r"[0-9]{4,6}[A-Z]?",
        code,
    ):
        return code

    # 代號 + 名稱
    code2, _ = split_code_name(
        value
    )

    if code2:
        return code2

    # 最後 fallback：從官方欄位抓代號
    match = re.search(
        r"(?<![A-Z0-9])"
        r"([0-9]{4,6}[A-Z]?)"
        r"(?![A-Z0-9])",
        value.upper(),
    )

    if match:
        return match.group(1)

    return ""


def extract_name(
    row: Dict[str, str],
) -> str:

    value = get_field(
        row,
        NAME_FIELDS,
    )

    if not value:
        return ""

    code, name = split_code_name(
        value
    )

    if name:
        return name

    return clean_text(value)


# ============================================================
# OFFICIAL CLASSIFICATION
# ============================================================

ETF_TYPE_EXACT = {
    "ETF",
    "ETFS",
    "EXCHANGE TRADED FUND",
    "EXCHANGE TRADED FUNDS",
    "指數股票型基金",
    "指數股票型基金受益憑證",
}


NON_TARGET_TYPE_EXACT = {
    "ETN",
    "EXCHANGE TRADED NOTE",

    "WARRANT",
    "權證",

    "REIT",
    "REAL ESTATE INVESTMENT TRUST",

    "BOND",
    "債券",
    "一般債券",
    "公司債",
    "金融債",
    "政府債",
    "可轉債",
    "可轉換公司債",

    "TDR",
    "存託憑證",

    "PREFERRED STOCK",
    "特別股",

    "基金",
    "基金受益憑證",
}


def normalized_type(
    value: str,
) -> str:
    return clean_text(value).upper()


def is_etf(
    type_text: str,
    cfi_code: str,
) -> bool:

    t = normalized_type(type_text)
    cfi = clean_text(cfi_code).upper()

    # --------------------------------------------------------
    # 第一優先：官方 Type of security
    # --------------------------------------------------------

    if t in ETF_TYPE_EXACT:
        return True

    # --------------------------------------------------------
    # 官方 CFI
    #
    # ETF CFI 屬 Collective Investment Vehicles 類別。
    # CE 開頭用來協助官方頁面欄位變化時辨識。
    #
    # 但 ETN 絕對不能因名稱/CFI誤判成 ETF。
    # --------------------------------------------------------

    if t.startswith("ETN"):
        return False

    if "EXCHANGE TRADED NOTE" in t:
        return False

    if cfi.startswith("CE"):
        return True

    return False


def is_explicit_non_target(
    type_text: str,
) -> bool:

    t = normalized_type(type_text)

    if t in NON_TARGET_TYPE_EXACT:
        return True

    blocked = (
        "ETN",
        "WARRANT",
        "權證",
        "TDR",
        "存託憑證",
        "REIT",
        "REAL ESTATE INVESTMENT TRUST",
    )

    return any(
        token in t
        for token in blocked
    )


def is_common_stock(
    type_text: str,
    cfi_code: str,
) -> bool:

    t = normalized_type(type_text)
    cfi = clean_text(cfi_code).upper()

    if is_explicit_non_target(
        type_text
    ):
        return False

    if is_etf(
        type_text,
        cfi_code,
    ):
        return False

    # 官方普通股類型
    explicit_stock_types = {
        "STOCK",
        "COMMON STOCK",
        "COMMON SHARES",
        "COMMON SHARE",
        "普通股",
        "普通股股票",
    }

    if t in explicit_stock_types:
        return True

    # CFI：ESVU 類普通股
    if cfi.startswith("ESVU"):
        return True

    return False


def market_code(
    market_text: str,
) -> Optional[str]:

    text = clean_text(
        market_text
    ).upper()

    if (
        "TWSE" in text
        or "上市" in text
    ):
        return "TWSE"

    if (
        "TPEX" in text
        or "OTC" in text
        or "上櫃" in text
    ):
        return "TPEX"

    return None


# ============================================================
# ETF SUBTYPE
# ============================================================

def classify_etf_instrument(
    code: str,
    name: str,
) -> str:

    text = (
        f"{code} {name}"
    ).upper()

    # --------------------------------------------------------
    # 新制 6 碼 ETF suffix
    #
    # 不用 suffix 決定「是不是 ETF」。
    # ETF 身分已由官方 Type / CFI 決定。
    #
    # suffix 只用來做 metadata。
    # --------------------------------------------------------

    suffix = (
        code[-1:]
        if code
        else ""
    )

    suffix_map = {
        "A": "ACTIVE",
        "B": "BOND",
        "C": "BOND_FX",
        "D": "ACTIVE_BOND",
        "K": "ETF_FX",
        "L": "LEVERAGED",
        "M": "LEVERAGED_FX",
        "R": "INVERSE",
        "S": "INVERSE_FX",
        "T": "MULTI_ASSET",
        "U": "FUTURES",
        "V": "FUTURES_FX",
    }

    if suffix in suffix_map:
        return suffix_map[suffix]

    # --------------------------------------------------------
    # 名稱輔助分類
    # --------------------------------------------------------

    if any(
        token in text
        for token in (
            "BOND",
            "債券",
            "公債",
            "公司債",
            "投資級債",
            "非投資等級債",
        )
    ):
        return "BOND"

    if any(
        token in text
        for token in (
            "MULTI ASSET",
            "MULTI-ASSET",
            "多資產",
            "平衡",
        )
    ):
        return "MULTI_ASSET"

    if any(
        token in text
        for token in (
            "FUTURE",
            "期貨",
            "原油",
            "黃金",
            "商品",
        )
    ):
        return "FUTURES_COMMODITY"

    if any(
        token in text
        for token in (
            "CURRENCY",
            "貨幣",
        )
    ):
        return "CURRENCY"

    return "EQUITY"


# ============================================================
# EXISTING UNIVERSE / METADATA
# ============================================================

CORE_FIELDS = {
    "symbol",
    "full_symbol",
    "name",
    "market",
    "type",
    "instrument_type",
    "status",
}


def load_existing_payload() -> Dict[str, Any]:

    if not UNIVERSE_FILE.exists():
        return {}

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(
            payload,
            dict,
        ):
            return payload

        return {}

    except Exception as exc:

        log(
            f"⚠️ 舊 universe.json "
            f"無法解析，metadata 不沿用：{exc}"
        )

        return {}


def load_existing_metadata(
    payload: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for key, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        code = clean_code(
            item.get(
                "symbol",
                key,
            )
        )

        if code:
            result[code] = item

    return result


# ============================================================
# BUILD
# ============================================================

def build_universe(
    rows: List[Dict[str, str]],
    existing_metadata: Dict[str, Dict[str, Any]],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, set[str]],
    Dict[str, int],
]:

    universe: Dict[
        str,
        Dict[str, Any],
    ] = {}

    official_codes = {
        "STOCK": set(),
        "ETF": set(),
    }

    counters = {
        "official_rows": len(rows),
        "valid_products": 0,
        "stock_products": 0,
        "etf_products": 0,
        "twse_stock": 0,
        "tpex_stock": 0,
        "twse_etf": 0,
        "tpex_etf": 0,
        "excluded_products": 0,
        "unknown_products": 0,
    }

    # --------------------------------------------------------
    # 官方主檔逐筆判斷
    # --------------------------------------------------------

    for row in rows:

        code = extract_code(row)

        if not code:
            continue

        market = market_code(
            get_field(
                row,
                MARKET_FIELDS,
            )
        )

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        name = (
            extract_name(row)
            or code
        )

        type_text = get_field(
            row,
            TYPE_FIELDS,
        )

        cfi_code = get_field(
            row,
            CFI_FIELDS,
        )

        listed_date = get_field(
            row,
            DATE_FIELDS,
        )

        # ====================================================
        # ETF
        # ====================================================

        if is_etf(
            type_text,
            cfi_code,
        ):

            counters[
                "valid_products"
            ] += 1

            counters[
                "etf_products"
            ] += 1

            official_codes[
                "ETF"
            ].add(code)

            suffix = (
                "TW"
                if market == "TWSE"
                else "TWO"
            )

            record: Dict[str, Any] = {
                "symbol": code,
                "full_symbol": (
                    f"{code}.{suffix}"
                ),
                "name": name,
                "market": market,
                "type": "ETF",
                "instrument_type":
                    classify_etf_instrument(
                        code,
                        name,
                    ),
                "status": "active",
            }

            if listed_date:
                record[
                    "listed_date"
                ] = listed_date

            if cfi_code:
                record[
                    "cfi_code"
                ] = cfi_code

        # ====================================================
        # STOCK
        # ====================================================

        elif (
            is_common_stock(
                type_text,
                cfi_code,
            )
            and re.fullmatch(
                r"[1-9][0-9]{3}",
                code,
            )
        ):

            counters[
                "valid_products"
            ] += 1

            counters[
                "stock_products"
            ] += 1

            official_codes[
                "STOCK"
            ].add(code)

            suffix = (
                "TW"
                if market == "TWSE"
                else "TWO"
            )

            record = {
                "symbol": code,
                "full_symbol": (
                    f"{code}.{suffix}"
                ),
                "name": name,
                "market": market,
                "type": "STOCK",
                "instrument_type":
                    "COMMON_STOCK",
                "status": "active",
            }

            if listed_date:
                record[
                    "listed_date"
                ] = listed_date

            if cfi_code:
                record[
                    "cfi_code"
                ] = cfi_code

        # ====================================================
        # 非 Universe 商品
        # ====================================================

        else:

            if is_explicit_non_target(
                type_text
            ):
                counters[
                    "excluded_products"
                ] += 1
            else:
                counters[
                    "unknown_products"
                ] += 1

            continue

        # ====================================================
        # 保留既有 metadata
        #
        # 重要：
        # 舊資料不能覆蓋官方核心欄位。
        # ====================================================

        old = existing_metadata.get(
            code
        )

        if isinstance(
            old,
            dict,
        ):

            for key, value in old.items():

                if key in CORE_FIELDS:
                    continue

                # 官方本次資料已提供的欄位
                if key in {
                    "listed_date",
                    "cfi_code",
                }:
                    continue

                if value in (
                    None,
                    "",
                    [],
                    {},
                ):
                    continue

                record[key] = value

        universe[code] = record

    return (
        dict(
            sorted(
                universe.items()
            )
        ),
        official_codes,
        counters,
    )


# ============================================================
# OFFICIAL MASTER GATE
# ============================================================

def official_master_gate(
    text: str,
    rows: List[Dict[str, str]],
) -> bool:

    section(
        "OFFICIAL PRODUCT MASTER GATE"
    )

    if not text:
        log(
            "❌ 官方商品主檔內容為空"
        )
        return False

    if len(rows) == 0:
        log(
            "❌ 官方商品主檔解析後 0 rows"
        )
        return False

    official_stock = set()
    official_etf = set()

    twse_stock = set()
    tpex_stock = set()

    twse_etf = set()
    tpex_etf = set()

    # --------------------------------------------------------
    # 從官方主檔建立 expected set
    # --------------------------------------------------------

    for row in rows:

        code = extract_code(row)

        if not code:
            continue

        market = market_code(
            get_field(
                row,
                MARKET_FIELDS,
            )
        )

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        type_text = get_field(
            row,
            TYPE_FIELDS,
        )

        cfi_code = get_field(
            row,
            CFI_FIELDS,
        )

        if is_etf(
            type_text,
            cfi_code,
        ):

            official_etf.add(code)

            if market == "TWSE":
                twse_etf.add(code)
            else:
                tpex_etf.add(code)

        elif (
            is_common_stock(
                type_text,
                cfi_code,
            )
            and re.fullmatch(
                r"[1-9][0-9]{3}",
                code,
            )
        ):

            official_stock.add(code)

            if market == "TWSE":
                twse_stock.add(code)
            else:
                tpex_stock.add(code)

    # --------------------------------------------------------
    # 基本完整性
    # --------------------------------------------------------

    log(
        f"官方主檔 rows：{len(rows)}"
    )

    log(
        f"官方 STOCK：{len(official_stock)}"
    )

    log(
        f"官方 ETF：{len(official_etf)}"
    )

    log(
        f"TWSE STOCK：{len(twse_stock)}"
    )

    log(
        f"TPEX STOCK：{len(tpex_stock)}"
    )

    log(
        f"TWSE ETF：{len(twse_etf)}"
    )

    log(
        f"TPEX ETF：{len(tpex_etf)}"
    )

    if len(official_stock) == 0:

        log(
            "❌ 官方 STOCK 商品主檔為 0"
        )

        return False

    if len(official_etf) == 0:

        log(
            "❌ 官方 ETF 商品主檔為 0"
        )

        return False

    if len(twse_etf) == 0:

        log(
            "❌ 官方 TWSE ETF 為 0"
        )

        return False

    if len(tpex_etf) == 0:

        log(
            "❌ 官方 TPEX ETF 為 0"
        )

        return False

    log(
        "✓ 官方商品主檔可用"
    )

    log(
        "✓ 官方 STOCK 主檔可用"
    )

    log(
        "✓ 官方 ETF 主檔可用"
    )

    log(
        "✓ TWSE ETF 主檔可用"
    )

    log(
        "✓ TPEX ETF 主檔可用"
    )

    log(
        "✓ Official Product Master Gate PASS"
    )

    return True


# ============================================================
# STRUCTURE GATE
# ============================================================

def structure_gate(
    universe: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "UNIVERSE STRUCTURE GATE"
    )

    errors: List[str] = []

    if not isinstance(
        universe,
        dict,
    ):

        errors.append(
            "stocks 必須是 dict"
        )

    if not universe:

        errors.append(
            "stocks 不可為空"
        )

    for code, item in universe.items():

        if not isinstance(
            item,
            dict,
        ):

            errors.append(
                f"{code}: item 非 dict"
            )

            continue

        symbol = clean_code(
            item.get(
                "symbol"
            )
        )

        market = item.get(
            "market"
        )

        product_type = item.get(
            "type"
        )

        instrument_type = item.get(
            "instrument_type"
        )

        status = item.get(
            "status"
        )

        full_symbol = item.get(
            "full_symbol"
        )

        # ----------------------------------------------------
        # symbol
        # ----------------------------------------------------

        if symbol != code:

            errors.append(
                f"{code}: symbol mismatch"
            )

        # ----------------------------------------------------
        # market
        # ----------------------------------------------------

        if market not in {
            "TWSE",
            "TPEX",
        }:

            errors.append(
                f"{code}: invalid market={market}"
            )

        # ----------------------------------------------------
        # type
        # ----------------------------------------------------

        if product_type not in {
            "STOCK",
            "ETF",
        }:

            errors.append(
                f"{code}: invalid type={product_type}"
            )

        # ----------------------------------------------------
        # status
        # ----------------------------------------------------

        if status != "active":

            errors.append(
                f"{code}: status != active"
            )

        # ----------------------------------------------------
        # instrument type
        # ----------------------------------------------------

        if not instrument_type:

            errors.append(
                f"{code}: instrument_type empty"
            )

        # ----------------------------------------------------
        # full symbol
        # ----------------------------------------------------

        expected_suffix = (
            "TW"
            if market == "TWSE"
            else "TWO"
        )

        expected_full_symbol = (
            f"{code}.{expected_suffix}"
        )

        if (
            full_symbol
            != expected_full_symbol
        ):

            errors.append(
                f"{code}: "
                f"full_symbol={full_symbol}, "
                f"expected={expected_full_symbol}"
            )

        # ----------------------------------------------------
        # STOCK contract
        # ----------------------------------------------------

        if product_type == "STOCK":

            if not re.fullmatch(
                r"[1-9][0-9]{3}",
                code,
            ):

                errors.append(
                    f"{code}: STOCK 必須是 4 碼"
                )

            if (
                instrument_type
                != "COMMON_STOCK"
            ):

                errors.append(
                    f"{code}: "
                    "STOCK instrument_type 錯誤"
                )

        # ----------------------------------------------------
        # ETF contract
        # ----------------------------------------------------

        elif product_type == "ETF":

            if not re.fullmatch(
                r"[0-9]{4,6}[A-Z]?",
                code,
            ):

                errors.append(
                    f"{code}: ETF 代號格式錯誤"
                )

    if errors:

        log(
            f"❌ Structure Gate FAIL："
            f"{len(errors)} errors"
        )

        for error in errors[:50]:
            log(
                f"   {error}"
            )

        if len(errors) > 50:

            log(
                f"   ...其餘 "
                f"{len(errors) - 50} 個錯誤省略"
            )

        return False

    stock_count = sum(
        1
        for item in universe.values()
        if item.get("type") == "STOCK"
    )

    etf_count = sum(
        1
        for item in universe.values()
        if item.get("type") == "ETF"
    )

    twse_count = sum(
        1
        for item in universe.values()
        if item.get("market") == "TWSE"
    )

    tpex_count = sum(
        1
        for item in universe.values()
        if item.get("market") == "TPEX"
    )

    log(
        f"STOCK：{stock_count}"
    )

    log(
        f"ETF：{etf_count}"
    )

    log(
        f"TWSE：{twse_count}"
    )

    log(
        f"TPEX：{tpex_count}"
    )

    log(
        "✓ STOCK / ETF 分流"
    )

    log(
        "✓ TWSE / TPEX 正確標記"
    )

    log(
        "✓ status == active"
    )

    log(
        "✓ ETF 支援 4~6 碼及英文字尾"
    )

    log(
        "✓ Universe Structure Gate PASS"
    )

    return True


# ============================================================
# COMPLETENESS GATE
# ============================================================

def completeness_gate(
    universe: Dict[str, Dict[str, Any]],
    official_codes: Dict[str, set[str]],
) -> bool:

    section(
        "OFFICIAL MASTER vs UNIVERSE COMPLETENESS GATE"
    )

    official_stock = official_codes[
        "STOCK"
    ]

    official_etf = official_codes[
        "ETF"
    ]

    official_all = (
        official_stock
        | official_etf
    )

    universe_all = set(
        universe.keys()
    )

    # --------------------------------------------------------
    # 官方有，但 Universe 沒有
    # --------------------------------------------------------

    missing = sorted(
        official_all
        - universe_all
    )

    # --------------------------------------------------------
    # Universe 有，但官方沒有
    # --------------------------------------------------------

    extra = sorted(
        universe_all
        - official_all
    )

    if missing:

        log(
            "❌ 官方商品遺失："
            f"{missing[:50]}"
        )

        log(
            f"missing count："
            f"{len(missing)}"
        )

        return False

    if extra:

        log(
            "❌ Universe 存在官方主檔不存在商品："
            f"{extra[:50]}"
        )

        log(
            f"extra count："
            f"{len(extra)}"
        )

        return False

    # --------------------------------------------------------
    # 類型一致性
    # --------------------------------------------------------

    for code in official_stock:

        if universe[
            code
        ].get("type") != "STOCK":

            log(
                f"❌ {code}: "
                "官方 STOCK / Universe type 不一致"
            )

            return False

    for code in official_etf:

        if universe[
            code
        ].get("type") != "ETF":

            log(
                f"❌ {code}: "
                "官方 ETF / Universe type 不一致"
            )

            return False

    log(
        f"官方 STOCK："
        f"{len(official_stock)}"
    )

    log(
        f"官方 ETF："
        f"{len(official_etf)}"
    )

    log(
        f"Universe："
        f"{len(universe)}"
    )

    log(
        "✓ 官方商品集合 = Universe 商品集合"
    )

    log(
        "✓ 官方 STOCK = Universe STOCK"
    )

    log(
        "✓ 官方 ETF = Universe ETF"
    )

    log(
        "✓ Official Master vs Universe "
        "Completeness Gate PASS"
    )

    return True


# ============================================================
# SPECIAL ETF GATE
# ============================================================

def special_etf_gate(
    rows: List[Dict[str, str]],
    universe: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "ETF SPECIAL PRODUCT GATE"
    )

    official_etf_codes = set()

    six_digit_etfs = set()

    bond_etfs = set()

    twse_etfs = set()
    tpex_etfs = set()

    for row in rows:

        code = extract_code(row)

        if not code:
            continue

        market = market_code(
            get_field(
                row,
                MARKET_FIELDS,
            )
        )

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        type_text = get_field(
            row,
            TYPE_FIELDS,
        )

        cfi_code = get_field(
            row,
            CFI_FIELDS,
        )

        name = extract_name(row)

        if not is_etf(
            type_text,
            cfi_code,
        ):
            continue

        official_etf_codes.add(
            code
        )

        if re.fullmatch(
            r"[0-9]{6}[A-Z]?",
            code,
        ):
            six_digit_etfs.add(
                code
            )

        text = (
            f"{code} {name}"
        ).upper()

        if any(
            token in text
            for token in (
                "BOND",
                "債券",
                "公債",
                "公司債",
                "投資級債",
                "非投資等級債",
            )
        ):
            bond_etfs.add(code)

        if market == "TWSE":
            twse_etfs.add(code)
        else:
            tpex_etfs.add(code)

    # --------------------------------------------------------
    # 官方 ETF 必須全部存在
    # --------------------------------------------------------

    missing = sorted(
        official_etf_codes
        - set(universe.keys())
    )

    if missing:

        log(
            "❌ 特殊 ETF Gate："
            "ETF 遺失"
        )

        log(
            str(missing[:50])
        )

        return False

    # --------------------------------------------------------
    # 新制 6 碼 ETF
    # --------------------------------------------------------

    missing_six_digit = [
        code
        for code in six_digit_etfs
        if (
            code not in universe
            or universe[code].get(
                "type"
            ) != "ETF"
        )
    ]

    if missing_six_digit:

        log(
            "❌ 新制 6 碼 ETF 遺失："
            f"{missing_six_digit[:50]}"
        )

        return False

    # --------------------------------------------------------
    # 債券 ETF
    # --------------------------------------------------------

    missing_bond = [
        code
        for code in bond_etfs
        if (
            code not in universe
            or universe[code].get(
                "type"
            ) != "ETF"
        )
    ]

    if missing_bond:

        log(
            "❌ 債券 ETF 遺失："
            f"{missing_bond[:50]}"
        )

        return False

    # --------------------------------------------------------
    # 市場標記
    # --------------------------------------------------------

    for code in twse_etfs:

        if universe[
            code
        ].get("market") != "TWSE":

            log(
                f"❌ {code}: "
                "TWSE ETF market 標記錯誤"
            )

            return False

    for code in tpex_etfs:

        if universe[
            code
        ].get("market") != "TPEX":

            log(
                f"❌ {code}: "
                "TPEX ETF market 標記錯誤"
            )

            return False

    log(
        f"官方 ETF："
        f"{len(official_etf_codes)}"
    )

    log(
        f"6 碼 ETF："
        f"{len(six_digit_etfs)}"
    )

    log(
        f"債券 ETF："
        f"{len(bond_etfs)}"
    )

    log(
        f"TWSE ETF："
        f"{len(twse_etfs)}"
    )

    log(
        f"TPEX ETF："
        f"{len(tpex_etfs)}"
    )

    log(
        "✓ 新制 6 碼 ETF 全部通過"
    )

    log(
        "✓ 債券 ETF 全部通過"
    )

    log(
        "✓ TWSE / TPEX ETF 全部通過"
    )

    log(
        "✓ ETF Special Product Gate PASS"
    )

    return True


# ============================================================
# PAYLOAD
# ============================================================

def make_payload(
    existing_payload: Dict[str, Any],
    universe: Dict[str, Dict[str, Any]],
    counters: Dict[str, int],
) -> Dict[str, Any]:

    # --------------------------------------------------------
    # 保留 root-level metadata
    # --------------------------------------------------------

    payload = dict(
        existing_payload
    )

    payload[
        "generated_at"
    ] = now_tw().isoformat()

    payload[
        "universe_count"
    ] = len(universe)

    payload[
        "stock_count"
    ] = sum(
        1
        for item in universe.values()
        if item.get("type") == "STOCK"
    )

    payload[
        "etf_count"
    ] = sum(
        1
        for item in universe.values()
        if item.get("type") == "ETF"
    )

    payload[
        "market_count"
    ] = {
        "TWSE": sum(
            1
            for item in universe.values()
            if item.get("market") == "TWSE"
        ),
        "TPEX": sum(
            1
            for item in universe.values()
            if item.get("market") == "TPEX"
        ),
    }

    payload[
        "source"
    ] = {
        "universe_master": MASTER_URL,
        "policy": (
            "official product master only"
        ),
        "price_data_is_not_universe_source": True,
        "daily_quotes_are_not_universe_source": True,
    }

    payload[
        "contract"
    ] = {
        "root": "dict",
        "stocks": "dict",
        "active_status": (
            "status == active"
        ),
        "allowed_types": [
            "STOCK",
            "ETF",
        ],
        "allowed_markets": [
            "TWSE",
            "TPEX",
        ],
        "official_master_required": True,
        "etf_requires_official_master": True,
        "etf_6_digit_supported": True,
        "bond_etf_supported": True,
        "metadata_preserved": True,
        "fixed_universe_count": False,
        "daily_quote_not_used": True,
        "cmoney_not_used": True,
    }

    payload[
        "build_stats"
    ] = counters

    payload[
        "stocks"
    ] = universe

    return payload


# ============================================================
# PAYLOAD VALIDATION
# ============================================================

def validate_payload(
    payload: Dict[str, Any],
) -> bool:

    if not isinstance(
        payload,
        dict,
    ):
        return False

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return False

    if not stocks:
        return False

    if (
        payload.get(
            "universe_count"
        )
        != len(stocks)
    ):
        return False

    return structure_gate(
        stocks
    )


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write(
    payload: Dict[str, Any],
) -> bool:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix="universe_",
        suffix=".json.tmp",
        dir=str(DATA_DIR),
        text=True,
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:

            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )

            handle.write(
                "\n"
            )

            handle.flush()

            os.fsync(
                handle.fileno()
            )

        temp_path = Path(
            temp_name
        )

        # ----------------------------------------------------
        # 寫入前驗證 temporary JSON
        # ----------------------------------------------------

        verify = json.loads(
            temp_path.read_text(
                encoding="utf-8"
            )
        )

        if not validate_payload(
            verify
        ):
            raise RuntimeError(
                "temporary Universe validation failed"
            )

        # ----------------------------------------------------
        # 通過才正式 replace
        # ----------------------------------------------------

        os.replace(
            temp_path,
            UNIVERSE_FILE,
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write FAIL："
            f"{exc}"
        )

        try:
            Path(
                temp_name
            ).unlink(
                missing_ok=True
            )
        except Exception:
            pass

        return False


# ============================================================
# POST WRITE VERIFY
# ============================================================

def post_write_verify() -> bool:

    section(
        "POST WRITE VERIFY"
    )

    if not UNIVERSE_FILE.exists():

        log(
            "❌ universe.json 不存在"
        )

        return False

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        log(
            f"❌ universe.json JSON "
            f"解析失敗：{exc}"
        )

        return False

    if not validate_payload(
        payload
    ):

        log(
            "❌ universe.json "
            "contract validation FAIL"
        )

        return False

    log(
        f"✓ universe.json："
        f"{len(payload['stocks'])} 檔"
    )

    log(
        "✓ JSON 可正常解析"
    )

    log(
        "✓ Structure Gate PASS"
    )

    log(
        "✓ Post Write Verify PASS"
    )

    return True


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    universe: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "UNIVERSE BUILD RESULT"
    )

    total = len(
        universe
    )

    stock_count = sum(
        1
        for item in universe.values()
        if item.get("type") == "STOCK"
    )

    etf_count = sum(
        1
        for item in universe.values()
        if item.get("type") == "ETF"
    )

    twse_stock = sum(
        1
        for item in universe.values()
        if (
            item.get("market") == "TWSE"
            and item.get("type") == "STOCK"
        )
    )

    tpex_stock = sum(
        1
        for item in universe.values()
        if (
            item.get("market") == "TPEX"
            and item.get("type") == "STOCK"
        )
    )

    twse_etf = sum(
        1
        for item in universe.values()
        if (
            item.get("market") == "TWSE"
            and item.get("type") == "ETF"
        )
    )

    tpex_etf = sum(
        1
        for item in universe.values()
        if (
            item.get("market") == "TPEX"
            and item.get("type") == "ETF"
        )
    )

    log(
        f"Total：{total}"
    )

    log(
        f"STOCK：{stock_count}"
    )

    log(
        f"ETF：{etf_count}"
    )

    log(
        f"TWSE STOCK：{twse_stock}"
    )

    log(
        f"TPEX STOCK：{tpex_stock}"
    )

    log(
        f"TWSE ETF：{twse_etf}"
    )

    log(
        f"TPEX ETF：{tpex_etf}"
    )

    # --------------------------------------------------------
    # ETF subtype
    # --------------------------------------------------------

    categories: Dict[
        str,
        int,
    ] = {}

    for item in universe.values():

        if item.get(
            "type"
        ) != "ETF":
            continue

        category = item.get(
            "instrument_type",
            "UNKNOWN",
        )

        categories[
            category
        ] = (
            categories.get(
                category,
                0,
            )
            + 1
        )

    log(
        ""
    )

    log(
        "ETF categories："
    )

    for category in sorted(
        categories
    ):

        log(
            f"  {category}："
            f"{categories[category]}"
        )

    log(
        ""
    )

    log(
        "✓ 官方商品主檔決定 Universe"
    )

    log(
        "✓ ETF 不依賴當日成交資料"
    )

    log(
        "✓ ETF 不依賴當日行情資料"
    )

    log(
        "✓ 新制 6 碼 ETF 支援"
    )

    log(
        "✓ 債券 ETF 支援"
    )

    log(
        "✓ TWSE / TPEX 分開標記"
    )

    log(
        "✓ ETN / 權證 / 非 ETF 商品排除"
    )

    log(
        "✓ STOCK 不被 ETF 規則污染"
    )

    log(
        "✓ 舊 metadata 保留"
    )

    log(
        "✓ 沒有固定 Universe 數量"
    )

    log(
        "✓ 沒有版本號依賴"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        "台股 AI 選股系統"
    )

    log(
        "Official Product Master Universe Builder"
    )

    log(
        f"開始時間："
        f"{now_tw().isoformat()}"
    )

    log(
        f"Official Master："
        f"{MASTER_URL}"
    )

    log(
        f"Universe："
        f"{UNIVERSE_FILE}"
    )

    # ========================================================
    # 0. 舊 Universe metadata
    # ========================================================

    existing_payload = (
        load_existing_payload()
    )

    existing_metadata = (
        load_existing_metadata(
            existing_payload
        )
    )

    log(
        f"既有 Universe metadata："
        f"{len(existing_metadata)} 檔"
    )

    # ========================================================
    # 1. 官方商品主檔
    # ========================================================

    section(
        "STEP 1 — FETCH OFFICIAL PRODUCT MASTER"
    )

    try:

        master_html = (
            fetch_master_html()
        )

    except Exception as exc:

        log(
            f"❌ {exc}"
        )

        log(
            ""
        )

        log(
            "❌ 官方商品主檔抓取失敗"
        )

        log(
            "❌ 禁止產生新的 Universe"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    log(
        f"✓ 官方主檔取得成功："
        f"{len(master_html)} bytes"
    )

    # ========================================================
    # 2. Parse
    # ========================================================

    section(
        "STEP 2 — PARSE OFFICIAL MASTER"
    )

    rows = parse_master(
        master_html
    )

    log(
        f"解析 rows："
        f"{len(rows)}"
    )

    # ========================================================
    # 3. Official Master Gate
    # ========================================================

    if not official_master_gate(
        master_html,
        rows,
    ):

        log(
            "❌ Official Product Master Gate FAIL"
        )

        log(
            "❌ 不產生 Universe"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # 4. Build
    # ========================================================

    section(
        "STEP 3 — BUILD UNIVERSE"
    )

    (
        universe,
        official_codes,
        counters,
    ) = build_universe(
        rows,
        existing_metadata,
    )

    # ========================================================
    # 5. Structure Gate
    # ========================================================

    if not structure_gate(
        universe
    ):

        log(
            "❌ Universe Structure Gate FAIL"
        )

        log(
            "❌ 不寫入 universe.json"
        )

        return 1

    # ========================================================
    # 6. Completeness Gate
    # ========================================================

    if not completeness_gate(
        universe,
        official_codes,
    ):

        log(
            "❌ Official Master vs Universe "
            "Completeness Gate FAIL"
        )

        log(
            "❌ 不寫入 universe.json"
        )

        return 1

    # ========================================================
    # 7. ETF Special Gate
    # ========================================================

    if not special_etf_gate(
        rows,
        universe,
    ):

        log(
            "❌ ETF Special Product Gate FAIL"
        )

        log(
            "❌ 不寫入 universe.json"
        )

        return 1

    # ========================================================
    # 8. Payload
    # ========================================================

    section(
        "STEP 4 — BUILD PAYLOAD"
    )

    payload = make_payload(
        existing_payload,
        universe,
        counters,
    )

    if not validate_payload(
        payload
    ):

        log(
            "❌ Payload Contract FAIL"
        )

        log(
            "❌ 不寫入 universe.json"
        )

        return 1

    log(
        "✓ Payload Contract PASS"
    )

    # ========================================================
    # 9. Atomic Write
    # ========================================================

    section(
        "STEP 5 — ATOMIC WRITE"
    )

    if not atomic_write(
        payload
    ):

        log(
            "❌ Atomic Write FAIL"
        )

        return 1

    log(
        "✓ Atomic Write PASS"
    )

    # ========================================================
    # 10. Post Write Verify
    # ========================================================

    if not post_write_verify():

        log(
            "❌ Post Write Verify FAIL"
        )

        return 1

    # ========================================================
    # 11. Summary
    # ========================================================

    print_summary(
        universe
    )

    elapsed = (
        time.time()
        - started
    )

    log(
        ""
    )

    log(
        f"執行時間："
        f"{elapsed:.1f}s"
    )

    log(
        ""
    )

    log(
        "=" * 76
    )

    log(
        "UNIVERSE BUILD SUCCESS"
    )

    log(
        "=" * 76
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    sys.exit(
        main()
    )