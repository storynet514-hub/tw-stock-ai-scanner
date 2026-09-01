#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - Scripts/build_universe.py
============================================================

唯一責任
------------------------------------------------------------
建立：
    Data/universe.json

本程式只負責：
    「官方目前有哪些商品，以及哪些商品允許進入 Universe」

============================================================
CORE CONTRACT
============================================================

1. 官方商品分類資料是 Universe 的唯一身份來源。

2. 官方資料必須明確確認商品身份。

3. 只有官方明確確認為：
       STOCK
       ETF
   的商品可以進入 Universe。

4. 官方 UNKNOWN / 無法判定身份：
       直接排除
       不得猜測

5. ETF：
       不限制 Symbol 長度
       不限制 Symbol 是否包含英文字母
       不限制 ETF 類型
       債券 ETF 可以存在

6. 一般債券：
       不是 ETF 時不得進入 Universe。

7. 以下商品不得進入 Universe：
       ETN
       REIT
       TDR
       WARRANT
       PREFERRED SHARE
       GENERAL BOND

8. FinMind 僅能補充 metadata。

9. FinMind 永遠不能：
       創造 symbol
       創造 STOCK
       創造 ETF
       把 UNKNOWN 變成 STOCK
       把 UNKNOWN 變成 ETF
       修改官方 STOCK / ETF
       修改官方 market

10. 不使用：
       price
       volume
       Yahoo
       CMoney

11. 官方身份來源不足：
       FAIL CLOSED

12. FAIL：
       不覆蓋既有 universe.json

13. 寫入前：
       identity cross-check

14. 寫入：
       atomic write

15. 寫入後：
       reload
       再次完整 validation

============================================================
IMPORTANT
============================================================

本檔不得寫死任何特定股票或 ETF Symbol。

例如：
    不得出現：
        00407A
        00408A
        00409A
        00410A
        006203
        0050
        2330
        ...

ETF 是否納入只由官方資料的：
    證券性質 == ETF

決定。

股票是否納入只由官方資料的：
    證券性質 == 股票

決定。

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

OFFICIAL_BASE_URL = (
    "https://isin.twse.com.tw/isin/class_main.jsp"
)

# 官方「證券編碼－分類查詢」
#
# issuetype=1
#     股票
#
# issuetype=I
#     ETF
#
# market=1
#     上市
#
# market=2
#     上櫃
#
# 官方查詢結果直接包含：
#     證券代號
#     證券名稱
#     市場
#     有價證券別
#
# 因此不再從 symbol 外觀猜測 STOCK / ETF。

OFFICIAL_QUERIES = (
    {
        "market": "TWSE",
        "issuetype": "1",
        "type": "STOCK",
    },
    {
        "market": "TPEX",
        "issuetype": "1",
        "type": "STOCK",
    },
    {
        "market": "TWSE",
        "issuetype": "I",
        "type": "ETF",
    },
    {
        "market": "TPEX",
        "issuetype": "I",
        "type": "ETF",
    },
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

MIN_STOCK_COUNT = 100
MIN_ETF_COUNT = 10

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

# 這些規則只能「排除」商品。
#
# 絕對不能用這些文字：
#     把 UNKNOWN 變 STOCK
#     把 UNKNOWN 變 ETF
#
# ETF 身份已由官方「有價證券別」決定。
#
# 因此「債券」只有在：
#     official_type != ETF
#
# 時才可能成為排除依據。

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
    "CORPORATE BOND",
    "GOVERNMENT BOND",
    "FINANCIAL BOND",
    "CONVERTIBLE BOND",
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; tw-stock-ai-scanner/"
            "universe-builder)"
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

    text = clean_text(
        value
    ).upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TWSE",
        ".TPEX",
    ):

        if text.endswith(
            suffix
        ):

            text = text[
                :-len(suffix)
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

    # 不限制 4 / 5 / 6 碼。
    # 只要求官方回傳的交易代號具有
    # 合法的數字 / 英文字母格式。
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
# RESPONSE DECODING
# ============================================================

def decode_response(
    response: requests.Response,
) -> str:

    raw = response.content

    declared = (
        response.encoding
        or ""
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
    params: Optional[
        Dict[str, Any]
    ] = None,
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
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response

        except Exception as exc:

            last_error = exc

            log(
                f"⚠️ HTTP attempt "
                f"{attempt}/{retries} "
                f"failed：{exc}"
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
# OFFICIAL ROW PARSER
# ============================================================

def parse_official_classification(
    text: str,
    expected_market: str,
    expected_type: str,
    source_url: str,
) -> List[
    Dict[str, Any]
]:

    parser = TableParser()

    parser.feed(
        text
    )

    results: List[
        Dict[str, Any]
    ] = []

    for row in parser.rows:

        cells = [
            clean_text(x)
            for x in row
            if clean_text(x)
        ]

        if len(cells) < 6:
            continue

        # 官方 class_main 結果欄位：
        #
        # 0 序號
        # 1 ISIN
        # 2 證券代號
        # 3 證券名稱
        # 4 市場
        # 5 有價證券別
        # 6 產業別
        # 7 上市/上櫃日期
        # 8 CFICode
        # 9 備註

        if len(cells) < 6:
            continue

        symbol = clean_symbol(
            cells[2]
        )

        if not is_valid_symbol(
            symbol
        ):
            continue

        name = clean_text(
            cells[3]
        )

        market = normalize_market(
            cells[4]
        )

        security_type = (
            normalize_security_type(
                cells[5]
            )
        )

        if market != expected_market:
            continue

        if security_type != expected_type:
            continue

        industry = ""

        if len(cells) >= 7:
            industry = clean_text(
                cells[6]
            )

        listed_date = ""

        if len(cells) >= 8:
            listed_date = clean_text(
                cells[7]
            )

        cfi_code = ""

        if len(cells) >= 9:
            cfi_code = clean_text(
                cells[8]
            )

        remarks = ""

        if len(cells) >= 10:
            remarks = clean_text(
                cells[9]
            )

        results.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "official_type": security_type,
                "industry": industry,
                "listed_date": listed_date,
                "cfi_code": cfi_code,
                "remarks": remarks,
                "official_source": source_url,
                "official_raw": " | ".join(
                    cells
                ),
            }
        )

    return results


# ============================================================
# OFFICIAL SECURITY TYPE
# ============================================================

def normalize_security_type(
    value: Any,
) -> Optional[str]:

    text = normalize_text(
        value
    )

    if not text:
        return None

    # 官方中文
    if (
        text == "ETF"
        or text == "指數股票型基金"
    ):
        return "ETF"

    if (
        text == "股票"
        or text == "普通股"
    ):
        return "STOCK"

    # 防禦性英文
    if text in {
        "ETF",
        "STOCK",
        "COMMONSTOCK",
    }:

        if text == "ETF":
            return "ETF"

        return "STOCK"

    return None


# ============================================================
# OFFICIAL MASTER FETCH
# ============================================================

def fetch_one_official_query(
    query: Dict[str, str],
) -> List[
    Dict[str, Any]
]:

    market = query[
        "market"
    ]

    expected_type = query[
        "type"
    ]

    params = {
        "Page": "1",
        "chklike": "Y",
        "issuetype": query[
            "issuetype"
        ],
        "market": (
            "1"
            if market == "TWSE"
            else "2"
        ),
    }

    response = http_get(
        OFFICIAL_BASE_URL,
        params=params,
    )

    text = decode_response(
        response
    )

    source_url = response.url

    log(
        f"→ HTTP "
        f"{response.status_code} "
        f"bytes="
        f"{len(response.content):,}"
    )

    rows = parse_official_classification(
        text,
        expected_market=market,
        expected_type=expected_type,
        source_url=source_url,
    )

    log(
        f"→ official "
        f"{market} "
        f"{expected_type} rows："
        f"{len(rows):,}"
    )

    return rows


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

    source_stats = {
        "TWSE_STOCK": 0,
        "TPEX_STOCK": 0,
        "TWSE_ETF": 0,
        "TPEX_ETF": 0,
    }

    successful_queries = 0

    for query in OFFICIAL_QUERIES:

        label = (
            f"{query['market']}_"
            f"{query['type']}"
        )

        log("")
        log(
            f"→ OFFICIAL {label}"
        )

        try:

            rows = fetch_one_official_query(
                query
            )

        except Exception as exc:

            raise RuntimeError(
                "官方身份來源失敗："
                f"{label}: {exc}"
            ) from exc

        if not rows:

            raise RuntimeError(
                "官方身份來源為空："
                f"{label}"
            )

        successful_queries += 1

        source_stats[
            label
        ] = len(rows)

        for item in rows:

            symbol = clean_symbol(
                item.get(
                    "symbol"
                )
            )

            if not is_valid_symbol(
                symbol
            ):
                continue

            official_type = item.get(
                "official_type"
            )

            market = item.get(
                "market"
            )

            if (
                official_type
                not in ALLOWED_TYPES
            ):
                continue

            if (
                market
                not in ALLOWED_MARKETS
            ):
                continue

            previous = merged.get(
                symbol
            )

            # ------------------------------------------------
            # 同一 Symbol 如果官方來源出現身份衝突，
            # 直接 fail。
            #
            # 不能猜。
            # ------------------------------------------------

            if previous is not None:

                previous_type = previous.get(
                    "official_type"
                )

                previous_market = previous.get(
                    "market"
                )

                if (
                    previous_type
                    != official_type
                ):

                    raise RuntimeError(
                        f"{symbol}: "
                        "官方身份衝突："
                        f"{previous_type} vs "
                        f"{official_type}"
                    )

                if (
                    previous_market
                    != market
                ):

                    raise RuntimeError(
                        f"{symbol}: "
                        "官方市場衝突："
                        f"{previous_market} vs "
                        f"{market}"
                    )

                # 同身份資料可更新 metadata。
                #
                # 官方身份本身不變。
                previous_name = clean_text(
                    previous.get(
                        "name"
                    )
                )

                if not previous_name:

                    previous[
                        "name"
                    ] = clean_text(
                        item.get(
                            "name"
                        )
                    )

                continue

            merged[
                symbol
            ] = dict(item)

    if (
        successful_queries
        != len(OFFICIAL_QUERIES)
    ):

        raise RuntimeError(
            "官方身份來源沒有全部成功"
        )

    official_stock = sum(
        1
        for item in merged.values()
        if item.get(
            "official_type"
        ) == "STOCK"
    )

    official_etf = sum(
        1
        for item in merged.values()
        if item.get(
            "official_type"
        ) == "ETF"
    )

    log("")
    log(
        "OFFICIAL CLASSIFICATION"
    )

    for key, value in source_stats.items():

        log(
            f"  {key}: "
            f"{value:,}"
        )

    log(
        f"✓ Official STOCK："
        f"{official_stock:,}"
    )

    log(
        f"✓ Official ETF："
        f"{official_etf:,}"
    )

    log(
        f"✓ Official symbols："
        f"{len(merged):,}"
    )

    # --------------------------------------------------------
    # 官方身份最低完整性
    # --------------------------------------------------------

    if official_stock < MIN_STOCK_COUNT:

        raise RuntimeError(
            "官方 STOCK 身份資料不足："
            f"{official_stock} < "
            f"{MIN_STOCK_COUNT}"
        )

    if official_etf < MIN_ETF_COUNT:

        raise RuntimeError(
            "官方 ETF 身份資料不足："
            f"{official_etf} < "
            f"{MIN_ETF_COUNT}"
        )

    # --------------------------------------------------------
    # 不允許 UNKNOWN 混入官方 identity master
    # --------------------------------------------------------

    for symbol, item in merged.items():

        if item.get(
            "official_type"
        ) not in ALLOWED_TYPES:

            raise RuntimeError(
                f"{symbol}: "
                "官方身份不是 STOCK/ETF"
            )

    return merged


# ============================================================
# FINMIND
# ============================================================

def finmind_headers() -> Dict[
    str,
    str
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

            if attempt < FINMIND_RETRIES:

                time.sleep(
                    RETRY_SLEEP_SECONDS
                    * attempt
                )

    raise RuntimeError(
        f"FinMind {dataset} failed: "
        f"{last_error}"
    )


def fetch_finmind_identity() -> Dict[
    str,
    Dict[str, Any],
]:

    try:

        records = fetch_finmind_dataset(
            FINMIND_INFO_DATASET
        )

    except Exception as exc:

        # FinMind 是 metadata supplement。
        #
        # 官方 identity 已經建立完成。
        #
        # FinMind 失敗不能阻止官方 Universe 建立。
        log(
            "⚠️ FinMind identity "
            "supplement unavailable："
            f"{exc}"
        )

        return {}

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
        f"✓ FinMind identity metadata："
        f"{len(result):,}"
    )

    return result


def fetch_finmind_active_etfs() -> Dict[
    str,
    Dict[str, Any],
]:

    try:

        records = fetch_finmind_dataset(
            FINMIND_ACTIVE_ETF_DATASET
        )

    except Exception as exc:

        log(
            "⚠️ FinMind ETF "
            "metadata unavailable："
            f"{exc}"
        )

        return {}

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
        f"✓ FinMind ETF metadata："
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

    except Exception as exc:

        log(
            f"⚠️ Existing universe "
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
# DEFENSIVE EXCLUSION
# ============================================================

def defensive_exclusion_reason(
    official_item: Dict[str, Any],
) -> Optional[str]:

    official_type = official_item.get(
        "official_type"
    )

    name = clean_text(
        official_item.get(
            "name"
        )
    )

    raw = clean_text(
        official_item.get(
            "official_raw"
        )
    )

    combined = (
        normalize_text(name)
        + normalize_text(raw)
    )

    # --------------------------------------------------------
    # ETF 是官方身份。
    #
    # ETF 不能因為名稱包含：
    #     債券
    #
    # 就被排除。
    #
    # 因此 bond exclusion 僅適用於 STOCK。
    # --------------------------------------------------------

    if contains_any(
        combined,
        ETN_WORDS,
    ):

        return "etn"

    if contains_any(
        combined,
        WARRANT_WORDS,
    ):

        return "warrant"

    if contains_any(
        combined,
        TDR_WORDS,
    ):

        return "tdr"

    if contains_any(
        combined,
        REIT_WORDS,
    ):

        return "reit"

    if official_type == "STOCK":

        if contains_any(
            combined,
            PREFERRED_WORDS,
        ):

            return "preferred_share"

        if contains_any(
            combined,
            BOND_WORDS,
        ):

            return "bond"

    return None


# ============================================================
# CANDIDATE
# ============================================================

def build_candidate(
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

        return None, "invalid_symbol"

    if market not in ALLOWED_MARKETS:

        return None, "invalid_market"

    if official_type not in ALLOWED_TYPES:

        return None, "official_identity_unknown"

    # --------------------------------------------------------
    # Defensive exclusion
    # --------------------------------------------------------

    exclusion = defensive_exclusion_reason(
        official
    )

    if exclusion:

        return None, exclusion

    # --------------------------------------------------------
    # Name
    #
    # Official name has priority.
    #
    # FinMind cannot replace identity.
    # --------------------------------------------------------

    finmind_name = clean_text(
        (
            finmind or {}
        ).get(
            "name"
        )
    )

    name = (
        official_name
        or finmind_name
        or symbol
    )

    # --------------------------------------------------------
    # Market conflict:
    #
    # official wins.
    # --------------------------------------------------------

    finmind_market = (
        (
            finmind or {}
        ).get(
            "market"
        )
    )

    if (
        finmind_market
        and finmind_market != market
    ):

        log(
            f"⚠️ {symbol}: "
            f"FinMind market="
            f"{finmind_market}, "
            f"official market="
            f"{market}; "
            f"使用官方身份"
        )

    # --------------------------------------------------------
    # ETF metadata
    #
    # active_etf 只能補資料。
    #
    # 它不能把 STOCK 變 ETF。
    # --------------------------------------------------------

    category = ""

    if active_etf:

        category = clean_text(
            active_etf.get(
                "category"
            )
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
        "type": official_type,
        "instrument_type": official_type,
        "status": ACTIVE_STATUS,
        "source": (
            "official_product_master"
        ),
        "official_type": official_type,
        "official_section": (
            official_type
        ),
    }

    # --------------------------------------------------------
    # Metadata only
    # --------------------------------------------------------

    industry = clean_text(
        official.get(
            "industry"
        )
    )

    if industry:
        candidate[
            "official_industry"
        ] = industry

    listed_date = clean_text(
        official.get(
            "listed_date"
        )
    )

    if listed_date:
        candidate[
            "official_listed_date"
        ] = listed_date

    cfi_code = clean_text(
        official.get(
            "cfi_code"
        )
    )

    if cfi_code:
        candidate[
            "official_cfi_code"
        ] = cfi_code

    if category:
        candidate[
            "finmind_category"
        ] = category

    return candidate, None


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
    # Existing metadata
    #
    # 只允許 metadata。
    #
    # 不允許覆蓋 identity。
    # --------------------------------------------------------

    allowed_existing_metadata = (
        "description",
        "tags",
        "classification",
        "sector",
        "category",
    )

    for key in (
        allowed_existing_metadata
    ):

        if key in existing:

            merged[key] = existing[
                key
            ]

    # --------------------------------------------------------
    # FinMind metadata
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

        finmind_date = clean_text(
            finmind.get(
                "date"
            )
        )

        if finmind_date:

            merged[
                "finmind_date"
            ] = finmind_date

    return merged


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

    errors: List[str] = []

    for symbol, item in stocks.items():

        official_item = official.get(
            symbol
        )

        if official_item is None:

            errors.append(
                f"{symbol}: "
                "not in official master"
            )

            continue

        # symbol
        if clean_symbol(
            item.get(
                "symbol"
            )
        ) != symbol:

            errors.append(
                f"{symbol}: "
                "symbol mismatch"
            )

        # market
        if (
            item.get(
                "market"
            )
            != official_item.get(
                "market"
            )
        ):

            errors.append(
                f"{symbol}: "
                "market mismatch"
            )

        # type
        if (
            item.get(
                "type"
            )
            != official_item.get(
                "official_type"
            )
        ):

            errors.append(
                f"{symbol}: "
                "type mismatch"
            )

        # official_type
        if (
            item.get(
                "official_type"
            )
            != official_item.get(
                "official_type"
            )
        ):

            errors.append(
                f"{symbol}: "
                "official_type mismatch"
            )

        # allowed type
        if item.get(
            "type"
        ) not in ALLOWED_TYPES:

            errors.append(
                f"{symbol}: "
                "invalid Universe type"
            )

        # status
        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            errors.append(
                f"{symbol}: "
                "status != active"
            )

        # source
        if item.get(
            "source"
        ) != "official_product_master":

            errors.append(
                f"{symbol}: "
                "invalid identity source"
            )

        # full symbol
        expected_suffix = (
            ".TW"
            if item.get(
                "market"
            ) == "TWSE"
            else ".TWO"
        )

        expected_full_symbol = (
            f"{symbol}"
            f"{expected_suffix}"
        )

        if item.get(
            "full_symbol"
        ) != expected_full_symbol:

            errors.append(
                f"{symbol}: "
                "full_symbol mismatch"
            )

    if errors:

        preview = errors[
            :20
        ]

        raise RuntimeError(
            "IDENTITY CROSS-CHECK FAILED："
            + " | ".join(
                preview
            )
        )

    log(
        f"✓ identity cross-check："
        f"{len(stocks):,} symbols OK"
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

    if not stocks:

        raise RuntimeError(
            "Universe 不得為空"
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
                "item 不是 dict"
            )

        if clean_symbol(
            symbol
        ) != symbol:

            raise RuntimeError(
                f"{symbol}: "
                "invalid key"
            )

        if not is_valid_symbol(
            symbol
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid symbol"
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

        instrument_type = item.get(
            "type"
        )

        if (
            instrument_type
            not in ALLOWED_TYPES
        ):

            raise RuntimeError(
                f"{symbol}: "
                "invalid type"
            )

        if symbol not in official:

            raise RuntimeError(
                f"{symbol}: "
                "not official"
            )

        official_item = official[
            symbol
        ]

        if (
            official_item.get(
                "official_type"
            )
            != instrument_type
        ):

            raise RuntimeError(
                f"{symbol}: "
                "official type mismatch"
            )

        if (
            official_item.get(
                "market"
            )
            != market
        ):

            raise RuntimeError(
                f"{symbol}: "
                "official market mismatch"
            )

        if item.get(
            "official_type"
        ) != instrument_type:

            raise RuntimeError(
                f"{symbol}: "
                "official_type mismatch"
            )

        expected_suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )

        expected_full_symbol = (
            f"{symbol}"
            f"{expected_suffix}"
        )

        if item.get(
            "full_symbol"
        ) != expected_full_symbol:

            raise RuntimeError(
                f"{symbol}: "
                "invalid full_symbol"
            )

        if item.get(
            "source"
        ) != "official_product_master":

            raise RuntimeError(
                f"{symbol}: "
                "invalid source"
            )

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

    if stock_count < MIN_STOCK_COUNT:

        raise RuntimeError(
            "Universe STOCK 數量異常："
            f"{stock_count} < "
            f"{MIN_STOCK_COUNT}"
        )

    if etf_count < MIN_ETF_COUNT:

        raise RuntimeError(
            "Universe ETF 數量異常："
            f"{etf_count} < "
            f"{MIN_ETF_COUNT}"
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

        # Temporary JSON syntax validation
        with open(
            temp_name,
            "r",
            encoding="utf-8",
        ) as handle:

            json.load(
                handle
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
# POST WRITE VALIDATION
# ============================================================

def validate_written_file(
    official: Dict[
        str,
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "universe.json 不存在"
        )

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        raise RuntimeError(
            f"寫入後 JSON 無法讀取：{exc}"
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):

        raise RuntimeError(
            "寫入後 payload 不是 dict"
        )

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "寫入後 stocks 不是 dict"
        )

    universe_count = payload.get(
        "universe_count"
    )

    if universe_count != len(
        stocks
    ):

        raise RuntimeError(
            "寫入後 universe_count mismatch"
        )

    stock_count = payload.get(
        "stock_count"
    )

    etf_count = payload.get(
        "etf_count"
    )

    if (
        not isinstance(
            stock_count,
            int,
        )
        or not isinstance(
            etf_count,
            int,
        )
    ):

        raise RuntimeError(
            "寫入後 STOCK/ETF count invalid"
        )

    if (
        stock_count
        + etf_count
        != universe_count
    ):

        raise RuntimeError(
            "寫入後 STOCK/ETF count mismatch"
        )

    # 完整 identity validation
    validate_universe(
        stocks,
        official,
    )

    identity_cross_check(
        stocks,
        official,
    )

    # source contract
    source = payload.get(
        "source"
    )

    if not isinstance(
        source,
        dict,
    ):

        raise RuntimeError(
            "寫入後 source invalid"
        )

    if source.get(
        "price_data_used"
    ) is not False:

        raise RuntimeError(
            "price_data_used 必須為 False"
        )

    if source.get(
        "volume_data_used"
    ) is not False:

        raise RuntimeError(
            "volume_data_used 必須為 False"
        )

    if source.get(
        "yahoo_used"
    ) is not False:

        raise RuntimeError(
            "yahoo_used 必須為 False"
        )

    if source.get(
        "cmoney_used"
    ) is not False:

        raise RuntimeError(
            "cmoney_used 必須為 False"
        )

    log(
        "✓ post-write reload validation：OK"
    )

    return payload


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
    # STEP 1 — OFFICIAL IDENTITY
    # ========================================================

    section(
        "STEP 1 — OFFICIAL IDENTITY"
    )

    official = (
        fetch_official_master()
    )

    # ========================================================
    # STEP 2 — FINMIND METADATA
    # ========================================================

    section(
        "STEP 2 — FINMIND METADATA"
    )

    # 注意：
    #
    # FinMind 是 supplement。
    #
    # 即使 FinMind 失敗，
    # 官方 Universe identity 仍然有效。
    #
    # FinMind 絕對不能參與身份建立。

    finmind = (
        fetch_finmind_identity()
    )

    active_etfs = (
        fetch_finmind_active_etfs()
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

    excluded = {
        "invalid_symbol": 0,
        "invalid_market": 0,
        "official_identity_unknown": 0,
        "etn": 0,
        "warrant": 0,
        "reit": 0,
        "tdr": 0,
        "preferred_share": 0,
        "bond": 0,
        "other": 0,
    }

    for symbol, official_item in (
        official.items()
    ):

        candidate, reason = (
            build_candidate(
                official_item,
                finmind.get(
                    symbol
                ),
                active_etfs.get(
                    symbol
                ),
            )
        )

        if candidate is None:

            if reason in excluded:

                excluded[
                    reason
                ] += 1

            else:

                excluded[
                    "other"
                ] += 1

            continue

        candidate = merge_metadata(
            candidate,
            existing.get(
                symbol,
                {},
            ),
            finmind.get(
                symbol
            ),
        )

        stocks[
            symbol
        ] = candidate

    log("")
    log(
        f"✓ Official identity："
        f"{len(official):,}"
    )

    log(
        f"✓ Universe candidates："
        f"{len(stocks):,}"
    )

    log(
        "✓ exclusions："
    )

    for key, value in (
        excluded.items()
    ):

        if value:

            log(
                f"  {key}: "
                f"{value:,}"
            )

    # ========================================================
    # STEP 4 — PRE-WRITE VALIDATION
    # ========================================================

    section(
        "STEP 4 — PRE-WRITE VALIDATION"
    )

    identity_cross_check(
        stocks,
        official,
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
        if item.get(
            "type"
        ) == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "type"
        ) == "ETF"
    )

    twse_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "market"
        ) == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "market"
        ) == "TPEX"
    )

    # ========================================================
    # PAYLOAD
    # ========================================================

    payload: Dict[
        str,
        Any
    ] = {

        "version": (
            "UNIVERSE-BUILD-V8"
        ),

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

            "identity_source": (
                "official_twse_isin_"
                "classification"
            ),

            "official_source": (
                OFFICIAL_BASE_URL
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

            "official_identity_required": (
                True
            ),

            "official_identity_source": (
                "twse_isin_classification"
            ),

            "finmind_metadata_only": (
                True
            ),

            "finmind_can_create_symbol": (
                False
            ),

            "finmind_can_create_stock": (
                False
            ),

            "finmind_can_create_etf": (
                False
            ),

            "finmind_can_change_type": (
                False
            ),

            "finmind_can_change_market": (
                False
            ),

            "etf_symbol_length_unrestricted": (
                True
            ),

            "etf_alpha_suffix_supported": (
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

            "price_used": (
                False
            ),

            "volume_used": (
                False
            ),

            "yahoo_used": (
                False
            ),

            "cmoney_used": (
                False
            ),

            "fixed_symbol_rules": (
                False
            ),

            "fixed_universe_count": (
                False
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

    if stock_count <= 0:

        raise RuntimeError(
            "Universe 沒有 STOCK"
        )

    if etf_count <= 0:

        raise RuntimeError(
            "Universe 沒有 ETF"
        )

    # --------------------------------------------------------
    # 防止異常地把整個 official master 原封不動寫入。
    # --------------------------------------------------------

    if (
        len(stocks)
        >= len(official)
    ):

        raise RuntimeError(
            "Universe suspiciously equals "
            "entire official identity master"
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

    log(
        f"✓ atomic write："
        f"{UNIVERSE_FILE}"
    )

    # ========================================================
    # STEP 7 — POST-WRITE READBACK
    # ========================================================

    section(
        "STEP 7 — POST-WRITE READBACK"
    )

    written = validate_written_file(
        official
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

        log(
            "✓ 官方身份建立完成"
        )

        log(
            "✓ Universe validation 完成"
        )

        log(
            "✓ identity cross-check 完成"
        )

        log(
            "✓ atomic write 完成"
        )

        log(
            "✓ post-write readback 完成"
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
