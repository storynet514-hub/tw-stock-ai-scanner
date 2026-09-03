#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

唯一職責：
    使用官方 TWSE ISIN 商品主檔建立 Data/universe.json。

核心契約：
1. Universe identity 只能來自官方商品主檔。
2. 只接受官方 Type of security == STOCKS / STOCK / ETF。
3. STOCKS 正規化為 STOCK。
4. Market 只能是 TWSE / TPEX。
5. 不使用價格、成交量、Yahoo、CMoney、FinMind 建立 Universe。
6. 不寫死任何股票 / ETF symbol。
7. 官方 Remarks 中明確標示終止上市/上櫃者不進入 active Universe。
8. 官方來源失敗或驗證失敗，不覆蓋既有 universe.json。
9. atomic write。
10. write 後 read-back validation。
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


# ============================================================================
# PATH
# ============================================================================

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================================
# OFFICIAL SOURCE
# ============================================================================

OFFICIAL_MASTER_URL = (
    "https://isin.twse.com.tw/isin/e_single_main.jsp"
)

OFFICIAL_SOURCE_NAME = "TWSE_ISIN_MASTER"


# ============================================================================
# CONFIG
# ============================================================================

REQUEST_TIMEOUT = 60
HTTP_RETRIES = 4
RETRY_SLEEP_SECONDS = 2.0

# 不是 Universe 固定數量。
# 只用來防止官方網站回傳錯誤頁/截斷頁時污染既有 Universe。
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


# ============================================================================
# HTTP SESSION
# ============================================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; tw-stock-ai-scanner/UniverseBuilder)"
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


# ============================================================================
# LOG
# ============================================================================

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


# ============================================================================
# TEXT
# ============================================================================

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


# ============================================================================
# SYMBOL VALIDATION
# ============================================================================

def is_valid_symbol(
    value: Any,
) -> bool:

    symbol = clean_symbol(value)

    if not symbol:
        return False

    return bool(
        re.fullmatch(
            r"[0-9]{4,6}[A-Z]?",
            symbol,
        )
    )


# ============================================================================
# MARKET
# ============================================================================

def normalize_market(
    value: Any,
) -> Optional[str]:

    text = normalize_text(value)

    if not text:
        return None

    if (
        text == "TWSE"
        or "TWSELISTED" in text
    ):
        return "TWSE"

    if (
        text == "TPEX"
        or "TPEXLISTED" in text
        or "OTCLISTED" in text
    ):
        return "TPEX"

    return None


# ============================================================================
# SECURITY TYPE
# ============================================================================

def normalize_security_type(
    value: Any,
) -> Optional[str]:

    text = normalize_text(value)

    if not text:
        return None

    # 官方普通股票目前使用 STOCKS。
    if text == "STOCKS":
        return "STOCK"

    # 相容其他官方資料可能使用 STOCK。
    if text == "STOCK":
        return "STOCK"

    # 官方 ETF。
    if text == "ETF":
        return "ETF"

    # 其他全部拒絕。
    return None


# ============================================================================
# HTML TABLE PARSER
# ============================================================================

class TableParser(HTMLParser):

    def __init__(self) -> None:
        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

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

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self.current_cell is not None:
            self.current_cell.append(data)

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


# ============================================================================
# HTTP
# ============================================================================

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
                f"HTTP retry "
                f"{attempt}/{HTTP_RETRIES}: "
                f"{exc}"
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


# ============================================================================
# RESPONSE DECODING
# ============================================================================

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

            if text.count(
                "\ufffd"
            ) < 3:

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


# ============================================================================
# HEADER DETECTION
# ============================================================================

HEADER_ALIASES = {
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
        .replace(
            ":",
            "",
        )
        .replace(
            "：",
            "",
        )
        .replace(
            ".",
            "",
        )
    )


def detect_header(
    rows: List[List[str]],
) -> Optional[
    Dict[str, int]
]:

    required = {
        "isin",
        "symbol",
        "name",
        "market",
        "type",
    }

    for row_index, row in enumerate(
        rows[:40]
    ):

        mapping: Dict[
            str,
            int
        ] = {}

        for index, cell in enumerate(
            row
        ):

            normalized = normalize_header(
                cell
            )

            for key, aliases in (
                HEADER_ALIASES.items()
            ):

                if normalized in aliases:

                    mapping[key] = index
                    break

        if required.issubset(
            mapping
        ):

            log(
                "Official header detected "
                f"at row {row_index + 1}"
            )

            return mapping

    return None


# ============================================================================
# ROW ACCESS
# ============================================================================

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


# ============================================================================
# LIFECYCLE
# ============================================================================

TERMINATION_MARKERS = (
    "終止上市櫃日",
    "終止上市日",
    "終止上櫃日",
    "終止櫃檯買賣日",
    "終止買賣日",
    "終止上市櫃",
    "終止上市",
    "終止上櫃",
    "終止櫃檯買賣",
)

DATE_RE = re.compile(
    r"(?:19|20)\d{6}"
)

ROC_DATE_RE = re.compile(
    r"\d{2,3}[/-]\d{1,2}[/-]\d{1,2}"
)


def termination_date_from_remarks(
    remarks: str,
) -> Optional[str]:

    text = clean_text(
        remarks
    )

    if not text:
        return None

    if not any(
        marker in text
        for marker in TERMINATION_MARKERS
    ):
        return None

    match = (
        DATE_RE.search(text)
        or ROC_DATE_RE.search(text)
    )

    if match:
        return match.group(0)

    return "marked"


# ============================================================================
# OFFICIAL ROW PARSING
# ============================================================================

def parse_official_rows(
    html: str,
) -> Tuple[
    List[Dict[str, Any]],
    Dict[str, int],
    Dict[str, int],
    List[str],
    List[str],
]:

    parser = TableParser()

    parser.feed(html)

    rows = parser.rows

    if not rows:
        raise RuntimeError(
            "Official master returned "
            "no HTML table rows."
        )

    header = detect_header(
        rows
    )

    if header is None:
        raise RuntimeError(
            "Official master header not found. "
            "Source structure may have changed."
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

    terminated_samples: List[
        str
    ] = []

    for row_index, row in enumerate(
        rows
    ):

        if row_index < 40:

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

        symbol = clean_symbol(
            symbol_raw
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

        official_type = normalize_text(
            type_raw
        )

        security_type = (
            normalize_security_type(
                type_raw
            )
        )

        market = normalize_market(
            market_raw
        )

        if official_type:

            type_counts[
                official_type
            ] = (
                type_counts.get(
                    official_type,
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

        if not is_valid_symbol(
            symbol
        ):
            continue

        if security_type not in ALLOWED_TYPES:

            if len(
                rejected_samples
            ) < 50:

                rejected_samples.append(
                    (
                        f"row={row_index + 1} "
                        f"symbol={symbol} "
                        f"type={type_raw!r} "
                        f"reason=non_universe_type"
                    )
                )

            continue

        if market not in ALLOWED_MARKETS:

            if len(
                rejected_samples
            ) < 50:

                rejected_samples.append(
                    (
                        f"row={row_index + 1} "
                        f"symbol={symbol} "
                        f"market={market_raw!r} "
                        f"reason=invalid_market"
                    )
                )

            continue

        # --------------------------------------------------------
        # Lifecycle
        #
        # 不硬編碼任何 symbol。
        # 直接使用官方 Remarks。
        # --------------------------------------------------------

        termination = (
            termination_date_from_remarks(
                remarks
            )
        )

        if termination is not None:

            if len(
                terminated_samples
            ) < 50:

                terminated_samples.append(
                    (
                        f"{symbol}:"
                        f"{termination}:"
                        f"{remarks}"
                    )
                )

            continue

        record = {
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
        terminated_samples,
    )


# ============================================================================
# INSTRUMENT CLASSIFICATION
# ============================================================================

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

    # --------------------------------------------------------
    # 注意：
    # type 已經由官方 Type of security 決定。
    #
    # category 只是 metadata。
    # 不可用分類結果改變 Universe identity。
    # --------------------------------------------------------

    if (
        "槓桿" in name
        or "LEVERAGED" in name
        or symbol.endswith("L")
    ):

        return (
            "LEVERAGED",
            "LEVERAGED",
        )

    if (
        "反向" in name
        or "INVERSE" in name
        or "BEAR" in name
        or symbol.endswith("R")
    ):

        return (
            "INVERSE",
            "INVERSE",
        )

    if (
        "債" in name
        or "BOND" in name
        or "公司債" in name
        or "公債" in name
        or symbol.endswith("B")
    ):

        return (
            "BOND",
            "BOND",
        )

    if (
        "ACTIVE" in name
        or symbol.endswith("A")
    ):

        return (
            "ACTIVE",
            "ACTIVE_EQUITY",
        )

    if symbol.endswith(
        (
            "K",
            "C",
        )
    ):

        return (
            "ETF_FX",
            "FX",
        )

    if (
        "多資產" in name
        or "MULTI ASSET" in name
        or "MULTI-ASSET" in name
        or symbol.endswith("T")
    ):

        return (
            "MULTI_ASSET",
            "MULTI_ASSET",
        )

    return (
        "EQUITY",
        "EQUITY",
    )


# ============================================================================
# FULL SYMBOL
# ============================================================================

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


# ============================================================================
# CANDIDATE
# ============================================================================

def build_candidate(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    symbol = clean_symbol(
        record["symbol"]
    )

    instrument_type, category = (
        classify_instrument(
            record
        )
    )

    return {
        "symbol": symbol,
        "full_symbol": build_full_symbol(
            symbol,
            record["market"],
        ),
        "name": clean_text(
            record.get(
                "name",
                "",
            )
        ),
        "market": record["market"],
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
        "remarks": clean_text(
            record.get(
                "remarks",
                "",
            )
        ),
        "source": OFFICIAL_SOURCE_NAME,
    }


# ============================================================================
# DEDUPLICATION
# ============================================================================

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

        existing_score = sum(
            value not in (
                "",
                None,
            )
            for value in existing.values()
        )

        candidate_score = sum(
            value not in (
                "",
                None,
            )
            for value in candidate.values()
        )

        if candidate_score > existing_score:

            result[
                symbol
            ] = candidate

    if conflicts:

        raise RuntimeError(
            "Official identity conflict detected: "
            + "; ".join(
                conflicts[:20]
            )
        )

    return result


# ============================================================================
# CANDIDATE VALIDATION
# ============================================================================

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

    if item.get(
        "market"
    ) not in ALLOWED_MARKETS:

        raise RuntimeError(
            f"Invalid market for "
            f"{symbol}: "
            f"{item.get('market')}"
        )

    if item.get(
        "type"
    ) not in ALLOWED_TYPES:

        raise RuntimeError(
            f"Invalid type for "
            f"{symbol}: "
            f"{item.get('type')}"
        )

    if item.get(
        "status"
    ) != ACTIVE_STATUS:

        raise RuntimeError(
            f"Invalid status for "
            f"{symbol}"
        )

    expected_full_symbol = (
        build_full_symbol(
            symbol,
            item["market"],
        )
    )

    if item.get(
        "full_symbol"
    ) != expected_full_symbol:

        raise RuntimeError(
            f"Invalid full_symbol for "
            f"{symbol}"
        )

    official_type = (
        normalize_security_type(
            item.get(
                "official_type",
                "",
            )
        )
    )

    if official_type != item.get(
        "type"
    ):

        raise RuntimeError(
            f"Official type mismatch for "
            f"{symbol}: "
            f"{item.get('official_type')} "
            f"!= "
            f"{item.get('type')}"
        )


# ============================================================================
# UNIVERSE VALIDATION
# ============================================================================

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
            "Official Universe size too small: "
            f"{len(stocks)} < "
            f"{MIN_OFFICIAL_SYMBOLS}. "
            "Refusing to overwrite "
            "existing universe.json."
        )

    stock_count = 0
    etf_count = 0
    twse_count = 0
    tpex_count = 0

    for symbol in sorted(
        stocks.keys()
    ):

        validate_candidate(
            symbol,
            stocks[symbol],
        )

        if stocks[symbol]["type"] == "STOCK":
            stock_count += 1
        elif stocks[symbol]["type"] == "ETF":
            etf_count += 1

        if stocks[symbol]["market"] == "TWSE":
            twse_count += 1
        elif stocks[symbol]["market"] == "TPEX":
            tpex_count += 1

    if stock_count <= 0:
        raise RuntimeError(
            "Universe contains no STOCK."
        )

    if etf_count <= 0:
        raise RuntimeError(
            "Universe contains no ETF."
        )

    if twse_count <= 0:
        raise RuntimeError(
            "Universe contains no TWSE."
        )

    if tpex_count <= 0:
        raise RuntimeError(
            "Universe contains no TPEX."
        )

    log(
        f"✓ Universe validation passed: "
        f"{len(stocks)} "
        f"/ STOCK={stock_count} "
        f"/ ETF={etf_count} "
        f"/ TWSE={twse_count} "
        f"/ TPEX={tpex_count}"
    )


# ============================================================================
# OFFICIAL MASTER FETCH
# ============================================================================

def fetch_official_master() -> str:

    section(
        "FETCH OFFICIAL ISIN MASTER"
    )

    log(
        f"Source: "
        f"{OFFICIAL_MASTER_URL}"
    )

    response = http_get(
        OFFICIAL_MASTER_URL
    )

    text = decode_response(
        response
    )

    if len(text) < 10000:

        raise RuntimeError(
            "Official master response "
            "unexpectedly small."
        )

    log(
        f"HTTP {response.status_code}"
    )

    log(
        f"Response bytes: "
        f"{len(response.content):,}"
    )

    log(
        f"Response chars: "
        f"{len(text):,}"
    )

    return text


# ============================================================================
# BUILD UNIVERSE
# ============================================================================

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
        terminated_samples,
    ) = parse_official_rows(
        html
    )

    log(
        "Accepted official STOCK/ETF "
        "rows after lifecycle filter: "
        f"{len(official_rows)}"
    )

    log(
        "Terminated official rows excluded: "
        f"{len(terminated_samples)}"
    )

    log("")
    log(
        "Official security type counts:"
    )

    for key, count in sorted(
        type_counts.items()
    ):

        log(
            f"  {key:<45} "
            f"{count:>6}"
        )

    log("")
    log(
        "Official market counts:"
    )

    for key, count in sorted(
        market_counts.items()
    ):

        log(
            f"  {key:<10} "
            f"{count:>6}"
        )

    if rejected_samples:

        log("")
        log(
            "Rejected samples:"
        )

        for sample in rejected_samples[:20]:
            log(
                f"  {sample}"
            )

    if terminated_samples:

        log("")
        log(
            "Terminated samples:"
        )

        for sample in terminated_samples[:20]:
            log(
                f"  {sample}"
            )

    candidates = [
        build_candidate(record)
        for record in official_rows
    ]

    stocks = (
        deduplicate_candidates(
            candidates
        )
    )

    log(
        f"Deduplicated Universe: "
        f"{len(stocks)}"
    )

    validate_universe(
        stocks
    )

    stock_count = sum(
        item["type"] == "STOCK"
        for item in stocks.values()
    )

    etf_count = sum(
        item["type"] == "ETF"
        for item in stocks.values()
    )

    market_count = {
        "TWSE": sum(
            item["market"] == "TWSE"
            for item in stocks.values()
        ),
        "TPEX": sum(
            item["market"] == "TPEX"
            for item in stocks.values()
        ),
    }

    universe = {
        "version": "UNIVERSE-BUILD",
        "generated_at": (
            now_taipei().isoformat()
        ),
        "universe_count": len(
            stocks
        ),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "market_count": market_count,
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
            "lifecycle_source": (
                "official Remarks field"
            ),
            "price_data_is_not_universe_source": True,
            "daily_quotes_are_not_universe_source": True,
            "yahoo_is_not_identity_source": True,
            "cmoney_is_not_identity_source": True,
            "finmind_is_not_identity_source": True,
        },
        "contract": {
            "root": "dict",
            "stocks": "dict",
            "active_status": "status == active",
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
            "lifecycle_from_official_remarks": True,
        },
        "stocks": dict(
            sorted(
                stocks.items()
            )
        ),
    }

    section(
        "FINAL UNIVERSE VALIDATION"
    )

    validate_universe(
        universe["stocks"]
    )

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
                    "INVALID_TYPE",
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

        for symbol, reason in (
            invalid_types[:50]
        ):

            log(
                f"{symbol}: "
                f"{reason}"
            )

        raise RuntimeError(
            "Final Universe identity "
            "validation failed."
        )

    log(
        "FINAL VALIDATION PASSED"
    )

    return universe


# ============================================================================
# ATOMIC WRITE
# ============================================================================

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

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:

            json.dump(
                data,
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


# ============================================================================
# READ-BACK
# ============================================================================

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

    data = json.loads(
        path.read_text(
            encoding="utf-8"
        )
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

    if data.get(
        "universe_count"
    ) != len(stocks):

        raise RuntimeError(
            "universe_count mismatch."
        )

    actual_stock_count = sum(
        item.get("type") == "STOCK"
        for item in stocks.values()
    )

    actual_etf_count = sum(
        item.get("type") == "ETF"
        for item in stocks.values()
    )

    if data.get(
        "stock_count"
    ) != actual_stock_count:

        raise RuntimeError(
            "stock_count mismatch."
        )

    if data.get(
        "etf_count"
    ) != actual_etf_count:

        raise RuntimeError(
            "etf_count mismatch."
        )

    log(
        "Read-back JSON is valid."
    )


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:

    section(
        "TW STOCK AI SCANNER"
    )

    log(
        "BUILD UNIVERSE"
    )

    log(
        f"Output: {UNIVERSE_FILE}"
    )

    try:

        universe = build_universe()

        section(
            "PRE-WRITE VALIDATION"
        )

        validate_universe(
            universe["stocks"]
        )

        section(
            "ATOMIC WRITE"
        )

        atomic_write_json(
            UNIVERSE_FILE,
            universe,
        )

        log(
            "universe.json written atomically."
        )

        read_back_validate(
            UNIVERSE_FILE
        )

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

        return 0

    except Exception as exc:

        section(
            "UNIVERSE BUILD FAILED"
        )

        log(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        log(
            "Existing universe.json was "
            "not intentionally replaced "
            "after validation failure."
        )

        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )