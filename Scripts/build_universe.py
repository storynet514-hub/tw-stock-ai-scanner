#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

UNIVERSE BUILDER V2
============================================================

設計目標
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 輸出
2. Universe 不依賴單一 HTML endpoint
3. FinMind TaiwanStockInfo 作為穩定的市場身份資料來源
4. FinMind TaiwanStockActiveETFInfo 補充主動式 ETF
5. FinMind TaiwanStockDelisting 作為終止商品硬阻擋
6. TWSE ISIN 商品主檔正常時進行官方交叉驗證
7. TWSE ISIN endpoint 異常時，不因 HTTP 200 + 800 bytes 直接失敗
8. 不使用價格資料單獨建立 Universe
9. 不使用成交量單獨建立 Universe
10. 價格 / 成交量屬於後續 Market Validation
11. Yahoo 不作為 Universe identity source
12. 不依賴 CMoney 建立 Universe
13. 不寫死 Universe 數量
14. 支援 4 / 5 / 6 碼
15. 支援字母尾碼，例如 00631L / 00632R / 00400A
16. 支援 6 碼 ETF
17. 支援債券 ETF
18. 支援主動式 ETF
19. 排除權證
20. 排除 ETN
21. 排除 REIT
22. 排除 TDR
23. 排除一般債券 / 公司債 / 可轉債
24. 排除興櫃
25. 官方 / FinMind 已終止商品不得進 active Universe
26. 舊 universe metadata 只能保留資訊，不得復活商品
27. active ∩ terminated 必須為空
28. 00838B 不得進 active Universe
29. build 失敗時絕不覆蓋既有 universe.json
30. Atomic Write
31. 寫入後重新讀取驗證
============================================================

資料優先級
------------------------------------------------------------

Identity / Market
    1. FinMind TaiwanStockInfo
    2. FinMind TaiwanStockActiveETFInfo
    3. TWSE ISIN 官方商品主檔交叉驗證

Lifecycle
    1. FinMind TaiwanStockDelisting
    2. 官方 TWSE / TPEx 終止資料
    3. 舊 Universe 僅作 metadata，不作 lifecycle authority

Market price / volume
    本腳本不負責建立價格資料。
    後續由 fetch_prices.py / Market Validation 處理。

重要原則
------------------------------------------------------------
「沒有價格」不是不存在。
「沒有成交量」不是不存在。
「舊資料存在」也不是仍然 active。

只有 identity + market + lifecycle 通過，
才可以進 active Universe。
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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# URL
# ============================================================

FINMIND_API = (
    "https://api.finmindtrade.com/api/v4/data"
)

TWSE_MASTER_URL = (
    "https://isin.twse.com.tw/isin/e_single_main.jsp"
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
RETRY_SLEEP = 2.0

FINMIND_RETRIES = 3

MASTER_MIN_BYTES = 10_000
MASTER_MIN_ROWS = 100

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

EXCLUDED_WORDS = (
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

    "REIT",
    "不動產投資信託",
    "REAL ESTATE INVESTMENT TRUST",

    "TDR",
    "海外存託憑證",
    "GLOBAL DEPOSITARY",
    "DEPOSITARY RECEIPT",

    "特別股",
    "PREFERRED STOCK",
    "PREFERRED SHARE",

    "公司債",
    "一般債券",
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


# ============================================================
# MARKET
# ============================================================

def normalize_market(value: Any) -> Optional[str]:
    text = normalize_text(value)

    if not text:
        return None

    if any(
        marker in text
        for marker in (
            "TPEX",
            "TPEx",
            "上櫃",
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

    return None


# ============================================================
# TYPE / CATEGORY
# ============================================================

def contains_excluded_word(
    *values: Any,
) -> bool:

    combined = "".join(
        normalize_text(value)
        for value in values
        if value is not None
    )

    for word in EXCLUDED_WORDS:
        if normalize_text(word) in combined:
            return True

    return False


def is_etf_record(
    industry_category: Any,
    name: Any,
) -> bool:

    industry = normalize_text(
        industry_category
    )

    name_text = normalize_text(
        name
    )

    if industry == "ETF":
        return True

    if "ETF" in industry:
        return True

    if "ETF" in name_text:
        return True

    if "指數股票型基金" in name_text:
        return True

    if "主動式ETF" in name_text:
        return True

    return False


# ============================================================
# HTML TABLE
# ============================================================

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
                self.current_cell.append(
                    " "
                )

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
            self.current_cell.append(
                data
            )


# ============================================================
# HTTP HELPERS
# ============================================================

def request_bytes(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
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

    headers = finmind_headers()

    params = {
        "dataset": dataset,
    }

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
                f"→ request {attempt}/"
                f"{FINMIND_RETRIES}"
            )

            log(
                f"  HTTP {response.status_code}"
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

            if (
                status not in (
                    None,
                    200,
                    "200",
                )
            ):
                raise RuntimeError(
                    "FinMind API status="
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
                    "FinMind data 不是 list"
                )

            records = []

            for item in data:

                if isinstance(
                    item,
                    dict,
                ):
                    records.append(
                        item
                    )

            log(
                f"✓ records："
                f"{len(records):,}"
            )

            return records

        except Exception as exc:

            last_error = exc

            log(
                f"⚠️ FinMind 第 "
                f"{attempt}/"
                f"{FINMIND_RETRIES} 次失敗："
                f"{exc}"
            )

            if attempt < FINMIND_RETRIES:

                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        f"FinMind dataset "
        f"{dataset} failed: "
        f"{last_error}"
    )


# ============================================================
# FINMIND: ACTIVE ETF
# ============================================================

def fetch_active_etf_info() -> Dict[str, Dict[str, Any]]:
    try:

        records = fetch_finmind_dataset(
            "TaiwanStockActiveETFInfo"
        )

    except Exception as exc:

        log(
            "⚠️ 主動式 ETF dataset "
            f"無法取得：{exc}"
        )

        return {}

    result: Dict[
        str,
        Dict[str, Any]
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
        f"✓ Active ETF records："
        f"{len(result):,}"
    )

    return result


# ============================================================
# FINMIND: DELISTING
# ============================================================

def fetch_delisted_symbols() -> Set[str]:

    records = fetch_finmind_dataset(
        "TaiwanStockDelisting"
    )

    symbols: Set[str] = set()

    for record in records:

        symbol = clean_symbol(
            record.get(
                "stock_id"
            )
        )

        if is_valid_symbol(
            symbol
        ):
            symbols.add(
                symbol
            )

    log(
        f"✓ FinMind terminated symbols："
        f"{len(symbols):,}"
    )

    return symbols


# ============================================================
# FINMIND: STOCK INFO
# ============================================================

def fetch_finmind_universe(
    active_etfs: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    records = fetch_finmind_dataset(
        "TaiwanStockInfo"
    )

    grouped: Dict[
        str,
        List[Dict[str, Any]]
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
            []
        ).append(
            record
        )

    result: Dict[
        str,
        Dict[str, Any]
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

        # FinMind 官方文件要求：
        # 同一 stock_id 取 date 最新的一筆。
        valid_rows.sort(
            key=lambda item: (
                item[0],
                item[2],
            ),
            reverse=True,
        )

        _, row, market = (
            valid_rows[0]
        )

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

        if contains_excluded_word(
            name,
            industry,
        ):
            continue

        etf = is_etf_record(
            industry,
            name,
        )

        if symbol in active_etfs:

            etf = True

            if (
                not name
                and active_etfs[
                    symbol
                ].get("name")
            ):
                name = active_etfs[
                    symbol
                ]["name"]

        security_type = (
            "ETF"
            if etf
            else "STOCK"
        )

        result[symbol] = {
            "symbol": symbol,
            "name": name,
            "market": market,
            "type": security_type,
            "industry_category": industry,
            "finmind_date": clean_text(
                row.get(
                    "date"
                )
            ),
            "source": "FINMIND",
        }

    log(
        f"✓ FinMind active candidate："
        f"{len(result):,}"
    )

    return result


# ============================================================
# TWSE OFFICIAL MASTER
# ============================================================

def decode_response(
    response: requests.Response,
) -> Tuple[str, str]:

    content = response.content

    candidates = []

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
        [
            "utf-8",
            "big5",
            "cp950",
        ]
    )

    best_text = ""
    best_encoding = ""
    best_score = -10**9

    for encoding in candidates:

        try:

            text = content.decode(
                encoding,
                errors="replace",
            )

            lower = text.lower()

            score = 0

            if "<html" in lower:
                score += 10

            if "<table" in lower:
                score += 30

            if "<tr" in lower:
                score += 20

            if "isin" in lower:
                score += 20

            # 正常主檔應該有大量數字代碼
            symbol_count = len(
                re.findall(
                    r"\b\d{4,6}[A-Z]?\b",
                    text.upper(),
                )
            )

            score += min(
                symbol_count,
                200,
            )

            if score > best_score:

                best_score = score
                best_text = text
                best_encoding = encoding

        except Exception:
            continue

    if not best_text:
        raise RuntimeError(
            "官方主檔無法解碼"
        )

    return (
        best_text,
        best_encoding,
    )


def fetch_twse_master_optional() -> Optional[List[List[str]]]:

    section(
        "TWSE OFFICIAL PRODUCT MASTER"
    )

    try:

        response = request_bytes(
            TWSE_MASTER_URL,
            retries=RETRIES,
        )

        content = response.content

        log(
            f"HTTP {response.status_code}"
        )

        log(
            "Content-Type: "
            f"{response.headers.get('Content-Type', '')}"
        )

        log(
            f"Bytes: {len(content):,}"
        )

        # 這是本次修正的核心：
        # HTTP 200 不代表 payload 正常。
        #
        # 但 payload 不正常也不再直接讓
        # Universe 整條 pipeline 死掉。
        if len(content) < MASTER_MIN_BYTES:

            log(
                "⚠️ 官方商品主檔 payload "
                f"過小：{len(content):,} bytes"
            )

            log(
                "→ 改由 FinMind "
                "建立 candidate Universe"
            )

            return None

        text, encoding = (
            decode_response(
                response
            )
        )

        log(
            f"Selected encoding: "
            f"{encoding}"
        )

        parser = TableParser()

        parser.feed(text)

        rows = parser.rows

        log(
            f"HTML table rows："
            f"{len(rows):,}"
        )

        if len(rows) < MASTER_MIN_ROWS:

            log(
                "⚠️ 官方主檔 table rows "
                "不足"
            )

            log(
                "→ 官方主檔僅作失效來源"
            )

            return None

        log(
            "✓ 官方商品主檔可用"
        )

        return rows

    except Exception as exc:

        log(
            f"⚠️ 官方商品主檔無法使用："
            f"{exc}"
        )

        log(
            "→ 不阻斷 Universe"
        )

        return None


# ============================================================
# TWSE MASTER SYMBOL EXTRACTION
# ============================================================

def extract_official_symbols(
    rows: Optional[List[List[str]]],
) -> Dict[str, Dict[str, Any]]:

    if not rows:
        return {}

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for row in rows:

        if not row:
            continue

        joined = " | ".join(
            clean_text(
                cell
            )
            for cell in row
        )

        candidates = re.findall(
            r"(?<![0-9A-Z])"
            r"[0-9]{4,6}[A-Z]?"
            r"(?![0-9A-Z])",
            joined.upper(),
        )

        symbols = []

        for candidate in candidates:

            symbol = clean_symbol(
                candidate
            )

            if is_valid_symbol(
                symbol
            ):
                symbols.append(
                    symbol
                )

        if not symbols:
            continue

        # 優先找第一個像商品名稱的文字
        name = ""

        for cell in row:

            value = clean_text(
                cell
            )

            if not value:
                continue

            if re.fullmatch(
                r"[0-9A-Z\-\s]+",
                value.upper(),
            ):
                continue

            name = value
            break

        market = None

        for cell in row:

            market = normalize_market(
                cell
            )

            if market:
                break

        for symbol in symbols:

            result[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": market,
                "source": "TWSE_ISIN",
            }

    log(
        f"✓ 官方可辨識商品："
        f"{len(result):,}"
    )

    return result


# ============================================================
# OFFICIAL DELISTING OPTIONAL
# ============================================================

def fetch_official_terminated_optional() -> Set[str]:

    section(
        "OFFICIAL TERMINATION VALIDATION"
    )

    result: Set[str] = set()

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    try:

        response = request_bytes(
            TWSE_DELISTED_URL,
            retries=3,
        )

        text = response.content.decode(
            "utf-8",
            errors="ignore",
        )

        for symbol in re.findall(
            r"(?<![0-9A-Z])"
            r"[0-9]{4,6}[A-Z]?"
            r"(?![0-9A-Z])",
            text.upper(),
        ):

            symbol = clean_symbol(
                symbol
            )

            if is_valid_symbol(
                symbol
            ):
                result.add(
                    symbol
                )

        log(
            f"✓ TWSE terminated："
            f"{len(result):,}"
        )

    except Exception as exc:

        log(
            "⚠️ TWSE terminated "
            f"無法取得：{exc}"
        )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    tpex_before = len(
        result
    )

    try:

        response = request_bytes(
            TPEX_DELISTED_URL,
            retries=3,
        )

        text = response.content.decode(
            "utf-8",
            errors="ignore",
        )

        for symbol in re.findall(
            r"(?<![0-9A-Z])"
            r"[0-9]{4,6}[A-Z]?"
            r"(?![0-9A-Z])",
            text.upper(),
        ):

            symbol = clean_symbol(
                symbol
            )

            if is_valid_symbol(
                symbol
            ):
                result.add(
                    symbol
                )

        log(
            f"✓ TPEX terminated："
            f"{len(result) - tpex_before:,}"
        )

    except Exception as exc:

        log(
            "⚠️ TPEX terminated "
            f"無法取得：{exc}"
        )

    log(
        f"✓ 官方 terminated 合計："
        f"{len(result):,}"
    )

    return result


# ============================================================
# OLD UNIVERSE METADATA
# ============================================================

def load_existing_universe() -> Dict[str, Dict[str, Any]]:

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
            f"⚠️ 既有 Universe "
            f"無法讀取：{exc}"
        )

        return {}


# ============================================================
# METADATA
# ============================================================

def infer_instrument_type(
    symbol: str,
    name: str,
    security_type: str,
    old: Optional[Dict[str, Any]],
) -> str:

    if old:

        value = clean_text(
            old.get(
                "instrument_type"
            )
        )

        if value:
            return value

    text = normalize_text(
        name
    )

    if security_type == "STOCK":
        return "STOCK"

    if "主動" in text or "ACTIVE" in text:
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
        symbol.endswith("U")
        or "原油" in text
        or "黃金" in text
        or "COMMODITY" in text
    ):
        return "ETF_FX"

    if (
        "債" in text
        or "BOND" in text
    ):
        return "BOND"

    return "EQUITY"


def infer_category(
    name: str,
    security_type: str,
    instrument_type: str,
    old: Optional[Dict[str, Any]],
) -> str:

    if old:

        value = clean_text(
            old.get(
                "category"
            )
        )

        if value:
            return value

    if security_type == "STOCK":
        return "STOCK"

    if instrument_type == "BOND":
        return "BOND"

    if instrument_type == "LEVERAGED":
        return "LEVERAGED"

    if instrument_type == "INVERSE":
        return "INVERSE"

    if instrument_type == "ACTIVE":
        return "ACTIVE_EQUITY"

    return "EQUITY"


# ============================================================
# BUILD RECORD
# ============================================================

def build_record(
    symbol: str,
    source: Dict[str, Any],
    old: Optional[Dict[str, Any]],
    active_etf: Optional[Dict[str, Any]],
) -> Dict[str, Any]:

    name = clean_text(
        source.get(
            "name"
        )
    )

    if not name and old:
        name = clean_text(
            old.get(
                "name"
            )
        )

    if (
        not name
        and active_etf
    ):
        name = clean_text(
            active_etf.get(
                "name"
            )
        )

    market = (
        source.get(
            "market"
        )
        or (
            old.get(
                "market"
            )
            if old
            else None
        )
        or (
            active_etf.get(
                "market"
            )
            if active_etf
            else None
        )
    )

    if market not in ALLOWED_MARKETS:
        raise ValueError(
            f"{symbol}: invalid market "
            f"{market}"
        )

    security_type = source.get(
        "type"
    )

    if security_type not in ALLOWED_TYPES:

        if active_etf:
            security_type = "ETF"

        elif old:
            security_type = old.get(
                "type"
            )

        else:
            security_type = "STOCK"

    instrument_type = (
        infer_instrument_type(
            symbol,
            name,
            security_type,
            old,
        )
    )

    category = infer_category(
        name,
        security_type,
        instrument_type,
        old,
    )

    record: Dict[str, Any] = {
        "symbol": symbol,
        "full_symbol": (
            f"{symbol}.TW"
            if market == "TWSE"
            else f"{symbol}.TWO"
        ),
        "name": name,
        "market": market,
        "type": security_type,
        "instrument_type": instrument_type,
        "status": ACTIVE_STATUS,
    }

    # --------------------------------------------------------
    # Preserve existing metadata when available.
    # These fields do NOT decide whether a symbol is active.
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

    if (
        "category" not in record
        or not record["category"]
    ):
        record["category"] = category

    # --------------------------------------------------------
    # FinMind does not expose official listing date in
    # TaiwanStockInfo. Never fabricate one.
    # --------------------------------------------------------

    if (
        "listed_date" not in record
        and active_etf
        and active_etf.get("date")
    ):
        # This is NOT a listing date.
        # Do not write it as listed_date.
        pass

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
            f"{symbol}: invalid status"
        )

    if not is_valid_symbol(
        symbol
    ):
        raise ValueError(
            f"{symbol}: invalid symbol"
        )

    if not item["name"]:
        raise ValueError(
            f"{symbol}: empty name"
        )

    if contains_excluded_word(
        item["name"]
    ):
        raise ValueError(
            f"{symbol}: excluded product "
            f"survived filtering"
        )


def validate_universe(
    stocks: Dict[str, Dict[str, Any]],
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
        f"  Universe：{len(stocks):,}"
    )
    log(
        f"  STOCK：{stock_count:,}"
    )
    log(
        f"  ETF：{etf_count:,}"
    )
    log(
        f"  TWSE：{twse_count:,}"
    )
    log(
        f"  TPEX：{tpex_count:,}"
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
# POST-WRITE VALIDATION
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
            "寫入後 JSON root "
            "不是 object"
        )

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "寫入後 stocks "
            "不是 object"
        )

    if payload.get(
        "universe_count"
    ) != len(stocks):

        raise RuntimeError(
            "寫入後 universe_count "
            "不一致"
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
        "Universe Builder V2"
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

    existing = (
        load_existing_universe()
    )

    log(
        f"既有 Universe metadata："
        f"{len(existing):,} 檔"
    )

    # --------------------------------------------------------
    # 1. FinMind
    # --------------------------------------------------------

    section(
        "STEP 1 — FINMIND IDENTITY"
    )

    active_etfs = (
        fetch_active_etf_info()
    )

    finmind_candidates = (
        fetch_finmind_universe(
            active_etfs
        )
    )

    if not finmind_candidates:

        raise RuntimeError(
            "FinMind TaiwanStockInfo "
            "沒有建立任何有效 candidate"
        )

    # --------------------------------------------------------
    # 2. Official master
    # --------------------------------------------------------

    section(
        "STEP 2 — OFFICIAL MASTER "
        "CROSS VALIDATION"
    )

    official_rows = (
        fetch_twse_master_optional()
    )

    official_candidates = (
        extract_official_symbols(
            official_rows
        )
    )

    # --------------------------------------------------------
    # 3. Termination
    # --------------------------------------------------------

    section(
        "STEP 3 — TERMINATION STATUS"
    )

    finmind_terminated = (
        fetch_delisted_symbols()
    )

    official_terminated = (
        fetch_official_terminated_optional()
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
    # 4. Candidate resolution
    # --------------------------------------------------------

    section(
        "STEP 4 — RESOLVE ACTIVE UNIVERSE"
    )

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    stats = {
        "finmind_candidates":
            len(finmind_candidates),

        "official_candidates":
            len(official_candidates),

        "terminated":
            len(terminated),

        "terminated_removed":
            0,

        "excluded":
            0,

        "active":
            0,

        "official_overlap":
            0,
    }

    for symbol in sorted(
        finmind_candidates.keys()
    ):

        source = (
            finmind_candidates[
                symbol
            ]
        )

        # ----------------------------------------------------
        # Hard lifecycle gate
        # ----------------------------------------------------

        if symbol in terminated:

            stats[
                "terminated_removed"
            ] += 1

            continue

        # ----------------------------------------------------
        # Official cross-check
        #
        # 官方主檔如果正常，而且該代號存在，
        # 視為強一致。
        #
        # 如果官方主檔因 endpoint 異常而不存在，
        # 不直接否定 FinMind。
        # ----------------------------------------------------

        official = (
            official_candidates.get(
                symbol
            )
        )

        if official:

            stats[
                "official_overlap"
            ] += 1

            official_market = (
                official.get(
                    "market"
                )
            )

            if (
                official_market
                in ALLOWED_MARKETS
                and source.get(
                    "market"
                ) != official_market
            ):

                # 官方市場資訊優先
                source = dict(
                    source
                )

                source[
                    "market"
                ] = official_market

            if (
                not source.get(
                    "name"
                )
                and official.get(
                    "name"
                )
            ):

                source = dict(
                    source
                )

                source[
                    "name"
                ] = official[
                    "name"
                ]

        # ----------------------------------------------------
        # Exclusion gate
        # ----------------------------------------------------

        if contains_excluded_word(
            source.get("name"),
            source.get(
                "industry_category"
            ),
        ):

            stats[
                "excluded"
            ] += 1

            continue

        # ----------------------------------------------------
        # Build record
        # ----------------------------------------------------

        old = existing.get(
            symbol
        )

        active_etf = active_etfs.get(
            symbol
        )

        try:

            record = build_record(
                symbol,
                source,
                old,
                active_etf,
            )

            validate_record(
                symbol,
                record,
            )

            stocks[
                symbol
            ] = record

        except Exception as exc:

            log(
                f"⚠️ 排除 {symbol}: "
                f"{exc}"
            )

            stats[
                "excluded"
            ] += 1

    stats[
        "active"
    ] = len(stocks)

    # --------------------------------------------------------
    # 5. Statistics
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
        f"Active candidate："
        f"{stats['active']:,}"
    )

    # --------------------------------------------------------
    # 6. Final validation BEFORE write
    # --------------------------------------------------------

    section(
        "STEP 5 — PRE-WRITE VALIDATION"
    )

    validate_universe(
        stocks,
        terminated,
    )

    # --------------------------------------------------------
    # 7. Build metadata
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
            "UNIVERSE-BUILD-V2",

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

            "termination_source":
                "FinMind TaiwanStockDelisting",

            "official_cross_validation":
                "TWSE ISIN product master",

            "price_data_is_not_identity_source":
                True,

            "daily_quotes_are_not_identity_source":
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

            "finmind_identity_required":
                True,

            "official_master_cross_validation":
                True,

            "etf_6_digit_supported":
                True,

            "bond_etf_supported":
                True,

            "active_etf_supported":
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

            "yahoo_not_identity_source":
                True,

            "cmoney_not_identity_source":
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
    # 8. Atomic write
    # --------------------------------------------------------

    section(
        "STEP 6 — ATOMIC WRITE"
    )

    atomic_write_json(
        UNIVERSE_FILE,
        payload,
    )

    log(
        f"✓ 已寫入："
        f"{UNIVERSE_FILE}"
    )

    # --------------------------------------------------------
    # 9. Post-write verification
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
        f"✓ 寫入後 Universe："
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

    # --------------------------------------------------------
    # Explicit 00838B gate
    # --------------------------------------------------------

    final_symbols = set(
        written["stocks"].keys()
    )

    if "00838B" in final_symbols:

        raise RuntimeError(
            "FATAL: 00838B "
            "存在於 active Universe"
        )

    log(
        "✓ 00838B 不存在於 active Universe"
    )

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