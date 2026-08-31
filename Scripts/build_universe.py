#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

OFFICIAL PRODUCT MASTER UNIVERSE BUILDER
============================================================

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 輸出
2. 官方商品主檔是 Universe 的主要來源
3. 不使用 Yahoo 建立 Universe
4. 不使用價格資料建立 Universe
5. 不使用成交量建立 Universe
6. 不使用 CMoney
7. 支援 TWSE
8. 支援 TPEX
9. 支援 4 / 5 / 6 碼商品
10. 支援字母尾碼，例如 00631L / 00632R / 00400A
11. 支援 6 碼 ETF
12. 支援債券 ETF
13. 排除 ETN
14. 排除權證
15. 排除 REIT
16. 排除 TDR
17. 排除一般債券 / 公司債
18. 官方終止商品不得進 active Universe
19. 官方主檔 HTTP 200 但 payload 無效 => FAIL
20. 官方主檔解析失敗 => FAIL
21. 官方終止狀態資料不足 => FAIL
22. Universe schema validation 失敗 => FAIL
23. FAIL 絕不覆蓋既有 universe.json
24. Atomic Write
25. 寫入後重新讀取驗證
26. 不寫死 Universe 數量
27. 不因歷史價格不足刪除商品
28. terminated 不得進 candidate
29. active ∩ terminated 必須為空
30. 00838B 不得進 active Universe
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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlencode

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

# 官方 ISIN 商品主檔
MASTER_URL = (
    "https://isin.twse.com.tw/isin/e_single_main.jsp"
)

# TWSE 終止上市公司
TWSE_DELISTED_COMPANY_URL = (
    "https://www.twse.com.tw/"
    "company/suspendListingCsvAndHtml"
    "?lang=zh&startYear=&type=html"
)

# TWSE ETF e添富：
# 新上市／終止上市公告
TWSE_ETF_LISTING_URL = (
    "https://www.twse.com.tw/"
    "zh/ETFortune/announcementList"
)

# TPEx 終止上櫃公司
TPEX_DELISTED_COMPANY_URL = (
    "https://www.tpex.org.tw/"
    "zh-tw/mainboard/listed/delisted.html"
)

# TPEx 市場公告查詢
TPEX_MARKET_ANNOUNCEMENT_URL = (
    "https://www.tpex.org.tw/"
    "zh-tw/service/pi/announce/market/retro.html"
)


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = 60
RETRIES = 5
RETRY_SLEEP = 2.0

# 官方商品主檔正常內容不應該只有幾百 bytes
MASTER_MIN_BYTES = 10_000

# 正常主檔至少應該出現大量代碼
MASTER_MIN_SYMBOLS = 100

# ETF listing announcement 分頁
TWSE_ETF_PAGE_SIZE = 10
TWSE_ETF_MAX_PAGES = 40


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
        "Referer": (
            "https://isin.twse.com.tw/isin/"
        ),
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
# EXCLUSIONS
# ============================================================

EXCLUDED_TYPE_WORDS = (
    "權證",
    "認購權證",
    "認售權證",
    "牛證",
    "熊證",
    "WARRANT",
    "CALL WARRANT",
    "PUT WARRANT",
    "ETN",
    "指數投資證券",
    "INDEX INVESTMENT SECURITIES",
    "海外存託憑證",
    "TDR",
    "GLOBAL DEPOSITARY",
    "DEPOSITARY RECEIPT",
    "特別股",
    "PREFERRED STOCK",
    "PREFERRED SHARE",
    "REIT",
    "不動產投資信託",
    "REAL ESTATE INVESTMENT TRUST",
    "一般債券",
    "公司債",
    "政府債券",
    "金融債",
    "可轉換公司債",
    "CORPORATE BOND",
    "GOVERNMENT BOND",
    "FINANCIAL BOND",
    "CONVERTIBLE BOND",
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

    text = html.unescape(str(value))

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


def normalize_text(value: Any) -> str:
    text = clean_text(value)

    return (
        text
        .upper()
        .replace(" ", "")
        .replace("\u3000", "")
    )


def normalize_key(value: Any) -> str:
    text = normalize_text(value)

    return re.sub(
        r"[^A-Z0-9\u4e00-\u9fff]+",
        "",
        text,
    )


# ============================================================
# SYMBOL
# ============================================================

def clean_code(value: Any) -> str:
    text = clean_text(value).upper()

    text = (
        text
        .replace(".TW", "")
        .replace(".TWO", "")
        .replace(".TPEX", "")
        .replace(".TWSE", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    return text


def is_valid_symbol(value: Any) -> bool:
    code = clean_code(value)

    return bool(
        re.fullmatch(
            r"[0-9]{4,6}[A-Z]?",
            code,
        )
    )


def extract_symbol_candidates(text: Any) -> Set[str]:
    value = clean_text(text).upper()

    result: Set[str] = set()

    for match in re.findall(
        r"(?<![0-9A-Z])"
        r"[0-9]{4,6}[A-Z]?"
        r"(?![0-9A-Z])",
        value,
    ):
        code = clean_code(match)

        if is_valid_symbol(code):
            result.add(code)

    return result


# ============================================================
# MARKET
# ============================================================

def normalize_market(value: Any) -> Optional[str]:
    text = normalize_text(value)

    if not text:
        return None

    # 先檢查 TPEX，避免未來文字同時包含上市/上櫃
    tpex_markers = (
        "TPEX",
        "TPEX LISTED",
        "TPEXLISTED",
        "TPEx",
        "上櫃",
        "上櫃股票",
        "上櫃證券",
        "上櫃有價證券",
        "OTC",
    )

    for marker in tpex_markers:
        if normalize_text(marker) in text:
            return "TPEX"

    twse_markers = (
        "TWSE",
        "TWSE LISTED",
        "TWSELISTED",
        "上市",
        "上市股票",
        "上市證券",
        "上市有價證券",
    )

    for marker in twse_markers:
        if normalize_text(marker) in text:
            return "TWSE"

    return None


# ============================================================
# TYPE
# ============================================================

def is_excluded_type(value: Any) -> bool:
    text = normalize_text(value)

    if not text:
        return False

    for word in EXCLUDED_TYPE_WORDS:
        if normalize_text(word) in text:
            return True

    return False


def normalize_security_type(
    security_type: Any,
    name: Any,
    cfi_code: Any,
) -> Optional[str]:

    type_text = normalize_text(
        security_type
    )

    name_text = normalize_text(
        name
    )

    cfi_text = normalize_text(
        cfi_code
    )

    combined = (
        type_text
        + " "
        + name_text
    )

    if is_excluded_type(combined):
        return None

    # 官方主檔 ETF
    if (
        "ETF" in type_text
        or "ETF" in name_text
        or "指數股票型基金" in type_text
        or "交換交易基金" in type_text
    ):
        return "ETF"

    # 普通股票
    stock_markers = (
        "STOCK",
        "COMMONSTOCK",
        "COMMONSHARE",
        "股票",
        "普通股",
    )

    for marker in stock_markers:
        if normalize_text(marker) in combined:
            return "STOCK"

    # CFI ESVU* 常見普通股
    if cfi_text.startswith("ESVU"):
        return "STOCK"

    return None


# ============================================================
# HTML TABLE PARSER
# ============================================================

class TableParser(HTMLParser):

    def __init__(self) -> None:
        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

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

        elif tag in {"td", "th"}:
            if self._row is not None:
                self._cell = []

        elif tag == "br":
            if self._cell is not None:
                self._cell.append(" ")

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if tag in {"td", "th"}:

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
            self._cell.append(data)


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
    seen: Set[str] = set()

    for value in values:

        value = clean_text(value).lower()

        if not value:
            continue

        value = aliases.get(
            value,
            value,
        )

        if value not in seen:
            seen.add(value)
            result.append(value)

    return result


def extract_meta_charset(
    content: bytes,
) -> List[str]:

    result: List[str] = []

    head = content[:30000]

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

    content_type = response.headers.get(
        "Content-Type",
        "",
    )

    match = re.search(
        r"charset\s*=\s*"
        r"['\"]?\s*"
        r"([^;'\"]+)",
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

    best_text = ""
    best_encoding = ""
    best_score = -999999

    for encoding in unique_encodings(
        candidates
    ):

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

        except Exception:
            continue

    if not best_text:
        raise RuntimeError(
            "官方 HTML 無法解碼"
        )

    if best_score < 0:
        raise RuntimeError(
            "官方 HTML 解碼結果無效"
        )

    return (
        best_text,
        best_encoding,
    )


def score_html(
    text: str,
) -> int:

    if not text:
        return -999999

    score = 0
    lower = text.lower()

    if "<html" in lower:
        score += 10

    if "<table" in lower:
        score += 30

    if "<tr" in lower:
        score += 15

    if "<td" in lower:
        score += 15

    markers = (
        "security code",
        "security name",
        "market",
        "type of security",
        "cficode",
        "isin code",
        "證券代號",
        "證券名稱",
        "市場別",
        "證券種類",
        "CFICode",
    )

    for marker in markers:
        if marker.lower() in lower:
            score += 15

    symbols = extract_symbol_candidates(
        text
    )

    if len(symbols) >= 10:
        score += 30

    if len(symbols) >= 100:
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


# ============================================================
# MASTER PAYLOAD VALIDATION
# ============================================================

def validate_master_payload(
    content: bytes,
    text: str,
) -> None:

    if len(content) < MASTER_MIN_BYTES:
        raise RuntimeError(
            "官方主檔 payload 過小："
            f"{len(content):,} bytes"
        )

    lower = text.lower()

    if "<table" not in lower:
        raise RuntimeError(
            "官方主檔 payload 沒有 table"
        )

    if "<tr" not in lower:
        raise RuntimeError(
            "官方主檔 payload 沒有 table rows"
        )

    required_markers = (
        "security code",
        "security name",
        "type of security",
    )

    marker_hits = sum(
        1
        for marker in required_markers
        if marker in lower
    )

    if marker_hits < 2:
        raise RuntimeError(
            "官方主檔 payload 缺少必要欄位"
        )

    symbols = extract_symbol_candidates(
        text
    )

    if len(symbols) < MASTER_MIN_SYMBOLS:
        raise RuntimeError(
            "官方主檔 payload 有效商品代號不足："
            f"{len(symbols)}"
        )


# ============================================================
# MASTER HTTP
# ============================================================

def master_request_url() -> str:
    params = {
        "_": str(
            int(
                time.time() * 1000
            )
        )
    }

    return (
        MASTER_URL
        + "?"
        + urlencode(params)
    )


def fetch_official_master() -> str:

    section(
        "STEP 1 — FETCH OFFICIAL PRODUCT MASTER"
    )

    last_error: Optional[
        Exception
    ] = None

    # 先 warm-up 官方 ISIN host
    try:
        SESSION.get(
            "https://isin.twse.com.tw/isin/",
            timeout=REQUEST_TIMEOUT,
        )
    except Exception:
        pass

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        url = master_request_url()

        try:

            log(
                f"→ 官方商品主檔 "
                f"{attempt}/{RETRIES}"
            )

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            log(
                f"    HTTP "
                f"{response.status_code}"
            )

            log(
                "    Content-Type: "
                + response.headers.get(
                    "Content-Type",
                    "",
                )
            )

            content = response.content

            log(
                f"    Bytes: "
                f"{len(content):,}"
            )

            response.raise_for_status()

            text, encoding = decode_html(
                content,
                response,
            )

            validate_master_payload(
                content,
                text,
            )

            log(
                f"    Selected encoding: "
                f"{encoding}"
            )

            log(
                "✓ 官方商品主檔 payload "
                "完整性驗證 PASS"
            )

            return text

        except Exception as exc:

            last_error = exc

            log(
                f"⚠️ 官方主檔第 "
                f"{attempt}/{RETRIES} 次失敗："
                f"{exc}"
            )

            if attempt < RETRIES:
                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        "官方商品主檔無法取得有效 payload："
        f"{last_error}"
    )


# ============================================================
# HEADER
# ============================================================

def header_index(
    row: List[str],
    candidates: Iterable[str],
) -> Optional[int]:

    normalized = [
        normalize_key(x)
        for x in row
    ]

    wanted = [
        normalize_key(x)
        for x in candidates
    ]

    for item in wanted:

        if item in normalized:
            return normalized.index(
                item
            )

    return None


def find_master_header(
    rows: List[List[str]],
) -> Tuple[int, Dict[str, int]]:

    for index, row in enumerate(
        rows
    ):

        if len(row) < 5:
            continue

        code_idx = header_index(
            row,
            (
                "Security Code",
                "證券代號",
                "有價證券代號",
            ),
        )

        name_idx = header_index(
            row,
            (
                "Security Name",
                "證券名稱",
                "有價證券名稱",
            ),
        )

        market_idx = header_index(
            row,
            (
                "Market",
                "市場別",
            ),
        )

        type_idx = header_index(
            row,
            (
                "Type of security",
                "Type of Security",
                "證券種類",
                "有價證券種類",
            ),
        )

        if (
            code_idx is None
            or name_idx is None
            or market_idx is None
            or type_idx is None
        ):
            continue

        listed_idx = header_index(
            row,
            (
                "Date Stock Listed",
                "上市日",
                "上櫃日",
                "上市日期",
                "掛牌日",
            ),
        )

        cfi_idx = header_index(
            row,
            (
                "CFICode",
                "CFI Code",
                "CFI代碼",
            ),
        )

        isin_idx = header_index(
            row,
            (
                "ISIN Code",
                "ISIN",
                "國際證券辨識號碼",
            ),
        )

        remarks_idx = header_index(
            row,
            (
                "Remarks",
                "備註",
            ),
        )

        return (
            index,
            {
                "code": code_idx,
                "name": name_idx,
                "market": market_idx,
                "type": type_idx,
                "listed": (
                    listed_idx
                    if listed_idx is not None
                    else -1
                ),
                "cfi": (
                    cfi_idx
                    if cfi_idx is not None
                    else -1
                ),
                "isin": (
                    isin_idx
                    if isin_idx is not None
                    else -1
                ),
                "remarks": (
                    remarks_idx
                    if remarks_idx is not None
                    else -1
                ),
            },
        )

    raise RuntimeError(
        "官方商品主檔找不到有效欄位標題"
    )


def row_value(
    row: List[str],
    index: int,
) -> str:

    if index < 0:
        return ""

    if index >= len(row):
        return ""

    return row[index]


# ============================================================
# PARSE MASTER
# ============================================================

def parse_official_master(
    html_text: str,
) -> List[Dict[str, Any]]:

    section(
        "STEP 2 — PARSE OFFICIAL PRODUCT MASTER"
    )

    parser = TableParser()

    parser.feed(
        html_text
    )

    rows = parser.rows

    log(
        f"→ HTML table rows："
        f"{len(rows):,}"
    )

    if not rows:
        raise RuntimeError(
            "官方主檔沒有任何 table rows"
        )

    header_row_index, columns = (
        find_master_header(rows)
    )

    log(
        f"→ Master header row："
        f"{header_row_index}"
    )

    log(
        "→ Columns："
        + ", ".join(
            f"{key}={value}"
            for key, value
            in columns.items()
            if value >= 0
        )
    )

    records: List[
        Dict[str, Any]
    ] = []

    for row in rows[
        header_row_index + 1:
    ]:

        code = clean_code(
            row_value(
                row,
                columns["code"],
            )
        )

        if not is_valid_symbol(code):
            continue

        name = clean_text(
            row_value(
                row,
                columns["name"],
            )
        )

        if not name:
            continue

        market_raw = clean_text(
            row_value(
                row,
                columns["market"],
            )
        )

        type_raw = clean_text(
            row_value(
                row,
                columns["type"],
            )
        )

        listed_date = clean_text(
            row_value(
                row,
                columns["listed"],
            )
        )

        cfi_code = clean_text(
            row_value(
                row,
                columns["cfi"],
            )
        )

        isin_code = clean_text(
            row_value(
                row,
                columns["isin"],
            )
        )

        remarks = clean_text(
            row_value(
                row,
                columns["remarks"],
            )
        )

        market = normalize_market(
            market_raw
        )

        security_type = (
            normalize_security_type(
                type_raw,
                name,
                cfi_code,
            )
        )

        records.append(
            {
                "symbol": code,
                "name": name,
                "market": market,
                "market_raw": market_raw,
                "type": security_type,
                "type_raw": type_raw,
                "listed_date": listed_date,
                "cfi_code": cfi_code,
                "isin_code": isin_code,
                "remarks": remarks,
            }
        )

    if not records:
        raise RuntimeError(
            "官方主檔解析後沒有有效 records"
        )

    log(
        f"✓ 官方主檔 records："
        f"{len(records):,}"
    )

    return records


# ============================================================
# GENERIC SYMBOL EXTRACTION
# ============================================================

def extract_symbols_from_rows(
    rows: List[List[str]],
) -> Set[str]:

    symbols: Set[str] = set()

    for row in rows:

        for cell in row:

            symbols.update(
                extract_symbol_candidates(
                    cell
                )
            )

    return symbols


def parse_symbols_from_html(
    text: str,
) -> Set[str]:

    parser = TableParser()

    parser.feed(text)

    symbols = extract_symbols_from_rows(
        parser.rows
    )

    if not symbols:
        symbols.update(
            extract_symbol_candidates(
                text
            )
        )

    return symbols


# ============================================================
# GENERIC OFFICIAL PAGE
# ============================================================

def fetch_official_html(
    url: str,
) -> Tuple[str, requests.Response]:

    last_error: Optional[
        Exception
    ] = None

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            text, encoding = decode_html(
                response.content,
                response,
            )

            log(
                f"    encoding={encoding}"
            )

            return (
                text,
                response,
            )

        except Exception as exc:

            last_error = exc

            log(
                f"⚠️ 官方資料第 "
                f"{attempt}/{RETRIES} 次失敗："
                f"{exc}"
            )

            if attempt < RETRIES:
                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        f"官方資料取得失敗："
        f"{last_error}"
    )


# ============================================================
# COMPANY DELISTED
# ============================================================

def fetch_company_delisted(
    url: str,
    label: str,
) -> Set[str]:

    log(
        f"→ {label}"
    )

    text, _ = fetch_official_html(
        url
    )

    symbols = parse_symbols_from_html(
        text
    )

    log(
        f"✓ {label}："
        f"{len(symbols):,}"
    )

    return symbols


# ============================================================
# TWSE ETF TERMINATION
# ============================================================

def fetch_twse_etf_terminated() -> Set[str]:

    log(
        "→ TWSE ETF 新上市／終止上市公告"
    )

    terminated: Set[str] = set()

    seen_pages: Set[int] = set()

    for page in range(
        TWSE_ETF_MAX_PAGES
    ):

        offset = (
            page
            * TWSE_ETF_PAGE_SIZE
        )

        if offset in seen_pages:
            break

        seen_pages.add(offset)

        params = {
            "max": TWSE_ETF_PAGE_SIZE,
            "offset": offset,
            "type": "listing",
        }

        url = (
            TWSE_ETF_LISTING_URL
            + "?"
            + urlencode(params)
        )

        try:

            text, _ = fetch_official_html(
                url
            )

        except Exception as exc:

            raise RuntimeError(
                "TWSE ETF 終止公告取得失敗："
                f"offset={offset} "
                f"{exc}"
            )

        parser = TableParser()

        parser.feed(text)

        page_text = clean_text(text)

        page_symbols = (
            extract_symbol_candidates(
                page_text
            )
        )

        # 只接受真正出現在 ETF 公告文字中的
        # 4~6 碼代號。
        #
        # 這裡不把所有數字視為代號。
        if page_symbols:
            terminated.update(
                page_symbols
            )

        # 官方 listing 分類目前是分頁資料。
        # 沒有公告內容代表已經到底。
        if (
            "沒有資料" in page_text
            or "查無資料" in page_text
            or not parser.rows
        ):
            break

        log(
            f"    ETF listing page "
            f"{page + 1}: "
            f"{len(page_symbols):,} codes"
        )

    log(
        f"✓ TWSE ETF termination "
        f"candidates："
        f"{len(terminated):,}"
    )

    return terminated


# ============================================================
# TPEX TERMINATION
# ============================================================

def fetch_tpex_terminated() -> Set[str]:

    section(
        "OFFICIAL STATUS — TPEX TERMINATED SECURITIES"
    )

    company_symbols = (
        fetch_company_delisted(
            TPEX_DELISTED_COMPANY_URL,
            "TPEX 終止上櫃公司",
        )
    )

    # TPEX 官方歷史市場公告頁
    #
    # 這個來源可能依網站當期查詢條件
    # 呈現不同內容，因此：
    # 1. 有資料就納入
    # 2. 若頁面沒有可解析的代號，
    #    不直接把整個 Universe 判定為 0
    #
    # 但 company termination 仍然是必要來源。
    announcement_symbols: Set[str] = set()

    try:

        text, _ = fetch_official_html(
            TPEX_MARKET_ANNOUNCEMENT_URL
        )

        announcement_symbols = (
            parse_symbols_from_html(
                text
            )
        )

        log(
            "✓ TPEX 市場公告可解析代號："
            f"{len(announcement_symbols):,}"
        )

    except Exception as exc:

        log(
            "⚠️ TPEX 市場公告頁目前無法解析："
            f"{exc}"
        )

        # 不把單一輔助頁故障直接當成
        # 官方 termination = 0。
        #
        # 真正 active Universe 還會經過
        # market/type/termination intersection
        # 驗證。

    result = (
        company_symbols
        | announcement_symbols
    )

    if not result:
        raise RuntimeError(
            "TPEX 官方終止狀態完全沒有資料"
        )

    log(
        f"✓ TPEX 官方終止候選合計："
        f"{len(result):,}"
    )

    return result


# ============================================================
# OFFICIAL TERMINATION STATUS
# ============================================================

def fetch_official_termination_status(
) -> Set[str]:

    section(
        "STEP 3 — FETCH OFFICIAL TERMINATION STATUS"
    )

    twse_company = (
        fetch_company_delisted(
            TWSE_DELISTED_COMPANY_URL,
            "TWSE 終止上市公司",
        )
    )

    twse_etf = (
        fetch_twse_etf_terminated()
    )

    tpex = (
        fetch_tpex_terminated()
    )

    terminated = (
        twse_company
        | twse_etf
        | tpex
    )

    if not terminated:
        raise RuntimeError(
            "官方終止商品名單為 0"
        )

    log(
        ""
    )

    log(
        "OFFICIAL TERMINATION SUMMARY"
    )

    log(
        f"  TWSE company："
        f"{len(twse_company):,}"
    )

    log(
        f"  TWSE ETF："
        f"{len(twse_etf):,}"
    )

    log(
        f"  TPEX："
        f"{len(tpex):,}"
    )

    log(
        f"  UNION："
        f"{len(terminated):,}"
    )

    return terminated


# ============================================================
# EXISTING METADATA
# ============================================================

def load_existing_metadata(
) -> Dict[str, Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():
        return {}

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as file:

            data = json.load(file)

    except Exception as exc:

        log(
            "⚠️ 既有 Universe metadata "
            f"無法讀取：{exc}"
        )

        return {}

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for key, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = clean_code(
            item.get(
                "symbol",
                key,
            )
        )

        if not is_valid_symbol(
            symbol
        ):
            continue

        result[symbol] = dict(
            item
        )

    return result


# ============================================================
# CLASSIFICATION
# ============================================================

def derive_instrument_metadata(
    record: Dict[str, Any],
    previous: Optional[
        Dict[str, Any]
    ],
) -> Tuple[
    str,
    Optional[str]
]:

    security_type = record[
        "type"
    ]

    name = normalize_text(
        record.get(
            "name"
        )
    )

    cfi = normalize_text(
        record.get(
            "cfi_code"
        )
    )

    if security_type == "STOCK":
        return (
            "STOCK",
            "COMMON_STOCK",
        )

    if security_type == "ETF":

        # 保留既有可靠分類
        if previous:

            previous_instrument = (
                clean_text(
                    previous.get(
                        "instrument_type"
                    )
                )
            )

            previous_category = (
                clean_text(
                    previous.get(
                        "category"
                    )
                )
            )

            if previous_instrument:
                return (
                    previous_instrument,
                    previous_category
                    or None,
                )

        if cfi.startswith(
            "CEOGD"
        ):
            return (
                "LEVERAGED",
                "LEVERAGED",
            )

        if (
            "BEAR" in name
            or "INVERSE" in name
            or "反" in name
        ):
            return (
                "INVERSE",
                "INVERSE",
            )

        if (
            "BOND" in name
            or "債" in name
            or cfi.startswith(
                "CEOIB"
            )
        ):
            return (
                "BOND",
                "BOND",
            )

        if (
            "ACTIVE" in name
            or "主動" in name
        ):
            return (
                "ACTIVE",
                "ACTIVE_EQUITY",
            )

        return (
            "EQUITY",
            "EQUITY",
        )

    return (
        "UNKNOWN",
        None,
    )


# ============================================================
# METADATA SCORE
# ============================================================

def metadata_score(
    item: Dict[str, Any],
) -> int:

    score = 0

    for key in (
        "symbol",
        "name",
        "market",
        "type",
        "listed_date",
        "cfi_code",
        "instrument_type",
        "category",
    ):

        value = item.get(
            key
        )

        if value not in {
            None,
            "",
        }:
            score += 1

    return score


# ============================================================
# BUILD ACTIVE UNIVERSE
# ============================================================

def build_active_universe(
    records: List[
        Dict[str, Any]
    ],
    terminated: Set[str],
    existing_metadata: Dict[
        str,
        Dict[str, Any]
    ],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, int]
]:

    section(
        "STEP 4 — BUILD ACTIVE UNIVERSE"
    )

    stats = {
        "records": len(records),
        "valid_code": 0,
        "valid_name": 0,
        "valid_market": 0,
        "stock": 0,
        "etf": 0,
        "excluded": 0,
        "unknown_type": 0,
        "terminated_removed": 0,
        "duplicate": 0,
    }

    candidates: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for record in records:

        symbol = clean_code(
            record.get(
                "symbol"
            )
        )

        if not is_valid_symbol(
            symbol
        ):
            continue

        stats[
            "valid_code"
        ] += 1

        name = clean_text(
            record.get(
                "name"
            )
        )

        if not name:
            continue

        stats[
            "valid_name"
        ] += 1

        market = record.get(
            "market"
        )

        if market not in ALLOWED_MARKETS:
            continue

        stats[
            "valid_market"
        ] += 1

        # ====================================================
        # HARD TERMINATION BLOCK
        # ====================================================

        if symbol in terminated:

            stats[
                "terminated_removed"
            ] += 1

            continue

        security_type = record.get(
            "type"
        )

        if security_type not in ALLOWED_TYPES:

            raw_combined = (
                clean_text(
                    record.get(
                        "type_raw"
                    )
                )
                + " "
                + name
            )

            if is_excluded_type(
                raw_combined
            ):
                stats[
                    "excluded"
                ] += 1
            else:
                stats[
                    "unknown_type"
                ] += 1

            continue

        if security_type == "STOCK":
            stats["stock"] += 1

        elif security_type == "ETF":
            stats["etf"] += 1

        # ====================================================
        # DUPLICATE
        # ====================================================

        if symbol in candidates:

            stats[
                "duplicate"
            ] += 1

            existing = candidates[
                symbol
            ]

            if (
                metadata_score(record)
                <=
                metadata_score(existing)
            ):
                continue

        previous = (
            existing_metadata.get(
                symbol
            )
        )

        instrument_type, category = (
            derive_instrument_metadata(
                record,
                previous,
            )
        )

        item: Dict[str, Any] = {
            "symbol": symbol,
            "full_symbol": (
                f"{symbol}."
                f"{'TW' if market == 'TWSE' else 'TWO'}"
            ),
            "name": name,
            "market": market,
            "type": security_type,
            "instrument_type": instrument_type,
            "status": ACTIVE_STATUS,
            "listed_date": clean_text(
                record.get(
                    "listed_date"
                )
            ),
            "cfi_code": clean_text(
                record.get(
                    "cfi_code"
                )
            ),
        }

        if category:
            item[
                "category"
            ] = category

        # 保留既有 metadata
        # 但官方核心欄位優先
        if previous:

            protected = {
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

            for key, value in previous.items():

                if key in protected:
                    continue

                if (
                    value is not None
                    and value != ""
                ):
                    item[
                        key
                    ] = value

        candidates[
            symbol
        ] = item

    # ========================================================
    # FINAL TERMINATION ASSERTION
    # ========================================================

    intersection = (
        set(candidates.keys())
        & terminated
    )

    if intersection:

        codes = sorted(
            intersection
        )

        raise RuntimeError(
            "Universe build failed："
            "terminated 商品仍存在 active Universe："
            + ", ".join(codes)
        )

    # ========================================================
    # REQUIRED SANITY
    # ========================================================

    if not candidates:
        raise RuntimeError(
            "Universe build failed："
            "Candidate Universe = 0"
        )

    stock_count = sum(
        1
        for item
        in candidates.values()
        if item.get("type")
        == "STOCK"
    )

    etf_count = sum(
        1
        for item
        in candidates.values()
        if item.get("type")
        == "ETF"
    )

    if stock_count == 0:
        raise RuntimeError(
            "Universe build failed："
            "STOCK = 0"
        )

    if etf_count == 0:
        raise RuntimeError(
            "Universe build failed："
            "ETF = 0"
        )

    # ========================================================
    # 00838B HARD ASSERTION
    # ========================================================

    if "00838B" in candidates:
        raise RuntimeError(
            "Universe build failed："
            "00838B 不得存在於 active Universe"
        )

    stats[
        "candidate"
    ] = len(candidates)

    stats[
        "stock_final"
    ] = stock_count

    stats[
        "etf_final"
    ] = etf_count

    return (
        candidates,
        stats,
    )


# ============================================================
# VALIDATION
# ============================================================

def validate_universe_schema(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
    terminated: Set[str],
) -> None:

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "Universe stocks 必須是 dict"
        )

    if not stocks:
        raise RuntimeError(
            "Universe stocks 不得為空"
        )

    market_count = {
        "TWSE": 0,
        "TPEX": 0,
    }

    type_count = {
        "STOCK": 0,
        "ETF": 0,
    }

    seen: Set[str] = set()

    for key, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            raise RuntimeError(
                f"{key} item 必須是 dict"
            )

        symbol = clean_code(
            item.get(
                "symbol"
            )
        )

        if key != symbol:
            raise RuntimeError(
                f"{key}: key/symbol mismatch"
            )

        if not is_valid_symbol(
            symbol
        ):
            raise RuntimeError(
                f"{symbol}: invalid symbol"
            )

        if symbol in seen:
            raise RuntimeError(
                f"{symbol}: duplicate"
            )

        seen.add(symbol)

        if item.get(
            "status"
        ) != ACTIVE_STATUS:
            raise RuntimeError(
                f"{symbol}: status != active"
            )

        market = item.get(
            "market"
        )

        if market not in ALLOWED_MARKETS:
            raise RuntimeError(
                f"{symbol}: invalid market"
            )

        record_type = item.get(
            "type"
        )

        if record_type not in ALLOWED_TYPES:
            raise RuntimeError(
                f"{symbol}: invalid type"
            )

        if symbol in terminated:
            raise RuntimeError(
                f"{symbol}: officially terminated"
            )

        market_count[
            market
        ] += 1

        type_count[
            record_type
        ] += 1

    if type_count[
        "STOCK"
    ] == 0:
        raise RuntimeError(
            "Universe validation："
            "STOCK = 0"
        )

    if type_count[
        "ETF"
    ] == 0:
        raise RuntimeError(
            "Universe validation："
            "ETF = 0"
        )

    if "00838B" in stocks:
        raise RuntimeError(
            "Universe validation："
            "00838B 不得存在"
        )

    intersection = (
        set(stocks.keys())
        & terminated
    )

    if intersection:
        raise RuntimeError(
            "Universe validation："
            "active ∩ terminated != 0："
            + ", ".join(
                sorted(intersection)
            )
        )

    log(
        "✓ Universe schema validation PASS"
    )

    log(
        f"  total："
        f"{len(stocks):,}"
    )

    log(
        f"  STOCK："
        f"{type_count['STOCK']:,}"
    )

    log(
        f"  ETF："
        f"{type_count['ETF']:,}"
    )

    log(
        f"  TWSE："
        f"{market_count['TWSE']:,}"
    )

    log(
        f"  TPEX："
        f"{market_count['TPEX']:,}"
    )


# ============================================================
# BUILD OUTPUT
# ============================================================

def build_output(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    market_count = {
        "TWSE": 0,
        "TPEX": 0,
    }

    stock_count = 0
    etf_count = 0

    for item in stocks.values():

        market = item[
            "market"
        ]

        record_type = item[
            "type"
        ]

        market_count[
            market
        ] += 1

        if record_type == "STOCK":
            stock_count += 1

        elif record_type == "ETF":
            etf_count += 1

    return {
        "version": "UNIVERSE-BUILD",
        "generated_at": (
            now_tw().isoformat()
        ),
        "universe_count": len(
            stocks
        ),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "market_count": market_count,
        "source": {
            "universe_master": MASTER_URL,
            "termination_twse_company": (
                TWSE_DELISTED_COMPANY_URL
            ),
            "termination_twse_etf": (
                TWSE_ETF_LISTING_URL
            ),
            "termination_tpex_company": (
                TPEX_DELISTED_COMPANY_URL
            ),
            "termination_tpex_market": (
                TPEX_MARKET_ANNOUNCEMENT_URL
            ),
            "policy": (
                "official product master only"
            ),
            "price_data_is_not_universe_source": True,
            "daily_quotes_are_not_universe_source": True,
            "yahoo_is_not_universe_source": True,
            "cmoney_not_used": True,
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
            "official_termination_required": True,
            "etf_requires_official_master": True,
            "etf_6_digit_supported": True,
            "bond_etf_supported": True,
            "metadata_preserved": True,
            "fixed_universe_count": False,
            "daily_quote_not_used": True,
            "cmoney_not_used": True,
            "terminated_hard_excluded": True,
        },
        "stocks": dict(
            sorted(
                stocks.items()
            )
        ),
    }


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write_json(
    path: Path,
    data: Any,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
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

    finally:

        if os.path.exists(
            temp_path
        ):
            os.unlink(
                temp_path
            )


# ============================================================
# FINAL WRITTEN VALIDATION
# ============================================================

def validate_written_universe(
    terminated: Set[str],
) -> None:

    section(
        "STEP 6 — FINAL WRITTEN UNIVERSE VALIDATION"
    )

    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            "寫入後找不到 universe.json"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8-sig",
    ) as file:

        data = json.load(
            file
        )

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            "Universe root 不是 dict"
        )

    stocks = data.get(
        "stocks"
    )

    validate_universe_schema(
        stocks,
        terminated,
    )

    declared_count = data.get(
        "universe_count"
    )

    actual_count = len(
        stocks
    )

    if declared_count != actual_count:
        raise RuntimeError(
            "universe_count mismatch："
            f"{declared_count} != "
            f"{actual_count}"
        )

    if data.get(
        "stock_count"
    ) != sum(
        1
        for x in stocks.values()
        if x.get("type") == "STOCK"
    ):
        raise RuntimeError(
            "stock_count mismatch"
        )

    if data.get(
        "etf_count"
    ) != sum(
        1
        for x in stocks.values()
        if x.get("type") == "ETF"
    ):
        raise RuntimeError(
            "etf_count mismatch"
        )

    log(
        "✓ 寫入後重新讀取驗證 PASS"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

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

    existing_metadata = (
        load_existing_metadata()
    )

    log(
        f"既有 Universe metadata："
        f"{len(existing_metadata):,} 檔"
    )

    # ========================================================
    # STEP 1
    # ========================================================

    try:

        master_html = (
            fetch_official_master()
        )

    except Exception as exc:

        section(
            "UNIVERSE BUILD FAILED"
        )

        log(
            "❌ 官方商品主檔抓取失敗："
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

    try:

        master_records = (
            parse_official_master(
                master_html
            )
        )

    except Exception as exc:

        section(
            "UNIVERSE BUILD FAILED"
        )

        log(
            "❌ 官方商品主檔解析失敗："
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

    try:

        terminated = (
            fetch_official_termination_status()
        )

    except Exception as exc:

        section(
            "UNIVERSE BUILD FAILED"
        )

        log(
            "❌ 官方終止狀態取得失敗："
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

    try:

        stocks, stats = (
            build_active_universe(
                master_records,
                terminated,
                existing_metadata,
            )
        )

    except Exception as exc:

        section(
            "UNIVERSE BUILD FAILED"
        )

        log(
            f"❌ {exc}"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # STATISTICS
    # ========================================================

    section(
        "UNIVERSE BUILD STATISTICS"
    )

    for key, label in (
        ("records", "Records"),
        ("valid_code", "Valid code"),
        ("valid_name", "Valid name"),
        ("valid_market", "Valid market"),
        ("stock", "STOCK parsed"),
        ("etf", "ETF parsed"),
        ("excluded", "Excluded"),
        ("unknown_type", "Unknown type"),
        (
            "terminated_removed",
            "Terminated removed",
        ),
        ("duplicate", "Duplicate"),
        ("candidate", "Candidate Universe"),
        ("stock_final", "STOCK"),
        ("etf_final", "ETF"),
    ):

        log(
            f"  {label}："
            f"{stats[key]:,}"
        )

    # ========================================================
    # STEP 5
    # ========================================================

    section(
        "STEP 5 — VALIDATE NEW UNIVERSE"
    )

    try:

        validate_universe_schema(
            stocks,
            terminated,
        )

        output = build_output(
            stocks
        )

    except Exception as exc:

        log(
            "❌ Universe validation failed："
            f"{exc}"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # ATOMIC WRITE
    # ========================================================

    try:

        atomic_write_json(
            UNIVERSE_FILE,
            output,
        )

    except Exception as exc:

        log(
            "❌ Universe atomic write failed："
            f"{exc}"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # STEP 6
    # ========================================================

    try:

        validate_written_universe(
            terminated
        )

    except Exception as exc:

        log(
            "❌ 寫入後驗證失敗："
            f"{exc}"
        )

        return 1

    # ========================================================
    # SUCCESS
    # ========================================================

    section(
        "UNIVERSE BUILD COMPLETED"
    )

    log(
        f"✓ Universe："
        f"{len(stocks):,}"
    )

    log(
        f"✓ STOCK："
        f"{stats['stock_final']:,}"
    )

    log(
        f"✓ ETF："
        f"{stats['etf_final']:,}"
    )

    log(
        f"✓ TWSE："
        f"{sum(1 for x in stocks.values() if x['market'] == 'TWSE'):,}"
    )

    log(
        f"✓ TPEX："
        f"{sum(1 for x in stocks.values() if x['market'] == 'TPEX'):,}"
    )

    log(
        "✓ 官方商品主檔 payload 驗證"
    )

    log(
        "✓ 官方終止商品硬排除"
    )

    log(
        "✓ 00838B 已阻斷"
    )

    log(
        "✓ active ∩ terminated = 0"
    )

    log(
        "✓ Atomic write"
    )

    log(
        "✓ Final validation PASS"
    )

    log(
        f"完成時間："
        f"{now_tw().isoformat()}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )