```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_bond_etf.py V1.0

============================================================
目的
============================================================

建立：

    Data/bond_etf.json

本檔案專門負責：

- 台股上市債券 ETF
- 台股上櫃債券 ETF
- 主動式債券 ETF
- 被動式債券 ETF

不修改：

    Data/universe.json

不依賴：

    Yahoo Finance
    CMoney
    第三方 ETF 資料網站

============================================================
官方來源
============================================================

1. TWSE 官方 ISIN
   https://isin.twse.com.tw/isin/e_C_public.jsp

   用於取得：
   - 上市 ETF
   - 股票代號
   - 名稱
   - 市場
   - ETF 身份

2. TPEX 官方「債券及固定收益 ETF」
   https://wwwov.tpex.org.tw/web/etf/etf_bond.php?l=zh-tw

   用於取得：
   - 上櫃債券 ETF
   - 股票代號
   - 名稱

3. TPEX 官方 ETF 分類規則

   被動式債券 ETF：
       第六碼 B

   主動式債券 ETF：
       第六碼 D

============================================================
重要原則
============================================================

1. 官方資料優先
2. 不使用 Yahoo fallback
3. 不使用固定股票 fallback
4. 不用單一 ETF 冒充完整 Universe
5. 官方資料不足直接 FAIL
6. 不覆蓋既有有效 bond_etf.json
7. Atomic Write
8. 寫入後重新讀取驗證
9. 股票代號唯一
10. 名稱不得空白
11. 不得以 symbol 當 name
12. 市場必須是 TWSE / TPEX
13. ETF 類型必須是 BOND_ETF
14. B = 被動式債券 ETF
15. D = 主動式債券 ETF
16. T = 多資產 ETF，不列入純債券 ETF

============================================================
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


VERSION = "V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "bond_etf.json"

REQUEST_TIMEOUT = 30


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Connection": "keep-alive",
}


TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/"
    "e_C_public.jsp"
)


TPEX_BOND_ETF_URL = (
    "https://wwwov.tpex.org.tw/"
    "web/etf/etf_bond.php"
)


# ============================================================
# 數量防呆
#
# 目前 TPEX 債券 ETF 數量遠高於這個門檻。
#
# 門檻不是要求固定數量。
# 只是避免官方來源掛掉後：
#
#     93 檔 → 0 檔
#
# 卻仍然產生空 Universe。
# ============================================================

MIN_TWSE_BOND_ETF = 5
MIN_TPEX_BOND_ETF = 20
MIN_TOTAL_BOND_ETF = 30


# ============================================================
# Log
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# 清理
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = (
        text
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .replace("&nbsp;", " ")
        .strip()
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def clean_code(value: Any) -> str:

    text = clean_text(value)

    text = re.sub(
        r"\.(TW|TWO)$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def is_valid_etf_code(code: str) -> bool:

    code = clean_code(code)

    if not code:
        return False

    if not re.fullmatch(
        r"\d{4,6}[A-Z]?",
        code,
    ):
        return False

    return True


def is_bond_etf_code(code: str) -> bool:

    code = clean_code(code).upper()

    if len(code) < 6:
        return False

    sixth = code[5]

    return sixth in ("B", "D")


def bond_etf_class(code: str) -> str:

    code = clean_code(code).upper()

    if len(code) < 6:
        return ""

    sixth = code[5]

    if sixth == "B":
        return "PASSIVE_BOND_ETF"

    if sixth == "D":
        return "ACTIVE_BOND_ETF"

    return ""


# ============================================================
# HTTP
# ============================================================

def safe_request(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
) -> Optional[requests.Response]:

    for attempt in range(1, 4):

        try:

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=timeout,
            )

            if response.status_code != 200:

                log(
                    f"⚠️ HTTP "
                    f"{response.status_code}: "
                    f"{response.url}"
                )

                if attempt < 3:
                    time.sleep(attempt)
                    continue

                return None

            if not response.content:

                log(
                    f"⚠️ Empty response: "
                    f"{response.url}"
                )

                if attempt < 3:
                    time.sleep(attempt)
                    continue

                return None

            return response

        except requests.RequestException as exc:

            log(
                f"⚠️ Request failed "
                f"(attempt {attempt}/3): "
                f"{url} | {exc}"
            )

            if attempt < 3:
                time.sleep(attempt)
                continue

            return None

        except Exception as exc:

            log(
                f"⚠️ Unexpected request error "
                f"(attempt {attempt}/3): "
                f"{url} | {exc}"
            )

            if attempt < 3:
                time.sleep(attempt)
                continue

            return None

    return None


# ============================================================
# HTML Table Parser
# ============================================================

class HTMLTableParser(HTMLParser):

    def __init__(self) -> None:

        super().__init__()

        self.in_table = False
        self.in_row = False
        self.in_cell = False

        self.current_cell = ""
        self.current_row: List[str] = []
        self.current_table: List[List[str]] = []

        self.tables: List[
            List[List[str]]
        ] = []

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ) -> None:

        tag = tag.lower()

        if tag == "table":

            self.in_table = True
            self.current_table = []

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
            self.current_cell = ""

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if (
            tag in ("td", "th")
            and self.in_cell
        ):

            value = clean_text(
                self.current_cell
            )

            self.current_row.append(
                value
            )

            self.current_cell = ""
            self.in_cell = False

        elif (
            tag == "tr"
            and self.in_row
        ):

            if self.current_row:

                self.current_table.append(
                    self.current_row
                )

            self.current_row = []
            self.in_row = False

        elif (
            tag == "table"
            and self.in_table
        ):

            if self.current_table:

                self.tables.append(
                    self.current_table
                )

            self.current_table = []
            self.in_table = False

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self.in_cell:

            self.current_cell += data


# ============================================================
# TWSE ISIN
# ============================================================

def fetch_twse_bond_etf(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    section(
        "1. TWSE 官方債券 ETF"
    )

    response = safe_request(
        session,
        TWSE_ISIN_URL,
        params={
            "strMode": "2",
        },
    )

    if response is None:

        log(
            "❌ TWSE ISIN 官方來源取得失敗"
        )

        return {}

    parser = HTMLTableParser()

    try:

        parser.feed(
            response.text
        )

    except Exception as exc:

        log(
            f"❌ TWSE HTML 解析失敗："
            f"{exc}"
        )

        return {}

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for table in parser.tables:

        for row in table:

            if len(row) < 2:
                continue

            code = ""
            name = ""
            market = ""
            security_type = ""

            # ------------------------------------------------
            # 尋找股票代號
            # ------------------------------------------------

            code_index = None

            for index, cell in enumerate(
                row
            ):

                value = clean_text(cell)

                # 例如：
                # 0050
                # 00679B
                # 00981D
                if re.fullmatch(
                    r"\d{4,6}[A-Z]?",
                    value,
                ):

                    code = value.upper()
                    code_index = index
                    break

            if not code_index is None:

                pass

            else:

                # 有些 ISIN 表格資料可能把代號
                # 與其他資訊放在同一欄。
                for index, cell in enumerate(
                    row
                ):

                    match = re.search(
                        r"\b(\d{4,6}[A-Z]?)\b",
                        clean_text(cell),
                    )

                    if match:

                        candidate = (
                            match.group(1)
                        ).upper()

                        if is_valid_etf_code(
                            candidate
                        ):

                            code = candidate
                            code_index = index
                            break

            if not code:
                continue

            if not is_bond_etf_code(
                code
            ):
                continue

            # ------------------------------------------------
            # 解析名稱
            # ------------------------------------------------

            for index, cell in enumerate(
                row
            ):

                if index == code_index:
                    continue

                value = clean_text(cell)

                if not value:
                    continue

                if value == code:
                    continue

                if re.fullmatch(
                    r"TW\d{10}[A-Z0-9]*",
                    value,
                ):
                    continue

                if (
                    "TWSE LISTED" in value.upper()
                    or "上市" in value
                ):
                    market = "TWSE"

                if (
                    value.upper() == "ETF"
                    or value == "ETF"
                ):
                    security_type = "ETF"

            # ------------------------------------------------
            # 名稱通常在代號後方
            # ------------------------------------------------

            if code_index is not None:

                for cell in row[
                    code_index + 1:
                ]:

                    value = clean_text(cell)

                    if not value:
                        continue

                    if value == code:
                        continue

                    if re.fullmatch(
                        r"TW\d{10}[A-Z0-9]*",
                        value,
                    ):
                        continue

                    if value.upper() == "ETF":
                        continue

                    if (
                        "TWSE LISTED"
                        in value.upper()
                    ):
                        continue

                    if re.fullmatch(
                        r"\d{4}/\d{1,2}/\d{1,2}",
                        value,
                    ):
                        continue

                    name = value
                    break

            if not name:
                continue

            # ------------------------------------------------
            # TWSE ISIN 官方資料
            # ------------------------------------------------

            result[code] = {

                "symbol": code,

                "full_symbol": (
                    f"{code}.TW"
                ),

                "name": name,

                "market": "TWSE",

                "type": "ETF",

                "asset_class": "BOND",

                "bond_etf_class": (
                    bond_etf_class(code)
                ),

                "source": (
                    "TWSE_OFFICIAL_ISIN"
                ),
            }

    log(
        f"✓ TWSE 官方債券 ETF："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX 債券 ETF
# ============================================================

def fetch_tpex_bond_etf(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    section(
        "2. TPEX 官方債券及固定收益 ETF"
    )

    response = safe_request(
        session,
        TPEX_BOND_ETF_URL,
        params={
            "l": "zh-tw",
        },
    )

    if response is None:

        log(
            "❌ TPEX 債券 ETF 官方來源取得失敗"
        )

        return {}

    parser = HTMLTableParser()

    try:

        parser.feed(
            response.text
        )

    except Exception as exc:

        log(
            f"❌ TPEX HTML 解析失敗："
            f"{exc}"
        )

        return {}

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # --------------------------------------------------------
    # 第一階段：
    # 直接從 HTML table 找代號。
    # --------------------------------------------------------

    for table in parser.tables:

        for row in table:

            if not row:
                continue

            row_text = " ".join(
                clean_text(cell)
                for cell in row
            )

            matches = re.findall(
                r"\b(\d{4,6}[A-Z]?)\b",
                row_text.upper(),
            )

            if not matches:
                continue

            for code in matches:

                code = clean_code(
                    code
                ).upper()

                if not is_valid_etf_code(
                    code
                ):
                    continue

                if not is_bond_etf_code(
                    code
                ):
                    continue

                name = ""

                code_position = None

                for index, cell in enumerate(
                    row
                ):

                    if code in clean_text(
                        cell
                    ).upper():

                        code_position = index
                        break

                if code_position is not None:

                    for cell in row[
                        code_position + 1:
                    ]:

                        candidate = clean_text(
                            cell
                        )

                        if not candidate:
                            continue

                        if candidate == code:
                            continue

                        if re.fullmatch(
                            r"[\d,.\-+%]+",
                            candidate,
                        ):
                            continue

                        if candidate in (
                            "代號",
                            "名稱",
                            "證券代號",
                            "ETF代號",
                        ):
                            continue

                        name = candidate
                        break

                # ------------------------------------------------
                # 若名稱沒有直接找到，
                # 再從整列找第一個合理名稱。
                # ------------------------------------------------

                if not name:

                    for cell in row:

                        candidate = clean_text(
                            cell
                        )

                        if not candidate:
                            continue

                        if candidate == code:
                            continue

                        if re.fullmatch(
                            r"\d{4,6}[A-Z]?",
                            candidate,
                        ):
                            continue

                        if re.fullmatch(
                            r"[\d,.\-+%]+",
                            candidate,
                        ):
                            continue

                        if candidate in (
                            "代號",
                            "名稱",
                            "證券代號",
                            "ETF代號",
                        ):
                            continue

                        name = candidate
                        break

                if not name:
                    continue

                result[code] = {

                    "symbol": code,

                    "full_symbol": (
                        f"{code}.TWO"
                    ),

                    "name": name,

                    "market": "TPEX",

                    "type": "ETF",

                    "asset_class": "BOND",

                    "bond_etf_class": (
                        bond_etf_class(code)
                    ),

                    "source": (
                        "TPEX_OFFICIAL_BOND_ETF"
                    ),
                }

    # --------------------------------------------------------
    # 第二階段：
    # 官方頁面可能是動態內容。
    #
    # 即使 table parser 沒抓到，
    # 仍從 HTML 原始內容尋找 B / D 型代號。
    # --------------------------------------------------------

    html = response.text

    raw_codes = re.findall(
        r"\b(\d{4,6}[BD])\b",
        html.upper(),
    )

    for raw_code in raw_codes:

        code = clean_code(
            raw_code
        ).upper()

        if not is_bond_etf_code(
            code
        ):
            continue

        if code in result:
            continue

        # ----------------------------------------------------
        # 從附近 HTML 文字嘗試取得名稱。
        # ----------------------------------------------------

        position = html.upper().find(
            code
        )

        name = ""

        if position >= 0:

            fragment = html[
                max(
                    0,
                    position - 500,
                ):
                position + 1000
            ]

            fragment = re.sub(
                r"<[^>]+>",
                " ",
                fragment,
            )

            fragment = clean_text(
                fragment
            )

            # 去除代號
            fragment = fragment.replace(
                code,
                " ",
            )

            # 找中文名稱
            match = re.search(
                r"([\u4e00-\u9fff]{2,30})",
                fragment,
            )

            if match:

                candidate = clean_text(
                    match.group(1)
                )

                if candidate not in (
                    "債券及固定收益ETF",
                    "商品資訊",
                    "投資標的信評資訊",
                ):

                    name = candidate

        # ----------------------------------------------------
        # 動態頁面只抓到代號、沒有名稱時，
        # 不允許猜名稱。
        # ----------------------------------------------------

        if not name:
            continue

        result[code] = {

            "symbol": code,

            "full_symbol": (
                f"{code}.TWO"
            ),

            "name": name,

            "market": "TPEX",

            "type": "ETF",

            "asset_class": "BOND",

            "bond_etf_class": (
                bond_etf_class(code)
            ),

            "source": (
                "TPEX_OFFICIAL_BOND_ETF"
            ),
        }

    log(
        f"✓ TPEX 官方債券 ETF："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# 合併
# ============================================================

def merge_sources(
    twse_data: Dict[str, Dict[str, Any]],
    tpex_data: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "3. 合併 TWSE / TPEX 債券 ETF"
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for code, item in twse_data.items():

        result[code] = dict(
            item
        )

    for code, item in tpex_data.items():

        # 市場來源不同時，
        # TPEX 官方資料優先。
        result[code] = dict(
            item
        )

    log(
        f"TWSE：{len(twse_data)} 檔"
    )

    log(
        f"TPEX：{len(tpex_data)} 檔"
    )

    log(
        f"合併：{len(result)} 檔"
    )

    return result


# ============================================================
# 驗證
# ============================================================

def validate_records(
    data: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "4. 債券 ETF 資料完整性驗證"
    )

    failed = False

    for code, item in data.items():

        symbol = clean_code(
            item.get("symbol")
        )

        name = clean_text(
            item.get("name")
        )

        market = clean_text(
            item.get("market")
        )

        asset_class = clean_text(
            item.get("asset_class")
        )

        bond_class = clean_text(
            item.get("bond_etf_class")
        )

        # ----------------------------------------------------
        # symbol
        # ----------------------------------------------------

        if symbol != code:

            log(
                f"❌ symbol mismatch："
                f"{code}"
            )

            failed = True

        if not is_valid_etf_code(
            code
        ):

            log(
                f"❌ 無效 ETF 代號："
                f"{code}"
            )

            failed = True

        # ----------------------------------------------------
        # bond ETF
        # ----------------------------------------------------

        if not is_bond_etf_code(
            code
        ):

            log(
                f"❌ 非 B/D 債券 ETF："
                f"{code}"
            )

            failed = True

        # ----------------------------------------------------
        # name
        # ----------------------------------------------------

        if not name:

            log(
                f"❌ 空白 name："
                f"{code}"
            )

            failed = True

        if name == code:

            log(
                f"❌ name 等於 symbol："
                f"{code}"
            )

            failed = True

        # ----------------------------------------------------
        # market
        # ----------------------------------------------------

        if market not in (
            "TWSE",
            "TPEX",
        ):

            log(
                f"❌ 市場錯誤："
                f"{code} = {market}"
            )

            failed = True

        # ----------------------------------------------------
        # asset class
        # ----------------------------------------------------

        if asset_class != "BOND":

            log(
                f"❌ asset_class 錯誤："
                f"{code}"
            )

            failed = True

        # ----------------------------------------------------
        # B / D
        # ----------------------------------------------------

        expected_class = (
            bond_etf_class(code)
        )

        if bond_class != expected_class:

            log(
                f"❌ bond_etf_class 錯誤："
                f"{code}"
            )

            failed = True

    if failed:

        return False

    log(
        f"✓ {len(data)} 檔債券 ETF "
        "完整性驗證通過"
    )

    return True


def validate_duplicates(
    data: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "5. 股票代號唯一性驗證"
    )

    codes = list(
        data.keys()
    )

    if len(codes) != len(
        set(codes)
    ):

        log(
            "❌ 發現重複 ETF 代號"
        )

        return False

    log(
        f"✓ ETF 代號唯一："
        f"{len(codes)} 檔"
    )

    return True


def validate_counts(
    data: Dict[str, Dict[str, Any]],
    twse_count: int,
    tpex_count: int,
) -> bool:

    section(
        "6. 官方來源數量防呆"
    )

    total = len(data)

    log(
        f"TWSE 債券 ETF："
        f"{twse_count}"
    )

    log(
        f"TPEX 債券 ETF："
        f"{tpex_count}"
    )

    log(
        f"TOTAL："
        f"{total}"
    )

    if twse_count < MIN_TWSE_BOND_ETF:

        log(
            f"❌ TWSE 官方資料不足："
            f"{twse_count} < "
            f"{MIN_TWSE_BOND_ETF}"
        )

        return False

    if tpex_count < MIN_TPEX_BOND_ETF:

        log(
            f"❌ TPEX 官方資料不足："
            f"{tpex_count} < "
            f"{MIN_TPEX_BOND_ETF}"
        )

        log(
            "❌ 禁止用少量資料建立 "
            "不完整債券 ETF Universe"
        )

        return False

    if total < MIN_TOTAL_BOND_ETF:

        log(
            f"❌ 債券 ETF 總數不足："
            f"{total} < "
            f"{MIN_TOTAL_BOND_ETF}"
        )

        return False

    log(
        "✓ 官方來源數量防呆通過"
    )

    return True


# ============================================================
# 標準化
# ============================================================

def normalize_records(
    data: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    normalized: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for code, item in data.items():

        code = clean_code(
            code
        ).upper()

        if not is_valid_etf_code(
            code
        ):
            continue

        if not is_bond_etf_code(
            code
        ):
            continue

        name = clean_text(
            item.get("name")
        )

        market = clean_text(
            item.get("market")
        )

        if not name:
            continue

        if market not in (
            "TWSE",
            "TPEX",
        ):
            continue

        suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )

        normalized[code] = {

            "symbol": code,

            "full_symbol": (
                f"{code}{suffix}"
            ),

            "name": name,

            "market": market,

            "type": "ETF",

            "asset_class": "BOND",

            "bond_etf_class": (
                bond_etf_class(code)
            ),

            "source": item.get(
                "source",
                "OFFICIAL",
            ),
        }

    return normalized


# ============================================================
# Atomic Write
# ============================================================

def atomic_write_json(
    path: Path,
    data: Dict[str, Any],
) -> bool:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_name(
        path.name + ".tmp"
    )

    try:

        with temp_path.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.write("\n")

        temp_path.replace(
            path
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write 失敗："
            f"{exc}"
        )

        try:

            if temp_path.exists():
                temp_path.unlink()

        except Exception:
            pass

        return False


# ============================================================
# 寫入後驗證
# ============================================================

def verify_written_file(
    path: Path,
) -> bool:

    section(
        "8. 寫入後重新讀取驗證"
    )

    try:

        with path.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(
                f
            )

    except Exception as exc:

        log(
            f"❌ JSON 重新讀取失敗："
            f"{exc}"
        )

        return False

    if not isinstance(
        data,
        dict,
    ):

        log(
            "❌ root 不是 object"
        )

        return False

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ stocks 不是 object"
        )

        return False

    if len(stocks) < MIN_TOTAL_BOND_ETF:

        log(
            f"❌ 寫入後數量不足："
            f"{len(stocks)}"
        )

        return False

    for code, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ item 不是 object："
                f"{code}"
            )

            return False

        name = clean_text(
            item.get("name")
        )

        market = clean_text(
            item.get("market")
        )

        if not name:

            log(
                f"❌ 寫入後空白 name："
                f"{code}"
            )

            return False

        if name == code:

            log(
                f"❌ 寫入後 name == symbol："
                f"{code}"
            )

            return False

        if market not in (
            "TWSE",
            "TPEX",
        ):

            log(
                f"❌ 寫入後 market 錯誤："
                f"{code}"
            )

            return False

        if not is_bond_etf_code(
            code
        ):

            log(
                f"❌ 寫入後出現非 B/D ETF："
                f"{code}"
            )

            return False

    log(
        f"✓ 寫入後債券 ETF："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ 所有 name / market / B-D "
        "分類再次驗證成功"
    )

    return True


# ============================================================
# Main
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"build_bond_etf.py {VERSION}"
    )

    log(
        "建立 Data/bond_etf.json"
    )

    log(
        "資料來源："
    )

    log(
        "1. TWSE 官方 ISIN"
    )

    log(
        "2. TPEX 官方債券及固定收益 ETF"
    )

    log(
        "3. 不使用 Yahoo fallback"
    )

    log(
        "4. 不使用固定股票 fallback"
    )

    session = requests.Session()

    # ========================================================
    # 1. TWSE
    # ========================================================

    twse_data = (
        fetch_twse_bond_etf(
            session
        )
    )

    # ========================================================
    # 2. TPEX
    # ========================================================

    tpex_data = (
        fetch_tpex_bond_etf(
            session
        )
    )

    # ========================================================
    # 官方來源 HARD FAIL
    # ========================================================

    if len(twse_data) < MIN_TWSE_BOND_ETF:

        section(
            "TWSE BOND ETF DATA FAIL"
        )

        log(
            f"❌ TWSE 官方債券 ETF："
            f"{len(twse_data)}"
        )

        log(
            f"❌ 最低要求："
            f"{MIN_TWSE_BOND_ETF}"
        )

        log(
            "❌ 不寫入 bond_etf.json"
        )

        return 1

    if len(tpex_data) < MIN_TPEX_BOND_ETF:

        section(
            "TPEX BOND ETF DATA FAIL"
        )

        log(
            f"❌ TPEX 官方債券 ETF："
            f"{len(tpex_data)}"
        )

        log(
            f"❌ 最低要求："
            f"{MIN_TPEX_BOND_ETF}"
        )

        log(
            "❌ 不寫入 bond_etf.json"
        )

        return 1

    # ========================================================
    # 3. 合併
    # ========================================================

    data = merge_sources(
        twse_data,
        tpex_data,
    )

    # ========================================================
    # 4. 標準化
    # ========================================================

    data = normalize_records(
        data
    )

    log(
        f"✓ 標準化後："
        f"{len(data)} 檔"
    )

    # ========================================================
    # 5. 完整性
    # ========================================================

    if not validate_records(
        data
    ):

        log(
            "❌ 資料完整性驗證失敗"
        )

        return 1

    # ========================================================
    # 6. 重複
    # ========================================================

    if not validate_duplicates(
        data
    ):

        return 1

    # ========================================================
    # 7. 市場統計
    # ========================================================

    twse_count = sum(
        1
        for item in data.values()
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in data.values()
        if item["market"] == "TPEX"
    )

    passive_count = sum(
        1
        for item in data.values()
        if item["bond_etf_class"]
        == "PASSIVE_BOND_ETF"
    )

    active_count = sum(
        1
        for item in data.values()
        if item["bond_etf_class"]
        == "ACTIVE_BOND_ETF"
    )

    if not validate_counts(
        data,
        twse_count,
        tpex_count,
    ):

        return 1

    # ========================================================
    # 8. Output
    # ========================================================

    output = {

        "schema_version": VERSION,

        "generated_at": (
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ),

        "asset_class": "BOND",

        "type": "ETF",

        "source": {

            "primary": [
                "TWSE_OFFICIAL_ISIN",
                "TPEX_OFFICIAL_BOND_ETF",
            ],

            "fallback": [],

            "description": (
                "Official Taiwan "
                "listed and TPEx bond ETF "
                "universe. "
                "No third-party fallback."
            ),
        },

        "universe_count": len(data),

        "market_count": {

            "TWSE": twse_count,

            "TPEX": tpex_count,
        },

        "bond_etf_class_count": {

            "PASSIVE_BOND_ETF": (
                passive_count
            ),

            "ACTIVE_BOND_ETF": (
                active_count
            ),
        },

        "stocks": data,
    }

    # ========================================================
    # 9. Atomic Write
    # ========================================================

    section(
        "9. Atomic Write Data/bond_etf.json"
    )

    if not atomic_write_json(
        OUTPUT_FILE,
        output,
    ):

        return 1

    log(
        "✓ Data/bond_etf.json "
        "Atomic Write 成功"
    )

    # ========================================================
    # 10. 寫入後驗證
    # ========================================================

    if not verify_written_file(
        OUTPUT_FILE
    ):

        log(
            "❌ 寫入後驗證失敗"
        )

        return 1

    # ========================================================
    # 11. PASS
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "BUILD BOND ETF PASS"
    )

    log(
        f"✓ Version：{VERSION}"
    )

    log(
        f"✓ Bond ETF："
        f"{len(data)} 檔"
    )

    log(
        f"✓ TWSE："
        f"{twse_count} 檔"
    )

    log(
        f"✓ TPEX："
        f"{tpex_count} 檔"
    )

    log(
        f"✓ Passive Bond ETF："
        f"{passive_count} 檔"
    )

    log(
        f"✓ Active Bond ETF："
        f"{active_count} 檔"
    )

    log(
        "✓ 官方來源"
    )

    log(
        "✓ 無 Yahoo fallback"
    )

    log(
        "✓ 無固定股票 fallback"
    )

    log(
        "✓ 無空白 name"
    )

    log(
        "✓ 無 symbol 當 name"
    )

    log(
        "✓ B / D 債券 ETF 分類"
    )

    log(
        "✓ 股票代號唯一"
    )

    log(
        "✓ 數量防呆"
    )

    log(
        "✓ Atomic Write"
    )

    log(
        "✓ 寫入後重新驗證"
    )

    log(
        f"✓ Data/bond_etf.json 建立完成"
    )

    log(
        f"✓ 耗時："
        f"{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
```
