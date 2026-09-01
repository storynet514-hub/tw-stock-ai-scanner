#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py
============================================================

用途
------------------------------------------------------------
建立 Data/universe.json。

本檔只負責：

    官方商品資料
        ↓
    官方身份判定
        ↓
    官方市場判定
        ↓
    官方 STOCK / ETF 分類
        ↓
    排除非 Universe 商品
        ↓
    FinMind metadata 補充
        ↓
    identity cross-check
        ↓
    final validation
        ↓
    atomic write
        ↓
    read-back validation


重要設計
------------------------------------------------------------

1. 不使用任何個別股票 / ETF symbol。
2. 不依賴固定 ETF 代號。
3. 不依賴固定 4 碼 / 5 碼 / 6 碼商品清單。
4. 商品身份必須來自官方資料。
5. FinMind 不負責建立 Universe 身份。
6. FinMind 不負責決定 STOCK / ETF。
7. FinMind 只補 metadata。
8. 官方身份不足時直接 FAIL。
9. FAIL 不覆蓋既有 universe.json。
10. 寫入前 validation。
11. atomic write。
12. 寫入後重新讀取並驗證。
13. 不讀取價格資料。
14. 不讀取成交量資料。
15. 不使用 Yahoo。
16. 不使用 CMoney。


Universe 允許類型
------------------------------------------------------------

STOCK
ETF


Universe 排除類型
------------------------------------------------------------

ETN
WARRANT
REIT
TDR
一般債券
特別股
其他無法確認身份之商品


ETF 判定
------------------------------------------------------------

ETF 不依賴代碼長度。

只要官方資料明確表達為 ETF，
即可接受其 symbol 格式。

因此：

    4 碼
    5 碼
    6 碼
    數字 + 英文字母

均不因格式而直接排除。


核心原則
------------------------------------------------------------

「symbol 長得像什麼」不是身份。

「FinMind 有沒有這個 symbol」也不是身份。

「有沒有價格 / 成交量」更不是身份。

唯一身份來源：
    官方商品資料。


版本
------------------------------------------------------------

UNIVERSE-BUILD-V8
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

OFFICIAL_SOURCES = (
    (
        "TWSE_ISIN_MASTER",
        "https://isin.twse.com.tw/isin/e_single_main.jsp",
        None,
    ),
    (
        "TWSE_LISTED",
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
        "TWSE",
    ),
    (
        "TPEX_LISTED",
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4",
        "TPEX",
    ),
)


# ============================================================
# FINMIND
# ============================================================

FINMIND_API = (
    "https://api.finmindtrade.com/api/v4/data"
)

FINMIND_INFO_DATASET = "TaiwanStockInfo"
FINMIND_ACTIVE_ETF_DATASET = (
    "TaiwanStockActiveETFInfo"
)


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
# OFFICIAL TYPES
# ============================================================

TYPE_STOCK = "STOCK"
TYPE_ETF = "ETF"
TYPE_ETN = "ETN"
TYPE_WARRANT = "WARRANT"
TYPE_OTHER = "OTHER"


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
    "REAL ESTATE INVESTMENT TRUST",
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
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; "
            "tw-stock-ai-scanner/UniverseBuilder)"
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
    text = clean_text(value).upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TWSE",
        ".TPEX",
    ):
        if text.endswith(suffix):
            text = text[
                :-len(suffix)
            ]
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


def contains_any(
    text: str,
    words: Iterable[str],
) -> bool:
    normalized = normalize_text(text)

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

    text = normalize_text(value)

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
# SYMBOL PARSER
# ============================================================

def extract_symbol_from_text(
    value: Any,
) -> str:

    text = clean_text(value)

    # 允許：
    #
    # 1234
    # 12345
    # 123456
    # 1234A
    # 123456A
    #
    # 不依賴任何特定商品代碼。

    match = re.match(
        r"^\s*([0-9]{4,6}[A-Za-z]?)"
        r"(?:\s+|$)",
        text,
    )

    if match:
        symbol = clean_symbol(
            match.group(1)
        )

        if is_valid_symbol(symbol):
            return symbol

    return ""


def extract_name_from_text(
    value: Any,
) -> str:

    text = clean_text(value)

    match = re.match(
        r"^\s*[0-9]{4,6}[A-Za-z]?"
        r"\s+(.+?)\s*$",
        text,
    )

    if match:
        return clean_text(
            match.group(1)
        )

    return text


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
# RESPONSE DECODING
# ============================================================

def decode_response(
    response: requests.Response,
) -> str:

    raw = response.content

    encodings = []

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


# ============================================================
# HTTP GET
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

            log(
                f"⚠️ HTTP retry "
                f"{attempt}/{retries}: "
                f"{url}"
            )

            log(
                f"   {exc}"
            )

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
# OFFICIAL TYPE DETECTION
# ============================================================

def detect_type_from_text(
    text: str,
) -> Optional[str]:

    normalized = normalize_text(
        text
    )

    # ETF 必須優先於一般債券。
    #
    # 例如：
    # 「債券ETF」
    #
    # 仍然是 ETF，而不是一般債券。

    if (
        "ETF" in normalized
        or "指數股票型基金" in normalized
        or "指數型基金" in normalized
        or "主動式ETF" in normalized
    ):
        return TYPE_ETF

    if contains_any(
        normalized,
        ETN_WORDS,
    ):
        return TYPE_ETN

    if contains_any(
        normalized,
        WARRANT_WORDS,
    ):
        return TYPE_WARRANT

    if contains_any(
        normalized,
        REIT_WORDS,
    ):
        return TYPE_OTHER

    if contains_any(
        normalized,
        TDR_WORDS,
    ):
        return TYPE_OTHER

    if contains_any(
        normalized,
        PREFERRED_WORDS,
    ):
        return TYPE_OTHER

    if contains_any(
        normalized,
        BOND_WORDS,
    ):
        return TYPE_OTHER

    return None


# ============================================================
# OFFICIAL ROW PARSER
# ============================================================

def parse_official_rows(
    text: str,
    source_name: str,
    default_market: Optional[str],
) -> List[
    Dict[str, Any]
]:

    parser = TableParser()

    parser.feed(text)

    results: List[
        Dict[str, Any]
    ] = []

    for row in parser.rows:

        cells = [
            clean_text(cell)
            for cell in row
            if clean_text(cell)
        ]

        if not cells:
            continue

        row_text = " | ".join(cells)

        symbol = ""

        symbol_cell_index = -1

        for index, cell in enumerate(
            cells
        ):

            candidate = (
                extract_symbol_from_text(
                    cell
                )
            )

            if candidate:

                symbol = candidate
                symbol_cell_index = index
                break

        if not symbol:
            continue

        # ----------------------------------------------------
        # Name
        # ----------------------------------------------------

        name = ""

        if symbol_cell_index >= 0:

            name = (
                extract_name_from_text(
                    cells[
                        symbol_cell_index
                    ]
                )
            )

        if (
            not name
            and symbol_cell_index + 1
            < len(cells)
        ):
            name = clean_text(
                cells[
                    symbol_cell_index + 1
                ]
            )

        if not name:
            name = symbol

        # ----------------------------------------------------
        # Market
        # ----------------------------------------------------

        market = None

        for cell in cells:

            market = normalize_market(
                cell
            )

            if market:
                break

        if market is None:
            market = default_market

        if market not in ALLOWED_MARKETS:
            continue

        # ----------------------------------------------------
        # Official row evidence
        # ----------------------------------------------------

        row_type = detect_type_from_text(
            row_text
        )

        # ----------------------------------------------------
        # STOCK
        # ----------------------------------------------------
        #
        # 只有來源本身是上市 / 上櫃商品清單，
        # 且該 row 沒有被官方資料標示成
        # ETF / ETN / WARRANT / REIT /
        # TDR / preferred / bond，
        # 才能視為普通股票。
        #
        # 不依賴固定 symbol。
        # 不依賴固定股票名單。
        # ----------------------------------------------------

        if row_type is None:

            if re.fullmatch(
                r"[0-9]{4}",
                symbol,
            ):
                row_type = TYPE_STOCK

        if row_type is None:
            row_type = TYPE_OTHER

        results.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "official_type": row_type,
                "source": source_name,
                "raw": row_text,
            }
        )

    return results


# ============================================================
# MERGE OFFICIAL EVIDENCE
# ============================================================

TYPE_PRIORITY = {
    TYPE_STOCK: 30,
    TYPE_ETF: 40,
    TYPE_ETN: 20,
    TYPE_WARRANT: 10,
    TYPE_OTHER: 0,
}


def merge_official_record(
    merged: Dict[
        str,
        Dict[str, Any]
    ],
    item: Dict[str, Any],
) -> None:

    symbol = clean_symbol(
        item.get("symbol")
    )

    if not is_valid_symbol(symbol):
        return

    existing = merged.get(symbol)

    if existing is None:

        merged[symbol] = dict(item)
        return

    old_type = existing.get(
        "official_type"
    )

    new_type = item.get(
        "official_type"
    )

    old_priority = TYPE_PRIORITY.get(
        old_type,
        0,
    )

    new_priority = TYPE_PRIORITY.get(
        new_type,
        0,
    )

    # ETF / STOCK 的官方明確證據優先。
    if new_priority > old_priority:

        merged[symbol] = dict(item)

        return

    # 同級時優先保留名稱較完整者。
    old_name = clean_text(
        existing.get("name")
    )

    new_name = clean_text(
        item.get("name")
    )

    if len(new_name) > len(old_name):

        merged[symbol] = dict(item)


# ============================================================
# OFFICIAL MASTER
# ============================================================

def fetch_official_master() -> Dict[
    str,
    Dict[str, Any]
]:

    section(
        "OFFICIAL PRODUCT MASTER"
    )

    merged: Dict[
        str,
        Dict[str, Any]
    ] = {}

    source_stats: Dict[
        str,
        Dict[str, int]
    ] = {}

    successful_sources = 0

    for (
        source_name,
        url,
        default_market,
    ) in OFFICIAL_SOURCES:

        log("")
        log(
            f"→ SOURCE: {source_name}"
        )

        try:

            response = http_get(
                url
            )

            text = decode_response(
                response
            )

            log(
                f"  HTTP "
                f"{response.status_code}"
                f" bytes="
                f"{len(response.content):,}"
            )

            rows = parse_official_rows(
                text,
                source_name,
                default_market,
            )

            log(
                f"  parsed rows："
                f"{len(rows):,}"
            )

            if not rows:

                log(
                    f"  ⚠️ "
                    f"{source_name} "
                    f"解析為 0 rows"
                )

                continue

            successful_sources += 1

            stats = {
                TYPE_STOCK: 0,
                TYPE_ETF: 0,
                TYPE_ETN: 0,
                TYPE_WARRANT: 0,
                TYPE_OTHER: 0,
            }

            for item in rows:

                item_type = item.get(
                    "official_type"
                )

                if item_type in stats:
                    stats[item_type] += 1

                merge_official_record(
                    merged,
                    item,
                )

            source_stats[
                source_name
            ] = stats

        except Exception as exc:

            log(
                f"  ❌ source failed: "
                f"{source_name}"
            )

            log(
                f"     {exc}"
            )

    # --------------------------------------------------------
    # Hard failure
    # --------------------------------------------------------

    if successful_sources == 0:

        raise RuntimeError(
            "所有官方商品身份來源均失敗"
        )

    if len(merged) < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "官方商品身份資料不足："
            f"{len(merged)}"
        )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    stock_count = sum(
        1
        for item in merged.values()
        if item.get(
            "official_type"
        ) == TYPE_STOCK
    )

    etf_count = sum(
        1
        for item in merged.values()
        if item.get(
            "official_type"
        ) == TYPE_ETF
    )

    etn_count = sum(
        1
        for item in merged.values()
        if item.get(
            "official_type"
        ) == TYPE_ETN
    )

    warrant_count = sum(
        1
        for item in merged.values()
        if item.get(
            "official_type"
        ) == TYPE_WARRANT
    )

    other_count = len(
        merged
    ) - (
        stock_count
        + etf_count
        + etn_count
        + warrant_count
    )

    section(
        "OFFICIAL CLASSIFICATION"
    )

    log(
        f"✓ official symbols："
        f"{len(merged):,}"
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
        f"  ETN："
        f"{etn_count:,}"
    )

    log(
        f"  WARRANT："
        f"{warrant_count:,}"
    )

    log(
        f"  OTHER："
        f"{other_count:,}"
    )

    official_allowed = {
        symbol: item
        for symbol, item in merged.items()
        if item.get(
            "official_type"
        ) in ALLOWED_TYPES
    }

    log(
        f"✓ official STOCK/ETF："
        f"{len(official_allowed):,}"
    )

    if len(
        official_allowed
    ) < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "官方 STOCK/ETF 身份資料不足："
            f"{len(official_allowed)}"
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
        ] = (
            f"Bearer {token}"
        )

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
                    f"{status}"
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
                row
                for row in data
                if isinstance(
                    row,
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
                f"⚠️ "
                f"{attempt} failed: "
                f"{exc}"
            )

            if attempt < FINMIND_RETRIES:
                time.sleep(
                    RETRY_SLEEP_SECONDS
                    * attempt
                )

    raise RuntimeError(
        f"FinMind dataset failed: "
        f"{dataset}: "
        f"{last_error}"
    )


def fetch_finmind_identity() -> Dict[
    str,
    Dict[str, Any]
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

        if not is_valid_symbol(symbol):
            continue

        grouped.setdefault(
            symbol,
            [],
        ).append(record)

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for symbol, rows in grouped.items():

        candidates = []

        for row in rows:

            market = normalize_market(
                row.get(
                    "type"
                )
            )

            if market not in ALLOWED_MARKETS:
                continue

            date = clean_text(
                row.get(
                    "date"
                )
            )

            candidates.append(
                (
                    date,
                    market,
                    row,
                )
            )

        if not candidates:
            continue

        candidates.sort(
            key=lambda x: (
                x[0],
                x[1],
            ),
            reverse=True,
        )

        date, market, row = (
            candidates[0]
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
            "date": date,
        }

    log(
        f"✓ FinMind identity metadata："
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
# CLASSIFY
# ============================================================

def build_candidate(
    official: Dict[str, Any],
    finmind: Optional[
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

    official_type = official.get(
        "official_type"
    )

    official_market = official.get(
        "market"
    )

    official_name = clean_text(
        official.get(
            "name"
        )
    )

    # --------------------------------------------------------
    # Official hard gate
    # --------------------------------------------------------

    if not is_valid_symbol(symbol):
        return None

    if official_market not in ALLOWED_MARKETS:
        return None

    if official_type not in ALLOWED_TYPES:
        return None

    # --------------------------------------------------------
    # Defensive exclusions
    # --------------------------------------------------------

    raw = clean_text(
        official.get(
            "raw"
        )
    )

    finmind_name = clean_text(
        (
            finmind or {}
        ).get(
            "name"
        )
    )

    combined = (
        normalize_text(
            official_name
        )
        + normalize_text(
            raw
        )
    )

    # 只允許「排除」。
    # 不允許任何資料來源將 OTHER 提升成 STOCK / ETF。

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

    if contains_any(
        combined,
        PREFERRED_WORDS,
    ):
        return None

    # 一般債券排除。
    #
    # 注意：
    # ETF 已經在 official_type 通過。
    # 因此「債券 ETF」不會因為名字含債券而被
    # 直接當成一般債券。
    #
    # 只有官方 type == STOCK 時，
    # 才把債券文字視為普通債券排除。

    if (
        official_type == TYPE_STOCK
        and contains_any(
            combined,
            BOND_WORDS,
        )
    ):
        return None

    # --------------------------------------------------------
    # Name
    # --------------------------------------------------------

    name = (
        official_name
        or finmind_name
        or symbol
    )

    suffix = (
        ".TW"
        if official_market == "TWSE"
        else ".TWO"
    )

    return {
        "symbol": symbol,
        "full_symbol": (
            f"{symbol}{suffix}"
        ),
        "name": name,
        "market": official_market,
        "type": official_type,
        "instrument_type": official_type,
        "status": ACTIVE_STATUS,
        "source": "official_product_master",
        "official_type": official_type,
        "official_source": clean_text(
            official.get(
                "source"
            )
        ),
    }


# ============================================================
# METADATA
# ============================================================

def merge_metadata(
    candidate: Dict[str, Any],
    existing: Dict[str, Any],
    finmind: Optional[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    result = dict(
        candidate
    )

    # 舊 Universe 只允許保存 metadata。
    metadata_keys = (
        "description",
        "tags",
        "classification",
        "sector",
        "category",
    )

    for key in metadata_keys:

        if key in existing:
            result[key] = existing[key]

    if finmind:

        industry = clean_text(
            finmind.get(
                "industry"
            )
        )

        if industry:
            result["industry"] = industry

        date = clean_text(
            finmind.get(
                "date"
            )
        )

        if date:
            result[
                "finmind_date"
            ] = date

    return result


# ============================================================
# IDENTITY CROSS CHECK
# ============================================================

def identity_cross_check(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
    official: Dict[
        str,
        Dict[str, Any]
    ],
) -> None:

    section(
        "IDENTITY CROSS-CHECK"
    )

    errors: List[str] = []

    for symbol, item in stocks.items():

        official_item = official.get(
            symbol
        )

        if official_item is None:

            errors.append(
                f"{symbol}: "
                "missing official identity"
            )

            continue

        official_type = official_item.get(
            "official_type"
        )

        official_market = official_item.get(
            "market"
        )

        item_type = item.get(
            "type"
        )

        item_market = item.get(
            "market"
        )

        if item_type != official_type:

            errors.append(
                f"{symbol}: "
                f"type={item_type} "
                f"official={official_type}"
            )

        if item_market != official_market:

            errors.append(
                f"{symbol}: "
                f"market={item_market} "
                f"official={official_market}"
            )

        if item.get(
            "official_type"
        ) != official_type:

            errors.append(
                f"{symbol}: "
                "official_type mismatch"
            )

    if errors:

        preview = errors[:20]

        raise RuntimeError(
            "identity cross-check failed:\n"
            + "\n".join(
                f"❌ {x}"
                for x in preview
            )
        )

    log(
        f"✓ identity cross-check："
        f"{len(stocks):,} symbols"
    )


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
            "Universe 數量不足："
            f"{len(stocks)}"
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
                "item 必須為 dict"
            )

        if clean_symbol(
            item.get(
                "symbol"
            )
        ) != symbol:

            raise RuntimeError(
                f"{symbol}: "
                "symbol mismatch"
            )

        if not is_valid_symbol(
            symbol
        ):
            raise RuntimeError(
                f"{symbol}: "
                "invalid symbol"
            )

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            raise RuntimeError(
                f"{symbol}: "
                "status != active"
            )

        market = item.get(
            "market"
        )

        if market not in ALLOWED_MARKETS:

            raise RuntimeError(
                f"{symbol}: "
                "invalid market"
            )

        item_type = item.get(
            "type"
        )

        if item_type not in ALLOWED_TYPES:

            raise RuntimeError(
                f"{symbol}: "
                "invalid type"
            )

        official_item = official.get(
            symbol
        )

        if official_item is None:

            raise RuntimeError(
                f"{symbol}: "
                "not in official master"
            )

        if official_item.get(
            "official_type"
        ) != item_type:

            raise RuntimeError(
                f"{symbol}: "
                "official type mismatch"
            )

        if official_item.get(
            "market"
        ) != market:

            raise RuntimeError(
                f"{symbol}: "
                "official market mismatch"
            )

        expected_suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )

        expected_full_symbol = (
            f"{symbol}{expected_suffix}"
        )

        if clean_text(
            item.get(
                "full_symbol"
            )
        ).upper() != expected_full_symbol:

            raise RuntimeError(
                f"{symbol}: "
                "full_symbol mismatch"
            )

        if item.get(
            "source"
        ) != "official_product_master":

            raise RuntimeError(
                f"{symbol}: "
                "invalid source"
            )

        if item_type == TYPE_STOCK:
            stock_count += 1

        elif item_type == TYPE_ETF:
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

        # Temporary file must itself be valid JSON.
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
# READ-BACK VALIDATION
# ============================================================

def validate_written_file(
    payload: Dict[str, Any],
    official: Dict[
        str,
        Dict[str, Any]
    ],
) -> None:

    section(
        "POST-WRITE READ-BACK VALIDATION"
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "written payload "
            "不是 dict"
        )

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "written stocks "
            "不是 dict"
        )

    if payload.get(
        "universe_count"
    ) != len(stocks):

        raise RuntimeError(
            "written universe_count mismatch"
        )

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "type"
        ) == TYPE_STOCK
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "type"
        ) == TYPE_ETF
    )

    if payload.get(
        "stock_count"
    ) != stock_count:

        raise RuntimeError(
            "written stock_count mismatch"
        )

    if payload.get(
        "etf_count"
    ) != etf_count:

        raise RuntimeError(
            "written etf_count mismatch"
        )

    validate_universe(
        stocks,
        official,
    )

    identity_cross_check(
        stocks,
        official,
    )

    log(
        "✓ read-back validation PASS"
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
        "UNIVERSE BUILDER V8"
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

    # ========================================================
    # STEP 2
    # ========================================================

    section(
        "STEP 2 — FINMIND METADATA"
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
        "etn": 0,
        "warrant": 0,
        "reit": 0,
        "tdr": 0,
        "preferred": 0,
        "bond": 0,
        "other": 0,
        "invalid_identity": 0,
    }

    official_allowed = 0

    for symbol, official_item in (
        official.items()
    ):

        official_type = official_item.get(
            "official_type"
        )

        if official_type not in ALLOWED_TYPES:

            if official_type == TYPE_ETN:
                excluded["etn"] += 1

            elif official_type == TYPE_WARRANT:
                excluded["warrant"] += 1

            elif contains_any(
                normalize_text(
                    official_item.get(
                        "raw"
                    )
                ),
                REIT_WORDS,
            ):
                excluded["reit"] += 1

            elif contains_any(
                normalize_text(
                    official_item.get(
                        "raw"
                    )
                ),
                TDR_WORDS,
            ):
                excluded["tdr"] += 1

            elif contains_any(
                normalize_text(
                    official_item.get(
                        "raw"
                    )
                ),
                PREFERRED_WORDS,
            ):
                excluded["preferred"] += 1

            elif contains_any(
                normalize_text(
                    official_item.get(
                        "raw"
                    )
                ),
                BOND_WORDS,
            ):
                excluded["bond"] += 1

            else:
                excluded["other"] += 1

            continue

        official_allowed += 1

        candidate = build_candidate(
            official_item,
            finmind.get(symbol),
        )

        if candidate is None:

            excluded[
                "invalid_identity"
            ] += 1

            continue

        candidate = merge_metadata(
            candidate,
            existing.get(
                symbol,
                {},
            ),
            finmind.get(symbol),
        )

        stocks[symbol] = candidate

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
        "STEP 4 — IDENTITY CROSS-CHECK"
    )

    identity_cross_check(
        stocks,
        official,
    )

    # ========================================================
    # STEP 5
    # ========================================================

    section(
        "STEP 5 — FINAL VALIDATION"
    )

    validate_universe(
        stocks,
        official,
    )

    stock_count = sum(
        1
        for item in stocks.values()
        if item["type"] == TYPE_STOCK
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item["type"] == TYPE_ETF
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

    if stock_count <= 0:

        raise RuntimeError(
            "Universe 沒有 STOCK"
        )

    if etf_count <= 0:

        raise RuntimeError(
            "Universe 沒有 ETF"
        )

    # ========================================================
    # PAYLOAD
    # ========================================================

    payload: Dict[
        str,
        Any
    ] = {

        "version": "UNIVERSE-BUILD-V8",

        "generated_at": (
            now_taipei().isoformat()
        ),

        "universe_count": len(
            stocks
        ),

        "stock_count": stock_count,

        "etf_count": etf_count,

        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
        },

        "source": {

            "identity_source": (
                "official_product_master"
            ),

            "finmind_role": (
                "metadata_supplement_only"
            ),

            "official_sources": [
                {
                    "name": name,
                    "url": url,
                }
                for name, url, _ in
                OFFICIAL_SOURCES
            ],

            "universe_identity": (
                "official"
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

            "official_identity_required": True,

            "official_identity_cross_check": True,

            "finmind_metadata_only": True,

            "atomic_write": True,

            "post_write_readback": True,
        },

        "stocks": stocks,
    }

    # ========================================================
    # STEP 6
    # ========================================================

    section(
        "STEP 6 — PRE-WRITE VALIDATION"
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
            "STOCK/ETF count mismatch"
        )

    identity_cross_check(
        payload["stocks"],
        official,
    )

    validate_universe(
        payload["stocks"],
        official,
    )

    # ========================================================
    # STEP 7
    # ========================================================

    section(
        "STEP 7 — ATOMIC WRITE"
    )

    atomic_write_json(
        UNIVERSE_FILE,
        payload,
    )

    log(
        f"✓ atomic write："
        f"{UNIVERSE_FILE}"
    )

    # ========================================================
    # STEP 8
    # ========================================================

    section(
        "STEP 8 — READ-BACK"
    )

    try:

        written = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        raise RuntimeError(
            "無法重新讀取 "
            "universe.json："
            f"{exc}"
        )

    validate_written_file(
        written,
        official,
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    section(
        "UNIVERSE BUILD SUMMARY"
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

    log(
        "✓ identity cross-check PASS"
    )

    log(
        "✓ pre-write validation PASS"
    )

    log(
        "✓ atomic write PASS"
    )

    log(
        "✓ post-write read-back PASS"
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
