#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

UNIVERSE BUILDER V3
============================================================

核心架構
------------------------------------------------------------
Universe = Identity + Market + Lifecycle + Instrument Classification

Universe 絕對不因為：
    - 沒有價格
    - 沒有成交量
    - 歷史資料不足

而刪除合法商品。

Price / Volume 屬於後續 Market Data Pipeline。

資料來源優先級
------------------------------------------------------------
Identity
    1. FinMind TaiwanStockInfo
    2. FinMind TaiwanStockActiveETFInfo

Official Cross Validation
    3. TWSE 官方公開商品主檔

Lifecycle
    1. FinMind TaiwanStockDelisting
    2. 官方終止資料（若可取得）

Metadata
    舊 universe.json 僅供 metadata preservation
    不得用來復活商品

Instrument Classification
------------------------------------------------------------
允許：
    STOCK
    ETF

排除：
    WARRANT
    ETN
    REIT
    TDR
    PREFERRED
    BOND
    CONVERTIBLE_BOND
    STRUCTURED_SECURITY
    OTHER

特殊規則
------------------------------------------------------------
1. 4 / 5 / 6 碼支援
2. 字母尾碼支援
3. 6 碼 ETF 可以存在
4. 主動式 ETF 可以存在
5. 債券 ETF 可以存在
6. 特別股不得存在
7. 權證不得存在
8. ETN 不得存在
9. REIT 不得存在
10. 舊 metadata 不得復活商品
11. terminated 不得進 active
12. 不使用 Yahoo 建立 Universe
13. 不使用價格建立 Universe
14. 不使用成交量建立 Universe
15. 不使用 CMoney 建立 Universe

Atomic Write
------------------------------------------------------------
所有 validation PASS 後才覆蓋 universe.json。
任何失敗都不破壞舊 Universe。
============================================================
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
from typing import Any, Dict, List, Optional, Set, Tuple

import requests


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# API
# ============================================================

FINMIND_API = (
    "https://api.finmindtrade.com/api/v4/data"
)

FINMIND_INFO = "TaiwanStockInfo"
FINMIND_ACTIVE_ETF = "TaiwanStockActiveETFInfo"
FINMIND_DELISTING = "TaiwanStockDelisting"

# TWSE 目前可用的公開商品資料頁
TWSE_PUBLIC_MASTER_URL = (
    "https://isin.twse.com.tw/isin/e_C_public.jsp"
    "?strMode=1"
)

# 備援
TWSE_PUBLIC_MASTER_URL_UTF8 = (
    "https://isin.twse.com.tw/isin/C_public.jsp"
    "?strMode=1"
)

TWSE_DELISTED_URL = (
    "https://www.twse.com.tw/"
    "company/suspendListingCsvAndHtml"
    "?lang=zh&startYear=&type=html"
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
FINMIND_RETRIES = 3
RETRY_SLEEP = 2.0

MASTER_MIN_BYTES = 1_000
MASTER_MIN_SYMBOLS = 100

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
            "application/json,"
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
# EXCLUSION WORDS
# ============================================================

WARRANT_WORDS = (
    "權證",
    "認購權證",
    "認售權證",
    "牛證",
    "熊證",
    "認購",
    "認售",
    "WARRANT",
    "CALL WARRANT",
    "PUT WARRANT",
)

ETN_WORDS = (
    "ETN",
    "指數投資證券",
    "指數投資",
    "INDEX INVESTMENT SECURITIES",
)

REIT_WORDS = (
    "REIT",
    "REITS",
    "不動產投資信託",
    "不動產投資信託受益證券",
    "不動產投資信託基金",
    "REAL ESTATE INVESTMENT TRUST",
)

TDR_WORDS = (
    "TDR",
    "海外存託憑證",
    "存託憑證",
    "GLOBAL DEPOSITARY",
    "DEPOSITARY RECEIPT",
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
    "PREFERRED STOCK",
    "PREFERRED SHARE",
    "PREFERENCE SHARE",
)

BOND_WORDS = (
    "公司債",
    "一般債券",
    "政府債券",
    "金融債",
    "可轉換公司債",
    "可轉債",
    "債券",
    "CORPORATE BOND",
    "GOVERNMENT BOND",
    "FINANCIAL BOND",
    "CONVERTIBLE BOND",
)

STRUCTURED_WORDS = (
    "受益證券",
    "資產基礎證券",
    "結構型商品",
    "結構型證券",
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

    text = str(value)

    text = (
        text
        .replace("\ufeff", "")
        .replace("\xa0", " ")
        .replace("\u3000", " ")
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

    return (
        clean_text(value)
        .upper()
        .replace(" ", "")
        .replace("\u3000", "")
    )


# ============================================================
# SYMBOL
# ============================================================

def clean_symbol(value: Any) -> str:

    text = clean_text(value).upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TPEX",
        ".TWSE",
    ):

        if text.endswith(suffix):

            text = text[
                : -len(suffix)
            ]

            break

    text = (
        text
        .replace(" ", "")
        .replace("\u3000", "")
    )

    return text


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


# ============================================================
# MARKET
# ============================================================

def normalize_market(
    value: Any,
) -> Optional[str]:

    text = normalize_text(value)

    if not text:
        return None

    if any(
        marker in text
        for marker in (
            "TPEX",
            "TPEx",
            "上櫃",
            "櫃買",
            "OTC",
        )
    ):
        return "TPEX"

    if any(
        marker in text
        for marker in (
            "TWSE",
            "上市",
        )
    ):
        return "TWSE"

    # FinMind type 常見為 twse / tpex
    if text == "TWSE":
        return "TWSE"

    if text == "TPEX":
        return "TPEX"

    return None


# ============================================================
# CLASSIFICATION
# ============================================================

def contains_any(
    text: str,
    words: Tuple[str, ...],
) -> bool:

    normalized = normalize_text(text)

    return any(
        normalize_text(word) in normalized
        for word in words
    )


def is_etf_record(
    industry: Any,
    name: Any,
    active_etf: bool = False,
) -> bool:

    industry_text = normalize_text(
        industry
    )

    name_text = normalize_text(
        name
    )

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
    # ETF 必須優先判定
    #
    # 不能因為 ETF 名稱含「債」就被當成一般債券。
    # --------------------------------------------------------

    if is_etf:
        return False, ""

    # --------------------------------------------------------
    # CFI
    #
    # 普通股常見：
    # ESVUFR
    #
    # 特別股：
    # EPNRAR
    #
    # 這裡只用 CFI 做「強排除」，
    # 不用 CFI 反向建立 Universe。
    # --------------------------------------------------------

    if cfi_text.startswith("EPN"):
        return True, "preferred_share_cfi"

    # --------------------------------------------------------
    # 權證
    # --------------------------------------------------------

    if contains_any(
        combined,
        WARRANT_WORDS,
    ):
        return True, "warrant"

    # --------------------------------------------------------
    # ETN
    # --------------------------------------------------------

    if contains_any(
        combined,
        ETN_WORDS,
    ):
        return True, "etn"

    # --------------------------------------------------------
    # REIT
    # --------------------------------------------------------

    if contains_any(
        combined,
        REIT_WORDS,
    ):
        return True, "reit"

    # --------------------------------------------------------
    # TDR
    # --------------------------------------------------------

    if contains_any(
        combined,
        TDR_WORDS,
    ):
        return True, "tdr"

    # --------------------------------------------------------
    # Preferred
    # --------------------------------------------------------

    if contains_any(
        combined,
        PREFERRED_WORDS,
    ):
        return True, "preferred_share"

    # --------------------------------------------------------
    # Bond / CB
    # --------------------------------------------------------

    if contains_any(
        combined,
        BOND_WORDS,
    ):
        return True, "bond"

    # --------------------------------------------------------
    # Structured security
    # --------------------------------------------------------

    if contains_any(
        combined,
        STRUCTURED_WORDS,
    ):
        return True, "structured_security"

    # --------------------------------------------------------
    # 特殊 T 商品
    #
    # 01003T / 01005T / 01008T
    # 等舊式特殊證券不應進股票 Universe。
    #
    # 注意：
    # ETF 已經在前面 return。
    # --------------------------------------------------------

    if re.fullmatch(
        r"[0-9]{5}T",
        symbol,
    ):
        return True, "structured_T_security"

    # --------------------------------------------------------
    # 6 碼商品
    #
    # 6 碼不是一律排除。
    #
    # 合法 6 碼 ETF 必須保留。
    #
    # 非 ETF 的 6 碼商品，
    # 在目前 Universe 契約中視為特殊商品。
    # --------------------------------------------------------

    if is_six_digit_symbol(symbol):
        return True, "six_digit_non_etf"

    # --------------------------------------------------------
    # P 尾碼
    #
    # 73107P / 73193P 等特殊權證商品。
    # --------------------------------------------------------

    if re.fullmatch(
        r"[0-9]{5}P",
        symbol,
    ):
        return True, "structured_P_security"

    return False, ""


# ============================================================
# HTML PARSER
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

        elif tag in {
            "td",
            "th",
        }:

            if self.current_row is not None:

                self.current_cell = []

        elif tag == "br":

            if self.current_cell is not None:

                self.current_cell.append(
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

            self.current_cell.append(
                data
            )


# ============================================================
# HTTP
# ============================================================

def request(
    url: str,
    *,
    params: Optional[
        Dict[str, Any]
    ] = None,
    headers: Optional[
        Dict[str, str]
    ] = None,
    retries: int = RETRIES,
) -> requests.Response:

    last_error: Optional[
        Exception
    ] = None

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response

        except Exception as exc:

            last_error = exc

            if attempt < retries:

                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        f"HTTP request failed: "
        f"{url}: {last_error}"
    )


# ============================================================
# FINMIND
# ============================================================

def finmind_headers() -> Dict[str, str]:

    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "tw-stock-ai-scanner/"
            "universe-builder-v3"
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

        headers["Authorization"] = (
            f"Bearer {token}"
        )

    return headers


def fetch_finmind_dataset(
    dataset: str,
) -> List[Dict[str, Any]]:

    section(
        f"FINMIND — {dataset}"
    )

    params = {
        "dataset": dataset,
    }

    headers = finmind_headers()

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
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            log(
                f"→ request "
                f"{attempt}/"
                f"{FINMIND_RETRIES}"
            )

            log(
                f"  HTTP "
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
                f"⚠️ 第 {attempt} 次失敗："
                f"{exc}"
            )

            if attempt < FINMIND_RETRIES:

                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        f"FinMind {dataset} failed: "
        f"{last_error}"
    )


# ============================================================
# ACTIVE ETF
# ============================================================

def fetch_active_etfs() -> Dict[
    str,
    Dict[str, Any],
]:

    records = fetch_finmind_dataset(
        FINMIND_ACTIVE_ETF
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

        if not is_valid_symbol(
            symbol
        ):
            continue

        market = normalize_market(
            record.get(
                "type"
            )
        )

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


# ============================================================
# FINMIND IDENTITY
# ============================================================

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
        FINMIND_INFO
    )

    grouped: Dict[
        str,
        List[
            Dict[str, Any]
        ],
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
        ).append(
            record
        )

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for symbol, rows in grouped.items():

        valid_rows = []

        for row in rows:

            market = normalize_market(
                row.get(
                    "type"
                )
            )

            if market not in ALLOWED_MARKETS:
                continue

            valid_rows.append(
                (
                    clean_text(
                        row.get(
                            "date"
                        )
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
            row.get(
                "stock_name"
            )
        )

        industry = clean_text(
            row.get(
                "industry_category"
            )
        )

        is_active_etf = (
            symbol in active_etfs
        )

        is_etf = is_etf_record(
            industry,
            name,
            is_active_etf,
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
                f"→ 排除 {symbol}: "
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
            "industry_category":
                industry,
            "finmind_date":
                clean_text(
                    row.get(
                        "date"
                    )
                ),
            "source":
                "FINMIND",
        }

    # --------------------------------------------------------
    # Active ETF explicit merge
    # --------------------------------------------------------

    for symbol, etf in active_etfs.items():

        existing = result.get(
            symbol
        )

        if existing:

            existing = dict(
                existing
            )

            existing["type"] = "ETF"

            if not existing.get(
                "name"
            ):

                existing["name"] = (
                    etf.get("name")
                    or ""
                )

            existing["market"] = (
                etf["market"]
            )

            result[symbol] = existing

        else:

            result[symbol] = {
                "symbol": symbol,
                "name": (
                    etf.get("name")
                    or ""
                ),
                "market": etf[
                    "market"
                ],
                "type": "ETF",
                "industry_category":
                    "ETF",
                "finmind_date":
                    etf.get(
                        "date"
                    ),
                "source":
                    "FINMIND_ACTIVE_ETF",
            }

    log(
        f"✓ FinMind identity candidates："
        f"{len(result):,}"
    )

    return result


# ============================================================
# TWSE OFFICIAL MASTER
# ============================================================

def decode_html(
    response: requests.Response,
) -> str:

    content = response.content

    candidates = []

    content_type = response.headers.get(
        "Content-Type",
        "",
    )

    match = re.search(
        r"charset\s*=\s*"
        r"['\"]?([^;'\"]+)",
        content_type,
        flags=re.IGNORECASE,
    )

    if match:
        candidates.append(
            match.group(1)
        )

    candidates.extend(
        [
            "utf-8",
            "big5",
            "cp950",
        ]
    )

    best_text = ""
    best_score = -10**9

    for encoding in candidates:

        try:

            text = content.decode(
                encoding,
                errors="replace",
            )

            upper = text.upper()

            score = 0

            if "<HTML" in upper:
                score += 10

            if "<TABLE" in upper:
                score += 20

            if "<TR" in upper:
                score += 20

            symbol_count = len(
                re.findall(
                    r"(?<![0-9A-Z])"
                    r"[0-9]{4,6}[A-Z]?"
                    r"(?![0-9A-Z])",
                    upper,
                )
            )

            score += min(
                symbol_count,
                500,
            )

            if score > best_score:

                best_score = score
                best_text = text

        except Exception:
            continue

    return best_text


def parse_official_master(
    text: str,
) -> Dict[
    str,
    Dict[str, Any],
]:

    parser = TableParser()

    parser.feed(text)

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for row in parser.rows:

        if not row:
            continue

        joined = " | ".join(
            clean_text(cell)
            for cell in row
        )

        symbols = re.findall(
            r"(?<![0-9A-Z])"
            r"[0-9]{4,6}[A-Z]?"
            r"(?![0-9A-Z])",
            joined.upper(),
        )

        if not symbols:
            continue

        # ----------------------------------------------------
        # CFI
        # ----------------------------------------------------

        cfi = ""

        for cell in row:

            value = clean_text(
                cell
            )

            if re.fullmatch(
                r"[A-Z]{6}",
                value.upper(),
            ):

                cfi = value.upper()

        # ----------------------------------------------------
        # Market
        # ----------------------------------------------------

        market = None

        for cell in row:

            market = normalize_market(
                cell
            )

            if market:
                break

        # ----------------------------------------------------
        # Name
        # ----------------------------------------------------

        name = ""

        for cell in row:

            value = clean_text(
                cell
            )

            if not value:
                continue

            if re.fullmatch(
                r"[0-9A-Z\-\s\.]+",
                value.upper(),
            ):
                continue

            if (
                "TW000" in value.upper()
            ):
                continue

            name = value

            break

        for raw_symbol in symbols:

            symbol = clean_symbol(
                raw_symbol
            )

            if not is_valid_symbol(
                symbol
            ):
                continue

            result[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": market,
                "cfi": cfi,
                "source":
                    "TWSE_OFFICIAL",
            }

    return result


def fetch_official_master() -> Dict[
    str,
    Dict[str, Any],
]:

    section(
        "TWSE OFFICIAL CROSS VALIDATION"
    )

    urls = (
        TWSE_PUBLIC_MASTER_URL,
        TWSE_PUBLIC_MASTER_URL_UTF8,
    )

    best: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for url in urls:

        try:

            response = request(
                url,
                retries=3,
            )

            log(
                f"→ HTTP "
                f"{response.status_code}"
            )

            log(
                f"→ bytes："
                f"{len(response.content):,}"
            )

            if len(
                response.content
            ) < MASTER_MIN_BYTES:

                log(
                    "⚠️ payload 過小"
                )

                continue

            text = decode_html(
                response
            )

            parsed = parse_official_master(
                text
            )

            log(
                f"→ official symbols："
                f"{len(parsed):,}"
            )

            if len(parsed) > len(best):

                best = parsed

            if len(parsed) >= MASTER_MIN_SYMBOLS:

                log(
                    "✓ 官方商品主檔可用"
                )

                return parsed

        except Exception as exc:

            log(
                f"⚠️ 官方主檔失敗："
                f"{exc}"
            )

    if best:

        log(
            "⚠️ 官方主檔部分可用，"
            f"symbols={len(best):,}"
        )

    else:

        log(
            "⚠️ 官方主檔不可用"
        )

    return best


# ============================================================
# DELISTING
# ============================================================

def fetch_finmind_delisted() -> Set[str]:

    records = fetch_finmind_dataset(
        FINMIND_DELISTING
    )

    result: Set[str] = set()

    for record in records:

        symbol = clean_symbol(
            record.get(
                "stock_id"
            )
        )

        if is_valid_symbol(
            symbol
        ):
            result.add(
                symbol
            )

    log(
        f"✓ FinMind terminated："
        f"{len(result):,}"
    )

    return result


def fetch_official_delisted() -> Set[str]:

    section(
        "OFFICIAL TERMINATION DATA"
    )

    result: Set[str] = set()

    for url, label in (
        (
            TWSE_DELISTED_URL,
            "TWSE",
        ),
        (
            TPEX_DELISTED_URL,
            "TPEX",
        ),
    ):

        try:

            response = request(
                url,
                retries=3,
            )

            text = response.content.decode(
                "utf-8",
                errors="ignore",
            )

            before = len(result)

            for raw in re.findall(
                r"(?<![0-9A-Z])"
                r"[0-9]{4,6}[A-Z]?"
                r"(?![0-9A-Z])",
                text.upper(),
            ):

                symbol = clean_symbol(
                    raw
                )

                if is_valid_symbol(
                    symbol
                ):
                    result.add(
                        symbol
                    )

            log(
                f"✓ {label} terminated："
                f"{len(result) - before:,}"
            )

        except Exception as exc:

            log(
                f"⚠️ {label} terminated "
                f"失敗：{exc}"
            )

    log(
        f"✓ Official terminated："
        f"{len(result):,}"
    )

    return result


# ============================================================
# EXISTING UNIVERSE
# ============================================================

def load_existing() -> Dict[
    str,
    Dict[str, Any],
]:

    if not UNIVERSE_FILE.exists():
        return {}

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            payload = json.load(f)

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

        result = {}

        for key, item in stocks.items():

            if not isinstance(
                item,
                dict,
            ):
                continue

            symbol = clean_symbol(
                item.get(
                    "symbol"
                )
                or key
            )

            if not is_valid_symbol(
                symbol
            ):
                continue

            result[symbol] = item

        return result

    except Exception as exc:

        log(
            f"⚠️ 舊 Universe 讀取失敗："
            f"{exc}"
        )

        return {}


# ============================================================
# RECORD
# ============================================================

def infer_instrument_type(
    symbol: str,
    name: str,
    record_type: str,
    old: Optional[
        Dict[str, Any]
    ],
) -> str:

    if record_type == "STOCK":
        return "STOCK"

    text = normalize_text(
        name
    )

    if "主動" in text:
        return "ACTIVE"

    if (
        symbol.endswith("L")
        or "槓桿" in text
        or "LEVERAGE" in text
        or "BULL" in text
    ):
        return "LEVERAGED"

    if (
        symbol.endswith("R")
        or "反向" in text
        or "INVERSE" in text
        or "BEAR" in text
    ):
        return "INVERSE"

    if (
        "債" in text
        or "BOND" in text
    ):
        return "BOND_ETF"

    if old:

        old_type = clean_text(
            old.get(
                "instrument_type"
            )
        )

        if old_type:
            return old_type

    return "EQUITY"


def build_record(
    symbol: str,
    source: Dict[str, Any],
    official: Optional[
        Dict[str, Any]
    ],
    old: Optional[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    name = clean_text(
        source.get(
            "name"
        )
    )

    if not name and official:

        name = clean_text(
            official.get(
                "name"
            )
        )

    if not name and old:

        name = clean_text(
            old.get(
                "name"
            )
        )

    market = source.get(
        "market"
    )

    if (
        official
        and official.get(
            "market"
        ) in ALLOWED_MARKETS
    ):

        # 官方只在市場資訊有效時交叉驗證
        official_market = official[
            "market"
        ]

        if market == official_market:
            market = official_market

    if market not in ALLOWED_MARKETS:

        raise ValueError(
            f"{symbol}: invalid market"
        )

    record_type = source.get(
        "type"
    )

    if record_type not in ALLOWED_TYPES:

        raise ValueError(
            f"{symbol}: invalid type"
        )

    instrument_type = (
        infer_instrument_type(
            symbol,
            name,
            record_type,
            old,
        )
    )

    record = {
        "symbol": symbol,

        "full_symbol": (
            f"{symbol}.TW"
            if market == "TWSE"
            else f"{symbol}.TWO"
        ),

        "name": name,

        "market": market,

        "type": record_type,

        "instrument_type":
            instrument_type,

        "status":
            ACTIVE_STATUS,
    }

    # --------------------------------------------------------
    # Preserve metadata only
    # --------------------------------------------------------

    if old:

        for field in (
            "listed_date",
            "cfi_code",
            "category",
        ):

            value = old.get(
                field
            )

            if value not in (
                None,
                "",
            ):

                record[field] = value

    if official:

        cfi = clean_text(
            official.get(
                "cfi"
            )
        )

        if cfi:

            record["cfi_code"] = cfi

    if "category" not in record:

        if record_type == "STOCK":
            record["category"] = "STOCK"

        elif instrument_type == "BOND_ETF":
            record["category"] = "BOND"

        elif instrument_type == "ACTIVE":
            record["category"] = (
                "ACTIVE_EQUITY"
            )

        else:
            record["category"] = "EQUITY"

    return record


# ============================================================
# VALIDATION
# ============================================================

def validate_record(
    symbol: str,
    item: Dict[str, Any],
) -> None:

    required = {
        "symbol",
        "full_symbol",
        "name",
        "market",
        "type",
        "instrument_type",
        "status",
    }

    missing = (
        required
        - set(item.keys())
    )

    if missing:

        raise ValueError(
            f"{symbol}: missing "
            f"{sorted(missing)}"
        )

    if item["symbol"] != symbol:

        raise ValueError(
            f"{symbol}: symbol mismatch"
        )

    if not is_valid_symbol(
        symbol
    ):

        raise ValueError(
            f"{symbol}: invalid symbol"
        )

    if item["market"] not in ALLOWED_MARKETS:

        raise ValueError(
            f"{symbol}: invalid market"
        )

    if item["type"] not in ALLOWED_TYPES:

        raise ValueError(
            f"{symbol}: invalid type"
        )

    if item["status"] != ACTIVE_STATUS:

        raise ValueError(
            f"{symbol}: status invalid"
        )

    if not item["name"]:

        raise ValueError(
            f"{symbol}: empty name"
        )

    excluded, reason = (
        is_excluded_instrument(
            symbol,
            item["name"],
            item.get(
                "category",
                "",
            ),
            item.get(
                "cfi_code",
                "",
            ),
            is_etf=(
                item["type"] == "ETF"
            ),
        )
    )

    if excluded:

        raise ValueError(
            f"{symbol}: excluded "
            f"instrument survived: "
            f"{reason}"
        )


def validate_universe(
    stocks: Dict[
        str,
        Dict[str, Any],
    ],
    terminated: Set[str],
) -> None:

    if not stocks:

        raise RuntimeError(
            "Universe 為 0"
        )

    active = set(
        stocks.keys()
    )

    overlap = (
        active
        & terminated
    )

    if overlap:

        raise RuntimeError(
            "active ∩ terminated != 0: "
            f"{sorted(overlap)[:50]}"
        )

    for symbol, item in stocks.items():

        validate_record(
            symbol,
            item,
        )

    if "00838B" in active:

        raise RuntimeError(
            "00838B 仍存在 active Universe"
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

    log("")
    log(
        "UNIVERSE VALIDATION"
    )

    log(
        f"  Universe："
        f"{len(stocks):,}"
    )

    log(
        f"  STOCK："
        f"{stock_count:,}"
    )

    log(
        f"  ETF："
        f"{etf_count:,}"
    )

    log(
        f"  TWSE："
        f"{twse_count:,}"
    )

    log(
        f"  TPEX："
        f"{tpex_count:,}"
    )

    log(
        f"  Terminated blocked："
        f"{len(terminated):,}"
    )

    log(
        "✓ active ∩ terminated = 0"
    )

    log(
        "✓ schema validation PASS"
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
        prefix=path.name + ".",
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
        ) as f:

            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.write("\n")

            f.flush()

            os.fsync(
                f.fileno()
            )

        os.replace(
            temp_path,
            path,
        )

    finally:

        if temp_path.exists():

            try:
                temp_path.unlink()

            except Exception:
                pass


# ============================================================
# POST WRITE
# ============================================================

def reload_and_validate(
    path: Path,
    terminated: Set[str],
) -> Dict[str, Any]:

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:

        payload = json.load(f)

    if not isinstance(
        payload,
        dict,
    ):

        raise RuntimeError(
            "JSON root invalid"
        )

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "stocks invalid"
        )

    if payload.get(
        "universe_count"
    ) != len(stocks):

        raise RuntimeError(
            "universe_count mismatch"
        )

    validate_universe(
        stocks,
        terminated,
    )

    return payload


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    section(
        "台股 AI 選股系統"
    )

    log(
        "UNIVERSE BUILDER V3"
    )

    log(
        f"開始時間："
        f"{now_tw().isoformat()}"
    )

    log(
        f"Universe："
        f"{UNIVERSE_FILE}"
    )

    # --------------------------------------------------------
    # Existing metadata
    # --------------------------------------------------------

    existing = load_existing()

    log(
        f"既有 Universe metadata："
        f"{len(existing):,} 檔"
    )

    # --------------------------------------------------------
    # Step 1
    # --------------------------------------------------------

    section(
        "STEP 1 — FINMIND IDENTITY"
    )

    active_etfs = (
        fetch_active_etfs()
    )

    finmind = (
        fetch_finmind_identity(
            active_etfs
        )
    )

    if not finmind:

        raise RuntimeError(
            "FinMind identity "
            "沒有建立任何 candidate"
        )

    # --------------------------------------------------------
    # Step 2
    # --------------------------------------------------------

    official = (
        fetch_official_master()
    )

    # --------------------------------------------------------
    # Step 3
    # --------------------------------------------------------

    section(
        "STEP 3 — LIFECYCLE"
    )

    finmind_terminated = (
        fetch_finmind_delisted()
    )

    official_terminated = (
        fetch_official_delisted()
    )

    terminated = (
        finmind_terminated
        | official_terminated
    )

    log(
        f"✓ Terminated union："
        f"{len(terminated):,}"
    )

    # --------------------------------------------------------
    # Step 4
    # --------------------------------------------------------

    section(
        "STEP 4 — RESOLVE ACTIVE UNIVERSE"
    )

    stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    stats = {
        "finmind_candidates":
            len(finmind),

        "official_candidates":
            len(official),

        "official_overlap":
            0,

        "terminated":
            len(terminated),

        "terminated_removed":
            0,

        "excluded":
            0,

        "active":
            0,
    }

    exclusion_reasons: Dict[
        str,
        int,
    ] = {}

    for symbol in sorted(
        finmind.keys()
    ):

        source = dict(
            finmind[symbol]
        )

        # ----------------------------------------------------
        # Lifecycle hard gate
        # ----------------------------------------------------

        if symbol in terminated:

            stats[
                "terminated_removed"
            ] += 1

            continue

        # ----------------------------------------------------
        # Official cross validation
        # ----------------------------------------------------

        official_item = official.get(
            symbol
        )

        if official_item:

            stats[
                "official_overlap"
            ] += 1

            # 官方名稱只補空值
            if (
                not source.get(
                    "name"
                )
                and official_item.get(
                    "name"
                )
            ):

                source["name"] = (
                    official_item[
                        "name"
                    ]
                )

        # ----------------------------------------------------
        # ETF determination
        # ----------------------------------------------------

        is_etf = (
            source.get(
                "type"
            ) == "ETF"
        )

        name = clean_text(
            source.get(
                "name"
            )
        )

        industry = clean_text(
            source.get(
                "industry_category"
            )
        )

        cfi = ""

        if official_item:

            cfi = clean_text(
                official_item.get(
                    "cfi"
                )
            )

        # ----------------------------------------------------
        # Instrument gate
        # ----------------------------------------------------

        excluded, reason = (
            is_excluded_instrument(
                symbol,
                name,
                industry,
                cfi,
                is_etf=is_etf,
            )
        )

        if excluded:

            stats[
                "excluded"
            ] += 1

            exclusion_reasons[
                reason
            ] = (
                exclusion_reasons.get(
                    reason,
                    0,
                )
                + 1
            )

            continue

        # ----------------------------------------------------
        # Build
        # ----------------------------------------------------

        old = existing.get(
            symbol
        )

        try:

            record = build_record(
                symbol,
                source,
                official_item,
                old,
            )

            validate_record(
                symbol,
                record,
            )

            stocks[symbol] = record

        except Exception as exc:

            log(
                f"⚠️ 排除 {symbol}: "
                f"{exc}"
            )

            stats[
                "excluded"
            ] += 1

            exclusion_reasons[
                "validation"
            ] = (
                exclusion_reasons.get(
                    "validation",
                    0,
                )
                + 1
            )

    stats[
        "active"
    ] = len(stocks)

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    section(
        "UNIVERSE BUILD STATISTICS"
    )

    log(
        f"FinMind candidates："
        f"{stats['finmind_candidates']:,}"
    )

    log(
        f"Official candidates："
        f"{stats['official_candidates']:,}"
    )

    log(
        f"Official overlap："
        f"{stats['official_overlap']:,}"
    )

    log(
        f"Terminated："
        f"{stats['terminated']:,}"
    )

    log(
        f"Terminated removed："
        f"{stats['terminated_removed']:,}"
    )

    log(
        f"Excluded："
        f"{stats['excluded']:,}"
    )

    log(
        f"Active："
        f"{stats['active']:,}"
    )

    log("")

    log(
        "EXCLUSION BREAKDOWN"
    )

    for reason, count in sorted(
        exclusion_reasons.items()
    ):

        log(
            f"  {reason}: "
            f"{count:,}"
        )

    # --------------------------------------------------------
    # Explicit safety checks
    # --------------------------------------------------------

    suspicious_symbols = {
        "01003T",
        "01005T",
        "01008T",
        "2833A",
        "2883A",
        "2887C",
        "2888A",
        "2888B",
        "2891A",
        "2897A",
        "3036A",
        "3702A",
        "4129A",
        "708785",
        "709966",
        "710516",
        "710533",
        "710560",
        "710561",
        "710566",
        "710569",
        "710575",
        "711126",
        "711127",
        "711133",
        "711134",
        "711135",
        "711140",
        "711145",
        "73107P",
        "73193P",
        "8916A",
    }

    survivors = (
        suspicious_symbols
        & set(stocks.keys())
    )

    if survivors:

        raise RuntimeError(
            "特殊商品仍進入 Universe："
            f"{sorted(survivors)}"
        )

    # --------------------------------------------------------
    # Important ordinary stocks
    #
    # 這些不得因為沒有 Price 而被 Universe 排除。
    # --------------------------------------------------------

    expected_common_stocks = {
        "2017",
        "2020",
        "2022",
        "2033",
        "2034",
        "2038",
        "2059",
        "2062",
    }

    missing_common_stocks = (
        expected_common_stocks
        - set(stocks.keys())
    )

    if missing_common_stocks:

        log(
            "⚠️ 注意：普通股 identity "
            "candidate 缺少："
            f"{sorted(missing_common_stocks)}"
        )

    # --------------------------------------------------------
    # Pre-write
    # --------------------------------------------------------

    section(
        "STEP 5 — PRE-WRITE VALIDATION"
    )

    validate_universe(
        stocks,
        terminated,
    )

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

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

    payload: Dict[str, Any] = {

        "version":
            "UNIVERSE-BUILD-V3",

        "generated_at":
            now_tw().isoformat(),

        "universe_count":
            len(stocks),

        "stock_count":
            stock_count,

        "etf_count":
            etf_count,

        "market_count": {
            "TWSE":
                twse_count,

            "TPEX":
                tpex_count,
        },

        "source": {

            "identity_primary":
                "FinMind TaiwanStockInfo",

            "active_etf_source":
                "FinMind TaiwanStockActiveETFInfo",

            "termination_primary":
                "FinMind TaiwanStockDelisting",

            "official_cross_validation":
                "TWSE ISIN C_public",

            "price_is_not_identity_source":
                True,

            "volume_is_not_identity_source":
                True,

            "yahoo_is_not_identity_source":
                True,

            "cmoney_is_not_identity_source":
                True,
        },

        "contract": {

            "root":
                "dict",

            "stocks":
                "dict",

            "active_status":
                "status == active",

            "allowed_types":
                sorted(
                    ALLOWED_TYPES
                ),

            "allowed_markets":
                sorted(
                    ALLOWED_MARKETS
                ),

            "finmind_identity":
                True,

            "active_etf_supported":
                True,

            "six_digit_etf_supported":
                True,

            "bond_etf_supported":
                True,

            "preferred_share_excluded":
                True,

            "warrant_excluded":
                True,

            "etn_excluded":
                True,

            "reit_excluded":
                True,

            "tdr_excluded":
                True,

            "bond_excluded":
                True,

            "terminated_blocked":
                True,

            "metadata_preserved":
                True,

            "fixed_universe_count":
                False,

            "price_is_validation_only":
                True,

            "volume_is_validation_only":
                True,

            "old_universe_cannot_revive":
                True,
        },

        "stocks":
            dict(
                sorted(
                    stocks.items()
                )
            ),
    }

    # --------------------------------------------------------
    # Atomic write
    # --------------------------------------------------------

    section(
        "STEP 6 — ATOMIC WRITE"
    )

    atomic_write_json(
        UNIVERSE_FILE,
        payload,
    )

    log(
        f"✓ 寫入："
        f"{UNIVERSE_FILE}"
    )

    # --------------------------------------------------------
    # Post-write
    # --------------------------------------------------------

    section(
        "STEP 7 — POST-WRITE VALIDATION"
    )

    written = (
        reload_and_validate(
            UNIVERSE_FILE,
            terminated,
        )
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

    final_symbols = set(
        written["stocks"].keys()
    )

    if "00838B" in final_symbols:

        raise RuntimeError(
            "FATAL: 00838B "
            "存在於 active Universe"
        )

    log(
        "✓ 00838B 不存在"
    )

    # --------------------------------------------------------
    # Ordinary stock preservation check
    # --------------------------------------------------------

    preserved = (
        expected_common_stocks
        & final_symbols
    )

    log(
        "✓ 可辨識普通股保留："
        f"{len(preserved)}/"
        f"{len(expected_common_stocks)}"
    )

    # --------------------------------------------------------
    # Completion
    # --------------------------------------------------------

    section(
        "UNIVERSE BUILD COMPLETED"
    )

    log(
        f"完成時間："
        f"{now_tw().isoformat()}"
    )

    return 0


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        log(
            "❌ 使用者中止"
        )

        sys.exit(130)

    except Exception as exc:

        section(
            "UNIVERSE BUILD FAILED"
        )

        log(
            f"❌ {exc}"
        )

        log(
            "❌ 不覆蓋既有 "
            "universe.json"
        )

        sys.exit(1)
