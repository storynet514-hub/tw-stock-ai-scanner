#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

OFFICIAL PRODUCT MASTER UNIVERSE BUILDER
============================================================

Universe 架構
------------------------------------------------------------

官方商品主檔
    ↓
商品結構解析
    ↓
STOCK / ETF 分類
    ↓
排除 ETN / 權證 / REIT / TDR / 一般債券等
    ↓
官方終止上市 / 終止上櫃狀態驗證
    ↓
目前有效商品
    ↓
status == active
    ↓
Data/universe.json


核心契約
------------------------------------------------------------

1. 官方商品主檔是 Universe 的主要來源
2. 不使用 Yahoo 建立 Universe
3. 不使用價格資料建立 Universe
4. 不使用成交量建立 Universe
5. 不使用 CMoney
6. 支援 TWSE
7. 支援 TPEX
8. 支援 4 / 5 / 6 碼商品
9. 支援 6 碼 ETF
10. 支援債券 ETF
11. 排除 ETN
12. 排除權證
13. 排除 REIT
14. 排除 TDR
15. 排除一般債券 / 公司債
16. STOCK / ETF 分流
17. 官方終止上市商品不得進入 active Universe
18. 官方終止上櫃商品不得進入 active Universe
19. 官方主檔抓取失敗 => FAIL
20. 官方主檔解析失敗 => FAIL
21. 官方狀態驗證失敗 => FAIL
22. Universe schema validation 失敗 => FAIL
23. FAIL 時絕不覆蓋既有 universe.json
24. Atomic Write
25. 寫入後重新讀取並驗證
26. 不寫死 Universe 數量
27. 不依賴每日價格資料確認商品是否存在
28. 不因歷史不足而刪除仍有效商品
29. 00838B 這類已終止商品必須在 Universe 階段被排除

資料責任
------------------------------------------------------------

Universe:
    build_universe.py

價格:
    fetch_prices.py

籌碼:
    fetch_chip.py

分析:
    analyze_stocks.py

UI:
    build_ui_data.py
    ↓
    ui_data.json
    ↓
    index.html
============================================================
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
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# OFFICIAL SOURCES
# ============================================================

MASTER_URL = (
    "https://isin.twse.com.tw/isin/e_single_main.jsp"
)

TWSE_DELISTED_URL = (
    "https://www.twse.com.tw/"
    "company/suspendListingCsvAndHtml"
)

TPEX_DELISTED_URL = (
    "https://www.tpex.org.tw/"
    "zh-tw/mainboard/listed/delisted.html"
)


# ============================================================
# HTTP
# ============================================================

TIMEOUT = 45
RETRIES = 4
RETRY_SLEEP = 2.0

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "*/*;q=0.8"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
    }
)


# ============================================================
# CONTRACT
# ============================================================

ALLOWED_MARKETS = {
    "TWSE",
    "TPEX",
}

ALLOWED_TYPES = {
    "STOCK",
    "ETF",
}

ACTIVE_STATUS = "active"


# ============================================================
# EXCLUSION RULES
# ============================================================

EXCLUDED_TYPE_WORDS = (
    "權證",
    "認購權證",
    "認售權證",
    "牛證",
    "熊證",
    "ETN",
    "指數投資證券",
    "海外存託憑證",
    "TDR",
    "特別股",
    "REIT",
    "不動產投資信託",
    "受益證券",
    "一般債券",
    "公司債",
    "政府債券",
    "金融債",
    "可轉換公司債",
)


ETF_WORDS = (
    "ETF",
    "指數股票型基金",
    "交換交易基金",
)


STOCK_WORDS = (
    "股票",
    "普通股",
)


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
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(
            ZoneInfo("Asia/Taipei")
        )

    except Exception:
        return datetime.now()


# ============================================================
# TEXT
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = html.unescape(
        str(value)
    )

    text = (
        text
        .replace("\xa0", " ")
        .replace("\u3000", " ")
        .replace("\ufeff", "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def normalize_key(value: Any) -> str:
    text = clean_text(
        value
    ).lower()

    return re.sub(
        r"[\s_\-\/\\\(\)（）:：.．]+",
        "",
        text,
    )


def clean_code(value: Any) -> str:
    text = clean_text(
        value
    ).upper()

    text = (
        text
        .replace(".TW", "")
        .replace(".TWO", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    return text


def is_valid_symbol(value: Any) -> bool:
    code = clean_code(
        value
    )

    return bool(
        re.fullmatch(
            r"[0-9]{4,6}[A-Z]?",
            code,
        )
    )


# ============================================================
# HTML TABLE PARSER
# ============================================================

class TableParser(HTMLParser):

    def __init__(self) -> None:

        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[
            List[str]
        ] = []

        self._row: Optional[
            List[str]
        ] = None

        self._cell: Optional[
            List[str]
        ] = None

    def handle_starttag(
        self,
        tag: str,
        attrs: List[
            Tuple[
                str,
                Optional[str]
            ]
        ],
    ) -> None:

        tag = tag.lower()

        if tag == "tr":

            self._row = []

        elif tag in {
            "td",
            "th",
        }:

            if self._row is not None:
                self._cell = []

        elif tag == "br":

            if self._cell is not None:
                self._cell.append(
                    " "
                )

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if tag in {
            "td",
            "th",
        }:

            if (
                self._row is not None
                and self._cell is not None
            ):

                self._row.append(
                    clean_text(
                        "".join(
                            self._cell
                        )
                    )
                )

            self._cell = None

        elif tag == "tr":

            if self._row:

                self.rows.append(
                    self._row
                )

            self._row = None
            self._cell = None

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self._cell is not None:
            self._cell.append(
                data
            )


# ============================================================
# ENCODING
# ============================================================

def unique_encodings(
    values: Iterable[str],
) -> List[str]:

    aliases = {
        "big-5": "big5",
        "big5-hkscs": "big5",
        "950": "cp950",
        "ms950": "cp950",
        "windows-950": "cp950",
        "utf8": "utf-8",
    }

    result: List[str] = []
    seen = set()

    for value in values:

        value = clean_text(
            value
        ).lower()

        if not value:
            continue

        value = aliases.get(
            value,
            value,
        )

        if value not in seen:

            seen.add(value)

            result.append(
                value
            )

    return result


def extract_meta_charset(
    content: bytes,
) -> List[str]:

    result: List[str] = []

    head = content[
        :20000
    ]

    patterns = (
        rb"<meta[^>]+charset\s*=\s*"
        rb"[\"']?\s*"
        rb"([a-zA-Z0-9._-]+)",

        rb"<meta[^>]+content\s*=\s*"
        rb"[\"'][^\"']*charset\s*=\s*"
        rb"([a-zA-Z0-9._-]+)",
    )

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            head,
            flags=re.IGNORECASE,
        ):

            try:

                encoding = (
                    match.group(1)
                    .decode(
                        "ascii",
                        errors="ignore",
                    )
                )

                if encoding:
                    result.append(
                        encoding
                    )

            except Exception:
                pass

    return result


def score_html(
    text: str,
) -> int:

    if not text:
        return -999999

    score = 0
    lower = text.lower()

    if "<html" in lower:
        score += 10

    if "<body" in lower:
        score += 5

    if "<table" in lower:
        score += 30

    if "<tr" in lower:
        score += 15

    if "<td" in lower:
        score += 15

    markers = (
        "證券代號",
        "證券名稱",
        "市場別",
        "有價證券",
        "證券種類",
        "上市日",
        "isin",
    )

    for marker in markers:

        if marker.lower() in lower:
            score += 15

    codes = re.findall(
        r"(?<![0-9A-Z])"
        r"[0-9]{4,6}[A-Z]?"
        r"(?![0-9A-Z])",
        text.upper(),
    )

    if len(codes) >= 10:
        score += 30

    if len(codes) >= 100:
        score += 30

    replacements = text.count(
        "\ufffd"
    )

    if replacements:
        score -= min(
            replacements,
            100,
        )

    return score


def decode_html(
    content: bytes,
    response: requests.Response,
) -> Tuple[str, str]:

    candidates: List[str] = []

    if content.startswith(
        b"\xef\xbb\xbf"
    ):
        candidates.append(
            "utf-8-sig"
        )

    if content.startswith(
        b"\xff\xfe"
    ):
        candidates.append(
            "utf-16"
        )

    if content.startswith(
        b"\xfe\xff"
    ):
        candidates.append(
            "utf-16-be"
        )

    content_type = response.headers.get(
        "Content-Type",
        "",
    )

    match = re.search(
        r"charset\s*=\s*"
        r"['\"]?\s*"
        r"([^;'\"\s]+)",
        content_type,
        flags=re.IGNORECASE,
    )

    if match:
        candidates.append(
            match.group(1)
        )

    candidates.extend(
        extract_meta_charset(
            content
        )
    )

    apparent = getattr(
        response,
        "apparent_encoding",
        None,
    )

    if apparent:
        candidates.append(
            apparent
        )

    candidates.extend(
        [
            "utf-8",
            "big5",
            "cp950",
        ]
    )

    encodings = unique_encodings(
        candidates
    )

    best_text = ""
    best_encoding = ""
    best_score = -999999

    for encoding in encodings:

        try:

            decoded = content.decode(
                encoding,
                errors="replace",
            )

            score = score_html(
                decoded
            )

            log(
                f"    encoding={encoding:<12} "
                f"score={score:<5} "
                f"length={len(decoded):,}"
            )

            if score > best_score:

                best_score = score
                best_text = decoded
                best_encoding = encoding

        except (
            LookupError,
            UnicodeDecodeError,
        ):
            continue

    if not best_text:
        raise RuntimeError(
            "官方資料無法解碼"
        )

    if best_score < 40:
        raise RuntimeError(
            "官方資料 HTML 結構驗證失敗："
            f"score={best_score}"
        )

    return (
        best_text,
        best_encoding,
    )


# ============================================================
# HTTP HTML
# ============================================================

def fetch_html(
    url: str,
    label: str,
) -> str:

    last_error = ""

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        try:

            log(
                f"→ {label} "
                f"{attempt}/{RETRIES}"
            )

            response = SESSION.get(
                url,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            log(
                f"    HTTP "
                f"{response.status_code}"
            )

            response.raise_for_status()

            content = response.content

            if not content:
                raise RuntimeError(
                    "response body 為空"
                )

            log(
                f"    Content-Type: "
                f"{response.headers.get('Content-Type', '')}"
            )

            log(
                f"    Bytes: "
                f"{len(content):,}"
            )

            text, encoding = (
                decode_html(
                    content,
                    response,
                )
            )

            log(
                f"    Selected encoding: "
                f"{encoding}"
            )

            return text

        except Exception as exc:

            last_error = str(exc)

            log(
                f"⚠️ {label}失敗："
                f"{last_error}"
            )

            if attempt < RETRIES:

                time.sleep(
                    RETRY_SLEEP
                    * attempt
                )

    raise RuntimeError(
        f"{label}失敗："
        f"{last_error}"
    )


# ============================================================
# MASTER STRUCTURE
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


def split_code_name(
    value: str,
) -> Tuple[str, str]:

    text = clean_text(
        value
    )

    match = re.match(
        r"^\s*"
        r"([0-9]{4,6}[A-Z]?)"
        r"\s+"
        r"(.+?)"
        r"\s*$",
        text,
    )

    if match:

        code = clean_code(
            match.group(1)
        )

        name = clean_text(
            match.group(2)
        )

        if is_valid_symbol(
            code
        ):

            return (
                code,
                name,
            )

    return (
        "",
        "",
    )


def get_field(
    row: Dict[str, str],
    aliases: Iterable[str],
) -> str:

    normalized = {
        normalize_key(key): clean_text(
            value
        )
        for key, value in row.items()
    }

    for alias in aliases:

        key = normalize_key(
            alias
        )

        value = normalized.get(
            key,
            "",
        )

        if value:
            return value

    return ""


def extract_code(
    row: Dict[str, str],
) -> str:

    value = get_field(
        row,
        CODE_FIELDS,
    )

    code = clean_code(
        value
    )

    if is_valid_symbol(
        code
    ):
        return code

    code2, _ = split_code_name(
        value
    )

    if code2:
        return code2

    match = re.search(
        r"(?<![0-9A-Z])"
        r"([0-9]{4,6}[A-Z]?)"
        r"(?![0-9A-Z])",
        value.upper(),
    )

    if match:

        code = clean_code(
            match.group(1)
        )

        if is_valid_symbol(
            code
        ):
            return code

    return ""


def extract_name(
    row: Dict[str, str],
    code: str,
) -> str:

    value = get_field(
        row,
        NAME_FIELDS,
    )

    if value:

        match = re.match(
            r"^\s*"
            r"[0-9]{4,6}[A-Z]?"
            r"\s+",
            value,
        )

        if match:
            value = value[
                match.end():
            ]

        return clean_text(
            value
        )

    return ""


def normalize_market(
    value: str,
) -> str:

    text = clean_text(
        value
    )

    if not text:
        return ""

    if (
        "上市" in text
        and "上櫃" not in text
    ):
        return "TWSE"

    if "上櫃" in text:
        return "TPEX"

    upper = text.upper()

    if "TWSE" in upper:
        return "TWSE"

    if "TPEX" in upper:
        return "TPEX"

    return ""


def is_excluded_type(
    value: str,
) -> bool:

    text = clean_text(
        value
    )

    if not text:
        return False

    upper = text.upper()

    for word in EXCLUDED_TYPE_WORDS:

        if word.upper() in upper:
            return True

    return False


def normalize_instrument_type(
    row: Dict[str, str],
    name: str,
) -> str:

    type_value = get_field(
        row,
        TYPE_FIELDS,
    )

    combined = (
        clean_text(type_value)
        + " "
        + clean_text(name)
    )

    if is_excluded_type(
        combined
    ):
        return ""

    upper = combined.upper()

    for word in ETF_WORDS:

        if word.upper() in upper:
            return "ETF"

    for word in STOCK_WORDS:

        if word in combined:
            return "STOCK"

    return ""


def normalize_listed_date(
    value: str,
) -> str:

    text = clean_text(
        value
    )

    if not text:
        return ""

    match = re.search(
        r"(\d{4})[/-]"
        r"(\d{1,2})[/-]"
        r"(\d{1,2})",
        text,
    )

    if match:

        return (
            f"{int(match.group(1)):04d}/"
            f"{int(match.group(2)):02d}/"
            f"{int(match.group(3)):02d}"
        )

    return text


def classify_etf(
    name: str,
) -> str:

    text = clean_text(
        name
    ).upper()

    active_words = (
        "ACTIVE",
        "主動",
        "主動式",
    )

    if any(
        word in text
        for word in active_words
    ):
        return "ACTIVE"

    bond_words = (
        "BOND",
        "債",
        "TREASURY",
        "CORPORATE",
        "GOVERNMENT",
        "HIGH YIELD",
        "INVESTMENT GRADE",
        "公債",
        "公司債",
        "金融債",
    )

    if any(
        word in text
        for word in bond_words
    ):
        return "BOND"

    commodity_words = (
        "GOLD",
        "COMMODITY",
        "黃金",
        "原物料",
        "商品",
    )

    if any(
        word in text
        for word in commodity_words
    ):
        return "COMMODITY"

    return "ETF"


def classify_category(
    instrument_type: str,
    subtype: str,
) -> str:

    if instrument_type == "STOCK":
        return "STOCK"

    if subtype == "ACTIVE":
        return "ACTIVE_EQUITY"

    if subtype == "BOND":
        return "BOND_ETF"

    if subtype == "COMMODITY":
        return "COMMODITY_ETF"

    return "ETF"


def rows_to_records(
    rows: List[List[str]],
) -> List[Dict[str, str]]:

    if not rows:
        return []

    header_index = None
    headers: List[str] = []

    for index, row in enumerate(
        rows
    ):

        normalized = {
            normalize_key(value)
            for value in row
        }

        has_code = bool(
            normalized
            & {
                "securitycode",
                "securitiescode",
                "證券代號",
                "有價證券代號",
                "有價證券代號及名稱",
            }
        )

        has_name = bool(
            normalized
            & {
                "securityname",
                "securitiesname",
                "證券名稱",
                "證券簡稱",
                "有價證券名稱",
                "有價證券代號及名稱",
            }
        )

        has_market = bool(
            normalized
            & {
                "market",
                "市場別",
                "市場",
            }
        )

        if (
            has_code
            and (
                has_name
                or has_market
            )
        ):

            header_index = index

            headers = [
                clean_text(value)
                for value in row
            ]

            break

    if header_index is None:
        return []

    result: List[
        Dict[str, str]
    ] = []

    for row in rows[
        header_index + 1:
    ]:

        if not row:
            continue

        record: Dict[
            str,
            str
        ] = {}

        for index, value in enumerate(
            row
        ):

            if index >= len(
                headers
            ):
                break

            key = clean_text(
                headers[index]
            )

            value = clean_text(
                value
            )

            if key:
                record[key] = value

        if record:
            result.append(
                record
            )

    return result


def parse_master(
    text: str,
) -> List[Dict[str, str]]:

    parser = TableParser()

    parser.feed(
        text
    )

    log(
        f"→ HTML table rows："
        f"{len(parser.rows):,}"
    )

    records = rows_to_records(
        parser.rows
    )

    valid = 0

    for record in records:

        if extract_code(
            record
        ):
            valid += 1

    if valid < 20:

        raise RuntimeError(
            "官方商品主檔解析後有效商品不足："
            f"{valid}"
        )

    return records


# ============================================================
# OFFICIAL TERMINATION LIST
# ============================================================

def extract_symbols_from_text(
    text: str,
) -> set[str]:

    result: set[str] = set()

    # 4~6 碼數字 + 可選英文尾碼
    candidates = re.findall(
        r"(?<![0-9A-Z])"
        r"([0-9]{4,6}[A-Z]?)"
        r"(?![0-9A-Z])",
        text.upper(),
    )

    for code in candidates:

        code = clean_code(
            code
        )

        if is_valid_symbol(
            code
        ):
            result.add(
                code
            )

    return result


def fetch_twse_terminated_symbols() -> set[str]:

    text = fetch_html(
        TWSE_DELISTED_URL
        + "?lang=zh"
        + "&startYear="
        + "&type=html",
        "TWSE 終止上市名單",
    )

    parser = TableParser()

    parser.feed(
        text
    )

    symbols: set[str] = set()

    for row in parser.rows:

        joined = " ".join(
            row
        )

        if (
            "終止上市日期" not in joined
            and "上市編號" not in joined
        ):

            codes = extract_symbols_from_text(
                joined
            )

            symbols.update(
                codes
            )

    if not symbols:

        raise RuntimeError(
            "TWSE 終止上市名單解析後為 0"
        )

    log(
        f"✓ TWSE 終止上市商品："
        f"{len(symbols):,}"
    )

    return symbols


def fetch_tpex_terminated_symbols() -> set[str]:

    text = fetch_html(
        TPEX_DELISTED_URL,
        "TPEX 終止上櫃名單",
    )

    parser = TableParser()

    parser.feed(
        text
    )

    symbols: set[str] = set()

    for row in parser.rows:

        joined = " ".join(
            row
        )

        codes = extract_symbols_from_text(
            joined
        )

        symbols.update(
            codes
        )

    if not symbols:

        # TPEX 頁面如果不是標準 table，
        # 再從完整 HTML 搜尋代號。
        symbols = (
            extract_symbols_from_text(
                text
            )
        )

    if not symbols:

        raise RuntimeError(
            "TPEX 終止上櫃名單解析後為 0"
        )

    log(
        f"✓ TPEX 終止上櫃商品："
        f"{len(symbols):,}"
    )

    return symbols


def fetch_terminated_symbols() -> set[str]:

    section(
        "OFFICIAL STATUS — TERMINATED SECURITIES"
    )

    twse = (
        fetch_twse_terminated_symbols()
    )

    tpex = (
        fetch_tpex_terminated_symbols()
    )

    result = twse | tpex

    log(
        f"✓ 官方終止商品合計："
        f"{len(result):,}"
    )

    return result


# ============================================================
# BUILD ITEMS
# ============================================================

def build_items(
    records: List[
        Dict[str, str]
    ],
    terminated: set[str],
) -> Dict[
    str,
    Dict[str, Any]
]:

    items: Dict[
        str,
        Dict[str, Any]
    ] = {}

    stats = {
        "records": 0,
        "valid_code": 0,
        "valid_name": 0,
        "valid_market": 0,
        "stock": 0,
        "etf": 0,
        "excluded": 0,
        "unknown_type": 0,
        "terminated": 0,
        "duplicate": 0,
    }

    for row in records:

        stats["records"] += 1

        code = extract_code(
            row
        )

        if not code:
            continue

        stats["valid_code"] += 1

        # ----------------------------------------------------
        # 最重要 Gate
        #
        # 商品曾經存在 ≠ 現在仍然有效
        # ----------------------------------------------------

        if code in terminated:

            stats["terminated"] += 1

            continue

        name = extract_name(
            row,
            code,
        )

        if not name:
            continue

        stats["valid_name"] += 1

        market = normalize_market(
            get_field(
                row,
                MARKET_FIELDS,
            )
        )

        if market not in ALLOWED_MARKETS:
            continue

        stats["valid_market"] += 1

        type_value = get_field(
            row,
            TYPE_FIELDS,
        )

        if is_excluded_type(
            type_value
        ):
            stats["excluded"] += 1
            continue

        instrument_type = (
            normalize_instrument_type(
                row,
                name,
            )
        )

        if instrument_type not in ALLOWED_TYPES:

            stats["unknown_type"] += 1

            continue

        if instrument_type == "STOCK":
            stats["stock"] += 1
        else:
            stats["etf"] += 1

        listed_date = (
            normalize_listed_date(
                get_field(
                    row,
                    DATE_FIELDS,
                )
            )
        )

        if instrument_type == "ETF":

            subtype = classify_etf(
                name
            )

        else:

            subtype = "COMMON"

        category = classify_category(
            instrument_type,
            subtype,
        )

        suffix = (
            "TW"
            if market == "TWSE"
            else "TWO"
        )

        item = {
            "symbol": code,
            "full_symbol": (
                f"{code}.{suffix}"
            ),
            "name": name,
            "market": market,
            "type": instrument_type,
            "instrument_type": subtype,
            "status": ACTIVE_STATUS,
            "listed_date": listed_date,
            "cfi_code": clean_text(
                get_field(
                    row,
                    CFI_FIELDS,
                )
            ),
            "category": category,
        }

        if code in items:

            stats["duplicate"] += 1

            old_score = sum(
                1
                for value in items[
                    code
                ].values()
                if value
            )

            new_score = sum(
                1
                for value in item.values()
                if value
            )

            if new_score > old_score:
                items[code] = item

        else:

            items[code] = item

    log("")
    log(
        "UNIVERSE BUILD STATISTICS"
    )
    log(
        f"  Records："
        f"{stats['records']:,}"
    )
    log(
        f"  Valid code："
        f"{stats['valid_code']:,}"
    )
    log(
        f"  Valid name："
        f"{stats['valid_name']:,}"
    )
    log(
        f"  Valid market："
        f"{stats['valid_market']:,}"
    )
    log(
        f"  STOCK："
        f"{stats['stock']:,}"
    )
    log(
        f"  ETF："
        f"{stats['etf']:,}"
    )
    log(
        f"  Excluded："
        f"{stats['excluded']:,}"
    )
    log(
        f"  Unknown type："
        f"{stats['unknown_type']:,}"
    )
    log(
        f"  Terminated removed："
        f"{stats['terminated']:,}"
    )
    log(
        f"  Duplicate："
        f"{stats['duplicate']:,}"
    )

    return dict(
        sorted(
            items.items(),
            key=lambda x: x[0],
        )
    )


# ============================================================
# VALIDATION
# ============================================================

def validate_item(
    key: str,
    item: Any,
) -> List[str]:

    errors: List[str] = []

    if not isinstance(
        item,
        dict,
    ):

        return [
            f"{key}: item 不是 object"
        ]

    required = {
        "symbol",
        "full_symbol",
        "name",
        "market",
        "type",
        "instrument_type",
        "status",
        "listed_date",
        "cfi_code",
        "category",
    }

    missing = (
        required
        - set(item.keys())
    )

    if missing:

        errors.append(
            f"{key}: 缺少欄位 "
            f"{sorted(missing)}"
        )

    if item.get(
        "symbol"
    ) != key:

        errors.append(
            f"{key}: symbol != key"
        )

    if not is_valid_symbol(
        item.get(
            "symbol",
            "",
        )
    ):

        errors.append(
            f"{key}: symbol 格式錯誤"
        )

    market = item.get(
        "market"
    )

    if market not in ALLOWED_MARKETS:

        errors.append(
            f"{key}: market 不合法"
        )

    expected_suffix = (
        ".TW"
        if market == "TWSE"
        else ".TWO"
        if market == "TPEX"
        else ""
    )

    expected_full = (
        f"{key}{expected_suffix}"
    )

    if item.get(
        "full_symbol"
    ) != expected_full:

        errors.append(
            f"{key}: full_symbol 錯誤"
        )

    if not item.get(
        "name"
    ):

        errors.append(
            f"{key}: name 為空"
        )

    if item.get(
        "type"
    ) not in ALLOWED_TYPES:

        errors.append(
            f"{key}: type 不合法"
        )

    if item.get(
        "status"
    ) != ACTIVE_STATUS:

        errors.append(
            f"{key}: status != active"
        )

    return errors


def validate_items(
    items: Dict[
        str,
        Dict[str, Any]
    ],
) -> None:

    errors: List[str] = []

    for key, item in items.items():

        errors.extend(
            validate_item(
                key,
                item,
            )
        )

    if not items:

        errors.append(
            "Universe 為 0"
        )

    if errors:

        log("")
        log(
            "❌ UNIVERSE VALIDATION FAILED"
        )

        for error in errors[
            :50
        ]:

            log(
                f"  - {error}"
            )

        raise RuntimeError(
            "Universe validation failed："
            f"{len(errors)} errors"
        )


def validate_universe(
    data: Dict[str, Any],
) -> None:

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "Universe root 必須是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "Universe stocks 必須是 dict"
        )

    if data.get(
        "universe_count"
    ) != len(stocks):

        raise RuntimeError(
            "universe_count 不一致："
            f"{data.get('universe_count')} "
            f"!= {len(stocks)}"
        )

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get("type")
        == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get("type")
        == "ETF"
    )

    if data.get(
        "stock_count"
    ) != stock_count:

        raise RuntimeError(
            "stock_count 不一致"
        )

    if data.get(
        "etf_count"
    ) != etf_count:

        raise RuntimeError(
            "etf_count 不一致"
        )

    market_count = {
        "TWSE": sum(
            1
            for item in stocks.values()
            if item.get("market")
            == "TWSE"
        ),
        "TPEX": sum(
            1
            for item in stocks.values()
            if item.get("market")
            == "TPEX"
        ),
    }

    if data.get(
        "market_count"
    ) != market_count:

        raise RuntimeError(
            "market_count 不一致"
        )

    validate_items(
        stocks
    )


# ============================================================
# EXISTING UNIVERSE
# ============================================================

def load_existing_universe() -> Optional[
    Dict[str, Any]
]:

    if not UNIVERSE_FILE.exists():
        return None

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as file:

            data = json.load(
                file
            )

        if isinstance(
            data,
            dict,
        ):

            return data

    except Exception as exc:

        log(
            f"⚠️ 舊 universe.json "
            f"讀取失敗：{exc}"
        )

    return None


# ============================================================
# BUILD DOCUMENT
# ============================================================

def build_document(
    items: Dict[
        str,
        Dict[str, Any]
    ],
    existing: Optional[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    stock_count = sum(
        1
        for item in items.values()
        if item.get("type")
        == "STOCK"
    )

    etf_count = sum(
        1
        for item in items.values()
        if item.get("type")
        == "ETF"
    )

    market_count = {
        "TWSE": sum(
            1
            for item in items.values()
            if item.get("market")
            == "TWSE"
        ),
        "TPEX": sum(
            1
            for item in items.values()
            if item.get("market")
            == "TPEX"
        ),
    }

    document = {
        "version": "UNIVERSE-BUILD",

        "generated_at": (
            now_tw().isoformat()
        ),

        "universe_count": len(
            items
        ),

        "stock_count": stock_count,

        "etf_count": etf_count,

        "market_count": market_count,

        "source": {
            "universe_master": MASTER_URL,
            "twse_terminated": (
                TWSE_DELISTED_URL
            ),
            "tpex_terminated": (
                TPEX_DELISTED_URL
            ),
            "policy": (
                "official product master "
                "plus official termination "
                "status validation"
            ),
            "price_data_is_not_universe_source": True,
            "daily_quotes_are_not_universe_source": True,
            "yahoo_is_not_universe_source": True,
            "cmoney_is_not_universe_source": True,
        },

        "contract": {
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
            "official_termination_check": True,
            "etf_6_digit_supported": True,
            "bond_etf_supported": True,
            "fixed_universe_count": False,
            "daily_quote_not_used": True,
            "yahoo_not_used": True,
            "cmoney_not_used": True,
        },

        "stocks": items,
    }

    if isinstance(
        existing,
        dict,
    ):

        for key in (
            "notes",
            "description",
        ):

            if key in existing:

                document[key] = (
                    existing[key]
                )

    return document


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write_json(
    path: Path,
    data: Dict[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )

    temp_path = Path(
        temp_name
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write(
                "\n"
            )

            file.flush()

            os.fsync(
                file.fileno()
            )

        os.replace(
            temp_path,
            path,
        )

    except Exception:

        try:

            temp_path.unlink(
                missing_ok=True
            )

        except Exception:
            pass

        raise


# ============================================================
# POST WRITE
# ============================================================

def validate_written_file() -> None:

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "寫入後 universe.json 不存在"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8-sig",
    ) as file:

        data = json.load(
            file
        )

    validate_universe(
        data
    )


# ============================================================
# SAMPLE CHECK
# ============================================================

def print_summary(
    document: Dict[str, Any],
) -> None:

    stocks = document[
        "stocks"
    ]

    log("")
    log("=" * 76)
    log("UNIVERSE BUILD SUMMARY")
    log("=" * 76)

    log(
        f"Universe："
        f"{len(stocks):,}"
    )

    log(
        f"STOCK："
        f"{document['stock_count']:,}"
    )

    log(
        f"ETF："
        f"{document['etf_count']:,}"
    )

    log(
        f"TWSE："
        f"{document['market_count']['TWSE']:,}"
    )

    log(
        f"TPEX："
        f"{document['market_count']['TPEX']:,}"
    )

    sample_codes = (
        "00400A",
        "00401A",
        "00402A",
        "00403A",
        "00404A",
        "00838B",
        "2330",
        "2337",
        "2426",
        "6643",
        "6743",
        "6670",
        "2615",
        "3441",
        "3229",
        "6588",
        "4977",
        "1583",
    )

    log("")
    log("SAMPLE CHECK")

    for code in sample_codes:

        item = stocks.get(
            code
        )

        if item:

            log(
                f"  ✓ {code:<7} "
                f"{item['type']:<5} "
                f"{item['market']:<4} "
                f"{item['name']}"
            )

        else:

            log(
                f"  - {code:<7} "
                f"not in active Universe"
            )

    log("=" * 76)


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    section(
        "台股 AI 選股系統"
    )

    log(
        "Official Product Master "
        "Universe Builder"
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

    existing = (
        load_existing_universe()
    )

    if existing:

        log(
            "既有 Universe metadata："
            f"{existing.get('universe_count', 0)} 檔"
        )

    else:

        log(
            "既有 Universe：不存在"
        )

    # ========================================================
    # STEP 1
    # ========================================================

    section(
        "STEP 1 — FETCH OFFICIAL PRODUCT MASTER"
    )

    try:

        master_html = fetch_html(
            MASTER_URL,
            "官方商品主檔",
        )

    except Exception as exc:

        log("")
        log(
            f"❌ 官方商品主檔抓取失敗："
            f"{exc}"
        )

        log(
            "❌ 禁止產生新的 Universe"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # STEP 2
    # ========================================================

    section(
        "STEP 2 — PARSE OFFICIAL PRODUCT MASTER"
    )

    try:

        records = parse_master(
            master_html
        )

        log(
            f"✓ 官方主檔 records："
            f"{len(records):,}"
        )

    except Exception as exc:

        log("")
        log(
            f"❌ 官方商品主檔解析失敗："
            f"{exc}"
        )

        log(
            "❌ 禁止產生新的 Universe"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # STEP 3
    # ========================================================

    section(
        "STEP 3 — FETCH OFFICIAL TERMINATION STATUS"
    )

    try:

        terminated = (
            fetch_terminated_symbols()
        )

    except Exception as exc:

        log("")
        log(
            f"❌ 官方終止狀態資料取得失敗："
            f"{exc}"
        )

        log(
            "❌ 禁止產生新的 Universe"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # STEP 4
    # ========================================================

    section(
        "STEP 4 — BUILD ACTIVE UNIVERSE"
    )

    try:

        items = build_items(
            records,
            terminated,
        )

        log("")
        log(
            f"→ Candidate Universe："
            f"{len(items):,}"
        )

        # 不設定固定 Universe 數量，
        # 但防止官方 endpoint 回錯誤頁面。
        if len(items) < 100:

            raise RuntimeError(
                "有效 Universe 少於 100 檔，"
                "疑似官方資料異常"
            )

        # ----------------------------------------------------
        # 明確確認已知終止商品不能存在
        # ----------------------------------------------------

        known_terminated = (
            "00838B",
        )

        for code in known_terminated:

            if code in items:

                raise RuntimeError(
                    f"{code} 已列入官方終止商品，"
                    "卻仍存在 active Universe"
                )

        validate_items(
            items
        )

    except Exception as exc:

        log("")
        log(
            f"❌ Universe build failed："
            f"{exc}"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # STEP 5
    # ========================================================

    section(
        "STEP 5 — BUILD UNIVERSE DOCUMENT"
    )

    try:

        document = build_document(
            items,
            existing,
        )

        validate_universe(
            document
        )

        log(
            "✓ Universe document "
            "validation PASS"
        )

    except Exception as exc:

        log("")
        log(
            f"❌ Universe validation failed："
            f"{exc}"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # STEP 6
    # ========================================================

    section(
        "STEP 6 — ATOMIC WRITE"
    )

    try:

        atomic_write_json(
            UNIVERSE_FILE,
            document,
        )

        log(
            f"✓ Atomic write："
            f"{UNIVERSE_FILE}"
        )

    except Exception as exc:

        log("")
        log(
            f"❌ Universe write failed："
            f"{exc}"
        )

        return 1

    # ========================================================
    # STEP 7
    # ========================================================

    section(
        "STEP 7 — POST-WRITE VALIDATION"
    )

    try:

        validate_written_file()

        log(
            "✓ Written universe.json "
            "validation PASS"
        )

    except Exception as exc:

        log("")
        log(
            f"❌ Post-write validation failed："
            f"{exc}"
        )

        return 1

    # ========================================================
    # SUMMARY
    # ========================================================

    print_summary(
        document
    )

    log("")
    log(
        f"完成時間："
        f"{now_tw().isoformat()}"
    )

    log("")
    log(
        "✅ BUILD UNIVERSE PASS"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        log("")
        log(
            "❌ 使用者中止"
        )

        sys.exit(130)

    except Exception as exc:

        log("")
        log(
            f"❌ 未預期錯誤："
            f"{exc}"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        sys.exit(1)
