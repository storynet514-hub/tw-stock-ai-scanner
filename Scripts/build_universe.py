#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py
============================================================

用途
------------------------------------------------------------
建立 Data/universe.json。

核心架構：

    官方 ISIN 商品主檔
            ↓
    精確解析官方欄位
            ↓
    官方 Market 判定
            ↓
    官方 Type of security 判定
            ↓
    只接受 STOCKS / STOCK / ETF
            ↓
    STOCKS → STOCK
            ↓
    排除 ETN / WARRANT / REIT / TDR / BOND /
    PREFERRED / 其他商品
            ↓
    metadata normalization
            ↓
    identity validation
            ↓
    final validation
            ↓
    atomic write
            ↓
    read-back validation


核心契約
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 輸出。
2. 商品身份唯一以官方商品主檔判定。
3. 不使用價格資料建立 Universe。
4. 不使用成交量建立 Universe。
5. 不使用 Yahoo 建立 Universe。
6. 不使用 CMoney 建立 Universe。
7. 不使用 FinMind 建立 Universe。
8. 不寫死任何股票或 ETF symbol。
9. 不依賴 ETF 代碼長度判斷 ETF。
10. 不依賴 ETF 名稱判斷 ETF。
11. 不依賴 symbol 是否存在 Yahoo 判斷身份。
12. 官方 Type of security 必須明確為 STOCKS / STOCK / ETF。
13. 官方 Market 必須明確為 TWSE LISTED 或 TPEx LISTED。
14. symbol 必須來自同一官方資料列。
15. ISIN / symbol / name / market / type / listed_date /
    CFI 必須來自同一 official row。
16. 官方資料不足時直接 FAIL。
17. 官方資料異常時直接 FAIL。
18. FAIL 不覆蓋既有 universe.json。
19. 寫入使用 atomic replace。
20. 寫入後重新讀取並再次驗證。
21. 不允許非官方 STOCK/ETF 商品混入 Universe。
22. 不因為某個 symbol 長得像股票就接受。
23. 不因為某個 symbol 長得像 ETF 就接受。
24. 不因為 FinMind/Yahoo 有資料就接受。
25. 只接受官方 Type of security == STOCKS / STOCK / ETF。


官方來源
------------------------------------------------------------

https://isin.twse.com.tw/isin/e_single_main.jsp


官方資料欄位：

Page No.
ISIN Code
Security Code
Security Name
Market
Type of security
Industrial Group
Date Stock Listed
CFICode
Remarks


允許：

    STOCKS
    STOCK
    ETF


注意：

官方目前普通股票類型實際使用：

    STOCKS

因此：

    STOCKS → STOCK

但絕對不能使用：

    if "STOCK" in text

否則：

    PREFERREDSTOCKS

會被錯誤接受。


排除：

    ETN
    WARRANT
    Real Estate Investment Trust (REIT)
    TDR
    Bond
    Preferred Stock
    Closed-end Fund
    其他非 STOCK / ETF 商品


ETF
------------------------------------------------------------

ETF 不依賴代碼長度。

以下均可以接受，只要官方 Type of security == ETF：

    0050
    006201
    006203
    00625K
    00631L
    009810
    00981A
    00400A
    00411A
    ...

也就是：

    4 碼
    5 碼
    6 碼
    4~6 碼 + 英文字母

都不會因格式直接排除。


重要修正
------------------------------------------------------------

舊版錯誤：

    官方 Type of security
        ↓
    STOCKS
        ↓
    normalize_security_type()
        ↓
    不接受
        ↓
    2758 檔普通股票全部被丟掉

結果只剩：

    ETF = 360


正確：

    官方 Type of security
        ↓
    STOCKS
        ↓
    normalize_security_type()
        ↓
    STOCK
        ↓
    ACCEPT


因此預期：

    STOCKS 2758 → STOCK
    ETF     360 → ETF

最終 Universe 約 3118 檔，
實際數量以官方資料及去重結果為準。


版本
------------------------------------------------------------

UNIVERSE-BUILD-V9.1
"""

from __future__ import annotations

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
# OFFICIAL SOURCE
# ============================================================

OFFICIAL_MASTER_URL = (
    "https://isin.twse.com.tw/isin/e_single_main.jsp"
)

OFFICIAL_SOURCE_NAME = "TWSE_ISIN_MASTER"


# ============================================================
# CONFIG
# ============================================================

REQUEST_TIMEOUT = 60
HTTP_RETRIES = 4
RETRY_SLEEP_SECONDS = 2.0

# 正常台股 STOCK + ETF 應遠高於此數量。
# 此值不是 Universe 固定數量，只是防止官方來源
# 回傳 HTML 錯誤頁、空頁、截斷頁時誤覆蓋舊 Universe。
MIN_OFFICIAL_SYMBOLS = 1500

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
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; "
            "tw-stock-ai-scanner/UniverseBuilder-V9.1)"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
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


def now_taipei() -> datetime:
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

    text = str(value)

    replacements = {
        "\ufeff": " ",
        "\xa0": " ",
        "\u3000": " ",
        "\r": " ",
        "\n": " ",
        "\t": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def normalize_text(value: Any) -> str:
    return (
        clean_text(value)
        .upper()
        .replace(" ", "")
        .replace("\u3000", "")
    )


def clean_symbol(value: Any) -> str:
    text = clean_text(value).upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TWSE",
        ".TPEX",
    ):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break

    return (
        text
        .replace(" ", "")
        .replace("\u3000", "")
    )


# ============================================================
# SYMBOL VALIDATION
# ============================================================

def is_valid_symbol(
    value: Any,
) -> bool:

    symbol = clean_symbol(value)

    if not symbol:
        return False

    # 台股新制商品代碼：
    #
    # 4~6 位數字
    # 或 4~6 位數字 + 1 位英文字母
    #
    # 這只是格式驗證。
    # 絕對不代表它是 STOCK / ETF。

    return bool(
        re.fullmatch(
            r"[0-9]{4,6}[A-Z]?",
            symbol,
        )
    )


# ============================================================
# MARKET
# ============================================================

def normalize_market(
    value: Any,
) -> Optional[str]:

    text = normalize_text(value)

    if not text:
        return None

    if (
        text == "TWSELISTED"
        or text == "TWSE"
        or "TWSELISTED" in text
    ):
        return "TWSE"

    if (
        text == "TPEXLISTED"
        or text == "TPEX"
        or "TPEXLISTED" in text
        or "TPEX" in text
        or "OTCLISTED" in text
    ):
        return "TPEX"

    return None


# ============================================================
# SECURITY TYPE
# ============================================================

def normalize_security_type(
    value: Any,
) -> Optional[str]:

    text = normalize_text(value)

    if not text:
        return None

    # ========================================================
    # 核心修正
    # ========================================================
    #
    # TWSE 官方商品主檔目前普通股票的 Type of security
    # 使用：
    #
    #     STOCKS
    #
    # 而不是：
    #
    #     STOCK
    #
    # 因此必須精確接受 STOCKS。
    #
    # 絕對不能寫：
    #
    #     if "STOCK" in text
    #
    # 因為：
    #
    #     PREFERREDSTOCKS
    #     STOCKFUTURES
    #
    # 都會因此被錯誤接受。
    # ========================================================

    if text == "STOCKS":
        return "STOCK"

    # 相容其他官方資料來源可能使用的單數形式。
    if text == "STOCK":
        return "STOCK"

    # 官方 ETF。
    if text == "ETF":
        return "ETF"

    # 其他所有官方商品類型一律拒絕。
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

        self.current_row: Optional[
            List[str]
        ] = None

        self.current_cell: Optional[
            List[str]
        ] = None

    def handle_starttag(
        self,
        tag: str,
        attrs: List[
            Tuple[
                str,
                Optional[str],
            ]
        ],
    ) -> None:

        tag = tag.lower()

        if tag == "tr":
            self.current_row = []

        elif tag in {"td", "th"}:
            if self.current_row is not None:
                self.current_cell = []

        elif tag == "br":
            if self.current_cell is not None:
                self.current_cell.append(" ")

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if tag in {"td", "th"}:

            if (
                self.current_row is not None
                and self.current_cell is not None
            ):
                self.current_row.append(
                    clean_text(
                        "".join(
                            self.current_cell
                        )
                    )
                )

            self.current_cell = None

        elif tag == "tr":

            if self.current_row:
                self.rows.append(
                    self.current_row
                )

            self.current_row = None
            self.current_cell = None

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self.current_cell is not None:
            self.current_cell.append(data)


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
        HTTP_RETRIES + 1,
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
                f"⚠️ HTTP retry "
                f"{attempt}/{HTTP_RETRIES}"
            )

            log(
                f"   {url}"
            )

            log(
                f"   {exc}"
            )

            if attempt < HTTP_RETRIES:
                time.sleep(
                    RETRY_SLEEP_SECONDS
                    * attempt
                )

    raise RuntimeError(
        "Official source request failed: "
        f"{url}: {last_error}"
    )


# ============================================================
# RESPONSE DECODING
# ============================================================

def decode_response(
    response: requests.Response,
) -> str:

    raw = response.content

    encodings: List[str] = []

    if response.encoding:
        encodings.append(
            response.encoding
        )

    encodings.extend(
        [
            "utf-8",
            "big5",
            "cp950",
        ]
    )

    seen = set()

    for encoding in encodings:

        if encoding in seen:
            continue

        seen.add(encoding)

        try:

            text = raw.decode(
                encoding
            )

            replacement_count = (
                text.count("\ufffd")
            )

            if replacement_count < 3:
                return text

        except (
            UnicodeDecodeError,
            LookupError,
        ):
            continue

    return raw.decode(
        "utf-8",
        errors="replace",
    )


# ============================================================
# HEADER DETECTION
# ============================================================

HEADER_ALIASES = {
    "page": {
        "PAGENO",
        "PAGENO.",
        "PAGE",
    },
    "isin": {
        "ISINCODE",
        "ISIN",
    },
    "symbol": {
        "SECURITYCODE",
        "SECURITYCODE.",
        "CODE",
        "SECURITYNO",
        "SECURITYNUMBER",
    },
    "name": {
        "SECURITYNAME",
        "NAME",
        "SECURITY",
    },
    "market": {
        "MARKET",
    },
    "type": {
        "TYPEOFSECURITY",
        "TYPEOFSECURITY.",
        "SECURITYTYPE",
        "TYPE",
    },
    "industry": {
        "INDUSTRIALGROUP",
        "INDUSTRY",
        "INDUSTRIAL",
    },
    "listed_date": {
        "DATESTOCKLISTED",
        "DATESTOCKLISTED.",
        "LISTEDDATE",
        "DATE",
    },
    "cfi": {
        "CFICODE",
        "CFI",
    },
    "remarks": {
        "REMARKS",
        "REMARK",
    },
}


def normalize_header(
    value: Any,
) -> str:

    return (
        normalize_text(value)
        .replace("：", "")
        .replace(":", "")
        .replace(".", "")
    )


def detect_header(
    rows: List[List[str]],
) -> Optional[
    Dict[str, int]
]:

    for row_index, row in enumerate(
        rows[:30]
    ):

        normalized = [
            normalize_header(cell)
            for cell in row
        ]

        mapping: Dict[
            str,
            int
        ] = {}

        for index, cell in enumerate(
            normalized
        ):

            for key, aliases in (
                HEADER_ALIASES.items()
            ):

                if cell in aliases:
                    mapping[key] = index
                    break

        required = {
            "isin",
            "symbol",
            "name",
            "market",
            "type",
        }

        if required.issubset(
            mapping.keys()
        ):
            log(
                "✓ Official header detected "
                f"at row {row_index + 1}"
            )

            return mapping

    return None


# ============================================================
# ROW ACCESS
# ============================================================

def get_cell(
    row: List[str],
    mapping: Dict[str, int],
    key: str,
) -> str:

    index = mapping.get(key)

    if index is None:
        return ""

    if index < 0:
        return ""

    if index >= len(row):
        return ""

    return clean_text(
        row[index]
    )


# ============================================================
# OFFICIAL ROW PARSING
# ============================================================

def parse_official_rows(
    html: str,
) -> Tuple[
    List[Dict[str, Any]],
    Dict[str, int],
    Dict[str, int],
    List[str],
]:

    parser = TableParser()

    parser.feed(html)

    rows = parser.rows

    if not rows:
        raise RuntimeError(
            "Official master returned no HTML table rows."
        )

    header = detect_header(
        rows
    )

    if header is None:
        raise RuntimeError(
            "Official master header not found. "
            "The source structure may have changed."
        )

    accepted: List[
        Dict[str, Any]
    ] = []

    type_counts: Dict[
        str,
        int
    ] = {}

    market_counts: Dict[
        str,
        int
    ] = {}

    rejected_samples: List[
        str
    ] = []

    for row_index, row in enumerate(
        rows
    ):

        # 跳過 header
        if row_index < 30:

            row_text = " ".join(
                normalize_text(cell)
                for cell in row
            )

            if (
                "SECURITYCODE" in row_text
                and "TYPEOFSECURITY" in row_text
            ):
                continue

        symbol_raw = get_cell(
            row,
            header,
            "symbol",
        )

        name = get_cell(
            row,
            header,
            "name",
        )

        market_raw = get_cell(
            row,
            header,
            "market",
        )

        type_raw = get_cell(
            row,
            header,
            "type",
        )

        isin = get_cell(
            row,
            header,
            "isin",
        )

        listed_date = get_cell(
            row,
            header,
            "listed_date",
        )

        cfi_code = get_cell(
            row,
            header,
            "cfi",
        )

        industry = get_cell(
            row,
            header,
            "industry",
        )

        remarks = get_cell(
            row,
            header,
            "remarks",
        )

        symbol = clean_symbol(
            symbol_raw
        )

        security_type = (
            normalize_security_type(
                type_raw
            )
        )

        market = normalize_market(
            market_raw
        )

        normalized_type = normalize_text(
            type_raw
        )

        # ----------------------------------------------------
        # 統計使用原始官方 Type
        # ----------------------------------------------------

        if normalized_type:

            type_counts[
                normalized_type
            ] = (
                type_counts.get(
                    normalized_type,
                    0,
                )
                + 1
            )

        if market:

            market_counts[
                market
            ] = (
                market_counts.get(
                    market,
                    0,
                )
                + 1
            )

        # ----------------------------------------------------
        # Identity validation
        # ----------------------------------------------------

        if not symbol:
            continue

        if not is_valid_symbol(
            symbol
        ):

            if len(
                rejected_samples
            ) < 50:

                rejected_samples.append(
                    f"row={row_index + 1} "
                    f"symbol={symbol_raw!r} "
                    f"reason=invalid_symbol"
                )

            continue

        # ----------------------------------------------------
        # 核心：
        #
        # security_type 必須由官方 Type of security
        # 精確轉換而來。
        #
        # STOCKS → STOCK
        # STOCK  → STOCK
        # ETF    → ETF
        #
        # 其他全部拒絕。
        # ----------------------------------------------------

        if security_type not in ALLOWED_TYPES:

            if len(
                rejected_samples
            ) < 50:

                rejected_samples.append(
                    f"row={row_index + 1} "
                    f"symbol={symbol} "
                    f"type={type_raw!r} "
                    f"reason=non_universe_type"
                )

            continue

        if market not in ALLOWED_MARKETS:

            if len(
                rejected_samples
            ) < 50:

                rejected_samples.append(
                    f"row={row_index + 1} "
                    f"symbol={symbol} "
                    f"market={market_raw!r} "
                    f"reason=invalid_market"
                )

            continue

        # ----------------------------------------------------
        # 官方 row identity
        # ----------------------------------------------------

        record: Dict[
            str,
            Any
        ] = {
            "symbol": symbol,
            "name": name,
            "market": market,
            "type": security_type,
            "status": ACTIVE_STATUS,
            "listed_date": listed_date,
            "cfi_code": cfi_code,
            "industry": industry,
            "isin": isin,
            "official_type": type_raw,
            "official_market": market_raw,
            "remarks": remarks,
            "source": OFFICIAL_SOURCE_NAME,
        }

        accepted.append(
            record
        )

    return (
        accepted,
        type_counts,
        market_counts,
        rejected_samples,
    )


# ============================================================
# INSTRUMENT CLASSIFICATION
# ============================================================

def classify_instrument(
    record: Dict[str, Any],
) -> Tuple[str, str]:

    if record["type"] == "STOCK":
        return (
            "STOCK",
            "STOCK",
        )

    if record["type"] != "ETF":
        return (
            "OTHER",
            "OTHER",
        )

    symbol = clean_symbol(
        record.get(
            "symbol",
            "",
        )
    )

    name = normalize_text(
        record.get(
            "name",
            "",
        )
    )

    cfi = normalize_text(
        record.get(
            "cfi_code",
            "",
        )
    )

    # --------------------------------------------------------
    # ETF 分類只是 metadata。
    #
    # Universe 身份早已由官方 Type of security == ETF
    # 決定。
    #
    # 以下不會改變 type。
    # --------------------------------------------------------

    # Active ETF
    if (
        "ACTIVE" in name
        or symbol.endswith("A")
    ):

        # 債券 Active ETF
        if (
            symbol.endswith("D")
            or "BOND" in name
            or "債" in name
        ):

            return (
                "ACTIVE",
                "ACTIVE_BOND",
            )

        return (
            "ACTIVE",
            "ACTIVE_EQUITY",
        )

    # Leveraged
    if (
        symbol.endswith("L")
        or "LEVERAGED" in name
        or "槓桿" in name
    ):

        return (
            "LEVERAGED",
            "LEVERAGED",
        )

    # Inverse
    if (
        symbol.endswith("R")
        or "INVERSE" in name
        or "BEAR" in name
        or "反向" in name
    ):

        return (
            "INVERSE",
            "INVERSE",
        )

    # Foreign currency ETF
    if symbol.endswith(
        ("K", "C")
    ):

        return (
            "ETF_FX",
            "FX",
        )

    # Bond ETF
    if (
        symbol.endswith("B")
        or "BOND" in name
        or "債券" in name
        or "公司債" in name
        or "公債" in name
    ):

        return (
            "BOND",
            "BOND",
        )

    # Multi-asset
    if (
        symbol.endswith("T")
        or "MULTI ASSET" in name
        or "MULTI-ASSET" in name
        or "多資產" in name
    ):

        return (
            "MULTI_ASSET",
            "MULTI_ASSET",
        )

    # --------------------------------------------------------
    # CFI fallback
    # --------------------------------------------------------

    if cfi.startswith(
        "CE"
    ):

        return (
            "EQUITY",
            "EQUITY",
        )

    return (
        "EQUITY",
        "EQUITY",
    )


# ============================================================
# FULL SYMBOL
# ============================================================

def build_full_symbol(
    symbol: str,
    market: str,
) -> str:

    suffix = (
        ".TW"
        if market == "TWSE"
        else ".TWO"
    )

    return (
        f"{symbol}{suffix}"
    )


# ============================================================
# CANDIDATE
# ============================================================

def build_candidate(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    symbol = clean_symbol(
        record["symbol"]
    )

    market = record["market"]

    instrument_type, category = (
        classify_instrument(
            record
        )
    )

    return {
        "symbol": symbol,
        "full_symbol": build_full_symbol(
            symbol,
            market,
        ),
        "name": clean_text(
            record.get(
                "name",
                "",
            )
        ),
        "market": market,
        "type": record["type"],
        "instrument_type": instrument_type,
        "status": ACTIVE_STATUS,
        "listed_date": clean_text(
            record.get(
                "listed_date",
                "",
            )
        ),
        "cfi_code": clean_text(
            record.get(
                "cfi_code",
                "",
            )
        ),
        "category": category,
        "industry": clean_text(
            record.get(
                "industry",
                "",
            )
        ),
        "isin": clean_text(
            record.get(
                "isin",
                "",
            )
        ),
        "official_type": clean_text(
            record.get(
                "official_type",
                "",
            )
        ),
        "official_market": clean_text(
            record.get(
                "official_market",
                "",
            )
        ),
        "source": OFFICIAL_SOURCE_NAME,
    }


# ============================================================
# DEDUPLICATION
# ============================================================

def deduplicate_candidates(
    candidates: List[
        Dict[str, Any]
    ],
) -> Dict[
    str,
    Dict[str, Any]
]:

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    conflicts: List[
        str
    ] = []

    for candidate in candidates:

        symbol = candidate[
            "symbol"
        ]

        existing = result.get(
            symbol
        )

        if existing is None:

            result[
                symbol
            ] = candidate

            continue

        # ----------------------------------------------------
        # 同一 symbol 出現多次：
        #
        # market/type 不一致 → FAIL
        # 完全一致 → duplicate
        # ----------------------------------------------------

        if (
            existing["market"]
            != candidate["market"]
            or existing["type"]
            != candidate["type"]
        ):

            conflicts.append(
                (
                    f"{symbol}: "
                    f"{existing['market']}/"
                    f"{existing['type']} != "
                    f"{candidate['market']}/"
                    f"{candidate['type']}"
                )
            )

            continue

        # 保留資訊較完整者
        existing_score = sum(
            1
            for value in existing.values()
            if value not in (
                "",
                None,
            )
        )

        candidate_score = sum(
            1
            for value in candidate.values()
            if value not in (
                "",
                None,
            )
        )

        if candidate_score > existing_score:

            result[
                symbol
            ] = candidate

    if conflicts:

        log("")
        log(
            "❌ Official identity conflicts:"
        )

        for conflict in conflicts[:50]:
            log(
                f"   {conflict}"
            )

        raise RuntimeError(
            "Official identity conflict detected."
        )

    return result


# ============================================================
# CANDIDATE VALIDATION
# ============================================================

def validate_candidate(
    symbol: str,
    item: Dict[str, Any],
) -> None:

    if clean_symbol(
        item.get(
            "symbol",
            "",
        )
    ) != symbol:

        raise RuntimeError(
            f"Universe symbol mismatch: "
            f"{symbol}"
        )

    if not is_valid_symbol(
        symbol
    ):

        raise RuntimeError(
            f"Invalid Universe symbol: "
            f"{symbol}"
        )

    market = item.get(
        "market"
    )

    if market not in ALLOWED_MARKETS:

        raise RuntimeError(
            f"Invalid market for "
            f"{symbol}: {market}"
        )

    security_type = item.get(
        "type"
    )

    if security_type not in ALLOWED_TYPES:

        raise RuntimeError(
            f"Invalid type for "
            f"{symbol}: {security_type}"
        )

    if item.get(
        "status"
    ) != ACTIVE_STATUS:

        raise RuntimeError(
            f"Invalid status for "
            f"{symbol}: "
            f"{item.get('status')}"
        )

    full_symbol = item.get(
        "full_symbol",
        "",
    )

    expected_full = build_full_symbol(
        symbol,
        market,
    )

    if full_symbol != expected_full:

        raise RuntimeError(
            f"Invalid full_symbol for "
            f"{symbol}: "
            f"{full_symbol} "
            f"!= "
            f"{expected_full}"
        )

    official_type = normalize_security_type(
        item.get(
            "official_type",
            "",
        )
    )

    if official_type != security_type:

        raise RuntimeError(
            f"Official type mismatch for "
            f"{symbol}: "
            f"{item.get('official_type')} "
            f"!= "
            f"{security_type}"
        )


# ============================================================
# UNIVERSE VALIDATION
# ============================================================

def validate_universe(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> None:

    if not stocks:

        raise RuntimeError(
            "Universe is empty."
        )

    if len(stocks) < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "Official Universe size is "
            f"too small: {len(stocks)} "
            f"< {MIN_OFFICIAL_SYMBOLS}. "
            "Refusing to overwrite existing "
            "universe.json."
        )

    seen = set()

    stock_count = 0
    etf_count = 0

    market_count = {
        "TWSE": 0,
        "TPEX": 0,
    }

    for symbol in sorted(
        stocks.keys()
    ):

        if symbol in seen:

            raise RuntimeError(
                f"Duplicate Universe symbol: "
                f"{symbol}"
            )

        seen.add(symbol)

        item = stocks[
            symbol
        ]

        validate_candidate(
            symbol,
            item,
        )

        if item["type"] == "STOCK":
            stock_count += 1

        elif item["type"] == "ETF":
            etf_count += 1

        market_count[
            item["market"]
        ] += 1

    if stock_count <= 0:

        raise RuntimeError(
            "Universe contains no STOCK."
        )

    if etf_count <= 0:

        raise RuntimeError(
            "Universe contains no ETF."
        )

    if market_count["TWSE"] <= 0:

        raise RuntimeError(
            "Universe contains no TWSE."
        )

    if market_count["TPEX"] <= 0:

        raise RuntimeError(
            "Universe contains no TPEX."
        )

    log("")
    log(
        "✓ Universe validation passed"
    )

    log(
        f"  Universe : {len(stocks)}"
    )

    log(
        f"  STOCK    : {stock_count}"
    )

    log(
        f"  ETF      : {etf_count}"
    )

    log(
        f"  TWSE     : "
        f"{market_count['TWSE']}"
    )

    log(
        f"  TPEX     : "
        f"{market_count['TPEX']}"
    )


# ============================================================
# OFFICIAL MASTER FETCH
# ============================================================

def fetch_official_master() -> str:

    section(
        "FETCH OFFICIAL ISIN MASTER"
    )

    log(
        f"Source: {OFFICIAL_MASTER_URL}"
    )

    response = http_get(
        OFFICIAL_MASTER_URL
    )

    text = decode_response(
        response
    )

    if len(text) < 10000:

        raise RuntimeError(
            "Official master response is "
            "unexpectedly small."
        )

    log(
        f"✓ HTTP {response.status_code}"
    )

    log(
        f"✓ Response bytes: "
        f"{len(response.content):,}"
    )

    log(
        f"✓ Response chars: "
        f"{len(text):,}"
    )

    return text


# ============================================================
# BUILD UNIVERSE
# ============================================================

def build_universe() -> Dict[str, Any]:

    html = fetch_official_master()

    section(
        "PARSE OFFICIAL PRODUCT MASTER"
    )

    (
        official_rows,
        type_counts,
        market_counts,
        rejected_samples,
    ) = parse_official_rows(
        html
    )

    log(
        f"✓ Accepted official STOCK/ETF rows: "
        f"{len(official_rows)}"
    )

    log("")
    log(
        "Official security type counts:"
    )

    for key, count in sorted(
        type_counts.items()
    ):

        log(
            f"  {key:<45} {count:>6}"
        )

    log("")
    log(
        "Official market counts:"
    )

    for key, count in sorted(
        market_counts.items()
    ):

        log(
            f"  {key:<10} {count:>6}"
        )

    log("")
    log(
        "Rejected sample rows:"
    )

    for sample in rejected_samples[:20]:

        log(
            f"  {sample}"
        )

    section(
        "BUILD UNIVERSE CANDIDATES"
    )

    candidates: List[
        Dict[str, Any]
    ] = []

    for record in official_rows:

        candidate = build_candidate(
            record
        )

        candidates.append(
            candidate
        )

    stocks = (
        deduplicate_candidates(
            candidates
        )
    )

    log(
        f"✓ Deduplicated Universe: "
        f"{len(stocks)}"
    )

    validate_universe(
        stocks
    )

    stock_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "ETF"
    )

    final_market_count = {
        "TWSE": sum(
            1
            for item in stocks.values()
            if item["market"] == "TWSE"
        ),
        "TPEX": sum(
            1
            for item in stocks.values()
            if item["market"] == "TPEX"
        ),
    }

    generated_at = (
        now_taipei()
        .isoformat()
    )

    universe = {
        "version": "UNIVERSE-BUILD",
        "generated_at": generated_at,
        "universe_count": len(
            stocks
        ),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "market_count": final_market_count,
        "source": {
            "universe_master": (
                OFFICIAL_MASTER_URL
            ),
            "source_name": (
                OFFICIAL_SOURCE_NAME
            ),
            "policy": (
                "official product master only"
            ),
            "identity_source": (
                "official Type of security "
                "and Market fields"
            ),
            "price_data_is_not_universe_source": True,
            "daily_quotes_are_not_universe_source": True,
            "finmind_is_not_identity_source": True,
            "yahoo_is_not_identity_source": True,
            "cmoney_is_not_identity_source": True,
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
            "allowed_official_types": [
                "STOCKS",
                "STOCK",
                "ETF",
            ],
            "allowed_markets": [
                "TWSE",
                "TPEX",
            ],
            "official_master_required": True,
            "official_type_required": True,
            "official_market_required": True,
            "etf_requires_official_master": True,
            "etf_6_digit_supported": True,
            "etf_letter_suffix_supported": True,
            "bond_etf_supported": True,
            "metadata_preserved": True,
            "fixed_universe_count": False,
            "daily_quote_not_used": True,
            "yahoo_not_used": True,
            "cmoney_not_used": True,
            "finmind_not_used_for_identity": True,
        },
        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda item: item[0],
            )
        ),
    }

    # --------------------------------------------------------
    # 最終完整驗證
    # --------------------------------------------------------

    section(
        "FINAL UNIVERSE VALIDATION"
    )

    validate_universe(
        universe["stocks"]
    )

    # --------------------------------------------------------
    # 特別檢查：
    # 不允許任何非官方 STOCK/ETF type。
    # --------------------------------------------------------

    invalid_types = []

    for symbol, item in (
        universe["stocks"].items()
    ):

        if item.get(
            "type"
        ) not in ALLOWED_TYPES:

            invalid_types.append(
                (
                    symbol,
                    item.get("type"),
                )
            )

        if normalize_security_type(
            item.get(
                "official_type",
                "",
            )
        ) != item.get(
            "type"
        ):

            invalid_types.append(
                (
                    symbol,
                    "OFFICIAL_TYPE_MISMATCH",
                )
            )

    if invalid_types:

        log(
            "❌ Invalid final Universe "
            "identity:"
        )

        for symbol, reason in (
            invalid_types[:50]
        ):

            log(
                f"  {symbol}: {reason}"
            )

        raise RuntimeError(
            "Final Universe identity "
            "validation failed."
        )

    log(
        "✓ FINAL VALIDATION PASSED"
    )

    return universe


# ============================================================
# ATOMIC JSON WRITE
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
        prefix=(
            f".{path.name}."
        ),
        suffix=".tmp",
        dir=str(
            path.parent
        ),
        text=True,
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
            temp_name,
            path,
        )

    except Exception:

        try:

            os.unlink(
                temp_name
            )

        except OSError:
            pass

        raise


# ============================================================
# READ-BACK VALIDATION
# ============================================================

def read_back_validate(
    path: Path,
) -> None:

    section(
        "READ-BACK VALIDATION"
    )

    if not path.exists():

        raise RuntimeError(
            "Universe file does not exist "
            "after atomic write."
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        data = json.load(
            file
        )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "Universe root is not dict."
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "Universe stocks is not dict."
        )

    validate_universe(
        stocks
    )

    if (
        data.get(
            "universe_count"
        )
        != len(stocks)
    ):

        raise RuntimeError(
            "universe_count mismatch."
        )

    actual_stock_count = sum(
        1
        for item in stocks.values()
        if item.get("type")
        == "STOCK"
    )

    actual_etf_count = sum(
        1
        for item in stocks.values()
        if item.get("type")
        == "ETF"
    )

    if (
        data.get(
            "stock_count"
        )
        != actual_stock_count
    ):

        raise RuntimeError(
            "stock_count mismatch."
        )

    if (
        data.get(
            "etf_count"
        )
        != actual_etf_count
    ):

        raise RuntimeError(
            "etf_count mismatch."
        )

    log(
        "✓ Read-back JSON is valid"
    )

    log(
        f"✓ Universe count: "
        f"{len(stocks)}"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    section(
        "TW STOCK AI SCANNER"
    )

    log(
        "BUILD UNIVERSE"
    )

    log(
        "Version: UNIVERSE-BUILD-V9.1"
    )

    log(
        f"Output: {UNIVERSE_FILE}"
    )

    try:

        universe = build_universe()

        # ----------------------------------------------------
        # 寫入前最後確認
        # ----------------------------------------------------

        section(
            "PRE-WRITE VALIDATION"
        )

        validate_universe(
            universe["stocks"]
        )

        # ----------------------------------------------------
        # Atomic write
        # ----------------------------------------------------

        section(
            "ATOMIC WRITE"
        )

        atomic_write_json(
            UNIVERSE_FILE,
            universe,
        )

        log(
            "✓ universe.json written "
            "atomically"
        )

        # ----------------------------------------------------
        # Read-back
        # ----------------------------------------------------

        read_back_validate(
            UNIVERSE_FILE
        )

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        section(
            "UNIVERSE BUILD COMPLETE"
        )

        log(
            f"Universe : "
            f"{universe['universe_count']}"
        )

        log(
            f"STOCK    : "
            f"{universe['stock_count']}"
        )

        log(
            f"ETF      : "
            f"{universe['etf_count']}"
        )

        log(
            f"TWSE     : "
            f"{universe['market_count']['TWSE']}"
        )

        log(
            f"TPEX     : "
            f"{universe['market_count']['TPEX']}"
        )

        log("")
        log(
            "✓ Data/universe.json is valid."
        )

        log(
            "✓ Official identity is valid."
        )

        log(
            "✓ STOCKS → STOCK normalization is valid."
        )

        log(
            "✓ STOCK / ETF classification "
            "is official."
        )

        log(
            "✓ No price data used."
        )

        log(
            "✓ No Yahoo used."
        )

        log(
            "✓ No CMoney used."
        )

        return 0

    except Exception as exc:

        section(
            "UNIVERSE BUILD FAILED"
        )

        log(
            f"❌ {type(exc).__name__}: "
            f"{exc}"
        )

        log("")
        log(
            "❗ Existing universe.json was "
            "NOT intentionally overwritten "
            "after validation failure."
        )

        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )