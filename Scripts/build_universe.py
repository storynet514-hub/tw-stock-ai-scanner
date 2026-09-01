#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - Scripts/build_universe.py
============================================================

唯一責任：
    建立 Data/universe.json

核心資料流：
    官方商品主檔
        ↓
    官方商品分類 / 市場 / Symbol
        ↓
    只接受官方明確確認為 STOCK / ETF
        ↓
    排除 ETN / REIT / TDR / WARRANT / 特別股 / 一般債券
        ↓
    FinMind 只補充 metadata
        ↓
    status = active
        ↓
    FINAL VALIDATION
        ↓
    atomic write Data/universe.json


核心契約
------------------------------------------------------------
1. 官方商品主檔是 Universe 的唯一硬性身份來源。
2. 官方商品主檔決定：
       symbol
       market
       STOCK / ETF
       是否屬於可納入商品
3. FinMind 不得創造商品。
4. FinMind 不得把 unknown 商品變成 STOCK。
5. FinMind 不得把官方 STOCK 改成 ETF。
6. FinMind 不得把官方 ETF 改成 STOCK。
7. 不使用價格判斷 Universe。
8. 不使用成交量判斷 Universe。
9. 不使用 Yahoo 判斷 Universe。
10. 不使用 CMoney 判斷 Universe。
11. ETF 不因四碼 / 五碼 / 六碼而排除。
12. 債券 ETF 可以存在。
13. 一般債券不是 ETF 時排除。
14. ETN / REIT / TDR / WARRANT 排除。
15. 特別股排除。
16. 只有官方明確確認為 STOCK / ETF 的商品才能進 Universe。
17. 官方分類無法確認者直接排除。
18. status 必須為 active。
19. 所有 validation 通過後才覆蓋 universe.json。
20. 任一階段失敗不得破壞既有 universe.json。
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
# OFFICIAL SOURCES
# ============================================================

# TWSE ISIN 官方商品資料。
#
# e_single_main.jsp：
# 官方主要證券商品總表。
#
# C_public.jsp?strMode=2：
# 上市商品。
#
# C_public.jsp?strMode=4：
# 上櫃商品。
#
# 注意：
# 這些頁面包含大量非 Universe 商品。
# 因此 parser 必須保留官方 section/type，
# 不能把整份資料直接視為股票。
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
# OFFICIAL SECTION NAMES
# ============================================================

SECTION_STOCK = "STOCK"
SECTION_ETF = "ETF"
SECTION_ETN = "ETN"
SECTION_WARRANT = "WARRANT"
SECTION_OTHER = "OTHER"


# ============================================================
# EXCLUSION WORDS
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

    text = text.replace("\ufeff", " ")
    text = text.replace("\xa0", " ")
    text = text.replace("\u3000", " ")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = text.replace("\t", " ")

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def normalize_text(value: Any) -> str:

    return (
        clean_text(value)
        .upper()
        .replace(" ", "")
        .replace("\u3000", "")
    )


def clean_symbol(value: Any) -> str:

    text = clean_text(
        value
    ).upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TWSE",
        ".TPEX",
    ):

        if text.endswith(suffix):

            text = text[
                : -len(suffix)
            ]

            break

    return (
        text
        .replace(" ", "")
        .replace("\u3000", "")
    )


def is_valid_symbol(
    value: Any,
) -> bool:

    symbol = clean_symbol(
        value
    )

    return bool(
        re.fullmatch(
            r"[0-9]{4,6}[A-Z]?",
            symbol,
        )
    )


def contains_any(
    text: str,
    words: Iterable[str],
) -> bool:

    normalized = normalize_text(
        text
    )

    return any(
        normalize_text(word)
        in normalized
        for word in words
    )


# ============================================================
# MARKET
# ============================================================

def normalize_market(
    value: Any,
) -> Optional[str]:

    text = normalize_text(
        value
    )

    if not text:
        return None

    if (
        "TPEX" in text
        or "OTC" in text
        or "上櫃" in text
        or "櫃買" in text
    ):
        return "TPEX"

    if (
        "TWSE" in text
        or "上市" in text
    ):
        return "TWSE"

    return None


# ============================================================
# SYMBOL / NAME
# ============================================================

def extract_code(
    value: Any,
) -> str:

    text = clean_text(
        value
    )

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

    text = clean_text(
        value
    )

    match = re.match(
        r"^[0-9]{4,6}[A-Za-z]?\s+(.+)$",
        text,
    )

    if match:

        return clean_text(
            match.group(1)
        )

    return text


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
# RESPONSE DECODING
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
        encodings.append(
            declared
        )

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
# HTTP
# ============================================================

def http_get(
    url: str,
    retries: int = HTTP_RETRIES,
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
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response

        except Exception as exc:

            last_error = exc

            if attempt < retries:

                time.sleep(
                    RETRY_SLEEP_SECONDS
                    * attempt
                )

    raise RuntimeError(
        f"HTTP request failed: "
        f"{url}: {last_error}"
    )


# ============================================================
# OFFICIAL SECTION DETECTION
# ============================================================

def detect_official_section(
    cells: List[str],
) -> Optional[str]:

    normalized = [
        normalize_text(x)
        for x in cells
    ]

    joined = "|".join(
        normalized
    )

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    if (
        "ETF" in joined
        or "指數股票型基金" in joined
        or "指數型基金" in joined
        or "主動式ETF" in joined
    ):
        return SECTION_ETF

    # --------------------------------------------------------
    # ETN
    # --------------------------------------------------------

    if (
        "ETN" in joined
        or "指數投資證券" in joined
    ):
        return SECTION_ETN

    # --------------------------------------------------------
    # WARRANT
    # --------------------------------------------------------

    if contains_any(
        joined,
        WARRANT_WORDS,
    ):
        return SECTION_WARRANT

    return None


# ============================================================
# OFFICIAL TYPE
# ============================================================

def infer_official_type(
    cells: List[str],
    symbol: str,
    name: str,
) -> str:

    normalized_cells = [
        normalize_text(x)
        for x in cells
    ]

    joined = "|".join(
        normalized_cells
    )

    name_text = normalize_text(
        name
    )

    # --------------------------------------------------------
    # Explicit ETF
    # --------------------------------------------------------

    if (
        "ETF" in joined
        or "指數股票型基金" in joined
        or "指數型基金" in joined
        or "主動式ETF" in joined
    ):

        return SECTION_ETF

    # --------------------------------------------------------
    # Explicit ETN
    # --------------------------------------------------------

    if (
        "ETN" in joined
        or "指數投資證券" in joined
    ):

        return SECTION_ETN

    # --------------------------------------------------------
    # Warrant
    # --------------------------------------------------------

    if contains_any(
        joined + name_text,
        WARRANT_WORDS,
    ):

        return SECTION_WARRANT

    # --------------------------------------------------------
    # Other explicit exclusions
    # --------------------------------------------------------

    if contains_any(
        joined + name_text,
        REIT_WORDS,
    ):

        return SECTION_OTHER

    if contains_any(
        joined + name_text,
        TDR_WORDS,
    ):

        return SECTION_OTHER

    if contains_any(
        joined + name_text,
        PREFERRED_WORDS,
    ):

        return SECTION_OTHER

    if contains_any(
        joined + name_text,
        BOND_WORDS,
    ):

        return SECTION_OTHER

    # --------------------------------------------------------
    # IMPORTANT
    #
    # 不能因為 symbol 長得像 4 碼股票就直接判 STOCK。
    #
    # 官方資料沒有明確 STOCK 身份時，
    # 回傳 UNKNOWN / OTHER。
    # --------------------------------------------------------

    # 常見普通股票：
    # 4 碼純數字。
    #
    # 但只有在官方頁面本身是上市 / 上櫃 Stocks
    # 商品清單時才允許判定。
    #
    # 本函式本身不負責猜測。
    return SECTION_OTHER


# ============================================================
# OFFICIAL ROW PARSING
# ============================================================

def parse_official_rows(
    text: str,
    source_url: str,
) -> List[
    Dict[str, Any]
]:

    parser = TableParser()

    parser.feed(
        text
    )

    candidates: List[
        Dict[str, Any]
    ] = []

    # strMode=2 = TWSE
    # strMode=4 = TPEX
    default_market: Optional[
        str
    ] = None

    if "strMode=2" in source_url:

        default_market = "TWSE"

    elif "strMode=4" in source_url:

        default_market = "TPEX"

    current_section: Optional[
        str
    ] = None

    for row in parser.rows:

        cells = [
            clean_text(x)
            for x in row
            if clean_text(x)
        ]

        if not cells:
            continue

        # ----------------------------------------------------
        # Section detection
        # ----------------------------------------------------

        row_text = normalize_text(
            " | ".join(cells)
        )

        detected_section = (
            detect_official_section(
                cells
            )
        )

        if detected_section:

            current_section = (
                detected_section
            )

        # ----------------------------------------------------
        # Find symbol
        # ----------------------------------------------------

        symbol = ""

        symbol_index = -1

        for index, cell in enumerate(
            cells
        ):

            code = extract_code(
                cell
            )

            if is_valid_symbol(
                code
            ):

                symbol = code
                symbol_index = index
                break

        if not symbol:
            continue

        # ----------------------------------------------------
        # Name
        # ----------------------------------------------------

        name = extract_name(
            cells[symbol_index]
        )

        if (
            not name
            and symbol_index + 1
            < len(cells)
        ):

            name = cells[
                symbol_index + 1
            ]

        if not name:
            name = symbol

        # ----------------------------------------------------
        # Market
        # ----------------------------------------------------

        market: Optional[
            str
        ] = None

        for cell in cells:

            detected_market = (
                normalize_market(
                    cell
                )
            )

            if detected_market:

                market = detected_market
                break

        if market is None:

            market = default_market

        if market not in ALLOWED_MARKETS:

            continue

        # ----------------------------------------------------
        # Header exclusion
        # ----------------------------------------------------

        if normalize_text(
            name
        ) in {
            "有價證券名稱",
            "證券代號",
            "證券名稱",
            "SECURITYNAME",
            "SYMBOL",
        }:

            continue

        # ----------------------------------------------------
        # Official type
        # ----------------------------------------------------

        explicit_type: Optional[
            str
        ] = None

        if (
            current_section
            == SECTION_ETF
        ):

            explicit_type = SECTION_ETF

        elif (
            current_section
            == SECTION_ETN
        ):

            explicit_type = SECTION_ETN

        elif (
            current_section
            == SECTION_WARRANT
        ):

            explicit_type = SECTION_WARRANT

        # Direct row evidence
        row_type = infer_official_type(
            cells,
            symbol,
            name,
        )

        if row_type in {
            SECTION_ETF,
            SECTION_ETN,
            SECTION_WARRANT,
        }:

            explicit_type = row_type

        # ----------------------------------------------------
        # STOCK
        # ----------------------------------------------------
        #
        # 官方上市 / 上櫃主檔中的普通股票：
        # 4碼純數字且沒有 ETF / ETN /
        # WARRANT / REIT / TDR 等標記。
        #
        # 這裡只接受明確的普通股票格式。
        # 不接受 6 碼未知商品。
        #

        if explicit_type is None:

            if re.fullmatch(
                r"[0-9]{4}",
                symbol,
            ):

                combined = (
                    normalize_text(
                        " ".join(cells)
                    )
                    + normalize_text(name)
                )

                if (
                    not contains_any(
                        combined,
                        ETN_WORDS,
                    )
                    and not contains_any(
                        combined,
                        WARRANT_WORDS,
                    )
                    and not contains_any(
                        combined,
                        REIT_WORDS,
                    )
                    and not contains_any(
                        combined,
                        TDR_WORDS,
                    )
                    and not contains_any(
                        combined,
                        PREFERRED_WORDS,
                    )
                    and not contains_any(
                        combined,
                        BOND_WORDS,
                    )
                ):

                    explicit_type = (
                        SECTION_STOCK
                    )

        if explicit_type is None:

            explicit_type = SECTION_OTHER

        candidates.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "official_type": explicit_type,
                "official_section": (
                    current_section
                    or ""
                ),
                "raw": " | ".join(
                    cells
                ),
                "official_source": (
                    source_url
                ),
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

    stats = {
        "rows": 0,
        "stock": 0,
        "etf": 0,
        "etn": 0,
        "warrant": 0,
        "other": 0,
    }

    successful_sources = 0

    for url in TWSE_PUBLIC_MASTER_URLS:

        try:

            response = http_get(
                url
            )

            text = decode_response(
                response
            )

            log(
                f"→ HTTP "
                f"{response.status_code} "
                f"bytes="
                f"{len(response.content):,}"
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

            stats["rows"] += len(
                rows
            )

            for item in rows:

                symbol = clean_symbol(
                    item.get("symbol")
                )

                if not is_valid_symbol(
                    symbol
                ):
                    continue

                official_type = item.get(
                    "official_type"
                )

                if official_type == SECTION_STOCK:
                    stats["stock"] += 1

                elif official_type == SECTION_ETF:
                    stats["etf"] += 1

                elif official_type == SECTION_ETN:
                    stats["etn"] += 1

                elif official_type == SECTION_WARRANT:
                    stats["warrant"] += 1

                else:
                    stats["other"] += 1

                # ------------------------------------------------
                # 優先級：
                # ETF > STOCK > OTHER
                #
                # 同一商品可能同時出現在多個官方來源。
                # ------------------------------------------------

                previous = merged.get(
                    symbol
                )

                if previous is None:

                    merged[symbol] = dict(
                        item
                    )

                    continue

                previous_type = previous.get(
                    "official_type"
                )

                priority = {
                    SECTION_ETF: 4,
                    SECTION_STOCK: 3,
                    SECTION_ETN: 2,
                    SECTION_WARRANT: 1,
                    SECTION_OTHER: 0,
                }

                if (
                    priority.get(
                        official_type,
                        0,
                    )
                    > priority.get(
                        previous_type,
                        0,
                    )
                ):

                    merged[symbol] = dict(
                        item
                    )

        except Exception as exc:

            log(
                f"⚠️ 官方主檔失敗："
                f"{url}"
            )

            log(
                f"   {exc}"
            )

    if successful_sources == 0:

        raise RuntimeError(
            "所有官方商品主檔來源都解析失敗"
        )

    log("")
    log(
        "OFFICIAL CLASSIFICATION"
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
        f"  ETN："
        f"{stats['etn']:,}"
    )

    log(
        f"  WARRANT："
        f"{stats['warrant']:,}"
    )

    log(
        f"  OTHER："
        f"{stats['other']:,}"
    )

    log(
        f"→ official symbols："
        f"{len(merged):,}"
    )

    if len(merged) < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "官方商品主檔不可用或解析不足："
            f"{len(merged)} < "
            f"{MIN_OFFICIAL_SYMBOLS}"
        )

    official_universe = {
        symbol: item
        for symbol, item
        in merged.items()
        if item.get(
            "official_type"
        ) in ALLOWED_TYPES
    }

    log(
        f"→ official STOCK/ETF："
        f"{len(official_universe):,}"
    )

    if len(
        official_universe
    ) < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "官方 STOCK/ETF 分類數量不足："
            f"{len(official_universe)} < "
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
) -> List[
    Dict[str, Any]
]:

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

        if not is_valid_symbol(
            symbol
        ):
            continue

        result[symbol] = {
            "symbol": symbol,
            "name": clean_text(
                record.get(
                    "stock_name"
                )
            ),
            "market": normalize_market(
                record.get(
                    "type"
                )
            ),
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
        f"✓ FinMind Active ETF："
        f"{len(result):,}"
    )

    return result


def fetch_finmind_identity() -> Dict[
    str,
    Dict[str, Any],
]:

    records = fetch_finmind_dataset(
        FINMIND_INFO_DATASET
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

            if market in ALLOWED_MARKETS:

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
            key=lambda x: (
                x[0],
                x[2],
            ),
            reverse=True,
        )

        _, row, market = (
            valid_rows[0]
        )

        result[symbol] = {
            "symbol": symbol,
            "name": clean_text(
                row.get(
                    "stock_name"
                )
            ),
            "market": market,
            "industry": clean_text(
                row.get(
                    "industry_category"
                )
            ),
            "date": clean_text(
                row.get(
                    "date"
                )
            ),
        }

    log(
        f"✓ FinMind identity records："
        f"{len(result):,}"
    )

    return result


# ============================================================
# EXISTING UNIVERSE
# ============================================================

def load_existing_universe() -> Dict[
    str,
    Dict[str, Any]
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
# CANDIDATE
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
        official.get(
            "symbol"
        )
    )

    market = official.get(
        "market"
    )

    official_type = official.get(
        "official_type"
    )

    official_name = clean_text(
        official.get(
            "name"
        )
    )

    # --------------------------------------------------------
    # HARD OFFICIAL GATE
    # --------------------------------------------------------

    if not is_valid_symbol(
        symbol
    ):

        return None

    if market not in ALLOWED_MARKETS:

        return None

    if official_type not in ALLOWED_TYPES:

        return None

    # --------------------------------------------------------
    # FINMIND IS SUPPLEMENT ONLY
    # --------------------------------------------------------

    finmind_name = clean_text(
        (
            finmind or {}
        ).get(
            "name"
        )
    )

    finmind_market = (
        (
            finmind or {}
        ).get(
            "market"
        )
    )

    finmind_industry = clean_text(
        (
            finmind or {}
        ).get(
            "industry"
        )
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # FinMind 不得改變 official_type。
    # FinMind 不得改變 official market。
    # --------------------------------------------------------

    if (
        finmind_market
        and finmind_market
        != market
    ):

        # 市場衝突：
        # 官方優先，FinMind 不得覆寫。
        log(
            f"⚠️ {symbol}: "
            f"FinMind market="
            f"{finmind_market}, "
            f"official market="
            f"{market}; "
            f"使用官方資料"
        )

    # --------------------------------------------------------
    # ETF identity
    # --------------------------------------------------------

    if official_type == SECTION_ETF:

        # ETF 名稱優先使用官方。
        name = (
            official_name
            or finmind_name
            or symbol
        )

    else:

        # STOCK 同樣以官方身份為準。
        name = (
            official_name
            or finmind_name
            or symbol
        )

    # --------------------------------------------------------
    # Defensive exclusions
    #
    # 這些規則只能把商品排除，
    # 不能把商品從 OTHER 變成 STOCK。
    # --------------------------------------------------------

    combined = (
        normalize_text(name)
        + normalize_text(
            finmind_industry
        )
        + normalize_text(
            official.get("raw")
        )
    )

    if contains_any(
        combined,
        ETN_WORDS,
    ):

        return None

    if contains_any(
        combined,
        WARRANT_WORDS,
    ):

        return None

    if contains_any(
        combined,
        REIT_WORDS,
    ):

        return None

    if contains_any(
        combined,
        TDR_WORDS,
    ):

        return None

    if official_type == SECTION_STOCK:

        if contains_any(
            combined,
            PREFERRED_WORDS,
        ):

            return None

        if contains_any(
            combined,
            BOND_WORDS,
        ):

            return None

    # --------------------------------------------------------
    # ETF:
    #
    # 六碼 OK
    # 債券 ETF OK
    # 主動式 ETF OK
    # --------------------------------------------------------

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
        "type": official_type,
        "instrument_type": official_type,
        "status": ACTIVE_STATUS,
        "source": "official_product_master",
        "official_type": official_type,
        "official_section": clean_text(
            official.get(
                "official_section"
            )
        ),
    }


# ============================================================
# METADATA MERGE
# ============================================================

def merge_metadata(
    candidate: Dict[str, Any],
    existing: Dict[str, Any],
    finmind: Optional[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    merged = dict(
        candidate
    )

    # --------------------------------------------------------
    # 舊 Universe：
    # 只能保留 metadata。
    #
    # 絕對不能覆蓋：
    # symbol
    # market
    # type
    # status
    # source
    # official_type
    # --------------------------------------------------------

    existing_metadata_keys = (
        "description",
        "tags",
        "classification",
        "sector",
        "category",
    )

    for key in existing_metadata_keys:

        if key in existing:

            merged[key] = existing[
                key
            ]

    # --------------------------------------------------------
    # FinMind metadata
    #
    # 只補充，不改身份。
    # --------------------------------------------------------

    if finmind:

        industry = clean_text(
            finmind.get(
                "industry"
            )
        )

        if industry:

            merged[
                "industry"
            ] = industry

        date = clean_text(
            finmind.get(
                "date"
            )
        )

        if date:

            merged[
                "finmind_date"
            ] = date

    return merged


# ============================================================
# VALIDATION
# ============================================================

def validate_universe(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
    official: Dict[
        str,
        Dict[str, Any]
    ],
) -> None:

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "stocks 必須為 dict"
        )

    if len(stocks) < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "Universe validation failed: "
            f"{len(stocks)} < "
            f"{MIN_OFFICIAL_SYMBOLS}"
        )

    stock_count = 0
    etf_count = 0

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"{symbol}: "
                "item is not dict"
            )

        # ----------------------------------------------------
        # symbol
        # ----------------------------------------------------

        item_symbol = clean_symbol(
            item.get(
                "symbol"
            )
        )

        if item_symbol != symbol:

            raise RuntimeError(
                f"{symbol}: "
                "symbol key mismatch"
            )

        if not is_valid_symbol(
            symbol
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid symbol"
            )

        # ----------------------------------------------------
        # status
        # ----------------------------------------------------

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            raise RuntimeError(
                f"{symbol}: "
                "status != active"
            )

        # ----------------------------------------------------
        # market
        # ----------------------------------------------------

        market = item.get(
            "market"
        )

        if market not in ALLOWED_MARKETS:

            raise RuntimeError(
                f"{symbol}: "
                "invalid market"
            )

        # ----------------------------------------------------
        # type
        # ----------------------------------------------------

        instrument_type = item.get(
            "type"
        )

        if instrument_type not in ALLOWED_TYPES:

            raise RuntimeError(
                f"{symbol}: "
                "invalid type"
            )

        # ----------------------------------------------------
        # official existence
        # ----------------------------------------------------

        if symbol not in official:

            raise RuntimeError(
                f"{symbol}: "
                "not present in "
                "official product master"
            )

        official_item = official[
            symbol
        ]

        # ----------------------------------------------------
        # HARD IDENTITY MATCH
        # ----------------------------------------------------

        if official_item.get(
            "official_type"
        ) != instrument_type:

            raise RuntimeError(
                f"{symbol}: "
                "Universe type differs "
                "from official type"
            )

        if official_item.get(
            "market"
        ) != market:

            raise RuntimeError(
                f"{symbol}: "
                "Universe market differs "
                "from official market"
            )

        # ----------------------------------------------------
        # full symbol
        # ----------------------------------------------------

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

        if full_symbol != (
            f"{symbol}{expected_suffix}"
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid full_symbol"
            )

        # ----------------------------------------------------
        # official source
        # ----------------------------------------------------

        if item.get(
            "source"
        ) != "official_product_master":

            raise RuntimeError(
                f"{symbol}: "
                "invalid source"
            )

        # ----------------------------------------------------
        # counts
        # ----------------------------------------------------

        if instrument_type == "STOCK":

            stock_count += 1

        elif instrument_type == "ETF":

            etf_count += 1

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
        f"{len(stocks):,} active"
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

            handle.write(
                "\n"
            )

            handle.flush()

            os.fsync(
                handle.fileno()
            )

        # Validate temporary JSON
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

def build_universe() -> Dict[
    str,
    Any
]:

    section(
        "台股 AI 選股系統"
    )

    log(
        "UNIVERSE BUILDER V7"
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
        f"既有 Universe："
        f"{len(existing):,} 檔"
    )

    # ========================================================
    # STEP 1
    # ========================================================

    section(
        "STEP 1 — OFFICIAL IDENTITY"
    )

    official = (
        fetch_official_master()
    )

    official_stock = sum(
        1
        for item in official.values()
        if item.get(
            "official_type"
        ) == "STOCK"
    )

    official_etf = sum(
        1
        for item in official.values()
        if item.get(
            "official_type"
        ) == "ETF"
    )

    log(
        f"✓ Official STOCK："
        f"{official_stock:,}"
    )

    log(
        f"✓ Official ETF："
        f"{official_etf:,}"
    )

    # ========================================================
    # STEP 2
    # ========================================================

    section(
        "STEP 2 — FINMIND SUPPLEMENT"
    )

    active_etfs = (
        fetch_active_etfs()
    )

    finmind = (
        fetch_finmind_identity()
    )

    # ========================================================
    # STEP 3
    # ========================================================

    section(
        "STEP 3 — BUILD"
    )

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    excluded = {
        "official_type_unknown": 0,
        "etn": 0,
        "warrant": 0,
        "reit": 0,
        "tdr": 0,
        "preferred_share": 0,
        "bond": 0,
        "other": 0,
    }

    official_allowed = 0

    for symbol, official_item in (
        official.items()
    ):

        official_type = official_item.get(
            "official_type"
        )

        if official_type not in ALLOWED_TYPES:

            excluded[
                "official_type_unknown"
            ] += 1

            continue

        official_allowed += 1

        candidate = classify_candidate(
            official_item,
            finmind.get(symbol),
            active_etfs.get(symbol),
        )

        if candidate is None:

            combined = normalize_text(
                official_item.get(
                    "raw"
                )
            )

            if contains_any(
                combined,
                ETN_WORDS,
            ):

                excluded["etn"] += 1

            elif contains_any(
                combined,
                WARRANT_WORDS,
            ):

                excluded["warrant"] += 1

            elif contains_any(
                combined,
                REIT_WORDS,
            ):

                excluded["reit"] += 1

            elif contains_any(
                combined,
                TDR_WORDS,
            ):

                excluded["tdr"] += 1

            elif contains_any(
                combined,
                PREFERRED_WORDS,
            ):

                excluded[
                    "preferred_share"
                ] += 1

            elif contains_any(
                combined,
                BOND_WORDS,
            ):

                excluded["bond"] += 1

            else:

                excluded["other"] += 1

            continue

        candidate = merge_metadata(
            candidate,
            existing.get(
                symbol,
                {},
            ),
            finmind.get(symbol),
        )

        stocks[
            symbol
        ] = candidate

    log(
        f"✓ official STOCK/ETF："
        f"{official_allowed:,}"
    )

    log(
        f"✓ Universe candidates："
        f"{len(stocks):,}"
    )

    log(
        "✓ exclusions："
    )

    for key, value in excluded.items():

        log(
            f"  {key}: "
            f"{value:,}"
        )

    # ========================================================
    # STEP 4
    # ========================================================

    section(
        "STEP 4 — VALIDATION"
    )

    validate_universe(
        stocks,
        official,
    )

    # ========================================================
    # COUNTS
    # ========================================================

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

    # ========================================================
    # PAYLOAD
    # ========================================================

    payload: Dict[
        str,
        Any
    ] = {

        "version": "UNIVERSE-BUILD-V7",

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
                "official_twse_isin"
            ),

            "official_master_urls": list(
                TWSE_PUBLIC_MASTER_URLS
            ),

            "identity_source": (
                "official_product_master"
            ),

            "classification_source": (
                "official_product_master"
            ),

            "finmind_role": (
                "metadata_supplement_only"
            ),

            "price_data_used": False,

            "volume_data_used": False,

            "yahoo_used": False,

            "cmoney_used": False,
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

            "official_type_required": (
                True
            ),

            "finmind_can_create_symbol": (
                False
            ),

            "finmind_can_change_type": (
                False
            ),

            "finmind_can_change_market": (
                False
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

            "general_bond_supported": (
                False
            ),

            "etn_supported": (
                False
            ),

            "reit_supported": (
                False
            ),

            "tdr_supported": (
                False
            ),

            "warrant_supported": (
                False
            ),

            "preferred_share_supported": (
                False
            ),

            "daily_quote_not_used": (
                True
            ),

            "price_not_used": (
                True
            ),

            "volume_not_used": (
                True
            ),

            "yahoo_not_used": (
                True
            ),

            "cmoney_not_used": (
                True
            ),
        },

        "stocks": stocks,
    }

    # ========================================================
    # STEP 5
    # ========================================================

    section(
        "STEP 5 — FINAL VALIDATION"
    )

    if payload[
        "universe_count"
    ] != len(
        payload["stocks"]
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

    # ========================================================
    # SANITY CHECK
    # ========================================================

    if stock_count <= 0:

        raise RuntimeError(
            "Universe 沒有任何 STOCK"
        )

    if etf_count <= 0:

        raise RuntimeError(
            "Universe 沒有任何 ETF"
        )

    # --------------------------------------------------------
    # 防止誤把整份官方 ISIN 商品資料寫入 Universe。
    # --------------------------------------------------------

    if (
        len(stocks)
        >= len(official)
    ):

        raise RuntimeError(
            "Universe suspiciously equals "
            "entire official master"
        )

    # ========================================================
    # STEP 6 — ATOMIC WRITE
    # ========================================================

    section(
        "STEP 6 — ATOMIC WRITE"
    )

    atomic_write_json(
        UNIVERSE_FILE,
        payload,
    )

    # ========================================================
    # POST WRITE VALIDATION
    # ========================================================

    written = json.loads(
        UNIVERSE_FILE.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        written,
        dict,
    ):

        raise RuntimeError(
            "post-write payload "
            "不是 dict"
        )

    written_stocks = written.get(
        "stocks"
    )

    if not isinstance(
        written_stocks,
        dict,
    ):

        raise RuntimeError(
            "post-write stocks "
            "不是 dict"
        )

    if written.get(
        "universe_count"
    ) != len(
        written_stocks
    ):

        raise RuntimeError(
            "post-write universe_count "
            "mismatch"
        )

    if (
        written.get(
            "stock_count"
        )
        + written.get(
            "etf_count"
        )
        != written.get(
            "universe_count"
        )
    ):

        raise RuntimeError(
            "post-write STOCK/ETF "
            "count mismatch"
        )

    for symbol, item in (
        written_stocks.items()
    ):

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            raise RuntimeError(
                f"post-write {symbol}: "
                "status != active"
            )

        if item.get(
            "type"
        ) not in ALLOWED_TYPES:

            raise RuntimeError(
                f"post-write {symbol}: "
                "invalid type"
            )

        if symbol not in official:

            raise RuntimeError(
                f"post-write {symbol}: "
                "not official"
            )

        if (
            item.get(
                "type"
            )
            != official[symbol].get(
                "official_type"
            )
        ):

            raise RuntimeError(
                f"post-write {symbol}: "
                "official type mismatch"
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
