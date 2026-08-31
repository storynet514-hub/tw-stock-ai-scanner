#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

OFFICIAL PRODUCT MASTER UNIVERSE BUILDER
============================================================

資料責任
------------------------------------------------------------

Universe:
    官方商品主檔
        ↓
    官方商品結構解析
        ↓
    STOCK / ETF
        ↓
    TWSE / TPEX
        ↓
    官方終止狀態硬排除
        ↓
    active Universe
        ↓
    Data/universe.json

核心契約
------------------------------------------------------------

1. 官方商品主檔是 Universe 唯一主要來源
2. 不使用 Yahoo 建立 Universe
3. 不使用價格資料建立 Universe
4. 不使用成交量建立 Universe
5. 不使用 CMoney
6. 支援 TWSE
7. 支援 TPEX
8. 支援 4 / 5 / 6 碼商品
9. 支援字母尾碼商品，例如 00631L / 00632R / 00400A
10. 支援 6 碼 ETF
11. 支援債券 ETF
12. 排除 ETN
13. 排除權證
14. 排除 REIT
15. 排除 TDR
16. 排除一般債券 / 公司債
17. 官方終止上市商品不得進入 active Universe
18. 官方終止上櫃商品不得進入 active Universe
19. 官方主檔抓取失敗 => FAIL
20. 官方主檔解析失敗 => FAIL
21. 官方終止狀態抓取失敗 => FAIL
22. Universe schema validation 失敗 => FAIL
23. FAIL 時絕不覆蓋既有 universe.json
24. Atomic Write
25. 寫入後重新讀取並驗證
26. 不寫死 Universe 數量
27. 不因歷史價格不足刪除商品
28. 不讓 terminated 商品進 candidate
29. 最終必須驗證 candidate 與 terminated 完全沒有交集

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

REQUEST_TIMEOUT = 60
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
    "受益證券",
    "BENEFICIARY CERTIFICATE",
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


# ============================================================
# MARKET
# ============================================================

def normalize_market(value: Any) -> Optional[str]:

    text = normalize_text(value)

    if not text:
        return None

    twse_markers = (
        "TWSE",
        "上市",
        "上市股票",
        "上市證券",
        "上市有價證券",
    )

    tpex_markers = (
        "TPEX",
        "TPEX",
        "TPEx",
        "上櫃",
        "上櫃股票",
        "上櫃證券",
        "上櫃有價證券",
        "OTC",
    )

    for marker in twse_markers:
        if normalize_text(marker) in text:
            return "TWSE"

    for marker in tpex_markers:
        if normalize_text(marker) in text:
            return "TPEX"

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

    if is_excluded_type(
        combined
    ):
        return None

    # 官方主檔目前直接提供 ETF
    if (
        "ETF" in type_text
        or "ETF" in name_text
        or "指數股票型基金" in type_text
        or "交換交易基金" in type_text
    ):
        return "ETF"

    # 普通股
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

    # 官方 CFI：
    # ESVUFR 為普通股常見 CFI
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
    seen: Set[str] = set()

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
        :30000
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
        "證券代號",
        "證券名稱",
        "市場別",
        "證券種類",
        "CFICode",
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


# ============================================================
# HTTP
# ============================================================

def http_get(
    url: str,
) -> requests.Response:

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
            )

            response.raise_for_status()

            return response

        except Exception as exc:

            last_error = exc

            log(
                f"⚠️ HTTP 第 "
                f"{attempt}/{RETRIES} 次失敗："
                f"{exc}"
            )

            if attempt < RETRIES:

                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        f"HTTP 取得失敗：{last_error}"
    )


# ============================================================
# FETCH OFFICIAL MASTER
# ============================================================

def fetch_official_master() -> str:

    section(
        "STEP 1 — FETCH OFFICIAL PRODUCT MASTER"
    )

    last_error: Optional[
        Exception
    ] = None

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        try:

            log(
                f"→ 官方商品主檔 "
                f"{attempt}/{RETRIES}"
            )

            response = SESSION.get(
                MASTER_URL,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            content = response.content

            log(
                f"    HTTP {response.status_code}"
            )

            log(
                f"    Content-Type: "
                f"{response.headers.get('Content-Type', '')}"
            )

            log(
                f"    Bytes: "
                f"{len(content):,}"
            )

            text, encoding = decode_html(
                content,
                response,
            )

            log(
                f"    Selected encoding: "
                f"{encoding}"
            )

            return text

        except Exception as exc:

            last_error = exc

            log(
                f"⚠️ 官方主檔抓取第 "
                f"{attempt}/{RETRIES} 次失敗："
                f"{exc}"
            )

            if attempt < RETRIES:

                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        f"官方商品主檔抓取失敗："
        f"{last_error}"
    )


# ============================================================
# HEADER DETECTION
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
) -> Tuple[
    int,
    Dict[str, int]
]:

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
            code_idx is not None
            and name_idx is not None
            and market_idx is not None
            and type_idx is not None
        ):

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


# ============================================================
# PARSE OFFICIAL MASTER
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
        find_master_header(
            rows
        )
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

        if not is_valid_symbol(
            code
        ):
            continue

        name = clean_text(
            row_value(
                row,
                columns["name"],
            )
        )

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
# FETCH DELISTED
# ============================================================

def extract_symbols_from_rows(
    rows: List[List[str]],
) -> Set[str]:

    symbols: Set[str] = set()

    for row in rows:

        for cell in row:

            text = clean_code(
                cell
            )

            if is_valid_symbol(
                text
            ):

                symbols.add(
                    text
                )

            # 某些官方頁面會把代碼
            # 與其他文字放在同一欄
            matches = re.findall(
                r"(?<![0-9A-Z])"
                r"[0-9]{4,6}[A-Z]?"
                r"(?![0-9A-Z])",
                clean_text(cell).upper(),
            )

            for match in matches:

                if is_valid_symbol(
                    match
                ):

                    symbols.add(
                        clean_code(
                            match
                        )
                    )

    return symbols


def fetch_delisted_page(
    url: str,
    label: str,
) -> Set[str]:

    log(
        f"→ {label}"
    )

    response = http_get(
        url
    )

    text, encoding = decode_html(
        response.content,
        response,
    )

    log(
        f"    encoding={encoding}"
    )

    parser = TableParser()

    parser.feed(
        text
    )

    symbols = extract_symbols_from_rows(
        parser.rows
    )

    return symbols


def fetch_official_termination_status(
) -> Set[str]:

    section(
        "STEP 3 — FETCH OFFICIAL TERMINATION STATUS"
    )

    log("")
    log("=" * 76)
    log(
        "OFFICIAL STATUS — TERMINATED SECURITIES"
    )
    log("=" * 76)

    twse_symbols = fetch_delisted_page(
        TWSE_DELISTED_URL,
        "TWSE 終止上市名單",
    )

    log(
        f"✓ TWSE 終止上市商品："
        f"{len(twse_symbols):,}"
    )

    tpex_symbols = fetch_delisted_page(
        TPEX_DELISTED_URL,
        "TPEX 終止上櫃名單",
    )

    log(
        f"✓ TPEX 終止上櫃商品："
        f"{len(tpex_symbols):,}"
    )

    terminated = (
        twse_symbols
        | tpex_symbols
    )

    if not terminated:

        raise RuntimeError(
            "官方終止商品名單為 0，"
            "禁止繼續建立 Universe"
        )

    log(
        f"✓ 官方終止商品合計："
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
            f"⚠️ 既有 Universe "
            f"無法讀取 metadata：{exc}"
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
) -> Tuple[str, Optional[str]]:

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

        # 先保留既有可靠分類
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

        # 依 CFI / 名稱做最小必要分類
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

            if (
                record.get(
                    "type_raw"
                )
                or record.get(
                    "name"
                )
            ):

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

        if symbol in candidates:

            stats[
                "duplicate"
            ] += 1

            # 若同代碼重複，保留較完整資料
            existing = candidates[
                symbol
            ]

            existing_score = (
                metadata_score(
                    existing
                )
            )

            current_score = (
                metadata_score(
                    record
                )
            )

            if current_score <= existing_score:
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
            "instrument_type": (
                instrument_type
            ),
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

        # 保留既有 metadata，
        # 但官方核心欄位永遠優先
        if previous:

            for key, value in previous.items():

                if key in {
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
                }:
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
    # FINAL HARD TERMINATION ASSERTION
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
            + ", ".join(
                codes
            )
        )

    # ========================================================
    # REQUIRED SANITY CHECKS
    # ========================================================

    if not candidates:

        raise RuntimeError(
            "Universe build failed："
            "Candidate Universe = 0"
        )

    stock_count = sum(
        1
        for item in candidates.values()
        if item.get("type") == "STOCK"
    )

    etf_count = sum(
        1
        for item in candidates.values()
        if item.get("type") == "ETF"
    )

    if stock_count == 0:

        raise RuntimeError(
            "Universe build failed："
            "STOCK = 0。"
            "官方主檔分類仍然錯誤，"
            "禁止覆蓋 universe.json"
        )

    if etf_count == 0:

        raise RuntimeError(
            "Universe build failed："
            "ETF = 0。"
            "官方主檔分類錯誤"
        )

    # 明確驗證歷史已知終止商品
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

    seen: Set[str] = set()

    market_count = {
        "TWSE": 0,
        "TPEX": 0,
    }

    type_count = {
        "STOCK": 0,
        "ETF": 0,
    }

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
                f"{symbol}: "
                "officially terminated"
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

    log("")
    log(
        "✓ Universe schema validation PASS"
    )

    log(
        f"  total：{len(stocks):,}"
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
            "termination_twse": (
                TWSE_DELISTED_URL
            ),
            "termination_tpex": (
                TPEX_DELISTED_URL
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
# FINAL VALIDATION AFTER WRITE
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
            "寫入後 Universe root 不是 dict"
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

    try:

        terminated = (
            fetch_official_termination_status()
        )

    except Exception as exc:

        section(
            "UNIVERSE BUILD FAILED"
        )

        log(
            f"❌ 官方終止狀態取得失敗："
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
        f"  STOCK parsed："
        f"{stats['stock']:,}"
    )

    log(
        f"  ETF parsed："
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
        f"{stats['terminated_removed']:,}"
    )

    log(
        f"  Duplicate："
        f"{stats['duplicate']:,}"
    )

    log("")
    log(
        f"→ Candidate Universe："
        f"{stats['candidate']:,}"
    )

    log(
        f"→ STOCK："
        f"{stats['stock_final']:,}"
    )

    log(
        f"→ ETF："
        f"{stats['etf_final']:,}"
    )

    # ========================================================
    # STEP 5 — OUTPUT VALIDATION
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
            f"❌ Universe validation failed："
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
            f"❌ Universe atomic write failed："
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
            f"❌ 寫入後驗證失敗："
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
        "✓ 官方終止商品全部排除"
    )

    log(
        "✓ 00838B 已硬阻斷"
    )

    log(
        "✓ Universe / terminated intersection = 0"
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