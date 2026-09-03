#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_chip.py
============================================================

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 來源
2. 只處理 status == "active"
3. 只接受 market == TWSE / TPEX
4. 只接受 type == STOCK / ETF
5. 不從交易資料建立 Universe
6. 不從 API 發現 Universe 外股票
7. 不使用 Yahoo / yfinance / FinMind / CMoney
8. 所有籌碼資料只接受官方 TWSE / TPEx
9. 不使用 0 代替缺失資料
10. 缺失資料一律 None
11. 缺失資料必須反映在 data_quality
12. 5D 必須是 5 個不同實際交易日
13. 20D 必須是 20 個不同實際交易日
14. 不使用自然日冒充交易日
15. 單一股票資料缺失不得讓整批中止
16. 官方整體資料源失敗必須 FAIL
17. 官方回應成功但解析 0 筆必須標記 schema/empty 問題
18. 不得把「API 最新快照」冒充歷史日期
19. 不得把第三方資料標記為 official
20. 若整體沒有任何有效官方資料，不覆蓋既有 chip.json
21. 寫檔使用 atomic write
22. 寫檔前必須完成 Universe / chip contract validation

資料：
------------------------------------------------------------
TWSE
    三大法人：
        T86

    融資融券：
        MI_MARGN

    成交量：
        MI_INDEX

    當沖：
        TWTB4U

TPEx
    三大法人：
        3itrade_hedge_result.php

    融資融券：
        margin_bal_result.php

    成交量：
        stk_quote_result.php

    當沖：
        官方當沖資料；若官方端點無法取得，
        明確標記 unavailable，不偽造資料。

重要：
------------------------------------------------------------
TPEx OpenAPI 的
tpex_mainboard_daily_close_quotes
是「最新交易日快照」，沒有歷史日期參數。

因此本程式不能拿它來回填 20 個歷史交易日。

歷史 TPEx 資料必須使用帶日期的官方盤後資料 endpoint。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests


# ============================================================
# CONFIG
# ============================================================

VERSION = "UNIVERSE-CHIP-3.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30
REQUEST_RETRY = 3
REQUEST_SLEEP = 0.45

LOOKBACK_CALENDAR_DAYS = 90

REQUIRED_5D = 5
REQUIRED_20D = 20

MIN_MARKET_RECORDS = 1


USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


# ============================================================
# OFFICIAL URLS
# ============================================================

TWSE_T86_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/fund/T86"
)

TWSE_MARGIN_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/marginTrading/MI_MARGN"
)

TWSE_VOLUME_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/MI_INDEX"
)

TWSE_DAYTRADE_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/trading/TWTB4U"
)


TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)

TPEX_MARGIN_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/margin_trading/"
    "margin_balance/margin_bal_result.php"
)

TPEX_VOLUME_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_close_quotes/stk_quote_result.php"
)

# TPEx 當沖 endpoint 可能依官方系統調整。
# 找不到或 schema 改變時，必須標記 unavailable，
# 絕對不能自行計算假資料。
TPEX_DAYTRADE_URLS = [
    (
        "https://www.tpex.org.tw/"
        "openapi/v1/"
        "tpex_intraday_trading_statistics"
    ),
]


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": (
            "application/json,"
            "text/plain,"
            "text/csv,"
            "text/html,"
            "*/*"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": "https://www.tpex.org.tw/",
    }
)


# ============================================================
# RUNTIME OBSERVABILITY
# ============================================================

SOURCE_STATS: Dict[str, Dict[str, Any]] = defaultdict(
    lambda: {
        "requests": 0,
        "success": 0,
        "request_error": 0,
        "json_error": 0,
        "empty": 0,
        "schema_error": 0,
        "records": 0,
        "dates_with_records": 0,
        "last_error": None,
    }
)


def source_stat(name: str) -> Dict[str, Any]:
    return SOURCE_STATS[name]


def mark_source(
    name: str,
    field: str,
    value: Any = 1,
) -> None:
    stats = source_stat(name)

    if isinstance(value, (int, float)):
        stats[field] = stats.get(field, 0) + value
    else:
        stats[field] = value


# ============================================================
# TIME
# ============================================================

def taiwan_now() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    )


def taiwan_today() -> date:
    return taiwan_now().date()


def now_iso() -> str:
    return taiwan_now().isoformat(
        timespec="seconds"
    )


# ============================================================
# BASIC CONVERSION
# ============================================================

def safe_float(value: Any) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("%", "")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("\u3000", "")
    )

    if text in {
        "-",
        "--",
        "---",
        "N/A",
        "NA",
        "null",
        "None",
        "nan",
        "NaN",
    }:
        return None

    try:
        return float(text)
    except Exception:
        return None


def safe_int(value: Any) -> Optional[int]:

    number = safe_float(value)

    if number is None:
        return None

    try:
        return int(number)
    except Exception:
        return None


def clean_symbol(value: Any) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    text = text.replace("\u3000", "")

    if text.endswith(".TWO"):
        text = text[:-4]

    elif text.endswith(".TW"):
        text = text[:-3]

    # TPEx/TWSE occasionally returns code + name.
    match = re.match(
        r"^([0-9]{4,6}[A-Z]?)",
        text,
    )

    if match:
        return match.group(1)

    return text


def normalize_field(value: Any) -> str:

    return (
        str(value)
        .strip()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("\n", "")
        .replace("\r", "")
        .replace("\t", "")
    )


# ============================================================
# DATE
# ============================================================

def parse_date(value: Any) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace("/", "-")
        .replace(".", "-")
        .replace("\u3000", "")
    )

    # YYYY-MM-DD
    try:
        if len(text) >= 10:
            return datetime.strptime(
                text[:10],
                "%Y-%m-%d",
            ).date().isoformat()
    except Exception:
        pass

    # YYYYMMDD
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(
                text,
                "%Y%m%d",
            ).date().isoformat()
        except Exception:
            pass

    # ROC: 115/09/02
    parts = text.split("-")

    if len(parts) >= 3:
        if all(
            p.isdigit()
            for p in parts[:3]
        ):
            year = int(parts[0])

            if year < 1911:
                year += 1911

            try:
                return date(
                    year,
                    int(parts[1]),
                    int(parts[2][:2]),
                ).isoformat()
            except Exception:
                pass

    # ROC compact: 1150902
    if len(text) == 7 and text.isdigit():
        try:
            year = int(text[:3]) + 1911
            month = int(text[3:5])
            day = int(text[5:7])

            return date(
                year,
                month,
                day,
            ).isoformat()
        except Exception:
            pass

    return None


def roc_date(gdate: str) -> str:

    d = datetime.strptime(
        gdate,
        "%Y-%m-%d",
    ).date()

    return (
        f"{d.year - 1911:03d}/"
        f"{d.month:02d}/"
        f"{d.day:02d}"
    )


def roc_compact_date(gdate: str) -> str:

    d = datetime.strptime(
        gdate,
        "%Y-%m-%d",
    ).date()

    return (
        f"{d.year - 1911:03d}"
        f"{d.month:02d}"
        f"{d.day:02d}"
    )


# ============================================================
# HTTP
# ============================================================

def http_get(
    source_name: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[requests.Response]:

    stats = source_stat(source_name)
    stats["requests"] += 1

    last_error = None

    for attempt in range(
        1,
        REQUEST_RETRY + 1,
    ):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            stats["success"] += 1

            time.sleep(REQUEST_SLEEP)

            return response

        except Exception as exc:
            last_error = exc

            if attempt < REQUEST_RETRY:
                time.sleep(
                    attempt * 1.0
                )

    stats["request_error"] += 1
    stats["last_error"] = str(last_error)

    print(
        f"      [{source_name}] "
        f"REQUEST ERROR: {last_error}"
    )

    return None


def get_json(
    source_name: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:

    response = http_get(
        source_name,
        url,
        params,
    )

    if response is None:
        return None

    try:
        return response.json()

    except Exception as exc:
        source_stat(
            source_name
        )["json_error"] += 1

        source_stat(
            source_name
        )["last_error"] = str(exc)

        print(
            f"      [{source_name}] "
            f"JSON ERROR: {exc}"
        )

        return None


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write_json(
    path: Path,
    payload: Any,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write("\n")
            file.flush()
            os.fsync(
                file.fileno()
            )

        os.replace(
            temp_path,
            path,
        )

    finally:

        if os.path.exists(
            temp_path
        ):
            os.unlink(
                temp_path
            )


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> Dict[str, Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            f"Universe not found: "
            f"{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        data = json.load(file)

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            "universe.json root must be dict"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "universe.json stocks must be dict"
        )

    active: Dict[str, Dict[str, Any]] = {}

    for key, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            raise RuntimeError(
                f"Universe item {key} "
                "must be dict"
            )

        if item.get(
            "symbol"
        ) != key:
            raise RuntimeError(
                f"Universe key/symbol "
                f"mismatch: {key}"
            )

        if item.get(
            "status"
        ) != "active":
            continue

        market = item.get(
            "market"
        )

        if market not in {
            "TWSE",
            "TPEX",
        }:
            raise RuntimeError(
                f"{key}: invalid market "
                f"{market}"
            )

        instrument_type = item.get(
            "type"
        )

        if instrument_type not in {
            "STOCK",
            "ETF",
        }:
            raise RuntimeError(
                f"{key}: invalid type "
                f"{instrument_type}"
            )

        active[key] = dict(item)

    if not active:
        raise RuntimeError(
            "No active Universe"
        )

    return active


# ============================================================
# GENERIC FIELD HELPERS
# ============================================================

def find_field(
    fields: Iterable[Any],
    exact: Iterable[str] = (),
    contains: Iterable[str] = (),
) -> Optional[int]:

    normalized = [
        normalize_field(x)
        for x in fields
    ]

    exact_values = [
        normalize_field(x)
        for x in exact
    ]

    contains_values = [
        normalize_field(x)
        for x in contains
    ]

    for candidate in exact_values:
        for index, field in enumerate(
            normalized
        ):
            if field == candidate:
                return index

    for candidate in contains_values:
        for index, field in enumerate(
            normalized
        ):
            if candidate in field:
                return index

    return None


def row_get(
    row: List[Any],
    index: Optional[int],
) -> Any:

    if index is None:
        return None

    if index < 0:
        return None

    if index >= len(row):
        return None

    return row[index]


def row_number(
    row: List[Any],
    index: Optional[int],
) -> Optional[float]:

    return safe_float(
        row_get(
            row,
            index,
        )
    )


# ============================================================
# TWSE INSTITUTIONAL
# ============================================================

def twse_institutional(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    source = "TWSE_T86"

    data = get_json(
        source,
        TWSE_T86_URL,
        {
            "response": "json",
            "date": gdate.replace(
                "-",
                "",
            ),
            "selectType": "ALLBUT0999",
        },
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if not isinstance(
        fields,
        list,
    ) or not isinstance(
        rows,
        list,
    ):
        source_stat(
            source
        )["schema_error"] += 1

        return result

    code_index = find_field(
        fields,
        exact=[
            "證券代號",
            "股票代號",
        ],
    )

    foreign_index = find_field(
        fields,
        exact=[
            "外陸資買賣超股數(不含外資自營商)",
        ],
        contains=[
            "外陸資買賣超股數",
        ],
    )

    trust_index = find_field(
        fields,
        exact=[
            "投信買賣超股數",
        ],
        contains=[
            "投信",
            "買賣超股數",
        ],
    )

    dealer_index = find_field(
        fields,
        exact=[
            "自營商買賣超股數",
        ],
        contains=[
            "自營商",
            "買賣超股數",
        ],
    )

    if code_index is None:
        source_stat(
            source
        )["schema_error"] += 1

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        code = clean_symbol(
            row_get(
                row,
                code_index,
            )
        )

        if not code:
            continue

        foreign = row_number(
            row,
            foreign_index,
        )

        trust = row_number(
            row,
            trust_index,
        )

        dealer = row_number(
            row,
            dealer_index,
        )

        if (
            foreign is None
            and trust is None
            and dealer is None
        ):
            continue

        total = None

        if all(
            value is not None
            for value in (
                foreign,
                trust,
                dealer,
            )
        ):
            total = (
                foreign
                + trust
                + dealer
            )

        result[code] = {
            "date": gdate,
            "foreign_net": foreign,
            "trust_net": trust,
            "dealer_net": dealer,
            "total_net": total,
        }

    if result:
        source_stat(
            source
        )["records"] += len(result)
        source_stat(
            source
        )["dates_with_records"] += 1
    else:
        source_stat(
            source
        )["empty"] += 1

    return result


# ============================================================
# TPEx INSTITUTIONAL
# ============================================================

def tpex_institutional(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    source = "TPEX_3INSTI"

    params = {
        "l": "zh-tw",
        "o": "json",
        "se": "EW",
        "t": "D",
        "d": roc_date(gdate),
        "s": "0,asc",
    }

    data = get_json(
        source,
        TPEX_INSTITUTIONAL_URL,
        params,
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    rows = data.get(
        "aaData"
    )

    if not isinstance(
        rows,
        list,
    ):
        source_stat(
            source
        )["schema_error"] += 1

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        if len(row) < 14:
            continue

        code = clean_symbol(
            row[0]
        )

        if not code:
            continue

        """
        TPEx 3itrade_hedge_result:

        row layout contains buy / sell / net
        triples for:
            外資及陸資
            外資自營商
            投信
            自營商
            自營商避險

        官方資料中的三大法人核心欄位：
            外資合計
            投信
            自營商合計

        依官方目前報表結構：
            外資合計淨額 -> index 9
            投信淨額     -> index 12
            自營商合計   -> index 21

        若欄位數不足，不猜位置、不造資料。
        """

        foreign = (
            safe_float(row[9])
            if len(row) > 9
            else None
        )

        trust = (
            safe_float(row[12])
            if len(row) > 12
            else None
        )

        dealer = (
            safe_float(row[21])
            if len(row) > 21
            else None
        )

        if (
            foreign is None
            and trust is None
            and dealer is None
        ):
            continue

        total = None

        if all(
            value is not None
            for value in (
                foreign,
                trust,
                dealer,
            )
        ):
            total = (
                foreign
                + trust
                + dealer
            )

        result[code] = {
            "date": gdate,
            "foreign_net": foreign,
            "trust_net": trust,
            "dealer_net": dealer,
            "total_net": total,
        }

    if result:
        source_stat(
            source
        )["records"] += len(result)
        source_stat(
            source
        )["dates_with_records"] += 1
    else:
        source_stat(
            source
        )["empty"] += 1

    return result


# ============================================================
# TWSE MARGIN
# ============================================================

def twse_margin(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    source = "TWSE_MARGIN"

    data = get_json(
        source,
        TWSE_MARGIN_URL,
        {
            "response": "json",
            "date": gdate.replace(
                "-",
                "",
            ),
            "selectType": "ALL",
        },
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if not isinstance(
        fields,
        list,
    ) or not isinstance(
        rows,
        list,
    ):
        source_stat(
            source
        )["schema_error"] += 1

        return result

    code_index = find_field(
        fields,
        exact=[
            "股票代號",
            "證券代號",
            "代號",
        ],
    )

    margin_index = find_field(
        fields,
        exact=[
            "融資餘額",
        ],
        contains=[
            "融資",
            "今日餘額",
        ],
    )

    short_index = find_field(
        fields,
        exact=[
            "融券餘額",
        ],
        contains=[
            "融券",
            "今日餘額",
        ],
    )

    offset_index = find_field(
        fields,
        exact=[
            "資券相抵",
            "資券互抵",
        ],
        contains=[
            "資券相抵",
            "資券互抵",
        ],
    )

    if code_index is None:
        source_stat(
            source
        )["schema_error"] += 1

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        code = clean_symbol(
            row_get(
                row,
                code_index,
            )
        )

        if not code:
            continue

        result[code] = {
            "date": gdate,
            "margin_balance":
                row_number(
                    row,
                    margin_index,
                ),
            "short_balance":
                row_number(
                    row,
                    short_index,
                ),
            "offset_volume":
                row_number(
                    row,
                    offset_index,
                ),
        }

    if result:
        source_stat(
            source
        )["records"] += len(result)
        source_stat(
            source
        )["dates_with_records"] += 1
    else:
        source_stat(
            source
        )["empty"] += 1

    return result


# ============================================================
# TPEx MARGIN
# ============================================================

def tpex_margin(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    source = "TPEX_MARGIN"

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": roc_date(gdate),
        "s": "0,asc",
    }

    data = get_json(
        source,
        TPEX_MARGIN_URL,
        params,
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    rows = data.get(
        "aaData"
    )

    if not isinstance(
        rows,
        list,
    ):
        source_stat(
            source
        )["schema_error"] += 1

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        if len(row) < 18:
            continue

        code = clean_symbol(
            row[0]
        )

        if not code:
            continue

        """
        TPEx STKDMARGIN / margin_bal_result
        固定欄位：

        0   證券代號
        1   前日融資餘額
        2   融資買進
        3   融資賣出
        4   現金償還
        5   本日融資餘額
        6   融資限額
        7   融資使用率
        8   前日融券餘額
        9   融券賣出
        10  融券買進
        11  融券償還
        12  本日融券餘額
        13  融券限額
        14  融券使用率
        15  本日融資餘額屬證金部分
        16  融券屬證金部分
        17  資券相抵

        資料單位依官方規格為千股。
        """

        margin_balance = (
            safe_float(row[5])
            if len(row) > 5
            else None
        )

        short_balance = (
            safe_float(row[12])
            if len(row) > 12
            else None
        )

        offset_volume = (
            safe_float(row[17])
            if len(row) > 17
            else None
        )

        result[code] = {
            "date": gdate,
            "margin_balance":
                margin_balance,
            "short_balance":
                short_balance,
            "offset_volume":
                offset_volume,
        }

    if result:
        source_stat(
            source
        )["records"] += len(result)
        source_stat(
            source
        )["dates_with_records"] += 1
    else:
        source_stat(
            source
        )["empty"] += 1

    return result


# ============================================================
# TWSE VOLUME
# ============================================================

def twse_volume(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    source = "TWSE_VOLUME"

    data = get_json(
        source,
        TWSE_VOLUME_URL,
        {
            "response": "json",
            "date": gdate.replace(
                "-",
                "",
            ),
            "type": "ALL",
        },
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    tables = data.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):
        source_stat(
            source
        )["schema_error"] += 1

        return result

    for table in tables:

        if not isinstance(
            table,
            dict,
        ):
            continue

        fields = table.get(
            "fields"
        )

        rows = table.get(
            "data"
        )

        if not isinstance(
            fields,
            list,
        ) or not isinstance(
            rows,
            list,
        ):
            continue

        code_index = find_field(
            fields,
            exact=[
                "證券代號",
            ],
        )

        volume_index = find_field(
            fields,
            exact=[
                "成交股數",
            ],
            contains=[
                "成交股數",
            ],
        )

        if (
            code_index is None
            or volume_index is None
        ):
            continue

        for row in rows:

            if not isinstance(
                row,
                list,
            ):
                continue

            code = clean_symbol(
                row_get(
                    row,
                    code_index,
                )
            )

            if not code:
                continue

            volume = row_number(
                row,
                volume_index,
            )

            if volume is None:
                continue

            result[code] = {
                "date": gdate,
                "volume": volume,
            }

    if result:
        source_stat(
            source
        )["records"] += len(result)
        source_stat(
            source
        )["dates_with_records"] += 1
    else:
        source_stat(
            source
        )["empty"] += 1

    return result


# ============================================================
# TPEx VOLUME
# ============================================================

def tpex_volume(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    source = "TPEX_VOLUME"

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": roc_date(gdate),
        "s": "0,asc",
    }

    data = get_json(
        source,
        TPEX_VOLUME_URL,
        params,
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    rows = data.get(
        "aaData"
    )

    if not isinstance(
        rows,
        list,
    ):
        source_stat(
            source
        )["schema_error"] += 1

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        if not row:
            continue

        code = clean_symbol(
            row[0]
        )

        if not code:
            continue

        """
        TPEx daily close quote:
        row[0] = code
        row[1] = name
        row[2] = open
        row[3] = high
        row[4] = low
        row[5] = close
        row[6] = avg
        row[7] = change
        row[8] = change %
        row[9] = volume
        ...
        """

        volume = None

        # First attempt: known official position.
        if len(row) > 9:
            volume = safe_float(
                row[9]
            )

        # Fallback: find first plausible
        # integer-like volume after price fields.
        if volume is None:

            for value in row[5:15]:

                candidate = safe_float(
                    value
                )

                if (
                    candidate is not None
                    and candidate >= 0
                    and candidate.is_integer()
                ):
                    volume = candidate
                    break

        if volume is None:
            continue

        result[code] = {
            "date": gdate,
            "volume": volume,
        }

    if result:
        source_stat(
            source
        )["records"] += len(result)
        source_stat(
            source
        )["dates_with_records"] += 1
    else:
        source_stat(
            source
        )["empty"] += 1

    return result


# ============================================================
# TWSE DAY TRADE
# ============================================================

def twse_day_trade(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    source = "TWSE_DAYTRADE"

    data = get_json(
        source,
        TWSE_DAYTRADE_URL,
        {
            "response": "json",
            "date": gdate.replace(
                "-",
                "",
            ),
            "selectType": "ALL",
        },
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if not isinstance(
        fields,
        list,
    ) or not isinstance(
        rows,
        list,
    ):
        source_stat(
            source
        )["schema_error"] += 1

        return result

    code_index = find_field(
        fields,
        exact=[
            "證券代號",
            "股票代號",
            "標的代碼",
        ],
    )

    day_volume_index = find_field(
        fields,
        contains=[
            "當日沖銷交易成交股數",
            "當沖成交股數",
            "當沖股數",
        ],
    )

    if code_index is None:
        source_stat(
            source
        )["schema_error"] += 1

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        code = clean_symbol(
            row_get(
                row,
                code_index,
            )
        )

        if not code:
            continue

        day_volume = row_number(
            row,
            day_volume_index,
        )

        result[code] = {
            "date": gdate,
            "day_trade_volume":
                day_volume,
        }

    if result:
        source_stat(
            source
        )["records"] += len(result)
        source_stat(
            source
        )["dates_with_records"] += 1
    else:
        source_stat(
            source
        )["empty"] += 1

    return result


# ============================================================
# TPEx DAY TRADE
# ============================================================

def tpex_day_trade(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    source = "TPEX_DAYTRADE"

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for url in TPEX_DAYTRADE_URLS:

        data = get_json(
            source,
            url,
        )

        if isinstance(
            data,
            dict,
        ):
            rows = data.get(
                "data"
            )

        elif isinstance(
            data,
            list,
        ):
            rows = data

        else:
            rows = None

        if not isinstance(
            rows,
            list,
        ):
            continue

        for item in rows:

            if not isinstance(
                item,
                dict,
            ):
                continue

            code = None

            for key in (
                "SecuritiesCompanyCode",
                "Code",
                "代號",
                "證券代號",
                "股票代號",
            ):

                if item.get(
                    key
                ) not in (
                    None,
                    "",
                ):
                    code = clean_symbol(
                        item[key]
                    )
                    break

            if not code:
                continue

            day_volume = None
            rate = None

            for key, value in item.items():

                field = normalize_field(
                    key
                )

                lower = field.lower()

                if (
                    "當日沖銷交易成交股數"
                    in field
                    or "當沖成交股數"
                    in field
                    or "intradaytradingvolume"
                    in lower
                ):
                    day_volume = safe_float(
                        value
                    )

                elif (
                    "當沖率"
                    in field
                    or "daytraderate"
                    in lower
                    or "intradaytradingrate"
                    in lower
                ):
                    rate = safe_float(
                        value
                    )

            if (
                day_volume is None
                and rate is None
            ):
                continue

            result[code] = {
                "date": gdate,
                "day_trade_volume":
                    day_volume,
                "day_trade_rate":
                    rate,
            }

    if result:
        source_stat(
            source
        )["records"] += len(result)
        source_stat(
            source
        )["dates_with_records"] += 1
    else:
        source_stat(
            source
        )["empty"] += 1

    return result


# ============================================================
# FETCH ONE DAY
# ============================================================

def fetch_day(
    gdate: str,
    market: str,
) -> Dict[str, Dict[str, Any]]:

    if market == "TWSE":

        institutional = twse_institutional(
            gdate
        )

        margin = twse_margin(
            gdate
        )

        volume = twse_volume(
            gdate
        )

        day_trade = twse_day_trade(
            gdate
        )

    elif market == "TPEX":

        institutional = tpex_institutional(
            gdate
        )

        margin = tpex_margin(
            gdate
        )

        volume = tpex_volume(
            gdate
        )

        day_trade = tpex_day_trade(
            gdate
        )

    else:

        raise ValueError(
            f"Unsupported market: {market}"
        )

    codes = (
        set(institutional)
        | set(margin)
        | set(volume)
        | set(day_trade)
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for code in codes:

        record = {
            "date": gdate,
            "market": market,
            "institutional":
                institutional.get(code),
            "margin":
                margin.get(code),
            "volume":
                volume.get(code),
            "day_trade":
                day_trade.get(code),
        }

        # TWSE 當沖率由官方當沖成交股數 /
        # 官方成交股數計算。
        if market == "TWSE":

            volume_record = record.get(
                "volume"
            )

            day_record = record.get(
                "day_trade"
            )

            if (
                isinstance(
                    volume_record,
                    dict,
                )
                and isinstance(
                    day_record,
                    dict,
                )
            ):

                total_volume = (
                    volume_record.get(
                        "volume"
                    )
                )

                day_volume = (
                    day_record.get(
                        "day_trade_volume"
                    )
                )

                if (
                    total_volume is not None
                    and total_volume > 0
                    and day_volume is not None
                ):

                    day_record[
                        "day_trade_rate"
                    ] = (
                        day_volume
                        / total_volume
                        * 100.0
                    )

        result[code] = record

    return result


# ============================================================
# OFFICIAL DATE DISCOVERY
# ============================================================

def discover_dates(
    market: str,
    end_date: date,
    required: int = REQUIRED_20D,
) -> List[str]:

    found: List[str] = []

    current = end_date
    checked = 0

    while (
        checked < LOOKBACK_CALENDAR_DAYS
        and len(found) < required
    ):

        gdate = current.isoformat()

        print(
            f"      {market} "
            f"date probe {gdate}"
        )

        if market == "TWSE":

            data = twse_institutional(
                gdate
            )

        elif market == "TPEX":

            data = tpex_institutional(
                gdate
            )

        else:
            raise ValueError(
                market
            )

        if data:

            found.append(
                gdate
            )

        current -= timedelta(
            days=1
        )

        checked += 1

    return found


# ============================================================
# HISTORY
# ============================================================

def fetch_market_history(
    market: str,
    dates: List[str],
) -> Dict[
    str,
    Dict[str, Dict[str, Any]]
]:

    history: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ] = defaultdict(dict)

    for index, gdate in enumerate(
        dates,
        start=1,
    ):

        print(
            f"    [{market}] "
            f"history "
            f"{index}/{len(dates)} "
            f"{gdate}"
        )

        daily = fetch_day(
            gdate,
            market,
        )

        for code, record in (
            daily.items()
        ):

            history[
                code
            ][gdate] = record

    return history


# ============================================================
# INSTITUTIONAL SUM
# ============================================================

def sum_institutional(
    records: List[
        Dict[str, Any]
    ],
) -> Dict[str, Optional[float]]:

    result: Dict[
        str,
        Optional[float]
    ] = {
        "foreign_net": None,
        "trust_net": None,
        "dealer_net": None,
        "total_net": None,
    }

    if not records:
        return result

    for field in (
        "foreign_net",
        "trust_net",
        "dealer_net",
    ):

        values = [
            record.get(field)
            for record in records
        ]

        if all(
            value is not None
            for value in values
        ):

            result[field] = sum(
                values
            )

    if all(
        result[field] is not None
        for field in (
            "foreign_net",
            "trust_net",
            "dealer_net",
        )
    ):

        result["total_net"] = (
            result["foreign_net"]
            + result["trust_net"]
            + result["dealer_net"]
        )

    return result


# ============================================================
# BUILD STOCK RECORD
# ============================================================

def build_stock_record(
    code: str,
    meta: Dict[str, Any],
    history: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ],
    latest_date: str,
) -> Dict[str, Any]:

    records = history.get(
        code,
        {}
    )

    available_dates = sorted(
        records.keys(),
        reverse=True,
    )

    dates_5d = available_dates[
        :REQUIRED_5D
    ]

    dates_20d = available_dates[
        :REQUIRED_20D
    ]

    latest = records.get(
        latest_date
    )

    latest_institutional = (
        latest.get(
            "institutional"
        )
        if isinstance(
            latest,
            dict,
        )
        else None
    )

    latest_margin = (
        latest.get(
            "margin"
        )
        if isinstance(
            latest,
            dict,
        )
        else None
    )

    latest_volume = (
        latest.get(
            "volume"
        )
        if isinstance(
            latest,
            dict,
        )
        else None
    )

    latest_day_trade = (
        latest.get(
            "day_trade"
        )
        if isinstance(
            latest,
            dict,
        )
        else None
    )

    institutional_records_5d = []

    for gdate in dates_5d:

        record = records.get(
            gdate
        )

        if not isinstance(
            record,
            dict,
        ):
            continue

        institutional = record.get(
            "institutional"
        )

        if isinstance(
            institutional,
            dict,
        ):
            institutional_records_5d.append(
                institutional
            )

    institutional_records_20d = []

    for gdate in dates_20d:

        record = records.get(
            gdate
        )

        if not isinstance(
            record,
            dict,
        ):
            continue

        institutional = record.get(
            "institutional"
        )

        if isinstance(
            institutional,
            dict,
        ):
            institutional_records_20d.append(
                institutional
            )

    institutional_5d = None

    if (
        len(
            institutional_records_5d
        )
        == REQUIRED_5D
    ):

        institutional_5d = (
            sum_institutional(
                institutional_records_5d
            )
        )

    institutional_20d = None

    if (
        len(
            institutional_records_20d
        )
        == REQUIRED_20D
    ):

        institutional_20d = (
            sum_institutional(
                institutional_records_20d
            )
        )

    latest_total = None

    if isinstance(
        latest_institutional,
        dict,
    ):

        values = [
            latest_institutional.get(
                "foreign_net"
            ),
            latest_institutional.get(
                "trust_net"
            ),
            latest_institutional.get(
                "dealer_net"
            ),
        ]

        if all(
            value is not None
            for value in values
        ):

            latest_total = sum(
                values
            )

    volume = None

    if isinstance(
        latest_volume,
        dict,
    ):
        volume = latest_volume.get(
            "volume"
        )

    day_trade_rate = None
    day_trade_volume = None

    if isinstance(
        latest_day_trade,
        dict,
    ):

        day_trade_rate = (
            latest_day_trade.get(
                "day_trade_rate"
            )
        )

        day_trade_volume = (
            latest_day_trade.get(
                "day_trade_volume"
            )
        )

    current_institutional = (
        isinstance(
            latest_institutional,
            dict,
        )
        and any(
            latest_institutional.get(
                key
            ) is not None
            for key in (
                "foreign_net",
                "trust_net",
                "dealer_net",
            )
        )
    )

    margin_ok = (
        isinstance(
            latest_margin,
            dict,
        )
        and any(
            latest_margin.get(
                key
            ) is not None
            for key in (
                "margin_balance",
                "short_balance",
                "offset_volume",
            )
        )
    )

    volume_ok = volume is not None

    day_trade_ok = (
        day_trade_rate is not None
        or day_trade_volume is not None
    )

    valid = (
        current_institutional
        or margin_ok
        or volume_ok
        or day_trade_ok
    )

    return {
        "symbol": code,
        "full_symbol":
            meta.get("full_symbol"),
        "name":
            meta.get("name"),
        "market":
            meta.get("market"),
        "type":
            meta.get("type"),
        "instrument_type":
            meta.get("instrument_type"),
        "status":
            "active",
        "listed_date":
            meta.get("listed_date"),

        "latest_trade_date":
            latest_date,

        "institutional": {
            "date":
                latest_date,

            "foreign_net":
                (
                    latest_institutional.get(
                        "foreign_net"
                    )
                    if isinstance(
                        latest_institutional,
                        dict,
                    )
                    else None
                ),

            "trust_net":
                (
                    latest_institutional.get(
                        "trust_net"
                    )
                    if isinstance(
                        latest_institutional,
                        dict,
                    )
                    else None
                ),

            "dealer_net":
                (
                    latest_institutional.get(
                        "dealer_net"
                    )
                    if isinstance(
                        latest_institutional,
                        dict,
                    )
                    else None
                ),

            "total_net":
                latest_total,

            "5d": {
                "trading_days":
                    len(
                        institutional_records_5d
                    ),

                "dates":
                    dates_5d,

                **(
                    institutional_5d
                    if institutional_5d is not None
                    else {
                        "foreign_net": None,
                        "trust_net": None,
                        "dealer_net": None,
                        "total_net": None,
                    }
                ),
            },

            "20d": {
                "trading_days":
                    len(
                        institutional_records_20d
                    ),

                "dates":
                    dates_20d,

                **(
                    institutional_20d
                    if institutional_20d is not None
                    else {
                        "foreign_net": None,
                        "trust_net": None,
                        "dealer_net": None,
                        "total_net": None,
                    }
                ),
            },
        },

        "margin": {
            "date":
                latest_date,

            "margin_balance":
                (
                    latest_margin.get(
                        "margin_balance"
                    )
                    if isinstance(
                        latest_margin,
                        dict,
                    )
                    else None
                ),

            "short_balance":
                (
                    latest_margin.get(
                        "short_balance"
                    )
                    if isinstance(
                        latest_margin,
                        dict,
                    )
                    else None
                ),

            "offset_volume":
                (
                    latest_margin.get(
                        "offset_volume"
                    )
                    if isinstance(
                        latest_margin,
                        dict,
                    )
                    else None
                ),
        },

        "trading": {
            "date":
                latest_date,

            "volume":
                volume,

            "day_trade_rate":
                day_trade_rate,

            "day_trade_volume":
                day_trade_volume,
        },

        "data_quality": {
            "current_institutional":
                current_institutional,

            "institutional_5d":
                len(
                    institutional_records_5d
                ) == REQUIRED_5D,

            "institutional_20d":
                len(
                    institutional_records_20d
                ) == REQUIRED_20D,

            "margin":
                margin_ok,

            "volume":
                volume_ok,

            "day_trade":
                day_trade_ok,

            "valid":
                valid,
        },
    }


# ============================================================
# VALIDATE OUTPUT
# ============================================================

def validate_output(
    payload: Dict[str, Any],
    universe: Dict[str, Dict[str, Any]],
) -> None:

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "chip root must be dict"
        )

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "chip.stocks must be dict"
        )

    universe_codes = set(
        universe.keys()
    )

    chip_codes = set(
        stocks.keys()
    )

    missing = (
        universe_codes
        - chip_codes
    )

    extra = (
        chip_codes
        - universe_codes
    )

    if missing:
        raise RuntimeError(
            "chip missing Universe "
            f"symbols: "
            f"{sorted(missing)[:20]}"
        )

    if extra:
        raise RuntimeError(
            "chip contains symbols "
            "outside Universe: "
            f"{sorted(extra)[:20]}"
        )

    for code, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            raise RuntimeError(
                f"{code}: item must be dict"
            )

        if item.get(
            "symbol"
        ) != code:
            raise RuntimeError(
                f"{code}: symbol mismatch"
            )

        if item.get(
            "status"
        ) != "active":
            raise RuntimeError(
                f"{code}: inactive"
            )

        universe_item = universe[
            code
        ]

        if item.get(
            "market"
        ) != universe_item.get(
            "market"
        ):
            raise RuntimeError(
                f"{code}: market mismatch"
            )

        if item.get(
            "type"
        ) != universe_item.get(
            "type"
        ):
            raise RuntimeError(
                f"{code}: type mismatch"
            )

        institutional = item.get(
            "institutional"
        )

        if not isinstance(
            institutional,
            dict,
        ):
            raise RuntimeError(
                f"{code}: institutional "
                "must be dict"
            )

        d5 = institutional.get(
            "5d"
        )

        d20 = institutional.get(
            "20d"
        )

        if not isinstance(
            d5,
            dict,
        ) or not isinstance(
            d20,
            dict,
        ):
            raise RuntimeError(
                f"{code}: invalid "
                "institutional history"
            )

        dates5 = d5.get(
            "dates",
            [],
        )

        dates20 = d20.get(
            "dates",
            [],
        )

        if not isinstance(
            dates5,
            list,
        ) or not isinstance(
            dates20,
            list,
        ):
            raise RuntimeError(
                f"{code}: dates must list"
            )

        if len(dates5) > REQUIRED_5D:
            raise RuntimeError(
                f"{code}: 5D overflow"
            )

        if len(dates20) > REQUIRED_20D:
            raise RuntimeError(
                f"{code}: 20D overflow"
            )

        if len(
            set(dates5)
        ) != len(dates5):
            raise RuntimeError(
                f"{code}: duplicate 5D"
            )

        if len(
            set(dates20)
        ) != len(dates20):
            raise RuntimeError(
                f"{code}: duplicate 20D"
            )

        if len(dates5) > len(dates20):
            raise RuntimeError(
                f"{code}: 5D > 20D"
            )


# ============================================================
# SOURCE HEALTH
# ============================================================

def validate_source_health(
    universe: Dict[str, Dict[str, Any]],
    dates_by_market: Dict[str, List[str]],
    history_by_market: Dict[
        str,
        Dict[str, Dict[str, Dict[str, Any]]]
    ],
) -> None:

    """
    整體官方資料健康檢查。

    不是要求每一檔股票都有資料。

    而是要求：
        只要 Universe 有某市場，
        該市場至少要有官方實際交易日，
        且至少要有有效官方紀錄。

    單一股票沒有資料：
        允許。

    整個市場完全沒有資料：
        FAIL。
    """

    for market in (
        "TWSE",
        "TPEX",
    ):

        market_codes = [
            code
            for code, item
            in universe.items()
            if item.get(
                "market"
            ) == market
        ]

        if not market_codes:
            continue

        dates = dates_by_market.get(
            market,
            [],
        )

        if not dates:
            raise RuntimeError(
                f"{market}: "
                "no official trading dates"
            )

        history = history_by_market.get(
            market,
            {},
        )

        valid_codes = 0

        for code in market_codes:

            records = history.get(
                code,
                {},
            )

            if not records:
                continue

            if any(
                isinstance(
                    record,
                    dict,
                )
                and (
                    record.get(
                        "institutional"
                    ) is not None
                    or record.get(
                        "margin"
                    ) is not None
                    or record.get(
                        "volume"
                    ) is not None
                )
                for record in records.values()
            ):
                valid_codes += 1

        if valid_codes < MIN_MARKET_RECORDS:
            raise RuntimeError(
                f"{market}: official "
                "source returned no "
                "usable records"
            )


# ============================================================
# BUILD
# ============================================================

def build() -> Dict[str, Any]:

    universe = load_universe()

    print("=" * 72)
    print(
        f"FETCH CHIP {VERSION}"
    )
    print("=" * 72)

    print(
        f"Active Universe: "
        f"{len(universe)}"
    )

    twse_codes = [
        code
        for code, item
        in universe.items()
        if item.get(
            "market"
        ) == "TWSE"
    ]

    tpex_codes = [
        code
        for code, item
        in universe.items()
        if item.get(
            "market"
        ) == "TPEX"
    ]

    print(
        f"TWSE: {len(twse_codes)}"
    )

    print(
        f"TPEX: {len(tpex_codes)}"
    )

    # --------------------------------------------------------
    # DATE DISCOVERY
    # --------------------------------------------------------

    print()
    print(
        "STEP 1 - OFFICIAL "
        "TRADING DATE DISCOVERY"
    )

    today = taiwan_today()

    twse_dates: List[str] = []
    tpex_dates: List[str] = []

    if twse_codes:

        twse_dates = discover_dates(
            "TWSE",
            today,
            REQUIRED_20D,
        )

    if tpex_codes:

        tpex_dates = discover_dates(
            "TPEX",
            today,
            REQUIRED_20D,
        )

    if twse_codes and not twse_dates:
        raise RuntimeError(
            "TWSE has no official "
            "trading dates"
        )

    if tpex_codes and not tpex_dates:
        raise RuntimeError(
            "TPEX has no official "
            "trading dates"
        )

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    print()
    print(
        "STEP 2 - FETCH OFFICIAL "
        "HISTORY"
    )

    twse_history: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ] = {}

    tpex_history: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ] = {}

    if twse_dates:

        twse_history = (
            fetch_market_history(
                "TWSE",
                twse_dates,
            )
        )

    if tpex_dates:

        tpex_history = (
            fetch_market_history(
                "TPEX",
                tpex_dates,
            )
        )

    history_by_market = {
        "TWSE": twse_history,
        "TPEX": tpex_history,
    }

    dates_by_market = {
        "TWSE": twse_dates,
        "TPEX": tpex_dates,
    }

    # --------------------------------------------------------
    # SOURCE HEALTH
    # --------------------------------------------------------

    print()
    print(
        "STEP 3 - SOURCE HEALTH"
    )

    validate_source_health(
        universe,
        dates_by_market,
        history_by_market,
    )

    # --------------------------------------------------------
    # LATEST TRADE DATE
    # --------------------------------------------------------

    latest_dates = []

    if twse_dates:
        latest_dates.append(
            twse_dates[0]
        )

    if tpex_dates:
        latest_dates.append(
            tpex_dates[0]
        )

    if not latest_dates:
        raise RuntimeError(
            "No official trading date"
        )

    latest_trade_date = max(
        latest_dates
    )

    # --------------------------------------------------------
    # BUILD STOCKS
    # --------------------------------------------------------

    print()
    print(
        "STEP 4 - BUILD CHIP"
    )

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    valid_count = 0

    for code, meta in universe.items():

        market = meta.get(
            "market"
        )

        if market == "TWSE":

            history = twse_history
            market_dates = twse_dates

        else:

            history = tpex_history
            market_dates = tpex_dates

        if market_dates:

            latest_for_market = (
                market_dates[0]
            )

            stock = build_stock_record(
                code,
                meta,
                history,
                latest_for_market,
            )

        else:

            stock = build_stock_record(
                code,
                meta,
                {},
                latest_trade_date,
            )

        if stock[
            "data_quality"
        ].get(
            "valid"
        ):
            valid_count += 1

        stocks[code] = stock

    # --------------------------------------------------------
    # SOURCE STATUS
    # --------------------------------------------------------

    source_status: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for name, stats in SOURCE_STATS.items():

        source_status[name] = dict(
            stats
        )

        requests_count = stats.get(
            "requests",
            0,
        )

        successful = stats.get(
            "success",
            0,
        )

        if stats.get(
            "request_error",
            0,
        ) > 0:
            status = "request_error"

        elif stats.get(
            "json_error",
            0,
        ) > 0:
            status = "json_error"

        elif stats.get(
            "schema_error",
            0,
        ) > 0:
            status = "schema_error"

        elif stats.get(
            "dates_with_records",
            0,
        ) == 0:
            status = "empty"

        elif (
            successful > 0
            and stats.get(
                "records",
                0,
            ) > 0
        ):
            status = "ok"

        else:
            status = "unknown"

        source_status[name][
            "status"
        ] = status

        source_status[name][
            "request_success_rate"
        ] = (
            successful / requests_count
            if requests_count
            else 0.0
        )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    payload = {
        "version":
            VERSION,

        "generated_at":
            now_iso(),

        "latest_trade_date":
            latest_trade_date,

        "universe_count":
            len(universe),

        "stock_count":
            sum(
                1
                for item
                in universe.values()
                if item.get(
                    "type"
                ) == "STOCK"
            ),

        "etf_count":
            sum(
                1
                for item
                in universe.values()
                if item.get(
                    "type"
                ) == "ETF"
            ),

        "valid_count":
            valid_count,

        "invalid_count":
            len(universe)
            - valid_count,

        "source_policy":
            "official_only",

        "universe_source":
            "Data/universe.json",

        "sources": {
            "TWSE": {
                "institutional":
                    TWSE_T86_URL,

                "margin":
                    TWSE_MARGIN_URL,

                "volume":
                    TWSE_VOLUME_URL,

                "day_trade":
                    TWSE_DAYTRADE_URL,
            },

            "TPEX": {
                "institutional":
                    TPEX_INSTITUTIONAL_URL,

                "margin":
                    TPEX_MARGIN_URL,

                "volume":
                    TPEX_VOLUME_URL,

                "day_trade":
                    TPEX_DAYTRADE_URLS[0],
            },
        },

        "source_status":
            source_status,

        "contract": {
            "universe":
                "Data/universe.json",

            "active_only":
                True,

            "universe_key_equals_symbol":
                True,

            "allowed_markets": [
                "TWSE",
                "TPEX",
            ],

            "allowed_types": [
                "STOCK",
                "ETF",
            ],

            "third_party_data":
                False,

            "zero_fallback":
                False,

            "inactive_symbols_excluded":
                True,

            "historical_dates":
                True,

            "official_only":
                True,
        },

        "stocks":
            stocks,
    }

    # --------------------------------------------------------
    # FINAL VALIDATION
    # --------------------------------------------------------

    print()
    print(
        "STEP 5 - FINAL VALIDATION"
    )

    validate_output(
        payload,
        universe,
    )

    forbidden = []

    for code in stocks:

        if code not in universe:
            forbidden.append(code)
            continue

        if universe[code].get(
            "status"
        ) != "active":
            forbidden.append(code)

    if forbidden:
        raise RuntimeError(
            "Forbidden symbols: "
            f"{forbidden[:20]}"
        )

    print(
        "✓ Universe / chip "
        "contract PASS"
    )

    print(
        f"✓ Active Universe: "
        f"{len(universe)}"
    )

    print(
        f"✓ Chip records: "
        f"{len(stocks)}"
    )

    print(
        f"✓ Valid records: "
        f"{valid_count}"
    )

    print(
        f"✓ Invalid records: "
        f"{len(universe) - valid_count}"
    )

    print(
        f"✓ Latest trade date: "
        f"{latest_trade_date}"
    )

    return payload


# ============================================================
# PRINT SOURCE DIAGNOSTICS
# ============================================================

def print_source_diagnostics(
    payload: Optional[Dict[str, Any]] = None,
) -> None:

    print()
    print("=" * 72)
    print(
        "OFFICIAL SOURCE DIAGNOSTICS"
    )
    print("=" * 72)

    for name in sorted(
        SOURCE_STATS
    ):

        stats = SOURCE_STATS[name]

        print()
        print(
            f"[{name}]"
        )

        print(
            f"  requests: "
            f"{stats.get('requests', 0)}"
        )

        print(
            f"  success: "
            f"{stats.get('success', 0)}"
        )

        print(
            f"  request_error: "
            f"{stats.get('request_error', 0)}"
        )

        print(
            f"  json_error: "
            f"{stats.get('json_error', 0)}"
        )

        print(
            f"  schema_error: "
            f"{stats.get('schema_error', 0)}"
        )

        print(
            f"  empty: "
            f"{stats.get('empty', 0)}"
        )

        print(
            f"  records: "
            f"{stats.get('records', 0)}"
        )

        print(
            f"  dates_with_records: "
            f"{stats.get('dates_with_records', 0)}"
        )

        if stats.get(
            "last_error"
        ):
            print(
                f"  last_error: "
                f"{stats['last_error']}"
            )

    if payload is not None:

        print()
        print(
            f"Overall valid_count: "
            f"{payload.get('valid_count')}"
        )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    print("=" * 72)
    print(
        "TAIWAN STOCK AI SCANNER"
    )
    print(
        "fetch_chip.py"
    )
    print(
        VERSION
    )
    print("=" * 72)

    try:

        payload = build()

        print_source_diagnostics(
            payload
        )

        # ----------------------------------------------------
        # CRITICAL SAFETY:
        # 若整批完全沒有有效官方資料，
        # 不覆蓋舊 chip.json。
        # ----------------------------------------------------

        if payload.get(
            "valid_count",
            0,
        ) <= 0:

            print()
            print(
                "ERROR: No valid "
                "official chip data."
            )

            print(
                "Existing chip.json "
                "will NOT be overwritten."
            )

            return 1

        # ----------------------------------------------------
        # READ-BACK VALIDATION BEFORE WRITE
        # ----------------------------------------------------

        atomic_write_json(
            OUTPUT_FILE,
            payload,
        )

        if not OUTPUT_FILE.exists():
            raise RuntimeError(
                "chip.json was not created"
            )

        with OUTPUT_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:

            written = json.load(file)

        universe = load_universe()

        validate_output(
            written,
            universe,
        )

        print()
        print("=" * 72)
        print(
            "CHIP WRITE PASS"
        )
        print("=" * 72)

        print(
            f"Output: "
            f"{OUTPUT_FILE}"
        )

        print(
            f"Universe: "
            f"{written['universe_count']}"
        )

        print(
            f"Valid: "
            f"{written['valid_count']}"
        )

        print(
            f"Invalid: "
            f"{written['invalid_count']}"
        )

        print(
            f"Latest trade date: "
            f"{written['latest_trade_date']}"
        )

        return 0

    except KeyboardInterrupt:

        print(
            "Interrupted."
        )

        return 130

    except Exception as exc:

        print()
        print("=" * 72)
        print(
            "CHIP FETCH FAILED"
        )
        print("=" * 72)

        print(
            f"ERROR: {exc}"
        )

        print_source_diagnostics()

        print()
        print(
            "Existing chip.json "
            "was NOT intentionally replaced."
        )

        return 1


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
