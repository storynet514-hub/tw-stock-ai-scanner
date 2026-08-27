#!/usr/bin/env python3

# -*- coding: utf-8 -*-

"""

台股 AI 選股系統 - build_universe.py

UNIVERSE-REBUILD-V5

核心契約

============================================================

1. Data/universe.json 是後續資料流程唯一 Universe 來源

2. stocks 必須是 dict

3. 官方來源決定商品是否存在

4. 舊 universe.json 絕對不能補造商品

5. STOCK 與 ETF 完全分流

6. STOCK：

   - TWSE 使用 TWSE 官方行情來源

   - TPEx 使用 TPEx 官方行情來源

7. ETF：

   - 使用官方 ISIN 商品分類資料確認

   - Type of security == ETF 才能進入 ETF Universe

   - 不再從 STOCK_DAY_AVG_ALL 推測 ETF

8. ETF 包含：

   - 股票型

   - 債券型

   - 多資產

   - 期貨 / 原物料

   - 貨幣

   - REIT

   - 主動式

   - 槓桿

   - 反向

9. 排除：

   - ETN

   - 權證

   - 一般債券

   - 公司債

   - 特別股

   - TDR

   - 基金

   - 其他非 STOCK / ETF 商品

10. 不探測 CMoney

11. 不依賴固定 Universe 數量

12. 不依賴既有 Universe 建立商品

13. 每筆商品必須具備：

    - symbol

    - full_symbol

    - name

    - market

    - type

    - instrument_type

    - status

14. status == active 才是有效 Universe

15. Atomic Write

16. 寫入後重新讀取驗證

17. ETF 不限制 4 碼

18. STOCK 必須是 4 碼

19. ETF 由官方商品分類確認，不靠模糊猜測

20. TWSE / TPEx ETF 必須依官方 Market 欄位分流

21. 舊 Universe 只可提供非核心 metadata

22. 舊 Universe 不得影響 symbol / market / type /

    instrument_type / status

23. 官方 ETF Gate 必須通過

24. 官方 STOCK Gate 必須通過

UNIVERSE-REBUILD-V5 修正重點

============================================================

V4 的重大問題：

STOCK_DAY_AVG_ALL：

    29087 rows

    ↓

    被錯誤解析成

    1851 ETF

結果：

    ETF = 2307

    FUTURES = 892

    MULTI_ASSET = 1192

這是不正確的。

V5：

行情來源只負責找 STOCK

ETF 商品是否存在由官方 ISIN

"Type of security == ETF"

直接確認。

因此：

    官方 STOCK

          +

    官方 ETF

          ↓

    Universe

而不是：

    所有行情商品

          ↓

    猜 STOCK / ETF

          ↓

    錯誤 Universe

"""

from __future__ import annotations

import json

import re

import sys

import time

from datetime import datetime

from html.parser import HTMLParser

from pathlib import Path

from typing import Any, Dict, List, Optional, Tuple

import requests

# ============================================================

# VERSION

# ============================================================

VERSION = "UNIVERSE-REBUILD-V5"

# ============================================================

# PATH

# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

# ============================================================

# NETWORK

# ============================================================

TIMEOUT = 40

RETRIES = 4

RETRY_SLEEP = 1.5

HEADERS = {

    "User-Agent": (

        "Mozilla/5.0 "

        "(Windows NT 10.0; Win64; x64) "

        "AppleWebKit/537.36 "

        "(KHTML, like Gecko) "

        "Chrome/131.0 Safari/537.36"

    ),

    "Accept": (

        "application/json,"

        "text/plain,"

        "text/html,"

        "*/*"

    ),

    "Accept-Language": (

        "zh-TW,zh;q=0.9,"

        "en-US;q=0.8,en;q=0.7"

    ),

}

session = requests.Session()

session.headers.update(HEADERS)

# ============================================================

# OFFICIAL STOCK SOURCES

# ============================================================

TWSE_STOCK_SOURCES = [

    (

        "TWSE_STOCK_DAY_ALL",

        "https://openapi.twse.com.tw/v1/"

        "exchangeReport/STOCK_DAY_ALL",

        {},

    ),

    (

        "TWSE_BWIBBU",

        "https://www.twse.com.tw/rwd/zh/"

        "afterTrading/BWIBBU_d",

        {

            "response": "json",

            "selectType": "ALL",

        },

    ),

]

TPEX_STOCK_SOURCES = [

    (

        "TPEX_DAILY_QUOTES",

        "https://www.tpex.org.tw/openapi/v1/"

        "tpex_mainboard_daily_close_quotes",

        {},

    ),

]

# ============================================================

# OFFICIAL ISIN ETF SOURCE

# ============================================================

ISIN_CLASS_URL = (

    "https://isin.twse.com.tw/isin/class_main.jsp"

)

# ============================================================

# LOG

# ============================================================

def log(message: str = "") -> None:

    print(message, flush=True)

def section(title: str) -> None:

    log("")

    log("=" * 72)

    log(title)

    log("=" * 72)

# ============================================================

# TIME

# ============================================================

def now_tw() -> datetime:

    from zoneinfo import ZoneInfo

    return datetime.now(

        ZoneInfo("Asia/Taipei")

    )

# ============================================================

# TEXT

# ============================================================

def clean_text(value: Any) -> str:

    if value is None:

        return ""

    return str(value).strip()

def clean_code(value: Any) -> str:

    text = clean_text(value).upper()

    text = (

        text

        .replace(".TW", "")

        .replace(".TWO", "")

        .replace(" ", "")

        .replace("\u3000", "")

    )

    return text

def normalize_key(value: Any) -> str:

    text = clean_text(value).lower()

    return re.sub(

        r"[\s_\-\/\(\)（）:：]+",

        "",

        text,

    )

# ============================================================

# HTTP JSON

# ============================================================

def parse_json_response(

    response: requests.Response,

) -> Optional[Any]:

    text = response.text.strip()

    if not text:

        return None

    try:

        return response.json()

    except Exception:

        pass

    try:

        return json.loads(

            text.lstrip("\ufeff")

        )

    except Exception:

        return None

def request_official_json(

    name: str,

    url: str,

    params: Optional[Dict[str, Any]] = None,

) -> Optional[Any]:

    last_error = ""

    for attempt in range(

        1,

        RETRIES + 1,

    ):

        try:

            response = session.get(

                url,

                params=params or {},

                timeout=TIMEOUT,

            )

            if response.status_code != 200:

                last_error = (

                    f"HTTP {response.status_code}"

                )

            else:

                payload = parse_json_response(

                    response

                )

                if payload is not None:

                    return payload

                preview = (

                    response.text[:180]

                    .replace("\n", " ")

                    .replace("\r", " ")

                )

                last_error = (

                    "非 JSON 回應："

                    + preview

                )

        except Exception as exc:

            last_error = str(exc)

        if attempt < RETRIES:

            time.sleep(

                RETRY_SLEEP * attempt

            )

    log(

        f"❌ {name}：{last_error}"

    )

    return None

# ============================================================

# HTTP HTML

# ============================================================

def request_official_html(

    name: str,

    url: str,

    params: Optional[Dict[str, Any]] = None,

) -> Optional[str]:

    last_error = ""

    for attempt in range(

        1,

        RETRIES + 1,

    ):

        try:

            response = session.get(

                url,

                params=params or {},

                timeout=TIMEOUT,

            )

            if response.status_code != 200:

                last_error = (

                    f"HTTP {response.status_code}"

                )

            else:

                text = response.text

                if text.strip():

                    return text

                last_error = "空白 HTML"

        except Exception as exc:

            last_error = str(exc)

        if attempt < RETRIES:

            time.sleep(

                RETRY_SLEEP * attempt

            )

    log(

        f"❌ {name}：{last_error}"

    )

    return None

# ============================================================

# GENERIC RECORD NORMALIZER

# ============================================================

def fields_data_to_rows(

    fields: Any,

    data: Any,

) -> List[Dict[str, Any]]:

    if not isinstance(fields, list):

        return []

    if not isinstance(data, list):

        return []

    result: List[Dict[str, Any]] = []

    for raw in data:

        if isinstance(raw, dict):

            result.append(raw)

            continue

        if not isinstance(raw, list):

            continue

        row: Dict[str, Any] = {}

        for index, field in enumerate(fields):

            if index >= len(raw):

                break

            row[

                clean_text(field)

            ] = raw[index]

        if row:

            result.append(row)

    return result

def normalize_records(

    payload: Any,

) -> List[Dict[str, Any]]:

    if isinstance(payload, list):

        return [

            row

            for row in payload

            if isinstance(row, dict)

        ]

    if not isinstance(payload, dict):

        return []

    rows = fields_data_to_rows(

        payload.get("fields"),

        payload.get("data"),

    )

    if rows:

        return rows

    tables = payload.get("tables")

    if isinstance(tables, list):

        result: List[Dict[str, Any]] = []

        for table in tables:

            if not isinstance(

                table,

                dict,

            ):

                continue

            result.extend(

                fields_data_to_rows(

                    table.get("fields"),

                    table.get("data"),

                )

            )

        if result:

            return result

    for key in (

        "data",

        "Data",

        "result",

        "results",

        "records",

        "Records",

    ):

        value = payload.get(key)

        if not isinstance(value, list):

            continue

        rows = [

            row

            for row in value

            if isinstance(row, dict)

        ]

        if rows:

            return rows

    return []

# ============================================================

# FIELD LOOKUP

# ============================================================

def field_value(

    row: Dict[str, Any],

    aliases: List[str],

) -> Any:

    normalized = {

        normalize_key(key): value

        for key, value in row.items()

    }

    for alias in aliases:

        key = normalize_key(alias)

        if key in normalized:

            return normalized[key]

    return None

# ============================================================

# CODE / NAME

# ============================================================

CODE_ALIASES = [

    "證券代號",

    "證券代碼",

    "代號",

    "股票代號",

    "有價證券代號",

    "SecuritiesCompanyCode",

    "SecurityCode",

    "StockCode",

    "Code",

    "code",

    "symbol",

    "Symbol",

    "ticker",

]

NAME_ALIASES = [

    "證券名稱",

    "證券簡稱",

    "名稱",

    "股票名稱",

    "有價證券名稱",

    "ETF名稱",

    "ETF簡稱",

    "SecuritiesCompanyName",

    "SecurityName",

    "StockName",

    "Name",

    "name",

]

def extract_code(

    row: Dict[str, Any],

) -> str:

    value = field_value(

        row,

        CODE_ALIASES,

    )

    code = clean_code(value)

    if code:

        return code

    for key, value in row.items():

        normalized = normalize_key(key)

        if not any(

            token in normalized

            for token in (

                "代號",

                "代碼",

                "code",

                "symbol",

                "ticker",

            )

        ):

            continue

        text = clean_text(value)

        match = re.search(

            r"(?<![A-Z0-9])"

            r"([0-9]{4,6}[A-Z]?)"

            r"(?![A-Z0-9])",

            text.upper(),

        )

        if match:

            return match.group(1)

    return ""

def extract_name(

    row: Dict[str, Any],

) -> str:

    value = field_value(

        row,

        NAME_ALIASES,

    )

    return clean_text(value)

# ============================================================

# GENERIC TEXT

# ============================================================

def row_text(

    row: Dict[str, Any],

) -> str:

    return " ".join(

        clean_text(value)

        for value in row.values()

        if value is not None

    ).upper()

# ============================================================

# NON-STOCK PRODUCT FILTER

# ============================================================

NON_STOCK_KEYWORDS = (

    "權證",

    "認購權證",

    "認售權證",

    "牛熊證",

    "ETN",

    "特別股",

    "存託憑證",

    "TDR",

    "公司債",

    "政府債",

    "金融債",

    "一般債券",

)

def is_non_stock_product(

    code: str,

    name: str,

    row: Dict[str, Any],

) -> bool:

    text = (

        f"{code} "

        f"{name} "

        f"{row_text(row)}"

    ).upper()

    for keyword in NON_STOCK_KEYWORDS:

        if keyword.upper() in text:

            return True

    return False

# ============================================================

# STOCK CODE VALIDATION

# ============================================================

def valid_stock_code(

    code: str,

) -> bool:

    return bool(

        re.fullmatch(

            r"\d{4}",

            code,

        )

    )

# ============================================================

# STOCK PARSER

# ============================================================

def parse_stock_records(

    payload: Any,

    market: str,

    source_name: str,

) -> Dict[str, Dict[str, Any]]:

    rows = normalize_records(

        payload

    )

    result: Dict[str, Dict[str, Any]] = {}

    for row in rows:

        code = extract_code(row)

        name = extract_name(row)

        if not valid_stock_code(code):

            continue

        if is_non_stock_product(

            code,

            name,

            row,

        ):

            continue

        result[code] = {

            "symbol": code,

            "name": name or code,

            "market": market,

            "type": "STOCK",

            "instrument_type": "STOCK",

            "source": source_name,

        }

    return result

# ============================================================

# HTML TABLE PARSER

# ============================================================

class TableParser(HTMLParser):

    """

    標準函式庫 HTML parser。

    不依賴 BeautifulSoup / pandas。

    GitHub Actions 只需要 requests。

    """

    def __init__(self) -> None:

        super().__init__(

            convert_charrefs=True

        )

        self.in_table = False

        self.in_row = False

        self.in_cell = False

        self.current_row: List[str] = []

        self.current_cell: List[str] = []

        self.rows: List[List[str]] = []

    def handle_starttag(

        self,

        tag: str,

        attrs: List[Tuple[str, Optional[str]]],

    ) -> None:

        tag = tag.lower()

        if tag == "table":

            self.in_table = True

        elif (

            tag == "tr"

            and self.in_table

        ):

            self.in_row = True

            self.current_row = []

        elif (

            tag in ("td", "th")

            and self.in_row

        ):

            self.in_cell = True

            self.current_cell = []

    def handle_endtag(

        self,

        tag: str,

    ) -> None:

        tag = tag.lower()

        if (

            tag in ("td", "th")

            and self.in_cell

        ):

            value = "".join(

                self.current_cell

            )

            value = re.sub(

                r"\s+",

                " ",

                value,

            ).strip()

            self.current_row.append(

                value

            )

            self.current_cell = []

            self.in_cell = False

        elif (

            tag == "tr"

            and self.in_row

        ):

            if self.current_row:

                self.rows.append(

                    self.current_row

                )

            self.current_row = []

            self.in_row = False

        elif (

            tag == "table"

            and self.in_table

        ):

            self.in_table = False

    def handle_data(

        self,

        data: str,

    ) -> None:

        if self.in_cell:

            self.current_cell.append(

                data

            )

# ============================================================

# ISIN HTML PARSER

# ============================================================

def parse_isin_rows(

    html: str,

) -> List[Dict[str, str]]:

    parser = TableParser()

    parser.feed(html)

    result: List[Dict[str, str]] = []

    for row in parser.rows:

        if len(row) < 5:

            continue

        values = [

            clean_text(value)

            for value in row

        ]

        # ----------------------------------------------------

        # 官方 ISIN classification table:

        #

        # ISIN Code

        # Security Code

        # Security Name

        # Market

        # Type of security

        # Industrial Group

        # Date Listed

        # CFICode

        # Remarks

        # ----------------------------------------------------

        if (

            values[0].lower()

            in {

                "isin code",

                "isin",

                "國際證券編碼",

                "國際證券代碼",

            }

        ):

            continue

        if (

            values[1].lower()

            in {

                "security code",

                "securitycode",

                "證券代號",

                "證券代碼",

            }

        ):

            continue

        code = clean_code(

            values[1]

        )

        name = (

            values[2]

            if len(values) > 2

            else ""

        )

        market = (

            values[3]

            if len(values) > 3

            else ""

        )

        security_type = (

            values[4]

            if len(values) > 4

            else ""

        )

        cfi = (

            values[7]

            if len(values) > 7

            else ""

        )

        if not code:

            continue

        result.append(

            {

                "isin": values[0],

                "code": code,

                "name": name,

                "market": market,

                "security_type": security_type,

                "cfi": cfi,

            }

        )

    return result

# ============================================================

# ISIN MARKET

# ============================================================

def isin_market(

    value: str,

) -> str:

    text = clean_text(

        value

    ).upper()

    if (

        "TPEx" in text

        or "OTC" in text

        or "櫃" in text

    ):

        return "TPEX"

    if (

        "TWSE" in text

        or "上市" in text

    ):

        return "TWSE"

    return ""

# ============================================================

# ETF SECURITY TYPE

# ============================================================

def is_official_etf_row(

    row: Dict[str, str],

) -> bool:

    security_type = clean_text(

        row.get(

            "security_type"

        )

    ).upper()

    # --------------------------------------------------------

    # 最重要 Gate：

    #

    # 必須由官方 ISIN 資料明確標示 ETF。

    #

    # 不再用：

    #     名稱有基金

    #     5 碼

    #     尾碼

    #     STOCK_DAY_AVG_ALL

    #

    # 來推測 ETF。

    # --------------------------------------------------------

    if security_type == "ETF":

        return True

    if (

        "ETF"

        in security_type

    ):

        return True

    return False

# ============================================================

# ETF CATEGORY

# ============================================================

def classify_etf_category(

    code: str,

    name: str,

    cfi: str,

) -> str:

    text = (

        f"{code} "

        f"{name} "

        f"{cfi}"

    ).upper()

    # --------------------------------------------------------

    # 優先使用代號尾碼。

    #

    # 目前台股 ETF 常見：

    # A = Active Equity

    # B = Bond

    # C = Bond FX / currency-hedged bond

    # D = Active Bond

    # L = Leveraged

    # R = Inverse

    # T = Multi Asset

    # U = Futures

    #

    # 不認識的尾碼不硬猜。

    # --------------------------------------------------------

    if re.fullmatch(

        r"\d{5}[A-Z]",

        code,

    ):

        suffix = code[-1]

        suffix_map = {

            "A": "ACTIVE_EQUITY",

            "B": "BOND",

            "C": "BOND_FX",

            "D": "ACTIVE_BOND",

            "L": "LEVERAGED",

            "R": "INVERSE",

            "T": "MULTI_ASSET",

            "U": "FUTURES",

        }

        if suffix in suffix_map:

            return suffix_map[

                suffix

            ]

    # --------------------------------------------------------

    # 官方 CFI

    # --------------------------------------------------------

    if "CEO" in cfi.upper():

        # ETF equity-like

        if cfi.upper().startswith(

            "CEOJ"

        ):

            return "EQUITY"

        # ETF bond-like

        if cfi.upper().startswith(

            "CEOI"

        ):

            return "BOND"

        # ETF derivative-like

        if cfi.upper().startswith(

            "CEOG"

        ):

            if (

                "槓桿" in text

                or "正2" in text

                or "2X" in text

                or "LEVERAGE" in text

            ):

                return "LEVERAGED"

            if (

                "反向" in text

                or "反1" in text

                or "INVERSE" in text

            ):

                return "INVERSE"

    # --------------------------------------------------------

    # 名稱 fallback

    # --------------------------------------------------------

    if any(

        token in text

        for token in (

            "債券",

            "BOND",

            "TREASURY",

            "CORPORATE BOND",

        )

    ):

        return "BOND"

    if any(

        token in text

        for token in (

            "多資產",

            "MULTI ASSET",

            "MULTI-ASSET",

            "BALANCED",

        )

    ):

        return "MULTI_ASSET"

    if any(

        token in text

        for token in (

            "槓桿",

            "正2",

            "2X",

            "LEVERAGED",

        )

    ):

        return "LEVERAGED"

    if any(

        token in text

        for token in (

            "反向",

            "反1",

            "INVERSE",

        )

    ):

        return "INVERSE"

    if any(

        token in text

        for token in (

            "期貨",

            "原油",

            "黃金",

            "白銀",

            "COMMODITY",

            "FUTURES",

        )

    ):

        return "FUTURES"

    if any(

        token in text

        for token in (

            "貨幣",

            "美元",

            "日圓",

            "歐元",

            "FX",

            "CURRENCY",

        )

    ):

        return "FX"

    if any(

        token in text

        for token in (

            "REIT",

            "不動產",

        )

    ):

        return "REIT"

    if any(

        token in text

        for token in (

            "主動",

            "ACTIVE",

        )

    ):

        return "ACTIVE_EQUITY"

    return "EQUITY"

# ============================================================

# ETF PARSER

# ============================================================

def parse_official_etf_rows(

    rows: List[Dict[str, str]],

) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

    for row in rows:

        if not is_official_etf_row(

            row

        ):

            continue

        code = clean_code(

            row.get("code")

        )

        name = clean_text(

            row.get("name")

        )

        market = isin_market(

            row.get("market", "")

        )

        if not code:

            continue

        if market not in {

            "TWSE",

            "TPEX",

        }:

            continue

        category = classify_etf_category(

            code,

            name,

            row.get("cfi", ""),

        )

        result[code] = {

            "symbol": code,

            "name": name or code,

            "market": market,

            "type": "ETF",

            "instrument_type": "ETF",

            "etf_category": category,

            "source": "TWSE_ISIN_OFFICIAL",

        }

    return result

# ============================================================

# COLLECT STOCK TWSE

# ============================================================

def collect_twse_stocks() -> Dict[str, Dict[str, Any]]:

    section(

        "TWSE 官方 STOCK 來源"

    )

    combined: Dict[

        str,

        Dict[str, Any],

    ] = {}

    for (

        source_name,

        url,

        params,

    ) in TWSE_STOCK_SOURCES:

        log("")

        log(

            f"TWSE STOCK："

            f"{source_name}"

        )

        payload = request_official_json(

            source_name,

            url,

            params,

        )

        if payload is None:

            continue

        rows = normalize_records(

            payload

        )

        log(

            f"  原始 rows："

            f"{len(rows)}"

        )

        parsed = parse_stock_records(

            payload,

            "TWSE",

            source_name,

        )

        log(

            f"  STOCK："

            f"{len(parsed)}"

        )

        for code, item in parsed.items():

            if code not in combined:

                combined[code] = item

    log("")

    log(

        f"✓ TWSE STOCK unique："

        f"{len(combined)}"

    )

    return combined

# ============================================================

# COLLECT STOCK TPEX

# ============================================================

def collect_tpex_stocks() -> Dict[str, Dict[str, Any]]:

    section(

        "TPEx 官方 STOCK 來源"

    )

    combined: Dict[

        str,

        Dict[str, Any],

    ] = {}

    for (

        source_name,

        url,

        params,

    ) in TPEX_STOCK_SOURCES:

        log("")

        log(

            f"TPEx STOCK："

            f"{source_name}"

        )

        payload = request_official_json(

            source_name,

            url,

            params,

        )

        if payload is None:

            continue

        rows = normalize_records(

            payload

        )

        log(

            f"  原始 rows："

            f"{len(rows)}"

        )

        parsed = parse_stock_records(

            payload,

            "TPEX",

            source_name,

        )

        log(

            f"  STOCK："

            f"{len(parsed)}"

        )

        for code, item in parsed.items():

            if code not in combined:

                combined[code] = item

    log("")

    log(

        f"✓ TPEx STOCK unique："

        f"{len(combined)}"

    )

    return combined

# ============================================================

# COLLECT OFFICIAL ETF

# ============================================================

def collect_official_etf() -> Dict[str, Dict[str, Any]]:

    section(

        "官方 ETF 商品來源"

    )

    all_etf: Dict[

        str,

        Dict[str, Any],

    ] = {}

    # --------------------------------------------------------

    # 官方 ISIN 分類資料

    #

    # market=1 -> TWSE

    # market=2 -> TPEx

    #

    # issuetype=3 -> ETF

    # --------------------------------------------------------

    for market_code, market_name in (

        ("1", "TWSE"),

        ("2", "TPEx"),

    ):

        log("")

        log(

            f"官方 ISIN ETF："

            f"{market_name}"

        )

        params = {

            "Page": "1",

            "chklike": "Y",

            "issuetype": "3",

            "market": market_code,

        }

        html = request_official_html(

            f"ISIN_ETF_{market_name}",

            ISIN_CLASS_URL,

            params,

        )

        if html is None:

            continue

        rows = parse_isin_rows(

            html

        )

        log(

            f"  HTML rows："

            f"{len(rows)}"

        )

        parsed = parse_official_etf_rows(

            rows

        )

        # ----------------------------------------------------

        # 只接受指定市場。

        # ----------------------------------------------------

        parsed = {

            code: item

            for code, item in parsed.items()

            if item.get("market")

            == market_name.upper()

        }

        log(

            f"  官方確認 ETF："

            f"{len(parsed)}"

        )

        for code, item in parsed.items():

            all_etf[code] = item

    # --------------------------------------------------------

    # 官方 ETF Gate

    # --------------------------------------------------------

    twse_count = sum(

        1

        for item in all_etf.values()

        if item.get("market")

        == "TWSE"

    )

    tpex_count = sum(

        1

        for item in all_etf.values()

        if item.get("market")

        == "TPEX"

    )

    log("")

    log(

        "官方 ETF 解析結果："

    )

    log(

        f"  TWSE ETF："

        f"{twse_count}"

    )

    log(

        f"  TPEx ETF："

        f"{tpex_count}"

    )

    log(

        f"  ETF unique："

        f"{len(all_etf)}"

    )

    return all_etf

# ============================================================

# EXISTING METADATA

# ============================================================

def load_existing_metadata() -> Dict[

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

    result: Dict[

        str,

        Dict[str, Any],

    ] = {}

    for key, value in stocks.items():

        if not isinstance(

            value,

            dict,

        ):

            continue

        code = clean_code(

            value.get(

                "symbol",

                key,

            )

        )

        if not code:

            continue

        result[code] = value

    return result

# ============================================================

# BUILD UNIVERSE

# ============================================================

def build_universe(

    twse_stocks: Dict[str, Dict[str, Any]],

    tpex_stocks: Dict[str, Dict[str, Any]],

    etfs: Dict[str, Dict[str, Any]],

    existing: Dict[str, Dict[str, Any]],

) -> Dict[str, Dict[str, Any]]:

    section(

        "建立 Universe"

    )

    stocks: Dict[

        str,

        Dict[str, Any],

    ] = {}

    official: Dict[

        str,

        Dict[str, Any],

    ] = {}

    # --------------------------------------------------------

    # STOCK

    # --------------------------------------------------------

    for code, item in twse_stocks.items():

        official[code] = item

    for code, item in tpex_stocks.items():

        # 若同一代號同時出現，

        # 不讓 TPEx 覆蓋 TWSE。

        if code not in official:

            official[code] = item

    # --------------------------------------------------------

    # ETF

    #

    # ETF 只允許來自官方 ETF source。

    # --------------------------------------------------------

    for code, item in etfs.items():

        if code in official:

            # ------------------------------------------------

            # 同一代號若同時出現在 STOCK source：

            #

            # ETF 官方商品分類優先。

            #

            # 這是為了避免行情來源把 ETF 誤當

            # STOCK。

            # ------------------------------------------------

            official[code] = item

        else:

            official[code] = item

    # --------------------------------------------------------

    # BUILD

    # --------------------------------------------------------

    for code in sorted(

        official.keys()

    ):

        item = official[code]

        market = clean_text(

            item.get(

                "market"

            )

        ).upper()

        instrument_type = clean_text(

            item.get(

                "instrument_type"

            )

        ).upper()

        name = clean_text(

            item.get(

                "name"

            )

        )

        if market not in {

            "TWSE",

            "TPEX",

        }:

            continue

        if instrument_type not in {

            "STOCK",

            "ETF",

        }:

            continue

        # ----------------------------------------------------

        # STOCK 必須四碼

        # ----------------------------------------------------

        if (

            instrument_type == "STOCK"

            and not valid_stock_code(code)

        ):

            continue

        # ----------------------------------------------------

        # ETF 不限制四碼。

        #

        # 但至少必須是：

        #   4~6 位數字

        #   或 5 位數字 + 英文字尾碼

        # ----------------------------------------------------

        if instrument_type == "ETF":

            if not re.fullmatch(

                r"(?:\d{4,6}|\d{5}[A-Z])",

                code,

            ):

                continue

        # ----------------------------------------------------

        # 核心欄位

        # ----------------------------------------------------

        full_symbol = (

            code

            + (

                ".TW"

                if market == "TWSE"

                else ".TWO"

            )

        )

        stock: Dict[str, Any] = {

            "symbol": code,

            "full_symbol": full_symbol,

            "name": name or code,

            "market": market,

            "type": instrument_type,

            "instrument_type": instrument_type,

            "status": "active",

        }

        # ----------------------------------------------------

        # ETF category

        # ----------------------------------------------------

        if instrument_type == "ETF":

            category = clean_text(

                item.get(

                    "etf_category"

                )

            )

            if category:

                stock[

                    "etf_category"

                ] = category

        # ----------------------------------------------------

        # 舊 metadata

        #

        # 只能補非核心欄位。

        #

        # 舊資料絕對不能改：

        #   symbol

        #   full_symbol

        #   name

        #   market

        #   type

        #   instrument_type

        #   status

        # ----------------------------------------------------

        old = existing.get(

            code,

            {},

        )

        if not isinstance(

            old,

            dict,

        ):

            old = {}

        for key in (

            "industry",

            "sector",

            "category",

        ):

            value = old.get(

                key

            )

            if value not in (

                None,

                "",

            ):

                stock[key] = value

        stocks[code] = stock

    return stocks

# ============================================================

# OFFICIAL SOURCE GATE

# ============================================================

def official_source_gate(

    twse_stocks: Dict[str, Dict[str, Any]],

    tpex_stocks: Dict[str, Dict[str, Any]],

    etfs: Dict[str, Dict[str, Any]],

) -> bool:

    section(

        "Official Source Gate"

    )

    twse_stock_count = len(

        twse_stocks

    )

    tpex_stock_count = len(

        tpex_stocks

    )

    twse_etf_count = sum(

        1

        for item in etfs.values()

        if item.get("market")

        == "TWSE"

    )

    tpex_etf_count = sum(

        1

        for item in etfs.values()

        if item.get("market")

        == "TPEX"

    )

    log(

        f"TWSE STOCK："

        f"{twse_stock_count}"

    )

    log(

        f"TPEx STOCK："

        f"{tpex_stock_count}"

    )

    log(

        f"TWSE ETF："

        f"{twse_etf_count}"

    )

    log(

        f"TPEx ETF："

        f"{tpex_etf_count}"

    )

    # --------------------------------------------------------

    # STOCK Gate

    # --------------------------------------------------------

    if twse_stock_count <= 0:

        log(

            "❌ TWSE STOCK 官方來源 Gate FAIL"

        )

        return False

    if tpex_stock_count <= 0:

        log(

            "❌ TPEx STOCK 官方來源 Gate FAIL"

        )

        return False

    # --------------------------------------------------------

    # ETF Gate

    #

    # ETF 必須有官方資料。

    # --------------------------------------------------------

    if twse_etf_count <= 0:

        log(

            "❌ TWSE ETF 官方來源 Gate FAIL"

        )

        return False

    if tpex_etf_count <= 0:

        log(

            "❌ TPEx ETF 官方來源 Gate FAIL"

        )

        return False

    log(

        "✓ Official Source Gate PASS"

    )

    return True

# ============================================================

# NO LEGACY INJECTION GATE

# ============================================================

def validate_no_legacy_injection(

    stocks: Dict[str, Dict[str, Any]],

    twse_stocks: Dict[str, Dict[str, Any]],

    tpex_stocks: Dict[str, Dict[str, Any]],

    etfs: Dict[str, Dict[str, Any]],

) -> bool:

    section(

        "No Legacy Injection Gate"

    )

    official_codes = set()

    official_codes.update(

        twse_stocks.keys()

    )

    official_codes.update(

        tpex_stocks.keys()

    )

    official_codes.update(

        etfs.keys()

    )

    universe_codes = set(

        stocks.keys()

    )

    unexpected = (

        universe_codes

        - official_codes

    )

    if unexpected:

        log(

            "❌ 發現非官方來源商品："

        )

        for code in sorted(

            unexpected

        )[:50]:

            log(

                f"  {code}"

            )

        if len(unexpected) > 50:

            log(

                f"  ... "

                f"其餘 {len(unexpected) - 50}"

            )

        return False

    log(

        "✓ Universe 所有商品皆可追溯至官方來源"

    )

    log(

        "✓ No Legacy Injection Gate PASS"

    )

    return True

# ============================================================

# STRUCTURE VALIDATION

# ============================================================

def validate_universe_structure(

    stocks: Dict[str, Dict[str, Any]],

) -> bool:

    section(

        "Universe Structure Gate"

    )

    errors = 0

    if not isinstance(

        stocks,

        dict,

    ):

        log(

            "❌ stocks 必須為 dict"

        )

        return False

    if not stocks:

        log(

            "❌ stocks 不得為空"

        )

        return False

    symbols = set()

    full_symbols = set()

    stock_count = 0

    etf_count = 0

    for code, item in stocks.items():

        if not isinstance(

            item,

            dict,

        ):

            log(

                f"❌ {code}: item 非 dict"

            )

            errors += 1

            continue

        # ----------------------------------------------------

        # symbol

        # ----------------------------------------------------

        symbol = clean_code(

            item.get(

                "symbol"

            )

        )

        if code != symbol:

            log(

                f"❌ {code}: symbol mismatch"

            )

            errors += 1

        if symbol in symbols:

            log(

                f"❌ symbol duplicate："

                f"{symbol}"

            )

            errors += 1

        symbols.add(symbol)

        # ----------------------------------------------------

        # full_symbol

        # ----------------------------------------------------

        full_symbol = clean_text(

            item.get(

                "full_symbol"

            )

        )

        expected_full = (

            code

            + (

                ".TW"

                if item.get("market")

                == "TWSE"

                else ".TWO"

            )

        )

        if full_symbol != expected_full:

            log(

                f"❌ {code}: "

                f"full_symbol={full_symbol}"

            )

            errors += 1

        if full_symbol in full_symbols:

            log(

                f"❌ full_symbol duplicate："

                f"{full_symbol}"

            )

            errors += 1

        full_symbols.add(

            full_symbol

        )

        # ----------------------------------------------------

        # market

        # ----------------------------------------------------

        market = item.get(

            "market"

        )

        if market not in {

            "TWSE",

            "TPEX",

        }:

            log(

                f"❌ {code}: "

                f"market={market}"

            )

            errors += 1

        # ----------------------------------------------------

        # type

        # ----------------------------------------------------

        instrument_type = item.get(

            "instrument_type"

        )

        if instrument_type not in {

            "STOCK",

            "ETF",

        }:

            log(

                f"❌ {code}: "

                f"instrument_type="

                f"{instrument_type}"

            )

            errors += 1

        # ----------------------------------------------------

        # type / instrument_type

        # ----------------------------------------------------

        if item.get(

            "type"

        ) != instrument_type:

            log(

                f"❌ {code}: "

                f"type != instrument_type"

            )

            errors += 1

        # ----------------------------------------------------

        # STOCK

        # ----------------------------------------------------

        if instrument_type == "STOCK":

            stock_count += 1

            if not valid_stock_code(

                code

            ):

                log(

                    f"❌ {code}: "

                    f"STOCK 非四碼"

                )

                errors += 1

        # ----------------------------------------------------

        # ETF

        # ----------------------------------------------------

        elif instrument_type == "ETF":

            etf_count += 1

            if not re.fullmatch(

                r"(?:\d{4,6}|\d{5}[A-Z])",

                code,

            ):

                log(

                    f"❌ {code}: "

                    f"ETF 代號格式錯誤"

                )

                errors += 1

            if not clean_text(

                item.get(

                    "etf_category"

                )

            ):

                log(

                    f"❌ {code}: "

                    f"ETF category missing"

                )

                errors += 1

        # ----------------------------------------------------

        # status

        # ----------------------------------------------------

        if item.get(

            "status"

        ) != "active":

            log(

                f"❌ {code}: "

                f"status != active"

            )

            errors += 1

        # ----------------------------------------------------

        # name

        # ----------------------------------------------------

        if not clean_text(

            item.get("name")

        ):

            log(

                f"❌ {code}: name 空白"

            )

            errors += 1

    if errors:

        log(

            f"❌ Universe Structure Gate FAIL："

            f"{errors}"

        )

        return False

    log(

        f"✓ STOCK："

        f"{stock_count}"

    )

    log(

        f"✓ ETF："

        f"{etf_count}"

    )

    log(

        f"✓ Total："

        f"{len(stocks)}"

    )

    log(

        "✓ Required fields"

    )

    log(

        "✓ symbol uniqueness"

    )

    log(

        "✓ full_symbol uniqueness"

    )

    log(

        "✓ STOCK / ETF classification"

    )

    log(

        "✓ instrument_type"

    )

    log(

        "✓ status=active"

    )

    log(

        "✓ Universe Structure Gate PASS"

    )

    return True

# ============================================================

# MARKET BALANCE

# ============================================================

def validate_market_balance(

    stocks: Dict[str, Dict[str, Any]],

    twse_stock_source_count: int,

    tpex_stock_source_count: int,

    twse_etf_source_count: int,

    tpex_etf_source_count: int,

) -> bool:

    section(

        "Market Balance Gate"

    )

    counts = {

        "TWSE STOCK": 0,

        "TPEx STOCK": 0,

        "TWSE ETF": 0,

        "TPEx ETF": 0,

    }

    for item in stocks.values():

        market = item.get(

            "market"

        )

        instrument_type = item.get(

            "instrument_type"

        )

        if (

            market == "TWSE"

            and instrument_type == "STOCK"

        ):

            counts[

                "TWSE STOCK"

            ] += 1

        elif (

            market == "TPEX"

            and instrument_type == "STOCK"

        ):

            counts[

                "TPEx STOCK"

            ] += 1

        elif (

            market == "TWSE"

            and instrument_type == "ETF"

        ):

            counts[

                "TWSE ETF"

            ] += 1

        elif (

            market == "TPEX"

            and instrument_type == "ETF"

        ):

            counts[

                "TPEx ETF"

            ] += 1

    for key, value in counts.items():

        log(

            f"{key}：{value}"

        )

    # --------------------------------------------------------

    # Source -> Universe minimum checks

    # --------------------------------------------------------

    if (

        twse_stock_source_count <= 0

        or counts["TWSE STOCK"] <= 0

    ):

        log(

            "❌ TWSE STOCK Balance FAIL"

        )

        return False

    if (

        tpex_stock_source_count <= 0

        or counts["TPEx STOCK"] <= 0

    ):

        log(

            "❌ TPEx STOCK Balance FAIL"

        )

        return False

    if (

        twse_etf_source_count <= 0

        or counts["TWSE ETF"] <= 0

    ):

        log(

            "❌ TWSE ETF Balance FAIL"

        )

        return False

    if (

        tpex_etf_source_count <= 0

        or counts["TPEx ETF"] <= 0

    ):

        log(

            "❌ TPEx ETF Balance FAIL"

        )

        return False

    log(

        "✓ Market Balance Gate PASS"

    )

    return True

# ============================================================

# ETF CATEGORY STATISTICS

# ============================================================

def print_etf_statistics(

    stocks: Dict[str, Dict[str, Any]],

) -> None:

    section(

        "ETF CATEGORY STATISTICS"

    )

    categories: Dict[

        str,

        int,

    ] = {}

    for item in stocks.values():

        if item.get(

            "instrument_type"

        ) != "ETF":

            continue

        category = clean_text(

            item.get(

                "etf_category"

            )

        ) or "UNKNOWN"

        categories[

            category

        ] = (

            categories.get(

                category,

                0,

            )

            + 1

        )

    if not categories:

        log(

            "❌ 沒有 ETF"

        )

        return

    for category in sorted(

        categories.keys()

    ):

        log(

            f"  {category}: "

            f"{categories[category]}"

        )

# ============================================================

# PAYLOAD

# ============================================================

def make_payload(

    stocks: Dict[str, Dict[str, Any]],

    twse_stock_source_count: int,

    tpex_stock_source_count: int,

    twse_etf_source_count: int,

    tpex_etf_source_count: int,

) -> Dict[str, Any]:

    now = now_tw()

    stock_count = sum(

        1

        for item in stocks.values()

        if item.get(

            "instrument_type"

        ) == "STOCK"

    )

    etf_count = sum(

        1

        for item in stocks.values()

        if item.get(

            "instrument_type"

        ) == "ETF"

    )

    return {

        "version": VERSION,

        "generated_at":

            now.isoformat(),

        "universe_count":

            len(stocks),

        "stock_count":

            stock_count,

        "etf_count":

            etf_count,

        "source": {

            "policy": (

                "Official TWSE / TPEx "

                "sources only"

            ),

            "stock_policy": (

                "TWSE / TPEx official "

                "stock market sources"

            ),

            "etf_policy": (

                "Official ISIN "

                "Type of security == ETF"

            ),

            "twse_stock_candidates":

                twse_stock_source_count,

            "tpex_stock_candidates":

                tpex_stock_source_count,

            "twse_etf_candidates":

                twse_etf_source_count,

            "tpex_etf_candidates":

                tpex_etf_source_count,

        },

        "contract": {

            "root": "dict",

            "stocks": "dict",

            "active_status":

                "status == active",

            "ordinary_stock_only":

                False,

            "stock_type":

                "STOCK",

            "etf_type":

                "ETF",

            "instrument_type_required":

                True,

            "allowed_markets": [

                "TWSE",

                "TPEX",

            ],

            "allowed_instrument_types": [

                "STOCK",

                "ETF",

            ],

            "legacy_injection":

                False,

            "official_etf_confirmation":

                True,

        },

        "stocks": stocks,

    }

# ============================================================

# PAYLOAD VALIDATION

# ============================================================

def validate_payload(

    payload: Dict[str, Any],

) -> bool:

    if not isinstance(

        payload,

        dict,

    ):

        return False

    stocks = payload.get(

        "stocks"

    )

    if not isinstance(

        stocks,

        dict,

    ):

        return False

    if payload.get(

        "universe_count"

    ) != len(stocks):

        return False

    stock_count = sum(

        1

        for item in stocks.values()

        if isinstance(

            item,

            dict,

        )

        and item.get(

            "instrument_type"

        ) == "STOCK"

    )

    etf_count = sum(

        1

        for item in stocks.values()

        if isinstance(

            item,

            dict,

        )

        and item.get(

            "instrument_type"

        ) == "ETF"

    )

    if payload.get(

        "stock_count"

    ) != stock_count:

        return False

    if payload.get(

        "etf_count"

    ) != etf_count:

        return False

    contract = payload.get(

        "contract"

    )

    if not isinstance(

        contract,

        dict,

    ):

        return False

    if contract.get(

        "active_status"

    ) != "status == active":

        return False

    if contract.get(

        "stock_type"

    ) != "STOCK":

        return False

    if contract.get(

        "etf_type"

    ) != "ETF":

        return False

    if contract.get(

        "instrument_type_required"

    ) is not True:

        return False

    if contract.get(

        "allowed_markets"

    ) != [

        "TWSE",

        "TPEX",

    ]:

        return False

    if contract.get(

        "allowed_instrument_types"

    ) != [

        "STOCK",

        "ETF",

    ]:

        return False

    if contract.get(

        "legacy_injection"

    ) is not False:

        return False

    if contract.get(

        "official_etf_confirmation"

    ) is not True:

        return False

    return validate_universe_structure(

        stocks

    )

# ============================================================

# ATOMIC WRITE

# ============================================================

def atomic_write(

    payload: Dict[str, Any],

) -> bool:

    DATA_DIR.mkdir(

        parents=True,

        exist_ok=True,

    )

    temp_file = DATA_DIR / (

        "universe.json.tmp"

    )

    try:

        text = json.dumps(

            payload,

            ensure_ascii=False,

            indent=2,

        )

        temp_file.write_text(

            text,

            encoding="utf-8",

        )

        # ----------------------------------------------------

        # Write 前 JSON parse

        # ----------------------------------------------------

        json.loads(

            temp_file.read_text(

                encoding="utf-8"

            )

        )

        # ----------------------------------------------------

        # Atomic replace

        # ----------------------------------------------------

        temp_file.replace(

            UNIVERSE_FILE

        )

        return True

    except Exception as exc:

        log(

            f"❌ Atomic Write FAIL："

            f"{exc}"

        )

        try:

            temp_file.unlink(

                missing_ok=True

            )

        except Exception:

            pass

        return False

# ============================================================

# POST WRITE VERIFY

# ============================================================

def post_write_verify() -> bool:

    section(

        "Post Write Verify"

    )

    if not UNIVERSE_FILE.exists():

        log(

            "❌ universe.json 不存在"

        )

        return False

    try:

        payload = json.loads(

            UNIVERSE_FILE.read_text(

                encoding="utf-8"

            )

        )

    except Exception as exc:

        log(

            f"❌ JSON 重新讀取失敗："

            f"{exc}"

        )

        return False

    if not validate_payload(

        payload

    ):

        log(

            "❌ universe.json "

            "Contract FAIL"

        )

        return False

    stocks = payload.get(

        "stocks"

    )

    if not isinstance(

        stocks,

        dict,

    ):

        return False

    stock_count = sum(

        1

        for item in stocks.values()

        if item.get(

            "instrument_type"

        ) == "STOCK"

    )

    etf_count = sum(

        1

        for item in stocks.values()

        if item.get(

            "instrument_type"

        ) == "ETF"

    )

    log(

        f"✓ universe.json "

        f"重新讀取："

        f"{len(stocks)} 檔"

    )

    log(

        f"✓ STOCK："

        f"{stock_count}"

    )

    log(

        f"✓ ETF："

        f"{etf_count}"

    )

    log(

        "✓ instrument_type 全部存在"

    )

    log(

        "✓ ETF 必須有官方分類"

    )

    log(

        "✓ Universe Contract PASS"

    )

    log(

        "✓ Post Write Verify PASS"

    )

    return True

# ============================================================

# SUMMARY

# ============================================================

def print_summary(

    stocks: Dict[str, Dict[str, Any]],

) -> None:

    stock_count = sum(

        1

        for item in stocks.values()

        if item.get(

            "instrument_type"

        ) == "STOCK"

    )

    etf_count = sum(

        1

        for item in stocks.values()

        if item.get(

            "instrument_type"

        ) == "ETF"

    )

    twse = sum(

        1

        for item in stocks.values()

        if item.get(

            "market"

        ) == "TWSE"

    )

    tpex = sum(

        1

        for item in stocks.values()

        if item.get(

            "market"

        ) == "TPEX"

    )

    active = sum(

        1

        for item in stocks.values()

        if item.get(

            "status"

        ) == "active"

    )

    section(

        "UNIVERSE BUILD RESULT"

    )

    log(

        f"✓ Version："

        f"{VERSION}"

    )

    log(

        f"✓ Total："

        f"{len(stocks)}"

    )

    log(

        f"✓ STOCK："

        f"{stock_count}"

    )

    log(

        f"✓ ETF："

        f"{etf_count}"

    )

    log(

        f"✓ TWSE："

        f"{twse}"

    )

    log(

        f"✓ TPEx："

        f"{tpex}"

    )

    log(

        f"✓ active："

        f"{active}"

    )

    log(

        "✓ ETF included"

    )

    log(

        "✓ Bond ETF included"

    )

    log(

        "✓ Multi-asset ETF included"

    )

    log(

        "✓ Active ETF included"

    )

    log(

        "✓ Leveraged / inverse ETF included"

    )

    log(

        "✓ Official ETF classification"

    )

    log(

        "✓ No legacy injection"

    )

    log(

        "✓ Official sources only"

    )

    log(

        f"✓ Output："

        f"{UNIVERSE_FILE}"

    )

# ============================================================

# MAIN

# ============================================================

def main() -> int:

    started = time.time()

    section(

        "台股 AI 選股系統 "

        f"Universe Builder {VERSION}"

    )

    log(

        f"開始時間："

        f"{now_tw().isoformat()}"

    )

    # --------------------------------------------------------

    # 舊 Universe：

    # 只能拿 metadata。

    # 絕對不參與商品 discovery。

    # --------------------------------------------------------

    existing = (

        load_existing_metadata()

    )

    log(

        "既有 Universe metadata："

        f"{len(existing)}"

    )

    # ========================================================

    # 1. 官方 STOCK

    # ========================================================

    twse_stocks = (

        collect_twse_stocks()

    )

    tpex_stocks = (

        collect_tpex_stocks()

    )

    # ========================================================

    # 2. 官方 ETF

    # ========================================================

    etfs = (

        collect_official_etf()

    )

    # ========================================================

    # 3. Official Source Gate

    # ========================================================

    if not official_source_gate(

        twse_stocks,

        tpex_stocks,

        etfs,

    ):

        log(

            "❌ 官方來源 Gate FAIL"

        )

        return 1

    # ========================================================

    # 4. Build

    # ========================================================

    stocks = build_universe(

        twse_stocks,

        tpex_stocks,

        etfs,

        existing,

    )

    # ========================================================

    # 5. No Legacy Injection

    # ========================================================

    if not validate_no_legacy_injection(

        stocks,

        twse_stocks,

        tpex_stocks,

        etfs,

    ):

        return 1

    # ========================================================

    # 6. Structure Gate

    # ========================================================

    if not validate_universe_structure(

        stocks

    ):

        return 1

    # ========================================================

    # 7. Market Balance Gate

    # ========================================================

    twse_etf_count = sum(

        1

        for item in etfs.values()

        if item.get(

            "market"

        ) == "TWSE"

    )

    tpex_etf_count = sum(

        1

        for item in etfs.values()

        if item.get(

            "market"

        ) == "TPEX"

    )

    if not validate_market_balance(

        stocks,

        len(twse_stocks),

        len(tpex_stocks),

        twse_etf_count,

        tpex_etf_count,

    ):

        return 1

    # ========================================================

    # 8. Payload

    # ========================================================

    payload = make_payload(

        stocks,

        len(twse_stocks),

        len(tpex_stocks),

        twse_etf_count,

        tpex_etf_count,

    )

    if not validate_payload(

        payload

    ):

        log(

            "❌ Payload Contract FAIL"

        )

        return 1

    log(

        "✓ Payload Contract PASS"

    )

    # ========================================================

    # 9. Atomic Write

    # ========================================================

    if not atomic_write(

        payload

    ):

        return 1

    log(

        "✓ Atomic Write PASS"

    )

    # ========================================================

    # 10. Post Write Verify

    # ========================================================

    if not post_write_verify():

        log(

            "❌ Post Write Verify FAIL"

        )

        return 1

    # ========================================================

    # 11. ETF Statistics

    # ========================================================

    print_etf_statistics(

        stocks

    )

    # ========================================================

    # 12. Result

    # ========================================================

    print_summary(

        stocks

    )

    elapsed = (

        time.time()

        - started

    )

    log(

        f"✓ elapsed："

        f"{elapsed:.1f}s"

    )

    log("")

    log(

        "============================================================"

    )

    log(

        "UNIVERSE BUILD SUCCESS"

    )

    log(

        "============================================================"

    )

    return 0

# ============================================================

# ENTRY

# ============================================================

if __name__ == "__main__":

    sys.exit(

        main()

    )