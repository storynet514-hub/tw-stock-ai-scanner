#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - Scripts/build_universe.py
============================================================

唯一責任：
    建立 Data/universe.json

核心契約
------------------------------------------------------------
1. 官方商品主檔是 Universe 的唯一身份來源。
2. 官方主檔不存在的 symbol 絕對不得進入 Universe。
3. 不使用價格、成交量、Yahoo、CMoney 判斷 Universe。
4. FinMind 只能補充官方商品的身份/分類 metadata。
5. FinMind 不得創造商品。
6. FinMind 缺資料不得把未知商品預設為 STOCK。
7. 官方商品必須先通過 STOCK / ETF 身份判定。
8. 只允許：
       market = TWSE / TPEX
       type   = STOCK / ETF
9. ETF 不因六碼而排除。
10. 債券 ETF 可以存在。
11. 一般債券不是 ETF 時排除。
12. 排除：
       WARRANT
       ETN
       REIT
       TDR
       PREFERRED SHARE
       一般債券
       結構型商品
13. status 必須為 active。
14. 驗證全部通過後才 atomic write。
15. 任一驗證失敗不得破壞既有 universe.json。
16. 不使用既有 Universe 製造新商品。
17. 不使用價格資料補 Universe。
18. 不使用成交資料補 Universe。

資料流程
------------------------------------------------------------
官方商品主檔
    ↓
官方欄位解析
    ↓
官方 Market / Type / CFI identity gate
    ↓
官方 STOCK / ETF candidate
    ↓
FinMind 補充 identity metadata
    ↓
特殊商品排除
    ↓
status = active
    ↓
FINAL VALIDATION
    ↓
atomic write
    ↓
Data/universe.json
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
# OFFICIAL TWSE / TPEx ISIN MASTER
# ============================================================

OFFICIAL_MASTER_URLS = (
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

# 官方商品主檔正常情況不可能只有幾十檔。
MIN_OFFICIAL_SYMBOLS = 100

# 防止 parser/API 異常造成 Universe 暴增。
#
# 這不是 Universe 固定數量。
# 只是異常資料保護。
MAX_UNIVERSE_SYMBOLS = 10000

# 如果本次 Universe 與既有 Universe 相比異常暴增，
# 直接停止寫入。
MAX_GROWTH_RATIO = 3.0

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
# OFFICIAL SECURITY TYPE
# ============================================================

# TWSE ISIN / 官方資料常見股票分類文字。
STOCK_TYPE_WORDS = (
    "普通股",
    "普通股票",
    "COMMONSTOCK",
    "COMMONSHARES",
    "COMMONSHARE",
    "STOCK",
)

# ETF 官方分類文字。
ETF_TYPE_WORDS = (
    "ETF",
    "指數股票型基金",
    "指數型基金",
    "交換交易基金",
    "上市指數股票型基金",
    "上櫃指數股票型基金",
    "指數投資證券以外之ETF",
)

# 必須優先排除的證券。
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
# HTTP
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
# LOGGING
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

    return text.replace(" ", "").replace("\u3000", "")


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
        re.fullmatch(r"[0-9]{6}", symbol)
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
# SECURITY TYPE
# ============================================================

def normalize_security_type(value: Any) -> str:
    return normalize_text(value)


def official_type_is_etf(
    security_type: str,
    name: str,
    raw: str,
) -> bool:

    combined = (
        normalize_text(security_type)
        + normalize_text(name)
        + normalize_text(raw)
    )

    return contains_any(
        combined,
        ETF_TYPE_WORDS,
    )


def official_type_is_stock(
    security_type: str,
    name: str,
) -> bool:

    type_text = normalize_security_type(
        security_type
    )

    name_text = normalize_text(name)

    if contains_any(
        type_text,
        STOCK_TYPE_WORDS,
    ):
        return True

    # 官方股票資料有時 Type 欄位文字不一致，
    # 但若名稱沒有任何特殊商品特徵，
    # 仍必須經過明確的官方 type / CFI gate。
    #
    # 這裡不單靠名稱判定 STOCK。
    if type_text in {
        "EQUITY",
        "SHARES",
        "SHARE",
    }:
        return True

    return False


# ============================================================
# EXCLUSION
# ============================================================

def exclusion_reason(
    symbol: str,
    name: str,
    security_type: str,
    cfi: str,
    *,
    is_etf: bool,
) -> Optional[str]:

    combined = (
        normalize_text(name)
        + normalize_text(security_type)
    )

    cfi_text = normalize_text(cfi)

    # --------------------------------------------------------
    # 特殊商品永遠優先排除
    # --------------------------------------------------------

    if contains_any(combined, WARRANT_WORDS):
        return "warrant"

    if contains_any(combined, ETN_WORDS):
        return "etn"

    if contains_any(combined, REIT_WORDS):
        return "reit"

    if contains_any(combined, TDR_WORDS):
        return "tdr"

    # --------------------------------------------------------
    # ETF 可以是債券 ETF
    # --------------------------------------------------------

    if is_etf:
        return None

    # --------------------------------------------------------
    # 非 ETF
    # --------------------------------------------------------

    if contains_any(combined, PREFERRED_WORDS):
        return "preferred_share"

    if cfi_text.startswith("EPN"):
        return "preferred_share_cfi"

    if contains_any(combined, BOND_WORDS):
        return "bond"

    if contains_any(combined, STRUCTURED_WORDS):
        return "structured_security"

    # --------------------------------------------------------
    # 六碼非 ETF
    # --------------------------------------------------------

    if is_six_digit_symbol(symbol):
        return "six_digit_non_etf"

    # --------------------------------------------------------
    # 特殊代號格式
    # --------------------------------------------------------

    if re.fullmatch(r"[0-9]{5}T", symbol):
        return "structured_T_security"

    if re.fullmatch(r"[0-9]{5}P", symbol):
        return "structured_P_security"

    return None


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
# HTTP GET
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

            log(
                f"⚠️ HTTP retry "
                f"{attempt}/{retries}: "
                f"{exc}"
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
# OFFICIAL ROW FIELD EXTRACTION
# ============================================================

def extract_symbol_from_cell(
    value: Any,
) -> str:

    text = clean_text(value)

    # 官方 Security Code 通常位於 cell 開頭。
    match = re.match(
        r"^([0-9]{4,6}[A-Za-z]?)"
        r"(?:\s+|$)",
        text,
    )

    if not match:
        return ""

    symbol = clean_symbol(
        match.group(1)
    )

    if not is_valid_symbol(symbol):
        return ""

    return symbol


def extract_security_name(
    value: Any,
) -> str:

    text = clean_text(value)

    match = re.match(
        r"^[0-9]{4,6}[A-Za-z]?"
        r"(?:\s+)(.+)$",
        text,
    )

    if match:
        return clean_text(
            match.group(1)
        )

    return text


def detect_header_map(
    cells: List[str],
) -> Dict[str, int]:

    result: Dict[str, int] = {}

    for index, cell in enumerate(cells):

        normalized = normalize_text(cell)

        if (
            "SECURITYCODE" in normalized
            or "證券代號" in normalized
            or "有價證券代號" in normalized
        ):
            result["symbol"] = index

        elif (
            "SECURITYNAME" in normalized
            or "證券名稱" in normalized
            or "有價證券名稱" in normalized
        ):
            result["name"] = index

        elif (
            "MARKET" in normalized
            or "市場別" in normalized
        ):
            result["market"] = index

        elif (
            "TYPEOFSECURITY" in normalized
            or "SECURITYTYPE" in normalized
            or "證券種類" in normalized
            or "有價證券種類" in normalized
        ):
            result["security_type"] = index

        elif (
            "CFICODE" in normalized
            or "CFI" == normalized
            or normalized.endswith("CFICODE")
        ):
            result["cfi"] = index

    return result


# ============================================================
# OFFICIAL PARSER
# ============================================================

def parse_official_rows(
    text: str,
    source_url: str,
) -> List[Dict[str, str]]:

    parser = TableParser()
    parser.feed(text)

    candidates: List[
        Dict[str, str]
    ] = []

    header_map: Dict[str, int] = {}

    for row in parser.rows:

        cells = [
            clean_text(cell)
            for cell in row
        ]

        if not cells:
            continue

        detected = detect_header_map(
            cells
        )

        if (
            "symbol" in detected
            or "security_type" in detected
        ):
            header_map = detected
            continue

        symbol = ""

        if "symbol" in header_map:

            index = header_map["symbol"]

            if index < len(cells):
                symbol = extract_symbol_from_cell(
                    cells[index]
                )

        # 若 header 解析不到，才使用 row-level
        # fallback 找 Security Code。
        if not symbol:

            for cell in cells:

                possible = (
                    extract_symbol_from_cell(
                        cell
                    )
                )

                if possible:

                    symbol = possible

                    break

        if not symbol:
            continue

        # ----------------------------------------------------
        # Name
        # ----------------------------------------------------

        name = ""

        if "name" in header_map:

            index = header_map["name"]

            if index < len(cells):

                name = clean_text(
                    cells[index]
                )

        if not name:

            for cell in cells:

                if (
                    symbol
                    and cell.startswith(symbol)
                ):

                    name = (
                        extract_security_name(
                            cell
                        )
                    )

                    break

        # ----------------------------------------------------
        # Market
        # ----------------------------------------------------

        market: Optional[str] = None

        if "market" in header_map:

            index = header_map["market"]

            if index < len(cells):

                market = normalize_market(
                    cells[index]
                )

        if market is None:

            for cell in cells:

                detected_market = (
                    normalize_market(cell)
                )

                if detected_market:

                    market = detected_market

                    break

        # URL mode fallback。
        if market is None:

            if "strMode=2" in source_url:
                market = "TWSE"

            elif "strMode=4" in source_url:
                market = "TPEX"

        if market not in ALLOWED_MARKETS:
            continue

        # ----------------------------------------------------
        # Security Type
        # ----------------------------------------------------

        security_type = ""

        if "security_type" in header_map:

            index = header_map[
                "security_type"
            ]

            if index < len(cells):

                security_type = clean_text(
                    cells[index]
                )

        # ----------------------------------------------------
        # CFI
        # ----------------------------------------------------

        cfi = ""

        if "cfi" in header_map:

            index = header_map["cfi"]

            if index < len(cells):

                cfi = clean_text(
                    cells[index]
                )

        # ----------------------------------------------------
        # Raw
        # ----------------------------------------------------

        raw = " | ".join(cells)

        # ----------------------------------------------------
        # 不接受明顯標題
        # ----------------------------------------------------

        if normalize_text(name) in {
            "有價證券名稱",
            "SECURITYNAME",
            "證券名稱",
        }:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "security_type": security_type,
                "cfi": cfi,
                "raw": raw,
                "official_source": source_url,
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

    source_success_count = 0

    for url in OFFICIAL_MASTER_URLS:

        try:

            response = http_get(url)

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

            if not rows:
                continue

            source_success_count += 1

            for row in rows:

                symbol = row[
                    "symbol"
                ]

                existing = merged.get(
                    symbol
                )

                # ------------------------------------------------
                # 同 symbol 必須保持一致。
                # ------------------------------------------------

                if existing is None:

                    merged[symbol] = row

                    continue

                existing_market = (
                    existing.get("market")
                )

                current_market = (
                    row.get("market")
                )

                if (
                    existing_market
                    != current_market
                ):

                    raise RuntimeError(
                        "官方主檔出現 "
                        "market conflict："
                        f"{symbol}: "
                        f"{existing_market} / "
                        f"{current_market}"
                    )

                # 若第一來源沒有 type/cfi/name，
                # 第二官方來源可以補充。
                for key in (
                    "name",
                    "security_type",
                    "cfi",
                ):

                    if (
                        not clean_text(
                            existing.get(key)
                        )
                        and clean_text(
                            row.get(key)
                        )
                    ):

                        existing[key] = row[key]

        except Exception as exc:

            log(
                f"⚠️ 官方來源失敗："
                f"{url}"
            )

            log(
                f"   {exc}"
            )

    log(
        f"→ official symbols："
        f"{len(merged):,}"
    )

    if source_success_count == 0:

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
        os.environ.get("FINMIND_TOKEN")
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
                record
                for record in data
                if isinstance(
                    record,
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
            record.get("stock_id")
        )

        if not is_valid_symbol(symbol):
            continue

        market = normalize_market(
            record.get("type")
        )

        if market not in ALLOWED_MARKETS:
            continue

        result[symbol] = {
            "symbol": symbol,
            "name": clean_text(
                record.get("stock_name")
            ),
            "market": market,
            "category": clean_text(
                record.get("category")
            ),
            "date": clean_text(
                record.get("date")
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
        List[Dict[str, Any]],
    ] = {}

    for record in records:

        symbol = clean_symbol(
            record.get("stock_id")
        )

        if not is_valid_symbol(symbol):
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

        result[symbol] = {
            "symbol": symbol,
            "name": clean_text(
                row.get("stock_name")
            ),
            "market": market,
            "industry": clean_text(
                row.get(
                    "industry_category"
                )
            ),
            "date": clean_text(
                row.get("date")
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

    except Exception as exc:

        log(
            f"⚠️ 無法讀取既有 Universe："
            f"{exc}"
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
# CLASSIFY OFFICIAL CANDIDATE
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
    Optional[str],
]:

    symbol = clean_symbol(
        official.get("symbol")
    )

    market = normalize_market(
        official.get("market")
    )

    if not is_valid_symbol(symbol):
        return None, "invalid_symbol"

    if market not in ALLOWED_MARKETS:
        return None, "invalid_market"

    official_name = clean_text(
        official.get("name")
    )

    security_type = clean_text(
        official.get(
            "security_type"
        )
    )

    official_cfi = clean_text(
        official.get("cfi")
    )

    official_raw = clean_text(
        official.get("raw")
    )

    finmind_name = clean_text(
        (finmind or {}).get("name")
    )

    finmind_industry = clean_text(
        (finmind or {}).get(
            "industry"
        )
    )

    active_etf_name = clean_text(
        (active_etf or {}).get(
            "name"
        )
    )

    # --------------------------------------------------------
    # 名稱：
    # 官方優先。
    # FinMind 只補充空白。
    # --------------------------------------------------------

    name = (
        official_name
        or finmind_name
        or active_etf_name
        or symbol
    )

    # --------------------------------------------------------
    # 官方身份判定
    # --------------------------------------------------------
    #
    # ETF：
    #   官方 Type / 官方商品名稱明確為 ETF
    #
    # STOCK：
    #   官方 Type 明確為股票
    #
    # UNKNOWN：
    #   絕不預設 STOCK。
    # --------------------------------------------------------

    official_is_etf = official_type_is_etf(
        security_type,
        official_name,
        official_raw,
    )

    official_is_stock = official_type_is_stock(
        security_type,
        official_name,
    )

    if official_is_etf:

        instrument_type = "ETF"

    elif official_is_stock:

        instrument_type = "STOCK"

    else:

        # ----------------------------------------------------
        # 官方身份未知。
        #
        # FinMind 不能把它變成 STOCK。
        # Active ETF 可作為 ETF 補充身份，
        # 但前提是 symbol 已存在官方主檔。
        # ----------------------------------------------------

        if active_etf is not None:

            instrument_type = "ETF"

        else:

            return None, "official_type_unknown"

    is_etf = (
        instrument_type == "ETF"
    )

    # --------------------------------------------------------
    # 市場一致性檢查
    # --------------------------------------------------------

    if active_etf is not None:

        finmind_etf_market = (
            normalize_market(
                active_etf.get(
                    "market"
                )
            )
        )

        if (
            finmind_etf_market
            and finmind_etf_market
            != market
        ):

            return (
                None,
                "official_finmind_market_conflict",
            )

    if finmind is not None:

        finmind_market = (
            normalize_market(
                finmind.get("market")
            )
        )

        if (
            finmind_market
            and finmind_market != market
        ):

            return (
                None,
                "official_finmind_market_conflict",
            )

    # --------------------------------------------------------
    # 特殊商品排除
    # --------------------------------------------------------

    reason = exclusion_reason(
        symbol,
        name,
        security_type,
        official_cfi,
        is_etf=is_etf,
    )

    if reason:

        return None, reason

    # --------------------------------------------------------
    # full symbol
    # --------------------------------------------------------

    suffix = (
        ".TW"
        if market == "TWSE"
        else ".TWO"
    )

    return (
        {
            "symbol": symbol,
            "full_symbol": (
                f"{symbol}{suffix}"
            ),
            "name": name,
            "market": market,
            "type": instrument_type,
            "instrument_type": instrument_type,
            "status": ACTIVE_STATUS,
            "source": "official_product_master",
        },
        None,
    )


# ============================================================
# METADATA MERGE
# ============================================================

def merge_metadata(
    candidate: Dict[str, Any],
    official: Dict[str, Any],
    finmind: Optional[
        Dict[str, Any]
    ],
    active_etf: Optional[
        Dict[str, Any]
    ],
    existing: Dict[str, Any],
) -> Dict[str, Any]:

    merged = dict(candidate)

    # --------------------------------------------------------
    # Official identity metadata
    # --------------------------------------------------------

    security_type = clean_text(
        official.get(
            "security_type"
        )
    )

    cfi = clean_text(
        official.get("cfi")
    )

    if security_type:
        merged[
            "official_security_type"
        ] = security_type

    if cfi:
        merged[
            "official_cfi"
        ] = cfi

    # --------------------------------------------------------
    # FinMind metadata
    # --------------------------------------------------------

    if finmind:

        industry = clean_text(
            finmind.get(
                "industry"
            )
        )

        date = clean_text(
            finmind.get(
                "date"
            )
        )

        if industry:
            merged["industry"] = industry

        if date:
            merged[
                "finmind_date"
            ] = date

    if active_etf:

        category = clean_text(
            active_etf.get(
                "category"
            )
        )

        date = clean_text(
            active_etf.get(
                "date"
            )
        )

        if category:
            merged[
                "category"
            ] = category

        if date:
            merged[
                "active_etf_date"
            ] = date

    # --------------------------------------------------------
    # Preserve existing application metadata only.
    #
    # 舊資料絕不能覆蓋：
    # symbol
    # market
    # type
    # status
    # source
    # full_symbol
    # --------------------------------------------------------

    if isinstance(
        existing,
        dict,
    ):

        preserved_keys = (
            "description",
            "tags",
            "classification",
            "sector",
        )

        for key in preserved_keys:

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

    count = len(stocks)

    if count < MIN_OFFICIAL_SYMBOLS:

        raise RuntimeError(
            "Universe validation failed: "
            f"{count} < "
            f"{MIN_OFFICIAL_SYMBOLS}"
        )

    if count > MAX_UNIVERSE_SYMBOLS:

        raise RuntimeError(
            "Universe validation failed: "
            f"{count:,} > "
            f"{MAX_UNIVERSE_SYMBOLS:,}"
        )

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"{symbol}: item is not dict"
            )

        normalized_symbol = clean_symbol(
            item.get("symbol")
        )

        if normalized_symbol != symbol:

            raise RuntimeError(
                f"{symbol}: symbol mismatch"
            )

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            raise RuntimeError(
                f"{symbol}: status != active"
            )

        market = normalize_market(
            item.get("market")
        )

        if market not in ALLOWED_MARKETS:

            raise RuntimeError(
                f"{symbol}: invalid market"
            )

        instrument_type = item.get(
            "type"
        )

        if instrument_type not in ALLOWED_TYPES:

            raise RuntimeError(
                f"{symbol}: invalid type"
            )

        # ----------------------------------------------------
        # 官方身份硬性 Gate
        # ----------------------------------------------------

        if symbol not in official:

            raise RuntimeError(
                f"{symbol}: "
                "not present in official master"
            )

        official_item = official[
            symbol
        ]

        official_market = normalize_market(
            official_item.get(
                "market"
            )
        )

        if official_market != market:

            raise RuntimeError(
                f"{symbol}: "
                "official market mismatch"
            )

        official_type = clean_text(
            official_item.get(
                "security_type"
            )
        )

        official_name = clean_text(
            official_item.get(
                "name"
            )
        )

        official_raw = clean_text(
            official_item.get(
                "raw"
            )
        )

        official_is_etf = (
            official_type_is_etf(
                official_type,
                official_name,
                official_raw,
            )
        )

        official_is_stock = (
            official_type_is_stock(
                official_type,
                official_name,
            )
        )

        if instrument_type == "ETF":

            # ETF 必須能由官方身份或
            # 已存在官方 symbol 的 Active ETF
            # 得到支持。
            #
            # validation 只確認官方資料存在；
            # candidate 階段已完成 Active ETF 補充。
            if not (
                official_is_etf
                or "ETF" in normalize_text(
                    official_type
                )
                or "ETF" in normalize_text(
                    official_name
                )
            ):

                # 如果 official type 不明但最後是 ETF，
                # 必須要求 official row 本身存在 ETF
                # 身份訊號。
                raise RuntimeError(
                    f"{symbol}: "
                    "ETF lacks official ETF identity"
                )

        elif instrument_type == "STOCK":

            if not official_is_stock:

                raise RuntimeError(
                    f"{symbol}: "
                    "STOCK lacks official "
                    "stock identity"
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

        if full_symbol != (
            f"{symbol}{expected_suffix}"
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid full_symbol"
            )

        if not is_valid_symbol(
            symbol
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid symbol"
            )

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "ETF"
    )

    if (
        stock_count
        + etf_count
        != count
    ):

        raise RuntimeError(
            "STOCK/ETF count mismatch"
        )

    log(
        f"✓ validation："
        f"{count:,} active candidates"
    )

    log(
        f"  STOCK：{stock_count:,}"
    )

    log(
        f"  ETF：{etf_count:,}"
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

        # 暫存檔 JSON validation。
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
        "UNIVERSE BUILDER V6"
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
        Dict[str, Any],
    ] = {}

    exclusion_counts: Dict[
        str,
        int,
    ] = {}

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

            reason_key = (
                reason
                or "unknown"
            )

            exclusion_counts[
                reason_key
            ] = (
                exclusion_counts.get(
                    reason_key,
                    0,
                )
                + 1
            )

            continue

        candidate = merge_metadata(
            candidate,
            official_item,
            finmind.get(symbol),
            active_etfs.get(symbol),
            existing.get(
                symbol,
                {},
            ),
        )

        stocks[symbol] = candidate

    log(
        f"✓ official symbols："
        f"{len(official):,}"
    )

    log(
        f"✓ Universe candidates："
        f"{len(stocks):,}"
    )

    if exclusion_counts:

        log("✓ exclusions：")

        for reason, count in sorted(
            exclusion_counts.items()
        ):

            log(
                f"  {reason}: "
                f"{count:,}"
            )

    # ========================================================
    # ABNORMAL GROWTH GATE
    # ========================================================

    if len(stocks) > MAX_UNIVERSE_SYMBOLS:

        raise RuntimeError(
            "Universe candidate count "
            "異常："
            f"{len(stocks):,} > "
            f"{MAX_UNIVERSE_SYMBOLS:,}"
        )

    if existing:

        previous_count = len(
            existing
        )

        if (
            previous_count >= MIN_OFFICIAL_SYMBOLS
            and len(stocks)
            > previous_count
            * MAX_GROWTH_RATIO
        ):

            raise RuntimeError(
                "Universe 異常暴增："
                f"previous={previous_count:,}, "
                f"current={len(stocks):,}, "
                f"ratio="
                f"{len(stocks) / previous_count:.2f}x"
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

    payload: Dict[str, Any] = {

        "version": "UNIVERSE-BUILD",

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

            "identity": (
                "official_product_master"
            ),

            "official_master_urls": (
                list(
                    OFFICIAL_MASTER_URLS
                )
            ),

            "finmind": (
                FINMIND_INFO_DATASET
            ),

            "finmind_active_etf": (
                FINMIND_ACTIVE_ETF_DATASET
            ),

            "finmind_role": (
                "identity_and_metadata_supplement_only"
            ),

            "finmind_can_create_symbols": (
                False
            ),

            "price_data_is_not_universe_source": (
                True
            ),

            "volume_data_is_not_universe_source": (
                True
            ),

            "yahoo_is_not_universe_source": (
                True
            ),

            "cmoney_is_not_universe_source": (
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

            "official_master_required": True,

            "official_master_is_identity_source": (
                True
            ),

            "finmind_identity_supplement_only": (
                True
            ),

            "finmind_cannot_create_symbol": (
                True
            ),

            "unknown_type_is_not_stock": (
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

            "structured_security_excluded": (
                True
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

            "atomic_write": (
                True
            ),

            "preserve_existing_on_failure": (
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
        payload["universe_count"]
        != len(
            payload["stocks"]
        )
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

    if (
        payload["market_count"]["TWSE"]
        + payload["market_count"]["TPEX"]
        != payload["universe_count"]
    ):

        raise RuntimeError(
            "market count mismatch"
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
    # POST-WRITE VALIDATION
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
            "post-write root is not dict"
        )

    written_stocks = written.get(
        "stocks"
    )

    if not isinstance(
        written_stocks,
        dict,
    ):

        raise RuntimeError(
            "post-write stocks is not dict"
        )

    if (
        written.get(
            "universe_count"
        )
        != len(written_stocks)
    ):

        raise RuntimeError(
            "post-write universe_count mismatch"
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
            "post-write STOCK/ETF mismatch"
        )

    # 再次確認每個 symbol 都是官方來源。
    for symbol in written_stocks:

        if symbol not in official:

            raise RuntimeError(
                f"post-write illegal symbol: "
                f"{symbol}"
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
    sys.exit(main())