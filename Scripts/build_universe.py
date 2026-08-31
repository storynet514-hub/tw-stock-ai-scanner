#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - Scripts/build_universe.py

OFFICIAL PRODUCT MASTER UNIVERSE BUILDER
============================================================

核心契約
------------------------------------------------------------
1. 官方商品主檔決定 Universe。
2. 不使用每日成交行情建立 Universe。
3. 不使用 CMoney。
4. 不使用 Yahoo。
5. ETF 不依賴當日成交量。
6. ETF 不依賴當日價格。
7. 支援 4 / 5 / 6 碼商品代號。
8. 支援新制 6 碼 ETF，例如 00400A。
9. 支援 TWSE / TPEX。
10. 保留 ETF，包括債券 ETF。
11. 排除 ETN。
12. 排除權證。
13. 排除一般債券 / 公司債。
14. 排除 REIT / TDR / 特別股等非目標商品。
15. STOCK / ETF 分流。
16. status == active 才能進入 Universe。
17. 官方主檔抓取失敗 => FAIL。
18. 官方主檔解析失敗 => FAIL。
19. 解析後 schema validation 失敗 => FAIL。
20. Gate FAIL => 絕對不覆蓋既有 universe.json。
21. Atomic Write。
22. 寫入後再次驗證。
23. 不寫死 Universe 數量。
24. 不追版本號。
25. 不用「特定英文 marker」判斷編碼成功。
26. 以 HTML table / 商品欄位結構判斷官方主檔是否有效。

官方來源
------------------------------------------------------------
TWSE / TPEx 官方 ISIN 商品主檔：

https://isin.twse.com.tw/isin/e_single_main.jsp

資料責任
------------------------------------------------------------
Universe：
    官方商品主檔

價格：
    fetch_prices.py

籌碼：
    fetch_chip.py

分析：
    analyze_stocks.py

UI：
    build_ui_data.py -> ui_data.json -> index.html

本程式絕對不碰：
    prices
    chip
    analysis
    UI
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
# OFFICIAL SOURCE
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
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}


session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# CONSTANTS
# ============================================================

ALLOWED_MARKETS = {"TWSE", "TPEX"}
ALLOWED_TYPES = {"STOCK", "ETF"}
ACTIVE_STATUS = "active"

# 官方市場欄位可能出現的文字
TWSE_MARKET_WORDS = {
    "上市",
    "上市股票",
    "上市櫃",
}

TPEX_MARKET_WORDS = {
    "上櫃",
    "上櫃股票",
}

# 明確排除的商品類型
EXCLUDED_TYPE_WORDS = (
    "權證",
    "認購權證",
    "認售權證",
    "牛證",
    "熊證",
    "ETN",
    "指數投資證券",
    "債券",
    "公司債",
    "政府債券",
    "金融債",
    "可轉換公司債",
    "海外存託憑證",
    "TDR",
    "特別股",
    "受益證券",
    "不動產投資信託",
    "REIT",
)

# ETF 關鍵字
ETF_WORDS = (
    "ETF",
    "指數股票型基金",
    "交換交易基金",
    "股票型基金",
    "債券型基金",
)

# 股票關鍵字
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
# TEXT NORMALIZATION
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
    )

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_key(value: Any) -> str:
    text = clean_text(value).lower()

    return re.sub(
        r"[\s_\-\/\\\(\)（）:：.．]+",
        "",
        text,
    )


def normalize_upper(value: Any) -> str:
    return clean_text(value).upper()


def clean_code(value: Any) -> str:
    text = clean_text(value).upper()

    text = text.replace(".TW", "")
    text = text.replace(".TWO", "")
    text = text.replace(" ", "")
    text = text.replace("\u3000", "")

    return text


def is_valid_symbol(value: str) -> bool:
    value = clean_code(value)

    return bool(
        re.fullmatch(
            r"[0-9]{4,6}[A-Z]?",
            value,
        )
    )


# ============================================================
# HTML TABLE PARSER
# ============================================================

class TableParser(HTMLParser):
    """
    只解析 table / tr / td / th。

    不依賴 pandas.read_html。
    """

    def __init__(self) -> None:
        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

        self.table_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:

        tag = tag.lower()

        if tag == "table":
            self.table_depth += 1

        elif tag == "tr":
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
                value = clean_text(
                    "".join(self._cell)
                )

                self._row.append(value)

            self._cell = None

        elif tag == "tr":

            if self._row:
                self.rows.append(
                    self._row
                )

            self._row = None
            self._cell = None

        elif tag == "table":

            self.table_depth = max(
                0,
                self.table_depth - 1,
            )

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self._cell is not None:
            self._cell.append(data)


# ============================================================
# ENCODING DETECTION
# ============================================================

def extract_meta_charset(
    content: bytes,
) -> List[str]:

    candidates: List[str] = []

    head = content[:10000]

    # HTML meta charset
    patterns = (
        rb"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)",
        rb"<meta[^>]+content\s*=\s*[\"'][^\"']*charset\s*=\s*"
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
                    .strip()
                )

                if encoding:
                    candidates.append(
                        encoding
                    )

            except Exception:
                pass

    return candidates


def unique_encodings(
    values: Iterable[str],
) -> List[str]:

    result: List[str] = []
    seen = set()

    aliases = {
        "big-5": "big5",
        "big5-hkscs": "big5",
        "950": "cp950",
        "ms950": "cp950",
        "windows-950": "cp950",
        "utf8": "utf-8",
    }

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


def score_decoded_html(
    text: str,
) -> int:
    """
    不使用單一 marker。

    以：
    - HTML 結構
    - table
    - tr
    - td
    - 證券代號
    - 證券名稱
    - 市場
    - ISIN
    - 商品代號 pattern

    綜合評分。
    """

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

    if "isin" in lower:
        score += 10

    chinese_markers = (
        "證券代號",
        "證券名稱",
        "市場別",
        "有價證券",
        "上市日",
        "證券種類",
    )

    for marker in chinese_markers:

        if marker in text:
            score += 15

    english_markers = (
        "security code",
        "security name",
        "market",
        "type of security",
        "date listed",
    )

    for marker in english_markers:

        if marker in lower:
            score += 8

    # 商品代號出現數量
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

    # 明顯錯誤替換字元大量存在
    replacement_count = text.count("\ufffd")

    if replacement_count:
        score -= min(
            100,
            replacement_count,
        )

    return score


def decode_official_html(
    content: bytes,
    response: requests.Response,
) -> Tuple[str, str]:

    candidates: List[str] = []

    # BOM
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

    # HTTP Content-Type charset
    content_type = response.headers.get(
        "Content-Type",
        "",
    )

    match = re.search(
        r"charset\s*=\s*['\"]?\s*([^;'\"\s]+)",
        content_type,
        flags=re.IGNORECASE,
    )

    if match:
        candidates.append(
            match.group(1)
        )

    # HTML meta charset
    candidates.extend(
        extract_meta_charset(
            content
        )
    )

    # requests apparent encoding
    if getattr(
        response,
        "apparent_encoding",
        None,
    ):
        candidates.append(
            response.apparent_encoding
        )

    # 官方頁面常見編碼
    candidates.extend(
        [
            "utf-8",
            "big5",
            "cp950",
            "big5-hkscs",
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

            score = score_decoded_html(
                decoded
            )

            log(
                f"    encoding={encoding:<12} "
                f"score={score:<5} "
                f"length={len(decoded)}"
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
            "官方商品主檔無法解碼"
        )

    # 至少要像 HTML 商品主檔
    if best_score < 40:
        raise RuntimeError(
            "官方商品主檔內容結構異常，"
            f"decode_score={best_score}"
        )

    return (
        best_text,
        best_encoding,
    )


# ============================================================
# OFFICIAL MASTER FETCH
# ============================================================

def fetch_master_html() -> str:

    last_error = ""

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        try:

            log(
                f"→ 官方商品主檔請求 "
                f"{attempt}/{RETRIES}"
            )

            response = session.get(
                MASTER_URL,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            log(
                f"    HTTP {response.status_code}"
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
                f"    Bytes: {len(content):,}"
            )

            text, encoding = (
                decode_official_html(
                    content,
                    response,
                )
            )

            parser = TableParser()
            parser.feed(text)

            row_count = len(
                parser.rows
            )

            log(
                f"    Selected encoding: "
                f"{encoding}"
            )

            log(
                f"    HTML rows: "
                f"{row_count:,}"
            )

            if row_count < 10:
                raise RuntimeError(
                    "官方商品主檔 table rows "
                    f"異常：{row_count}"
                )

            # 最終結構檢查
            if not looks_like_master(
                parser.rows
            ):
                raise RuntimeError(
                    "官方商品主檔未通過 "
                    "HTML 商品結構驗證"
                )

            return text

        except Exception as exc:

            last_error = str(exc)

            log(
                f"⚠️ 官方主檔抓取第 "
                f"{attempt}/{RETRIES} 次失敗："
                f"{last_error}"
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
# MASTER STRUCTURE DETECTION
# ============================================================

def row_contains_any(
    row: List[str],
    values: Iterable[str],
) -> bool:

    joined = " ".join(
        clean_text(value)
        for value in row
    )

    normalized = normalize_key(
        joined
    )

    for value in values:

        if (
            value in joined
            or normalize_key(value)
            in normalized
        ):
            return True

    return False


def looks_like_master(
    rows: List[List[str]],
) -> bool:

    if not rows:
        return False

    header_hits = 0
    valid_code_rows = 0

    for row in rows:

        if row_contains_any(
            row,
            (
                "Security Code",
                "證券代號",
                "有價證券代號",
            ),
        ):
            header_hits += 1

        if row_contains_any(
            row,
            (
                "Security Name",
                "證券名稱",
                "有價證券名稱",
            ),
        ):
            header_hits += 1

        if row_contains_any(
            row,
            (
                "Market",
                "市場別",
            ),
        ):
            header_hits += 1

        for value in row:

            if is_valid_symbol(
                value
            ):
                valid_code_rows += 1

            # 代號 + 名稱
            code, _ = split_code_name(
                value
            )

            if code:
                valid_code_rows += 1

    if header_hits >= 2:
        return True

    return valid_code_rows >= 20


# ============================================================
# MASTER FIELD ALIASES
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


# ============================================================
# FIELD ACCESS
# ============================================================

def get_field(
    row: Dict[str, str],
    aliases: Iterable[str],
) -> str:

    normalized = {
        normalize_key(key): clean_text(value)
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


# ============================================================
# CODE / NAME EXTRACTION
# ============================================================

def split_code_name(
    value: str,
) -> Tuple[str, str]:

    text = clean_text(value)

    # 例如：
    # 00400A CATHAY HIGH DIVIDEND...
    # 2330 台積電

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

        if is_valid_symbol(code):
            return code, name

    # 中文全形空格
    match = re.match(
        r"^\s*"
        r"([0-9]{4,6}[A-Z]?)"
        r"\s*\u3000+"
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

        if is_valid_symbol(code):
            return code, name

    return "", ""


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

    if is_valid_symbol(code):
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

        if is_valid_symbol(code):
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

        # 如果同時包含代號，去除前綴
        prefix = re.match(
            r"^\s*"
            r"([0-9]{4,6}[A-Z]?)"
            r"\s+",
            value,
        )

        if prefix:

            value = value[
                prefix.end():
            ]

        return clean_text(value)

    # 從代號及名稱欄位拆
    combined = get_field(
        row,
        (
            "有價證券代號及名稱",
            "Security Code and Name",
        ),
    )

    code2, name2 = split_code_name(
        combined
    )

    if code2 == code and name2:
        return name2

    return ""


# ============================================================
# MARKET
# ============================================================

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


# ============================================================
# TYPE
# ============================================================

def is_explicitly_excluded_type(
    value: str,
) -> bool:

    text = clean_text(
        value
    )

    if not text:
        return False

    for word in EXCLUDED_TYPE_WORDS:

        if word in text:
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

    upper = combined.upper()

    # --------------------------------------------------------
    # 明確排除
    # --------------------------------------------------------

    if is_explicitly_excluded_type(
        combined
    ):
        return ""

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    for word in ETF_WORDS:

        if word.upper() in upper:
            return "ETF"

    # --------------------------------------------------------
    # 股票
    # --------------------------------------------------------

    for word in STOCK_WORDS:

        if word in combined:
            return "STOCK"

    # --------------------------------------------------------
    # CFI 補助判斷
    # --------------------------------------------------------

    cfi = get_field(
        row,
        CFI_FIELDS,
    ).upper()

    # ETF CFI 常見：
    # C = collective investment
    # E = equity / bond fund structures
    #
    # 只作為輔助，不單獨把所有 CFI C 類商品變 ETF。
    if cfi.startswith("CE"):
        return "ETF"

    return ""


# ============================================================
# ETF CLASSIFICATION
# ============================================================

def classify_etf(
    name: str,
    cfi_code: str,
) -> str:

    text = clean_text(
        name
    ).upper()

    cfi = clean_text(
        cfi_code
    ).upper()

    # 主動式 ETF
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

    # 債券 ETF
    bond_words = (
        "BOND",
        "債",
        "TREASURY",
        "CORPORATE",
        "GOVERNMENT",
        "HIGH YIELD",
        "INVESTMENT GRADE",
        "非投資等級",
        "公債",
        "公司債",
        "金融債",
    )

    if any(
        word in text
        for word in bond_words
    ):
        return "BOND"

    # 商品 / 黃金 / 原物料 ETF
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

    # REIT 已經在 type filter 排除

    # 其餘 ETF
    return "ETF"


def classify_category(
    instrument_type: str,
    name: str,
    instrument_subtype: str,
) -> str:

    if instrument_type == "STOCK":
        return "STOCK"

    if instrument_type != "ETF":
        return ""

    if instrument_subtype == "ACTIVE":
        return "ACTIVE_EQUITY"

    if instrument_subtype == "BOND":
        return "BOND_ETF"

    if instrument_subtype == "COMMODITY":
        return "COMMODITY_ETF"

    return "ETF"


# ============================================================
# LISTED DATE
# ============================================================

def normalize_listed_date(
    value: str,
) -> str:

    text = clean_text(
        value
    )

    if not text:
        return ""

    # YYYY/MM/DD
    match = re.search(
        r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
        text,
    )

    if match:

        year = int(
            match.group(1)
        )
        month = int(
            match.group(2)
        )
        day = int(
            match.group(3)
        )

        return (
            f"{year:04d}/"
            f"{month:02d}/"
            f"{day:02d}"
        )

    # YYYYMMDD
    match = re.search(
        r"(?<!\d)"
        r"(\d{4})(\d{2})(\d{2})"
        r"(?!\d)",
        text,
    )

    if match:

        return (
            f"{match.group(1)}/"
            f"{match.group(2)}/"
            f"{match.group(3)}"
        )

    return text


# ============================================================
# MASTER ROW NORMALIZATION
# ============================================================

def rows_to_records(
    rows: List[List[str]],
) -> List[Dict[str, str]]:

    if not rows:
        return []

    # --------------------------------------------------------
    # 找 header
    # --------------------------------------------------------

    header_index: Optional[int] = None
    headers: List[str] = []

    for index, row in enumerate(rows):

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

        has_type = bool(
            normalized
            & {
                "typeofsecurity",
                "證券種類",
                "有價證券種類",
            }
        )

        if (
            has_code
            and (
                has_name
                or has_market
                or has_type
            )
        ):
            header_index = index
            headers = [
                clean_text(value)
                for value in row
            ]
            break

    # --------------------------------------------------------
    # 官方頁面有時欄位標題可能被拆成兩行
    # --------------------------------------------------------

    if header_index is None:

        for index, row in enumerate(rows):

            joined = " ".join(
                clean_text(value)
                for value in row
            )

            normalized = normalize_key(
                joined
            )

            if (
                "證券代號" in normalized
                and (
                    "市場別" in normalized
                    or "證券種類" in normalized
                    or "isin" in normalized
                )
            ):

                header_index = index
                headers = [
                    clean_text(value)
                    for value in row
                ]
                break

    # --------------------------------------------------------
    # 如果官方 HTML 沒有可辨識 header
    # 不猜欄位位置。
    # --------------------------------------------------------

    if header_index is None:
        return []

    result: List[Dict[str, str]] = []

    for row in rows[
        header_index + 1:
    ]:

        if not row:
            continue

        record: Dict[str, str] = {}

        for index, value in enumerate(row):

            if index >= len(headers):
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
            result.append(record)

    return result


# ============================================================
# DIRECT ROW FALLBACK
# ============================================================

def extract_records_directly(
    rows: List[List[str]],
) -> List[Dict[str, str]]:
    """
    官方頁面如果 header 結構有變動，
    不盲猜固定 index。

    只接受可以明確辨認：
        code
        name
        market
    的 row。

    這是 parser 的第二道防線。
    """

    result: List[Dict[str, str]] = []

    for row in rows:

        if len(row) < 3:
            continue

        values = [
            clean_text(value)
            for value in row
        ]

        code = ""
        code_index = -1

        for index, value in enumerate(
            values
        ):

            if is_valid_symbol(value):

                code = clean_code(
                    value
                )
                code_index = index
                break

            code2, _ = split_code_name(
                value
            )

            if code2:

                code = code2
                code_index = index
                break

        if not code:
            continue

        # 代號後面的第一個非空文字通常是名稱
        name = ""

        for index, value in enumerate(
            values
        ):

            if index == code_index:
                continue

            if not value:
                continue

            if (
                "上市" in value
                or "上櫃" in value
                or "TWSE" in value.upper()
                or "TPEX" in value.upper()
            ):
                continue

            if is_valid_symbol(value):
                continue

            if len(value) >= 2:
                name = value
                break

        market = ""

        for value in values:

            normalized = normalize_market(
                value
            )

            if normalized:

                market = normalized
                break

        if not market:
            continue

        result.append(
            {
                "Security Code": code,
                "Security Name": name,
                "Market": market,
            }
        )

    return result


# ============================================================
# MASTER PARSE
# ============================================================

def parse_master(
    text: str,
) -> List[Dict[str, str]]:

    parser = TableParser()
    parser.feed(text)

    rows = parser.rows

    log(
        f"→ HTML table rows："
        f"{len(rows):,}"
    )

    records = rows_to_records(
        rows
    )

    log(
        f"→ Header parser records："
        f"{len(records):,}"
    )

    # --------------------------------------------------------
    # 若 header parser 找不到足夠資料，再走結構化 fallback
    # --------------------------------------------------------

    valid_from_header = 0

    for row in records:

        code = extract_code(row)

        if code:
            valid_from_header += 1

    if valid_from_header < 20:

        log(
            "⚠️ Header parser 有效商品不足，"
            "啟用 direct row parser"
        )

        direct_records = (
            extract_records_directly(
                rows
            )
        )

        if len(direct_records) > len(
            records
        ):
            records = direct_records

    return records


# ============================================================
# BUILD UNIVERSE ITEMS
# ============================================================

def build_items(
    records: List[Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:

    items: Dict[str, Dict[str, Any]] = {}

    stats = {
        "rows": 0,
        "valid_code": 0,
        "market": 0,
        "stock": 0,
        "etf": 0,
        "excluded": 0,
        "unknown_type": 0,
        "duplicates": 0,
    }

    for row in records:

        stats["rows"] += 1

        code = extract_code(
            row
        )

        if not code:
            continue

        stats["valid_code"] += 1

        name = extract_name(
            row,
            code,
        )

        if not name:
            # 沒有名稱不能進 production Universe
            continue

        market = normalize_market(
            get_field(
                row,
                MARKET_FIELDS,
            )
        )

        if market not in ALLOWED_MARKETS:
            continue

        stats["market"] += 1

        type_value = get_field(
            row,
            TYPE_FIELDS,
        )

        # 明確排除
        if is_explicitly_excluded_type(
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
        elif instrument_type == "ETF":
            stats["etf"] += 1

        listed_date = (
            normalize_listed_date(
                get_field(
                    row,
                    DATE_FIELDS,
                )
            )
        )

        cfi_code = clean_text(
            get_field(
                row,
                CFI_FIELDS,
            )
        )

        if instrument_type == "ETF":

            instrument_subtype = (
                classify_etf(
                    name,
                    cfi_code,
                )
            )

        else:

            instrument_subtype = "COMMON"

        category = classify_category(
            instrument_type,
            name,
            instrument_subtype,
        )

        full_symbol_suffix = (
            "TW"
            if market == "TWSE"
            else "TWO"
        )

        item = {
            "symbol": code,
            "full_symbol": (
                f"{code}.{full_symbol_suffix}"
            ),
            "name": name,
            "market": market,
            "type": instrument_type,
            "instrument_type": (
                instrument_subtype
            ),
            "status": ACTIVE_STATUS,
            "listed_date": listed_date,
            "cfi_code": cfi_code,
            "category": category,
        }

        # ----------------------------------------------------
        # duplicate
        # ----------------------------------------------------

        if code in items:

            stats["duplicates"] += 1

            existing = items[code]

            # 若重複資料，一律優先資訊較完整者
            existing_score = sum(
                1
                for value in existing.values()
                if value
            )

            new_score = sum(
                1
                for value in item.values()
                if value
            )

            if new_score > existing_score:
                items[code] = item

            continue

        items[code] = item

    log("")
    log("MASTER PARSE STATISTICS")
    log(
        f"  原始 records：{stats['rows']:,}"
    )
    log(
        f"  有效代號：{stats['valid_code']:,}"
    )
    log(
        f"  有效市場：{stats['market']:,}"
    )
    log(
        f"  STOCK：{stats['stock']:,}"
    )
    log(
        f"  ETF：{stats['etf']:,}"
    )
    log(
        f"  排除：{stats['excluded']:,}"
    )
    log(
        f"  未辨識類型：{stats['unknown_type']:,}"
    )
    log(
        f"  重複代號：{stats['duplicates']:,}"
    )

    return dict(
        sorted(
            items.items(),
            key=lambda pair: pair[0],
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

    symbol = item.get(
        "symbol",
        "",
    )

    if symbol != key:

        errors.append(
            f"{key}: symbol != key"
        )

    if not is_valid_symbol(
        str(symbol)
    ):

        errors.append(
            f"{key}: symbol 格式錯誤"
        )

    full_symbol = str(
        item.get(
            "full_symbol",
            "",
        )
    )

    expected_suffix = (
        ".TW"
        if item.get("market")
        == "TWSE"
        else ".TWO"
        if item.get("market")
        == "TPEX"
        else ""
    )

    if expected_suffix:

        expected = (
            f"{symbol}{expected_suffix}"
        )

        if full_symbol != expected:

            errors.append(
                f"{key}: full_symbol 錯誤 "
                f"{full_symbol} != {expected}"
            )

    if not item.get("name"):

        errors.append(
            f"{key}: name 為空"
        )

    if item.get("market") not in (
        ALLOWED_MARKETS
    ):

        errors.append(
            f"{key}: market 不合法"
        )

    if item.get("type") not in (
        ALLOWED_TYPES
    ):

        errors.append(
            f"{key}: type 不合法"
        )

    if item.get("status") != (
        ACTIVE_STATUS
    ):

        errors.append(
            f"{key}: status 不是 active"
        )

    return errors


def validate_items(
    items: Dict[str, Dict[str, Any]],
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
            "Universe stocks 為 0"
        )

    if errors:

        log("")
        log("❌ UNIVERSE VALIDATION FAILED")

        for error in errors[:50]:
            log(
                f"  - {error}"
            )

        if len(errors) > 50:
            log(
                f"  ... 其餘 "
                f"{len(errors) - 50} 個錯誤略過"
            )

        raise RuntimeError(
            f"Universe validation failed："
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
            "Universe root 不是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "Universe stocks 不是 dict"
        )

    count = data.get(
        "universe_count"
    )

    if count != len(stocks):

        raise RuntimeError(
            "universe_count 錯誤："
            f"{count} != {len(stocks)}"
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
            "stock_count 錯誤："
            f"{data.get('stock_count')} "
            f"!= {stock_count}"
        )

    if data.get(
        "etf_count"
    ) != etf_count:

        raise RuntimeError(
            "etf_count 錯誤："
            f"{data.get('etf_count')} "
            f"!= {etf_count}"
        )

    market_count = data.get(
        "market_count"
    )

    if not isinstance(
        market_count,
        dict,
    ):
        raise RuntimeError(
            "market_count 不是 object"
        )

    expected_market_count = {
        "TWSE": 0,
        "TPEX": 0,
    }

    for item in stocks.values():

        market = item.get(
            "market"
        )

        if market in expected_market_count:
            expected_market_count[
                market
            ] += 1

    if market_count != (
        expected_market_count
    ):

        raise RuntimeError(
            "market_count 錯誤："
            f"{market_count} != "
            f"{expected_market_count}"
        )

    validate_items(
        stocks
    )


# ============================================================
# EXISTING UNIVERSE METADATA
# ============================================================

def load_existing_universe() -> Optional[Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():
        return None

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as file:

            data = json.load(file)

        if isinstance(
            data,
            dict,
        ):
            return data

    except Exception as exc:

        log(
            f"⚠️ 既有 universe.json "
            f"無法讀取：{exc}"
        )

    return None


# ============================================================
# BUILD ROOT
# ============================================================

def build_universe_document(
    items: Dict[str, Dict[str, Any]],
    existing: Optional[Dict[str, Any]],
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

    document: Dict[str, Any] = {
        "version": "UNIVERSE-BUILD",
        "generated_at": (
            now_tw().isoformat()
        ),
        "universe_count": len(items),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "market_count": market_count,
        "source": {
            "universe_master": MASTER_URL,
            "policy": (
                "official product master only"
            ),
            "price_data_is_not_universe_source": True,
            "daily_quotes_are_not_universe_source": True,
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
            "etf_requires_official_master": True,
            "etf_6_digit_supported": True,
            "bond_etf_supported": True,
            "metadata_preserved": True,
            "fixed_universe_count": False,
            "daily_quote_not_used": True,
            "cmoney_not_used": True,
        },
        "stocks": items,
    }

    # --------------------------------------------------------
    # 保留既有 root metadata 中非核心計數欄位
    #
    # 不把舊 stocks / count 複製回去。
    # 避免舊 Universe 汙染新 Universe。
    # --------------------------------------------------------

    if isinstance(
        existing,
        dict,
    ):

        for key in (
            "notes",
            "description",
        ):

            if key in existing:
                document[key] = existing[
                    key
                ]

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

            file.write("\n")

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
# POST-WRITE VALIDATION
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
# SUMMARY
# ============================================================

def print_summary(
    data: Dict[str, Any],
) -> None:

    stocks = data[
        "stocks"
    ]

    stock_count = data[
        "stock_count"
    ]

    etf_count = data[
        "etf_count"
    ]

    market_count = data[
        "market_count"
    ]

    log("")
    log("=" * 76)
    log("UNIVERSE BUILD SUMMARY")
    log("=" * 76)

    log(
        f"Universe：{len(stocks):,}"
    )

    log(
        f"STOCK：{stock_count:,}"
    )

    log(
        f"ETF：{etf_count:,}"
    )

    log(
        f"TWSE：{market_count['TWSE']:,}"
    )

    log(
        f"TPEX：{market_count['TPEX']:,}"
    )

    # 重要抽樣
    sample_codes = (
        "00400A",
        "00401A",
        "00402A",
        "00403A",
        "00404A",
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

        existing_count = (
            existing.get(
                "universe_count",
                0,
            )
        )

        log(
            f"既有 Universe metadata："
            f"{existing_count} 檔"
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

        html_text = (
            fetch_master_html()
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
            html_text
        )

        if len(records) < 20:

            raise RuntimeError(
                "解析後 records "
                f"異常：{len(records)}"
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
        "STEP 3 — BUILD ACTIVE UNIVERSE"
    )

    try:

        items = build_items(
            records
        )

        log("")
        log(
            f"→ Candidate Universe："
            f"{len(items):,}"
        )

        # ----------------------------------------------------
        # 絕對防呆：
        # 如果官方主檔突然只回極少商品，
        # 絕不覆蓋舊 Universe。
        #
        # 這不是固定 Universe 數量。
        # 是防止官方 endpoint 回錯頁 / CAPTCHA /
        # 錯誤 HTML 時把 Universe 清空。
        # ----------------------------------------------------

        if len(items) < 100:

            raise RuntimeError(
                "解析後有效 Universe 少於 "
                "100 檔，疑似官方主檔內容異常"
            )

        # ----------------------------------------------------
        # 6 碼 ETF gate
        # ----------------------------------------------------

        six_digit_etf = [
            code
            for code, item
            in items.items()
            if item["type"] == "ETF"
            and len(
                re.sub(
                    r"[A-Z]+$",
                    "",
                    code,
                )
            ) >= 5
        ]

        log(
            f"→ 5/6碼 ETF candidate："
            f"{len(six_digit_etf):,}"
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
    # STEP 4
    # ========================================================

    section(
        "STEP 4 — BUILD UNIVERSE DOCUMENT"
    )

    try:

        document = (
            build_universe_document(
                items,
                existing,
            )
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
            f"❌ Universe document validation "
            f"failed：{exc}"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # STEP 5
    # ========================================================

    section(
        "STEP 5 — ATOMIC WRITE"
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
    # STEP 6
    # ========================================================

    section(
        "STEP 6 — POST-WRITE VALIDATION"
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
            f"❌ 未預期錯誤：{exc}"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        sys.exit(1)