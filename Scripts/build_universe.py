#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - Scripts/build_universe.py
============================================================

唯一責任
------------------------------------------------------------
建立：

    Data/universe.json

Universe 架構
------------------------------------------------------------

官方 TWSE / TPEX 商品主檔
        ↓
官方商品存在性 Gate
        ↓
確認 TWSE / TPEX 正式市場商品
        ↓
FinMind 身份補充
        ↓
Active ETF 補充 ETF 身份
        ↓
排除：
    WARRANT
    ETN
    REIT
    TDR
    PREFERRED SHARE
    一般債券
    非 ETF 六碼商品
    其他未知商品
        ↓
只允許：
    STOCK
    ETF
        ↓
status = active
        ↓
FINAL VALIDATION
        ↓
atomic write
        ↓
post-write validation

核心契約
------------------------------------------------------------

1. 官方商品主檔是 Universe 的唯一商品存在性來源。

2. FinMind 不得創造 Universe 商品。

3. FinMind 不得把官方不存在的商品變成 STOCK。

4. FinMind Active ETF 可以對「官方已存在」的商品補充 ETF 身份。

5. ETF 不因六碼而排除。

6. 債券 ETF 可以存在。

7. 一般債券不是 ETF 時排除。

8. ETN / REIT / TDR / 權證 / 特別股排除。

9. 不使用：
       價格
       成交量
       Yahoo
       CMoney

10. 只允許：
       market = TWSE / TPEX
       type   = STOCK / ETF
       status = active

11. 不固定 Universe 數量。

12. 官方來源解析不足時：
       不覆蓋既有 universe.json。

13. 驗證全部通過後才寫入 universe.json。

14. 寫入失敗不得破壞既有 universe.json。
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

"""
重要：

不要使用 e_single_main.jsp 當 Universe 候選來源。

e_single_main.jsp 是 ISIN 系統的廣泛商品總表，
包含大量：

- 股票
- 債券
- 權證
- ETN
- TDR
- 興櫃
- 其他證券
- 各種 ISIN 商品

因此不能把其中所有 4~6 碼代碼直接視為 Universe。

這裡只使用官方上市 / 上櫃分類來源。

strMode=2
    官方上市證券分類

strMode=4
    官方上櫃證券分類

注意：

即使官方頁面包含多種證券，
也必須再經過 CFI / 名稱 / FinMind 身份 Gate。
"""

OFFICIAL_LISTED_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
)

OFFICIAL_OTC_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
)

OFFICIAL_PRODUCT_MASTER_URLS = (
    OFFICIAL_LISTED_URL,
    OFFICIAL_OTC_URL,
)


# ============================================================
# FINMIND
# ============================================================

FINMIND_API = (
    "https://api.finmindtrade.com/api/v4/data"
)

FINMIND_INFO_DATASET = (
    "TaiwanStockInfo"
)

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

MIN_LISTED_SYMBOLS = 100

MIN_OTC_SYMBOLS = 100


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
            "(compatible; "
            "tw-stock-ai-scanner/"
            "universe-builder)"
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

    text = text.replace(
        "\ufeff",
        " ",
    )

    text = text.replace(
        "\xa0",
        " ",
    )

    text = text.replace(
        "\u3000",
        " ",
    )

    text = text.replace(
        "\r",
        " ",
    )

    text = text.replace(
        "\n",
        " ",
    )

    text = text.replace(
        "\t",
        " ",
    )

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
                : -len(suffix)
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


def is_six_digit_symbol(
    symbol: str,
) -> bool:

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
# SYMBOL EXTRACTION
# ============================================================

def extract_code(
    value: Any,
) -> str:

    text = clean_text(value)

    # 允許：
    #
    # 2330 台積電
    # 00409A ...
    # 00929 ...
    # 9103 ...
    # 1234A
    #

    match = re.match(
        r"^([0-9]{4,6}[A-Za-z]?)"
        r"(?:\s+|　+|$)",
        text,
    )

    if match:

        return clean_symbol(
            match.group(1)
        )

    # 某些官方 HTML parser 可能
    # 把 code/name 拆開。
    #
    # 單獨 code。

    match = re.fullmatch(
        r"[0-9]{4,6}[A-Za-z]?",
        text,
    )

    if match:

        return clean_symbol(
            match.group(0)
        )

    return ""


def extract_name(
    value: Any,
) -> str:

    text = clean_text(value)

    match = re.match(
        r"^[0-9]{4,6}[A-Za-z]?"
        r"\s+(.+)$",
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

class TableParser(
    HTMLParser
):

    def __init__(
        self,
    ) -> None:

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

            if (
                self.current_row
                is not None
            ):

                self.current_cell = []

        elif tag == "br":

            if (
                self.current_cell
                is not None
            ):

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
                self.current_row
                is not None
                and self.current_cell
                is not None
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

        if (
            self.current_cell
            is not None
        ):

            self.current_cell.append(
                data
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

            log(
                f"⚠️ HTTP attempt "
                f"{attempt}/{retries} "
                f"failed: {exc}"
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
# ENCODING
# ============================================================

def decode_response(
    response: requests.Response,
) -> str:

    raw = response.content

    candidates: List[str] = []

    if response.encoding:
        candidates.append(
            response.encoding.lower()
        )

    candidates.extend(
        [
            "utf-8",
            "big5",
            "cp950",
        ]
    )

    seen = set()

    for encoding in candidates:

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
# OFFICIAL ROW PARSING
# ============================================================

def looks_like_header(
    cells: List[str],
) -> bool:

    combined = normalize_text(
        " ".join(cells)
    )

    header_words = (
        "有價證券代號",
        "有價證券名稱",
        "證券代號",
        "證券名稱",
        "SECURITYCODE",
        "SECURITYNAME",
        "ISINCODE",
        "CFICODE",
    )

    return any(
        word in combined
        for word in header_words
    )


def parse_official_rows(
    text: str,
    market: str,
    source_url: str,
) -> List[
    Dict[str, Any]
]:

    parser = TableParser()

    parser.feed(text)

    result: List[
        Dict[str, Any]
    ] = []

    seen = set()

    for row in parser.rows:

        cells = [
            clean_text(cell)
            for cell in row
        ]

        cells = [
            cell
            for cell in cells
            if cell
        ]

        if len(cells) < 2:
            continue

        if looks_like_header(
            cells
        ):
            continue

        symbol = ""

        code_index = -1

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

                code_index = index

                break

        if not symbol:
            continue

        # ----------------------------------------------------
        # Name
        # ----------------------------------------------------

        name = ""

        # 常見官方格式：
        #
        # 2330 台積電
        #
        if code_index >= 0:

            name = extract_name(
                cells[code_index]
            )

        # 如果 code/name 被拆成兩個 cell。
        if (
            not name
            and code_index + 1
            < len(cells)
        ):

            possible_name = clean_text(
                cells[
                    code_index + 1
                ]
            )

            if (
                not is_valid_symbol(
                    possible_name
                )
            ):

                name = possible_name

        if not name:

            name = symbol

        # ----------------------------------------------------
        # ISIN
        # ----------------------------------------------------

        isin = ""

        for cell in cells:

            match = re.search(
                r"\bTW[0-9A-Z]{10}\b",
                cell.upper(),
            )

            if match:

                isin = match.group(0)

                break

        # ----------------------------------------------------
        # CFI
        # ----------------------------------------------------

        cfi = ""

        # CFI 通常是六碼英文字母。
        #
        # 不把所有英文字母當 CFI。
        #
        for cell in cells:

            value = (
                normalize_text(cell)
            )

            if re.fullmatch(
                r"[A-Z]{6}",
                value,
            ):

                cfi = value

        # ----------------------------------------------------
        # Dedup
        # ----------------------------------------------------

        key = (
            symbol,
            market,
        )

        if key in seen:
            continue

        seen.add(key)

        result.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "isin": isin,
                "cfi": cfi,
                "raw": " | ".join(cells),
                "official_source": (
                    source_url
                ),
            }
        )

    return result


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

    source_counts = {
        "TWSE": 0,
        "TPEX": 0,
    }

    for url, market in (
        (
            OFFICIAL_LISTED_URL,
            "TWSE",
        ),
        (
            OFFICIAL_OTC_URL,
            "TPEX",
        ),
    ):

        try:

            response = http_get(
                url
            )

            text = decode_response(
                response
            )

            log(
                f"→ {market} HTTP "
                f"{response.status_code} "
                f"bytes="
                f"{len(response.content):,}"
            )

            rows = parse_official_rows(
                text,
                market,
                url,
            )

            log(
                f"→ {market} parsed rows："
                f"{len(rows):,}"
            )

            source_counts[
                market
            ] = len(rows)

            for item in rows:

                symbol = item[
                    "symbol"
                ]

                # ------------------------------------------------
                # 官方來源優先。
                #
                # 同一 symbol 如果同時被不同市場
                # parser 抓到，不能盲目覆蓋。
                #
                # 正常情況下應只有一個市場。
                # ------------------------------------------------

                existing = merged.get(
                    symbol
                )

                if existing is None:

                    merged[symbol] = item

                elif (
                    existing["market"]
                    == item["market"]
                ):

                    merged[symbol] = item

                else:

                    # 市場衝突：
                    # 不直接選邊。
                    #
                    # 保留第一筆並標記。
                    existing[
                        "market_conflict"
                    ] = True

                    existing[
                        "market_conflict_with"
                    ] = item[
                        "market"
                    ]

        except Exception as exc:

            log(
                f"❌ 官方 {market} "
                f"來源失敗：{exc}"
            )

            raise RuntimeError(
                f"官方 {market} "
                f"商品主檔不可用："
                f"{exc}"
            ) from exc

    # --------------------------------------------------------
    # SOURCE GATE
    # --------------------------------------------------------

    if (
        source_counts["TWSE"]
        < MIN_LISTED_SYMBOLS
    ):

        raise RuntimeError(
            "官方 TWSE 商品主檔"
            "解析不足："
            f"{source_counts['TWSE']} < "
            f"{MIN_LISTED_SYMBOLS}"
        )

    if (
        source_counts["TPEX"]
        < MIN_OTC_SYMBOLS
    ):

        raise RuntimeError(
            "官方 TPEX 商品主檔"
            "解析不足："
            f"{source_counts['TPEX']} < "
            f"{MIN_OTC_SYMBOLS}"
        )

    if (
        len(merged)
        < MIN_OFFICIAL_SYMBOLS
    ):

        raise RuntimeError(
            "官方商品主檔解析不足："
            f"{len(merged)} < "
            f"{MIN_OFFICIAL_SYMBOLS}"
        )

    log(
        f"✓ official listed："
        f"{source_counts['TWSE']:,}"
    )

    log(
        f"✓ official OTC："
        f"{source_counts['TPEX']:,}"
    )

    log(
        f"✓ official symbols："
        f"{len(merged):,}"
    )

    return merged


# ============================================================
# FINMIND
# ============================================================

def finmind_headers() -> Dict[
    str,
    str,
]:

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

            if attempt < (
                FINMIND_RETRIES
            ):

                time.sleep(
                    RETRY_SLEEP_SECONDS
                    * attempt
                )

    raise RuntimeError(
        f"FinMind "
        f"{dataset} failed: "
        f"{last_error}"
    )


# ============================================================
# FINMIND ACTIVE ETF
# ============================================================

def fetch_active_etfs() -> Dict[
    str,
    Dict[str, Any]
]:

    records = fetch_finmind_dataset(
        FINMIND_ACTIVE_ETF_DATASET
    )

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
            record.get("type")
        )

        # 某些版本 type 不一定有 TWSE/TPEX。
        #
        # 重要：
        # 這裡不讓 FinMind 創造商品。
        #
        # market 先允許未知，
        # 最後一定由 official master
        # 決定市場。

        name = clean_text(
            record.get(
                "stock_name"
            )
        )

        result[symbol] = {
            "symbol": symbol,
            "name": name,
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
            "is_active_etf": True,
        }

    log(
        f"✓ FinMind Active ETF："
        f"{len(result):,}"
    )

    return result


# ============================================================
# FINMIND STOCK IDENTITY
# ============================================================

def fetch_finmind_identity() -> Dict[
    str,
    Dict[str, Any]
]:

    records = fetch_finmind_dataset(
        FINMIND_INFO_DATASET
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
        ).append(record)

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for symbol, rows in (
        grouped.items()
    ):

        # 只保留有市場資訊的資料。
        valid_rows = []

        for row in rows:

            market = normalize_market(
                row.get("type")
            )

            if market in (
                "TWSE",
                "TPEX",
            ):

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
        f"✓ FinMind identity "
        f"records："
        f"{len(result):,}"
    )

    return result


# ============================================================
# INSTRUMENT CLASSIFICATION
# ============================================================

def is_etf_name(
    name: str,
) -> bool:

    text = normalize_text(
        name
    )

    return (
        "ETF" in text
        or "指數股票型基金" in text
        or "主動式ETF" in text
        or "主動式指數" in text
    )


def is_excluded_instrument(
    symbol: str,
    name: str,
    industry: str,
    cfi: str = "",
    *,
    is_etf: bool = False,
) -> Tuple[
    bool,
    str,
]:

    combined = (
        normalize_text(name)
        + normalize_text(industry)
    )

    cfi_text = normalize_text(
        cfi
    )

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
    # WARRANT
    # --------------------------------------------------------

    if contains_any(
        combined,
        WARRANT_WORDS,
    ):

        return True, "warrant"

    # --------------------------------------------------------
    # TDR
    # --------------------------------------------------------

    if contains_any(
        combined,
        TDR_WORDS,
    ):

        return True, "tdr"

    # --------------------------------------------------------
    # ETF GATE
    # --------------------------------------------------------
    #
    # ETF：
    #
    #   不因六碼排除
    #   不因債券名稱排除
    #
    # 只要官方商品存在，
    # 且 FinMind Active ETF 或名稱
    # 明確確認 ETF，
    # 即可保留。
    #

    if is_etf:

        return False, ""

    # --------------------------------------------------------
    # PREFERRED
    # --------------------------------------------------------

    if cfi_text.startswith(
        "EPN"
    ):

        return True, (
            "preferred_share_cfi"
        )

    if contains_any(
        combined,
        PREFERRED_WORDS,
    ):

        return True, (
            "preferred_share"
        )

    # --------------------------------------------------------
    # BOND
    # --------------------------------------------------------

    if contains_any(
        combined,
        BOND_WORDS,
    ):

        return True, "bond"

    # --------------------------------------------------------
    # STRUCTURED
    # --------------------------------------------------------

    if contains_any(
        combined,
        STRUCTURED_WORDS,
    ):

        return True, (
            "structured_security"
        )

    # --------------------------------------------------------
    # Structured symbol
    # --------------------------------------------------------

    if re.fullmatch(
        r"[0-9]{5}T",
        symbol,
    ):

        return True, (
            "structured_T_security"
        )

    if re.fullmatch(
        r"[0-9]{5}P",
        symbol,
    ):

        return True, (
            "structured_P_security"
        )

    # --------------------------------------------------------
    # SIX DIGIT NON ETF
    # --------------------------------------------------------

    if is_six_digit_symbol(
        symbol
    ):

        return True, (
            "six_digit_non_etf"
        )

    return False, ""


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
) -> Tuple[
    Optional[Dict[str, Any]],
    str,
]:

    # --------------------------------------------------------
    # OFFICIAL HARD GATE
    # --------------------------------------------------------

    symbol = clean_symbol(
        official.get(
            "symbol"
        )
    )

    if not is_valid_symbol(
        symbol
    ):

        return None, (
            "invalid_symbol"
        )

    market = normalize_market(
        official.get(
            "market"
        )
    )

    if market not in (
        "TWSE",
        "TPEX",
    ):

        return None, (
            "invalid_market"
        )

    # --------------------------------------------------------
    # MARKET CONFLICT
    # --------------------------------------------------------

    if official.get(
        "market_conflict"
    ):

        return None, (
            "official_market_conflict"
        )

    # --------------------------------------------------------
    # NAMES
    # --------------------------------------------------------

    official_name = clean_text(
        official.get(
            "name"
        )
    )

    finmind_name = clean_text(
        (
            finmind or {}
        ).get(
            "name"
        )
    )

    etf_name = clean_text(
        (
            active_etf or {}
        ).get(
            "name"
        )
    )

    name = (
        official_name
        or finmind_name
        or etf_name
        or symbol
    )

    # --------------------------------------------------------
    # CFI
    # --------------------------------------------------------

    official_cfi = clean_text(
        official.get(
            "cfi"
        )
    )

    # --------------------------------------------------------
    # INDUSTRY
    # --------------------------------------------------------

    industry = clean_text(
        (
            finmind or {}
        ).get(
            "industry"
        )
    )

    # --------------------------------------------------------
    # ETF IDENTITY
    # --------------------------------------------------------
    #
    # 最重要的邏輯：
    #
    # active_etf != None
    #
    # 只代表：
    #
    # FinMind 認定這個 symbol 是 Active ETF。
    #
    # 但它只有在 official symbol 已經存在
    # 的前提下才有資格進 Universe。
    #
    # 因此 FinMind 不可能創造商品。
    #

    confirmed_etf = (
        active_etf is not None
        or is_etf_name(name)
        or "ETF" in normalize_text(
            industry
        )
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # 「只有 FinMind identity」
    # 不可以自動變 STOCK。
    #
    # 官方已存在但沒有 FinMind identity：
    #
    #   - 如果官方名稱明確 ETF → ETF
    #   - 如果官方名稱是一般股票 → STOCK
    #   - 如果無法判斷 → UNKNOWN → 排除
    #
    # 這避免把未知商品全部變 STOCK。
    # --------------------------------------------------------

    if not confirmed_etf:

        # 官方名稱必須看起來像一般股票。
        #
        # 一般股票常見：
        #   4碼
        #   5碼特殊股票
        #
        # 但六碼非 ETF 不允許。
        #

        if is_six_digit_symbol(
            symbol
        ):

            return None, (
                "official_type_unknown"
            )

        # 如果名稱明確屬於其他商品，
        # 在 exclusion function 排除。

        instrument_type = "STOCK"

    else:

        instrument_type = "ETF"

    # --------------------------------------------------------
    # EXCLUSION
    # --------------------------------------------------------

    excluded, reason = (
        is_excluded_instrument(
            symbol,
            name,
            industry,
            official_cfi,
            is_etf=(
                instrument_type
                == "ETF"
            ),
        )
    )

    if excluded:

        return None, reason

    # --------------------------------------------------------
    # ETF MARKET CONSISTENCY
    # --------------------------------------------------------

    if active_etf is not None:

        finmind_market = normalize_market(
            active_etf.get(
                "market"
            )
        )

        if (
            finmind_market
            and finmind_market
            != market
        ):

            # FinMind 不得覆蓋官方市場。
            #
            # 市場衝突時排除，
            # 而不是自行選擇。

            return None, (
                "etf_market_conflict"
            )

    # --------------------------------------------------------
    # FINAL TYPE
    # --------------------------------------------------------

    if instrument_type not in (
        "STOCK",
        "ETF",
    ):

        return None, (
            "unknown_instrument_type"
        )

    suffix = (
        ".TW"
        if market == "TWSE"
        else ".TWO"
    )

    candidate = {
        "symbol": symbol,

        "full_symbol": (
            f"{symbol}{suffix}"
        ),

        "name": name,

        "market": market,

        "type": instrument_type,

        "instrument_type": (
            instrument_type
        ),

        "status": ACTIVE_STATUS,

        "source": (
            "official_product_master"
        ),

        "identity_source": {
            "official": True,
            "finmind": (
                finmind is not None
            ),
            "finmind_active_etf": (
                active_etf is not None
            ),
        },
    }

    return candidate, ""


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

    except Exception as exc:

        log(
            f"⚠️ existing universe "
            f"讀取失敗：{exc}"
        )

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

    merged = dict(
        candidate
    )

    # 只允許附加 metadata。
    #
    # 舊 Universe 不得覆蓋：
    #
    # symbol
    # market
    # type
    # status
    # source
    # full_symbol

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

            merged[key] = (
                existing[key]
            )

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

    if not stocks:

        raise RuntimeError(
            "Universe is empty"
        )

    if len(stocks) < (
        MIN_OFFICIAL_SYMBOLS
    ):

        raise RuntimeError(
            "Universe validation "
            "failed: "
            f"{len(stocks)} < "
            f"{MIN_OFFICIAL_SYMBOLS}"
        )

    for symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"{symbol}: "
                "item is not dict"
            )

        # ----------------------------------------------------
        # KEY / SYMBOL
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
        # STATUS
        # ----------------------------------------------------

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            raise RuntimeError(
                f"{symbol}: "
                "status != active"
            )

        # ----------------------------------------------------
        # MARKET
        # ----------------------------------------------------

        market = normalize_market(
            item.get(
                "market"
            )
        )

        if market not in (
            "TWSE",
            "TPEX",
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid market"
            )

        # ----------------------------------------------------
        # TYPE
        # ----------------------------------------------------

        item_type = item.get(
            "type"
        )

        if item_type not in (
            "STOCK",
            "ETF",
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid type"
            )

        # ----------------------------------------------------
        # OFFICIAL HARD GATE
        # ----------------------------------------------------

        if symbol not in official:

            raise RuntimeError(
                f"{symbol}: "
                "not present in "
                "official product master"
            )

        official_market = normalize_market(
            official[symbol].get(
                "market"
            )
        )

        if official_market != market:

            raise RuntimeError(
                f"{symbol}: "
                "official market mismatch"
            )

        # ----------------------------------------------------
        # FULL SYMBOL
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

        expected_full_symbol = (
            f"{symbol}{expected_suffix}"
        )

        if full_symbol != (
            expected_full_symbol
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid full_symbol"
            )

        # ----------------------------------------------------
        # TYPE-SPECIFIC VALIDATION
        # ----------------------------------------------------

        if item_type == "ETF":

            identity = item.get(
                "identity_source",
                {},
            )

            if not isinstance(
                identity,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol}: "
                    "ETF identity missing"
                )

            # ETF 必須至少有一個可靠身份依據：
            #
            # 官方名稱明確 ETF
            # 或 FinMind Active ETF
            # 或 FinMind industry ETF
            #
            #
            # 這裡不能要求「官方 parser type == ETF」。
            #
            # 00409A 就是這個案例。
            #

            has_finmind_etf = bool(
                identity.get(
                    "finmind_active_etf"
                )
            )

            has_finmind = bool(
                identity.get(
                    "finmind"
                )
            )

            official_name = normalize_text(
                official[symbol].get(
                    "name"
                )
            )

            official_says_etf = (
                "ETF" in official_name
                or "指數股票型基金"
                in official_name
                or "主動式ETF"
                in official_name
            )

            if not (
                has_finmind_etf
                or (
                    has_finmind
                    and "ETF"
                    in normalize_text(
                        item.get(
                            "name"
                        )
                    )
                )
                or official_says_etf
            ):

                raise RuntimeError(
                    f"{symbol}: "
                    "ETF identity "
                    "cannot be verified"
                )

        # ----------------------------------------------------
        # NON ETF SIX DIGIT
        # ----------------------------------------------------

        if (
            item_type != "ETF"
            and is_six_digit_symbol(
                symbol
            )
        ):

            raise RuntimeError(
                f"{symbol}: "
                "six-digit non-ETF "
                "entered Universe"
            )

    # --------------------------------------------------------
    # COUNTS
    # --------------------------------------------------------

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
                sort_keys=False,
            )

            handle.write("\n")

            handle.flush()

            os.fsync(
                handle.fileno()
            )

        # ----------------------------------------------------
        # TEMP FILE VALIDATION
        # ----------------------------------------------------

        temp_payload = json.loads(
            Path(
                temp_name
            ).read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(
            temp_payload,
            dict,
        ):

            raise RuntimeError(
                "temporary JSON "
                "is not object"
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
    # STEP 1 — OFFICIAL IDENTITY
    # ========================================================

    section(
        "STEP 1 — OFFICIAL IDENTITY"
    )

    official = (
        fetch_official_master()
    )

    # ========================================================
    # STEP 2 — FINMIND SUPPLEMENT
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
    # STEP 3 — BUILD
    # ========================================================

    section(
        "STEP 3 — BUILD"
    )

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    exclusions: Dict[
        str,
        int
    ] = {}

    # --------------------------------------------------------
    # 絕對規則：
    #
    # 迭代 official。
    #
    # 不能迭代 FinMind。
    #
    # 因此 FinMind 永遠不能創造 Universe。
    # --------------------------------------------------------

    for symbol, official_item in (
        official.items()
    ):

        candidate, reason = (
            classify_candidate(
                official_item,
                finmind.get(symbol),
                active_etfs.get(symbol),
            )
        )

        if candidate is None:

            exclusions[
                reason
            ] = (
                exclusions.get(
                    reason,
                    0,
                )
                + 1
            )

            continue

        candidate = merge_metadata(
            candidate,
            existing.get(
                symbol,
                {},
            ),
        )

        stocks[
            symbol
        ] = candidate

    # --------------------------------------------------------
    # BUILD SUMMARY
    # --------------------------------------------------------

    log(
        f"✓ official symbols："
        f"{len(official):,}"
    )

    log(
        f"✓ Universe candidates："
        f"{len(stocks):,}"
    )

    log(
        "✓ exclusions："
    )

    for reason, count in sorted(
        exclusions.items(),
        key=lambda item: (
            -item[1],
            item[0],
        ),
    ):

        log(
            f"  {reason}: "
            f"{count:,}"
        )

    # ========================================================
    # STEP 4 — VALIDATION
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
        if item["type"]
        == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item["type"]
        == "ETF"
    )

    twse_count = sum(
        1
        for item in stocks.values()
        if item["market"]
        == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item["market"]
        == "TPEX"
    )

    # ========================================================
    # STEP 5 — PAYLOAD
    # ========================================================

    payload: Dict[
        str,
        Any
    ] = {

        "version": (
            "UNIVERSE-BUILD-V7"
        ),

        "generated_at": (
            now_taipei().isoformat()
        ),

        "universe_count": (
            len(stocks)
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
                list(
                    OFFICIAL_PRODUCT_MASTER_URLS
                )
            ),

            "policy": (
                "official listed/OTC "
                "product master is the "
                "hard existence gate"
            ),

            "price_data_is_not_universe_source": (
                True
            ),

            "daily_quotes_are_not_universe_source": (
                True
            ),

            "yahoo_is_not_universe_source": (
                True
            ),

            "cmoney_is_not_universe_source": (
                True
            ),

            "finmind_is_identity_supplement_only": (
                True
            ),

            "finmind_cannot_create_products": (
                True
            ),

            "finmind_cannot_create_stock": (
                True
            ),

            "finmind_active_etf_can_confirm_existing_product": (
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

            "official_master_is_existence_gate": (
                True
            ),

            "finmind_is_supplement_only": (
                True
            ),

            "finmind_cannot_create_product": (
                True
            ),

            "finmind_cannot_create_stock": (
                True
            ),

            "etf_6_digit_supported": (
                True
            ),

            "bond_etf_supported": (
                True
            ),

            "general_bond_excluded": (
                True
            ),

            "warrant_excluded": (
                True
            ),

            "etn_excluded": (
                True
            ),

            "reit_excluded": (
                True
            ),

            "tdr_excluded": (
                True
            ),

            "preferred_share_excluded": (
                True
            ),

            "fixed_universe_count": (
                False
            ),

            "daily_quote_not_used": (
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
    # STEP 5 — FINAL VALIDATION
    # ========================================================

    section(
        "STEP 5 — FINAL VALIDATION"
    )

    if (
        payload[
            "universe_count"
        ]
        != len(
            payload["stocks"]
        )
    ):

        raise RuntimeError(
            "universe_count mismatch"
        )

    if (
        payload[
            "stock_count"
        ]
        + payload[
            "etf_count"
        ]
        != payload[
            "universe_count"
        ]
    ):

        raise RuntimeError(
            "stock/ETF count mismatch"
        )

    validate_universe(
        payload["stocks"],
        official,
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
    # STEP 7 — POST WRITE VALIDATION
    # ========================================================

    section(
        "STEP 7 — POST-WRITE VALIDATION"
    )

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
            "written universe "
            "is not object"
        )

    written_stocks = (
        written.get(
            "stocks"
        )
    )

    if not isinstance(
        written_stocks,
        dict,
    ):

        raise RuntimeError(
            "written stocks "
            "is not dict"
        )

    if (
        written.get(
            "universe_count"
        )
        != len(
            written_stocks
        )
    ):

        raise RuntimeError(
            "post-write "
            "universe_count "
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
            "post-write "
            "STOCK/ETF mismatch"
        )

    # 再做一次完整驗證。
    validate_universe(
        written_stocks,
        official,
    )

    # ========================================================
    # SUCCESS
    # ========================================================

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
            "❌ 不覆蓋既有 "
            "universe.json"
        )

        return 1


if __name__ == "__main__":

    sys.exit(
        main()
    )