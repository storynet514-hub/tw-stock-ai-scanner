#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - Scripts/build_universe.py
============================================================

唯一責任：
    建立 Data/universe.json

資料流程：
    官方商品主檔
        ↓
    確認上市 / 上櫃合法商品
        ↓
    FinMind 身份資料補充
        ↓
    排除權證 / ETN / REIT / TDR / 特別股 / 一般債券等
        ↓
    判斷 STOCK / ETF
        ↓
    status = active
        ↓
    FINAL VALIDATION
        ↓
    atomic write Data/universe.json

核心契約
------------------------------------------------------------
1. 官方商品主檔是 Universe 的唯一硬性身份來源。
2. 不使用價格、成交量、Yahoo、CMoney 判斷 Universe。
3. FinMind 只能補充身份與分類，不得創造官方主檔不存在的商品。
4. 只允許：
       market = TWSE / TPEX
       type   = STOCK / ETF
5. ETF 不因六碼而排除。
6. 債券 ETF 可以存在。
7. 一般債券不是 ETF 時排除。
8. ETN / REIT / TDR / 權證 / 特別股排除。
9. 只有 status == active 才能進 Universe。
10. 驗證全部通過後才覆蓋 universe.json。
11. 任何失敗都不得破壞既有 universe.json。
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
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# OFFICIAL DATA SOURCES
# ============================================================

# 官方 TWSE ISIN 商品主檔。
#
# e_single_main.jsp：
# 官方主要證券商品總表。
#
# strMode=2：
# 上市商品分類。
#
# strMode=4：
# 上櫃商品分類。
#
# 這三個來源都是官方 TWSE ISIN 系統。
TWSE_PUBLIC_MASTER_URLS = (
    "https://isin.twse.com.tw/isin/e_single_main.jsp",
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4",
)


# ============================================================
# FINMIND
# ============================================================

FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

FINMIND_INFO_DATASET = "TaiwanStockInfo"
FINMIND_ACTIVE_ETF_DATASET = "TaiwanStockActiveETFInfo"


# ============================================================
# CONFIG
# ============================================================

REQUEST_TIMEOUT = 60

HTTP_RETRIES = 4
FINMIND_RETRIES = 3

RETRY_SLEEP_SECONDS = 2.0

# 官方主檔正常情況遠大於這個數量。
# 低於此值直接視為官方來源異常。
MIN_OFFICIAL_SYMBOLS = 100


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

WARRANT_WORDS = (
    "權證",
    "認購權證",
    "認售權證",
    "牛證",
    "熊證",
    "WARRANT",
    "CALLWARRANT",
    "PUTWARRANT",
)

ETN_WORDS = (
    "ETN",
    "指數投資證券",
    "INDEXINVESTMENTSECURITIES",
)

REIT_WORDS = (
    "REIT",
    "REITS",
    "不動產投資信託",
    "不動產投資信託受益證券",
    "不動產投資信託基金",
    "REALESTATEINVESTMENTTRUST",
)

TDR_WORDS = (
    "TDR",
    "海外存託憑證",
    "存託憑證",
    "GLOBALDEPOSITARY",
    "DEPOSITARYRECEIPT",
)

PREFERRED_WORDS = (
    "特別股",
    "甲特",
    "乙特",
    "丙特",
    "丁特",
    "戊特",
    "優先股",
    "優先特別股",
    "PREFERREDSTOCK",
    "PREFERREDSHARE",
    "PREFERENCESHARE",
)

BOND_WORDS = (
    "公司債",
    "一般債券",
    "政府債券",
    "金融債",
    "可轉換公司債",
    "可轉債",
    "CORPORATEBOND",
    "GOVERNMENTBOND",
    "FINANCIALBOND",
    "CONVERTIBLEBOND",
)

STRUCTURED_WORDS = (
    "受益證券",
    "資產基礎證券",
    "結構型商品",
    "結構型證券",
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; tw-stock-ai-scanner/universe-builder)"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
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

        return datetime.now(ZoneInfo("Asia/Taipei"))

    except Exception:
        return datetime.now()


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\ufeff", " ")
    text = text.replace("\xa0", " ")
    text = text.replace("\u3000", " ")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = text.replace("\t", " ")

    return re.sub(r"\s+", " ", text).strip()


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
            text = text[: -len(suffix)]
            break

    return (
        text
        .replace(" ", "")
        .replace("\u3000", "")
    )


def is_valid_symbol(value: Any) -> bool:
    symbol = clean_symbol(value)

    return bool(
        re.fullmatch(
            r"[0-9]{4,6}[A-Z]?",
            symbol,
        )
    )


def is_six_digit_symbol(symbol: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9]{6}",
            symbol,
        )
    )


def contains_any(
    text: str,
    words: Iterable[str],
) -> bool:

    normalized = normalize_text(text)

    return any(
        normalize_text(word) in normalized
        for word in words
    )


# ============================================================
# MARKET
# ============================================================

def normalize_market(value: Any) -> Optional[str]:
    text = normalize_text(value)

    if not text:
        return None

    # TPEx / TPEX / OTC / 上櫃 / 櫃買
    if (
        "TPEX" in text
        or "TPEX" in text
        or "OTC" in text
        or "上櫃" in text
        or "櫃買" in text
    ):
        return "TPEX"

    # TWSE / 上市
    if (
        "TWSE" in text
        or "上市" in text
    ):
        return "TWSE"

    return None


# ============================================================
# INSTRUMENT CLASSIFICATION
# ============================================================

def is_etf_record(
    industry: Any,
    name: Any,
    active_etf: bool = False,
) -> bool:

    industry_text = normalize_text(industry)
    name_text = normalize_text(name)

    if active_etf:
        return True

    if industry_text == "ETF":
        return True

    if "ETF" in industry_text:
        return True

    if "ETF" in name_text:
        return True

    if "指數股票型基金" in name_text:
        return True

    if "主動式ETF" in name_text:
        return True

    return False


def is_excluded_instrument(
    symbol: str,
    name: str,
    industry: str,
    cfi: str = "",
    *,
    is_etf: bool = False,
) -> Tuple[bool, str]:

    combined = (
        normalize_text(name)
        + normalize_text(industry)
    )

    cfi_text = normalize_text(cfi)

    # --------------------------------------------------------
    # ETN
    # --------------------------------------------------------

    if contains_any(combined, ETN_WORDS):
        return True, "etn"

    # --------------------------------------------------------
    # REIT
    # --------------------------------------------------------

    if contains_any(combined, REIT_WORDS):
        return True, "reit"

    # --------------------------------------------------------
    # WARRANT
    # --------------------------------------------------------

    if contains_any(combined, WARRANT_WORDS):
        return True, "warrant"

    # --------------------------------------------------------
    # TDR
    # --------------------------------------------------------

    if contains_any(combined, TDR_WORDS):
        return True, "tdr"

    # --------------------------------------------------------
    # ETF 特別處理
    # --------------------------------------------------------
    #
    # ETF 可以是：
    #   4碼
    #   5碼
    #   6碼
    #   債券 ETF
    #
    # 所以不能因為「六碼」或「債券」直接排除 ETF。
    #
    if is_etf:
        return False, ""

    # --------------------------------------------------------
    # PREFERRED SHARE
    # --------------------------------------------------------

    if cfi_text.startswith("EPN"):
        return True, "preferred_share_cfi"

    if contains_any(combined, PREFERRED_WORDS):
        return True, "preferred_share"

    # --------------------------------------------------------
    # GENERAL BOND
    # --------------------------------------------------------

    if contains_any(combined, BOND_WORDS):
        return True, "bond"

    # --------------------------------------------------------
    # STRUCTURED SECURITY
    # --------------------------------------------------------

    if contains_any(combined, STRUCTURED_WORDS):
        return True, "structured_security"

    # --------------------------------------------------------
    # Structured symbols
    # --------------------------------------------------------

    if re.fullmatch(
        r"[0-9]{5}T",
        symbol,
    ):
        return True, "structured_T_security"

    if re.fullmatch(
        r"[0-9]{5}P",
        symbol,
    ):
        return True, "structured_P_security"

    # --------------------------------------------------------
    # Six digit non ETF
    # --------------------------------------------------------
    #
    # 六碼 ETF 必須保留。
    # 六碼非 ETF 商品則排除。
    #

    if is_six_digit_symbol(symbol):
        return True, "six_digit_non_etf"

    return False, ""


# ============================================================
# HTML TABLE PARSER
# ============================================================

class TableParser(HTMLParser):

    def __init__(self) -> None:

        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

        self.current_row: Optional[List[str]] = None

        self.current_cell: Optional[List[str]] = None

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
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
    retries: int = HTTP_RETRIES,
) -> requests.Response:

    last_error: Optional[Exception] = None

    for attempt in range(
        1,
        retries + 1,
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

            if attempt < retries:

                time.sleep(
                    RETRY_SLEEP_SECONDS * attempt
                )

    raise RuntimeError(
        f"HTTP request failed: {url}: {last_error}"
    )


# ============================================================
# ENCODING
# ============================================================

def decode_response(
    response: requests.Response,
) -> str:

    raw = response.content

    declared = (
        response.encoding or ""
    ).lower()

    encodings: List[str] = []

    if declared:
        encodings.append(declared)

    encodings.extend(
        [
            "utf-8",
            "big5",
            "cp950",
        ]
    )

    for encoding in encodings:

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


# ============================================================
# OFFICIAL ROW PARSER
# ============================================================

def extract_code(
    value: Any,
) -> str:

    text = clean_text(value)

    match = re.match(
        r"^([0-9]{4,6}[A-Za-z]?)\s+",
        text,
    )

    if match:

        return clean_symbol(
            match.group(1)
        )

    match = re.match(
        r"^([0-9]{4,6}[A-Za-z]?)$",
        text,
    )

    if match:

        return clean_symbol(
            match.group(1)
        )

    return ""


def extract_name(
    value: Any,
) -> str:

    text = clean_text(value)

    match = re.match(
        r"^[0-9]{4,6}[A-Za-z]?\s+(.+)$",
        text,
    )

    if match:

        return clean_text(
            match.group(1)
        )

    return text


def parse_official_rows(
    text: str,
    source_url: str,
) -> List[Dict[str, str]]:

    parser = TableParser()

    parser.feed(text)

    candidates: List[
        Dict[str, str]
    ] = []

    for row in parser.rows:

        cells = [
            clean_text(x)
            for x in row
            if clean_text(x)
        ]

        if len(cells) < 2:
            continue

        code_index = -1

        symbol = ""

        for index, cell in enumerate(cells):

            code = extract_code(cell)

            if is_valid_symbol(code):

                symbol = code

                code_index = index

                break

        if not symbol:
            continue

        name = extract_name(
            cells[code_index]
        )

        if (
            not name
            and code_index + 1 < len(cells)
        ):

            name = cells[
                code_index + 1
            ]

        market: Optional[str] = None

        for cell in cells:

            detected = normalize_market(
                cell
            )

            if detected:

                market = detected

                break

        # strMode 官方分類可以直接決定市場。
        if market is None:

            if "strMode=2" in source_url:

                market = "TWSE"

            elif "strMode=4" in source_url:

                market = "TPEX"

        if market not in ALLOWED_MARKETS:
            continue

        # 排除標題列。
        if normalize_text(name) in {
            "有價證券名稱",
            "SECURITYNAME",
        }:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "raw": " | ".join(cells),
            }
        )

    return candidates


# ============================================================
# OFFICIAL MASTER
# ============================================================

def fetch_official_master() -> Dict[
    str,
    Dict[str, Any],
]:

    section(
        "OFFICIAL PRODUCT MASTER"
    )

    merged: Dict[
        str,
        Dict[str, Any],
    ] = {}

    successful_sources = 0

    for url in TWSE_PUBLIC_MASTER_URLS:

        try:

            response = http_get(url)

            text = decode_response(
                response
            )

            log(
                "→ HTTP "
                f"{response.status_code} "
                f"bytes={len(response.content):,}"
            )

            rows = parse_official_rows(
                text,
                url,
            )

            log(
                f"→ parsed official rows："
                f"{len(rows):,}"
            )

            if rows:

                successful_sources += 1

            for item in rows:

                symbol = item[
                    "symbol"
                ]

                merged[symbol] = {
                    "symbol": symbol,
                    "name": item["name"],
                    "market": item["market"],
                    "official_source": url,
                }

        except Exception as exc:

            log(
                f"⚠️ 官方主檔失敗："
                f"{url}"
            )

            log(
                f"   {exc}"
            )

    log(
        f"→ official symbols："
        f"{len(merged):,}"
    )

    if successful_sources == 0:

        raise RuntimeError(
            "所有官方商品主檔來源都解析失敗"
        )

    if len(merged) < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "官方商品主檔不可用或解析不足："
            f"{len(merged)} < "
            f"{MIN_OFFICIAL_SYMBOLS}"
        )

    return merged


# ============================================================
# FINMIND
# ============================================================

def finmind_headers() -> Dict[str, str]:

    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "tw-stock-ai-scanner/"
            "universe-builder"
        ),
    }

    token = (
        os.environ.get(
            "FINMIND_TOKEN"
        )
        or os.environ.get(
            "FINMIND_API_TOKEN"
        )
    )

    if token:

        headers[
            "Authorization"
        ] = f"Bearer {token}"

    return headers


def fetch_finmind_dataset(
    dataset: str,
) -> List[Dict[str, Any]]:

    section(
        f"FINMIND — {dataset}"
    )

    last_error: Optional[
        Exception
    ] = None

    for attempt in range(
        1,
        FINMIND_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                FINMIND_API,
                params={
                    "dataset": dataset
                },
                headers=finmind_headers(),
                timeout=REQUEST_TIMEOUT,
            )

            log(
                f"→ request "
                f"{attempt}/"
                f"{FINMIND_RETRIES} "
                f"HTTP "
                f"{response.status_code}"
            )

            response.raise_for_status()

            payload = response.json()

            if not isinstance(
                payload,
                dict,
            ):

                raise RuntimeError(
                    "FinMind response "
                    "不是 object"
                )

            status = payload.get(
                "status"
            )

            if status not in (
                None,
                200,
                "200",
            ):

                raise RuntimeError(
                    "FinMind status="
                    f"{status}: "
                    f"{payload.get('msg', '')}"
                )

            data = payload.get(
                "data"
            )

            if not isinstance(
                data,
                list,
            ):

                raise RuntimeError(
                    "FinMind data "
                    "不是 list"
                )

            records = [
                item
                for item in data
                if isinstance(
                    item,
                    dict,
                )
            ]

            log(
                f"✓ records："
                f"{len(records):,}"
            )

            return records

        except Exception as exc:

            last_error = exc

            log(
                f"⚠️ 第 "
                f"{attempt} 次失敗："
                f"{exc}"
            )

            if attempt < FINMIND_RETRIES:

                time.sleep(
                    RETRY_SLEEP_SECONDS
                    * attempt
                )

    raise RuntimeError(
        f"FinMind {dataset} failed: "
        f"{last_error}"
    )


def fetch_active_etfs() -> Dict[
    str,
    Dict[str, Any],
]:

    records = fetch_finmind_dataset(
        FINMIND_ACTIVE_ETF_DATASET
    )

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for record in records:

        symbol = clean_symbol(
            record.get(
                "stock_id"
            )
        )

        market = normalize_market(
            record.get("type")
        )

        if not is_valid_symbol(
            symbol
        ):
            continue

        if market not in ALLOWED_MARKETS:
            continue

        result[symbol] = {
            "symbol": symbol,
            "name": clean_text(
                record.get(
                    "stock_name"
                )
            ),
            "market": market,
            "category": clean_text(
                record.get(
                    "category"
                )
            ),
            "date": clean_text(
                record.get(
                    "date"
                )
            ),
        }

    log(
        f"✓ Active ETF："
        f"{len(result):,}"
    )

    return result


def fetch_finmind_identity(
    active_etfs: Dict[
        str,
        Dict[str, Any],
    ],
) -> Dict[
    str,
    Dict[str, Any],
]:

    records = fetch_finmind_dataset(
        FINMIND_INFO_DATASET
    )

    grouped: Dict[
        str,
        List[Dict[str, Any]],
    ] = {}

    for record in records:

        symbol = clean_symbol(
            record.get(
                "stock_id"
            )
        )

        if not is_valid_symbol(
            symbol
        ):
            continue

        grouped.setdefault(
            symbol,
            [],
        ).append(record)

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for symbol, rows in grouped.items():

        valid_rows = []

        for row in rows:

            market = normalize_market(
                row.get("type")
            )

            if market in ALLOWED_MARKETS:

                valid_rows.append(
                    (
                        clean_text(
                            row.get("date")
                        ),
                        row,
                        market,
                    )
                )

        if not valid_rows:
            continue

        valid_rows.sort(
            key=lambda item: (
                item[0],
                item[2],
            ),
            reverse=True,
        )

        _, row, market = valid_rows[0]

        name = clean_text(
            row.get("stock_name")
        )

        industry = clean_text(
            row.get(
                "industry_category"
            )
        )

        is_etf = is_etf_record(
            industry,
            name,
            symbol in active_etfs,
        )

        excluded, reason = (
            is_excluded_instrument(
                symbol,
                name,
                industry,
                "",
                is_etf=is_etf,
            )
        )

        if excluded:

            log(
                f"→ FinMind 排除 "
                f"{symbol}: "
                f"{reason}"
            )

            continue

        result[symbol] = {
            "symbol": symbol,
            "name": name,
            "market": market,
            "type": (
                "ETF"
                if is_etf
                else "STOCK"
            ),
            "industry": industry,
            "date": clean_text(
                row.get("date")
            ),
        }

    log(
        f"✓ FinMind identity "
        f"candidates："
        f"{len(result):,}"
    )

    return result


# ============================================================
# EXISTING UNIVERSE
# ============================================================

def load_existing_universe() -> Dict[
    str,
    Dict[str, Any],
]:

    if not UNIVERSE_FILE.exists():

        return {}

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return {}

    if not isinstance(
        payload,
        dict,
    ):

        return {}

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        return {}

    return stocks


# ============================================================
# BUILD CANDIDATE
# ============================================================

def classify_candidate(
    official: Dict[str, Any],
    finmind: Optional[
        Dict[str, Any]
    ],
    active_etf: Optional[
        Dict[str, Any]
    ],
) -> Optional[
    Dict[str, Any]
]:

    symbol = clean_symbol(
        official.get("symbol")
    )

    market = normalize_market(
        official.get("market")
    )

    if not is_valid_symbol(
        symbol
    ):
        return None

    if market not in ALLOWED_MARKETS:
        return None

    official_name = clean_text(
        official.get("name")
    )

    finmind_name = clean_text(
        (
            finmind or {}
        ).get("name")
    )

    etf_name = clean_text(
        (
            active_etf or {}
        ).get("name")
    )

    name = (
        finmind_name
        or etf_name
        or official_name
        or symbol
    )

    industry = clean_text(
        (
            finmind or {}
        ).get("industry")
    )

    is_etf = (
        active_etf is not None
        or is_etf_record(
            industry,
            name,
        )
    )

    excluded, _ = (
        is_excluded_instrument(
            symbol,
            name,
            industry,
            "",
            is_etf=is_etf,
        )
    )

    if excluded:
        return None

    instrument_type = (
        "ETF"
        if is_etf
        else "STOCK"
    )

    suffix = (
        ".TW"
        if market == "TWSE"
        else ".TWO"
    )

    return {
        "symbol": symbol,
        "full_symbol": (
            f"{symbol}{suffix}"
        ),
        "name": name,
        "market": market,
        "type": instrument_type,
        "instrument_type": (
            "ETF"
            if is_etf
            else "STOCK"
        ),
        "status": ACTIVE_STATUS,
        "source": (
            "official_product_master"
        ),
    }


# ============================================================
# METADATA
# ============================================================

def merge_metadata(
    candidate: Dict[str, Any],
    existing: Dict[str, Any],
) -> Dict[str, Any]:

    if not isinstance(
        existing,
        dict,
    ):

        return candidate

    merged = dict(candidate)

    # 只保留舊 Universe 的附加 metadata。
    # 不允許舊資料覆蓋新的：
    # symbol / market / type / status / source。
    metadata_keys = (
        "industry",
        "category",
        "description",
        "tags",
        "classification",
        "sector",
    )

    for key in metadata_keys:

        if key in existing:

            merged[key] = existing[key]

    return merged


# ============================================================
# VALIDATION
# ============================================================

def validate_universe(
    stocks: Dict[
        str,
        Dict[str, Any],
    ],
    official: Dict[
        str,
        Dict[str, Any],
    ],
) -> None:

    if len(stocks) < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "Universe validation failed: "
            f"{len(stocks)} < "
            f"{MIN_OFFICIAL_SYMBOLS}"
        )

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"{symbol}: "
                "item is not dict"
            )

        item_symbol = clean_symbol(
            item.get("symbol")
        )

        if item_symbol != symbol:

            raise RuntimeError(
                f"{symbol}: "
                "symbol key mismatch"
            )

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            raise RuntimeError(
                f"{symbol}: "
                "status != active"
            )

        market = normalize_market(
            item.get("market")
        )

        if market not in ALLOWED_MARKETS:

            raise RuntimeError(
                f"{symbol}: "
                "invalid market"
            )

        if item.get(
            "type"
        ) not in ALLOWED_TYPES:

            raise RuntimeError(
                f"{symbol}: "
                "invalid type"
            )

        # 官方主檔硬性 gate。
        if symbol not in official:

            raise RuntimeError(
                f"{symbol}: "
                "not present in "
                "official product master"
            )

        if not is_valid_symbol(
            symbol
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid symbol"
            )

        expected_suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )

        full_symbol = clean_text(
            item.get(
                "full_symbol"
            )
        ).upper()

        if not full_symbol.endswith(
            expected_suffix
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid full_symbol"
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

    if (
        stock_count
        + etf_count
        != len(stocks)
    ):

        raise RuntimeError(
            "STOCK/ETF count mismatch"
        )

    log(
        f"✓ validation："
        f"{len(stocks):,} "
        f"active candidates"
    )

    log(
        f"  STOCK："
        f"{stock_count:,}"
    )

    log(
        f"  ETF："
        f"{etf_count:,}"
    )


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write_json(
    path: Path,
    payload: Dict[str, Any],
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
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )

            handle.write("\n")

            handle.flush()

            os.fsync(
                handle.fileno()
            )

        # 寫入正式檔之前，先確認暫存 JSON 可讀。
        json.loads(
            Path(
                temp_name
            ).read_text(
                encoding="utf-8"
            )
        )

        os.replace(
            temp_name,
            path,
        )

    finally:

        if os.path.exists(
            temp_name
        ):

            os.unlink(
                temp_name
            )


# ============================================================
# BUILD
# ============================================================

def build_universe() -> Dict[str, Any]:

    section(
        "台股 AI 選股系統"
    )

    log(
        "UNIVERSE BUILDER V5"
    )

    log(
        f"開始時間："
        f"{now_taipei().isoformat()}"
    )

    log(
        f"Universe："
        f"{UNIVERSE_FILE}"
    )

    existing = (
        load_existing_universe()
    )

    log(
        f"既有 Universe metadata："
        f"{len(existing):,} 檔"
    )

    # --------------------------------------------------------
    # STEP 1
    # --------------------------------------------------------

    section(
        "STEP 1 — IDENTITY"
    )

    official = (
        fetch_official_master()
    )

    active_etfs = (
        fetch_active_etfs()
    )

    finmind = (
        fetch_finmind_identity(
            active_etfs
        )
    )

    # --------------------------------------------------------
    # STEP 2
    # --------------------------------------------------------

    section(
        "STEP 2 — BUILD"
    )

    stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    excluded = 0

    for symbol, official_item in (
        official.items()
    ):

        candidate = (
            classify_candidate(
                official_item,
                finmind.get(symbol),
                active_etfs.get(symbol),
            )
        )

        if candidate is None:

            excluded += 1

            continue

        candidate = merge_metadata(
            candidate,
            existing.get(
                symbol,
                {},
            ),
        )

        stocks[symbol] = candidate

    log(
        f"✓ candidates："
        f"{len(stocks):,}"
    )

    log(
        f"✓ excluded："
        f"{excluded:,}"
    )

    # --------------------------------------------------------
    # STEP 3
    # --------------------------------------------------------

    section(
        "STEP 3 — VALIDATION"
    )

    validate_universe(
        stocks,
        official,
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

    twse_count = sum(
        1
        for item in stocks.values()
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item["market"] == "TPEX"
    )

    # --------------------------------------------------------
    # PAYLOAD
    # --------------------------------------------------------

    payload: Dict[str, Any] = {

        "version": "UNIVERSE-BUILD",

        "generated_at": (
            now_taipei().isoformat()
        ),

        "universe_count": len(
            stocks
        ),

        "stock_count": (
            stock_count
        ),

        "etf_count": (
            etf_count
        ),

        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
        },

        "source": {

            "universe_master": (
                TWSE_PUBLIC_MASTER_URLS[0]
            ),

            "universe_master_fallbacks": (
                list(
                    TWSE_PUBLIC_MASTER_URLS[1:]
                )
            ),

            "policy": (
                "official product master only"
            ),

            "price_data_is_not_universe_source": (
                True
            ),

            "daily_quotes_are_not_universe_source": (
                True
            ),

            "finmind_is_identity_supplement_only": (
                True
            ),
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

            "official_master_required": (
                True
            ),

            "fixed_universe_count": (
                False
            ),

            "etf_6_digit_supported": (
                True
            ),

            "bond_etf_supported": (
                True
            ),

            "metadata_preserved": (
                True
            ),

            "daily_quote_not_used": (
                True
            ),

            "cmoney_not_used": (
                True
            ),
        },

        "stocks": stocks,
    }

    # --------------------------------------------------------
    # STEP 4
    # --------------------------------------------------------

    section(
        "STEP 4 — FINAL VALIDATION"
    )

    if (
        payload["universe_count"]
        != len(payload["stocks"])
    ):

        raise RuntimeError(
            "universe_count mismatch"
        )

    if (
        payload["stock_count"]
        + payload["etf_count"]
        != payload["universe_count"]
    ):

        raise RuntimeError(
            "stock/ETF count mismatch"
        )

    validate_universe(
        payload["stocks"],
        official,
    )

    # --------------------------------------------------------
    # STEP 5
    # --------------------------------------------------------

    section(
        "STEP 5 — ATOMIC WRITE"
    )

    atomic_write_json(
        UNIVERSE_FILE,
        payload,
    )

    # 寫入後重新讀取。
    written = json.loads(
        UNIVERSE_FILE.read_text(
            encoding="utf-8"
        )
    )

    if (
        written.get(
            "universe_count"
        )
        != len(
            written.get(
                "stocks",
                {},
            )
        )
    ):

        raise RuntimeError(
            "post-write validation failed"
        )

    log(
        f"✓ 寫入："
        f"{UNIVERSE_FILE}"
    )

    log(
        f"✓ Universe："
        f"{written['universe_count']:,}"
    )

    log(
        f"✓ STOCK："
        f"{written['stock_count']:,}"
    )

    log(
        f"✓ ETF："
        f"{written['etf_count']:,}"
    )

    log(
        f"✓ TWSE："
        f"{written['market_count']['TWSE']:,}"
    )

    log(
        f"✓ TPEX："
        f"{written['market_count']['TPEX']:,}"
    )

    return written


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    try:

        build_universe()

        section(
            "UNIVERSE BUILD COMPLETED"
        )

        return 0

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


if __name__ == "__main__":
    sys.exit(
        main()
    )
