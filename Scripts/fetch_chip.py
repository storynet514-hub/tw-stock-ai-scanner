#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_chip.py
============================================================

NEW ARCHITECTURE
============================================================

唯一 Universe：
    Data/universe.json

資料流程：
    universe.json
        ↓
    status == active
        ↓
    market == TWSE / TPEX
        ↓
    官方交易資料
        ↓
    實際交易日判定
        ↓
    三大法人
        ↓
    5D / 20D
        ↓
    融資 / 融券
        ↓
    資券相抵
        ↓
    成交量
        ↓
    當沖
        ↓
    Data/chip.json

============================================================
核心契約
============================================================

1. Data/universe.json 是唯一 Universe 來源
2. universe.json root 必須是 dict
3. universe.json stocks 必須是 dict
4. stocks key 必須等於 item.symbol
5. 只處理 status == "active"
6. market 只能是 TWSE / TPEX
7. type 只能是 STOCK / ETF
8. 不從成交資料建立 Universe
9. 不從 API 發現額外股票
10. 不從 Yahoo / yfinance / FinMind / CMoney 取得籌碼
11. TWSE / TPEX 完全依 Universe.market 分流
12. 不依股票代碼尾碼猜市場
13. 不使用 0 代替缺失資料
14. 缺失資料使用 None
15. 缺失資料必須標記 data_quality
16. 5D 必須使用 5 個不同的實際交易日
17. 20D 必須使用 20 個不同的實際交易日
18. 不使用自然日冒充交易日
19. 已終止 Universe 標的不得進入 chip.json
20. Universe 外標的不得進入 chip.json
21. 單一標的失敗不得讓整批中止
22. 如果完全沒有取得有效官方資料，不覆蓋既有 chip.json
23. 寫檔使用 atomic write
24. 寫檔前執行 Universe / chip 結構驗證

============================================================
官方來源
============================================================

TWSE：
    T86
    MI_INDEX
    marginTWTAS / MI_MARGN
    TWTB4U

TPEx：
    官方 OpenAPI / 官方交易資料

注意：
    官方資料端點如果當日沒有資料，
    絕對不使用第三方資料補洞。

============================================================
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

VERSION = "UNIVERSE-CHIP-2.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30
REQUEST_RETRY = 3
REQUEST_SLEEP = 0.20

LOOKBACK_CALENDAR_DAYS = 60

REQUIRED_5D = 5
REQUIRED_20D = 20


USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


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
            "*/*"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
)


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

def safe_float(
    value: Any,
) -> Optional[float]:

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
    }:
        return None

    try:
        return float(text)

    except Exception:
        return None


def safe_int(
    value: Any,
) -> Optional[int]:

    number = safe_float(value)

    if number is None:
        return None

    try:
        return int(number)

    except Exception:
        return None


def clean_symbol(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    if text.endswith(".TW"):
        text = text[:-3]

    elif text.endswith(".TWO"):
        text = text[:-4]

    return text


def parse_date(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace("/", "-")
        .replace(".", "-")
    )

    # ROC YYYY/MM/DD or YYY/MM/DD
    parts = text.split("-")

    if (
        len(parts) >= 3
        and parts[0].isdigit()
        and parts[1].isdigit()
        and parts[2].isdigit()
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

    if (
        len(text) == 8
        and text.isdigit()
    ):

        try:

            return datetime.strptime(
                text,
                "%Y%m%d",
            ).date().isoformat()

        except Exception:
            return None

    if len(text) >= 10:

        candidate = text[:10]

        try:

            return datetime.strptime(
                candidate,
                "%Y-%m-%d",
            ).date().isoformat()

        except Exception:
            return None

    return None


def roc_date(
    gdate: str,
) -> str:

    d = datetime.strptime(
        gdate,
        "%Y-%m-%d",
    ).date()

    return (
        f"{d.year - 1911:03d}/"
        f"{d.month:02d}/"
        f"{d.day:02d}"
    )


# ============================================================
# HTTP
# ============================================================

def http_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[requests.Response]:

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

            time.sleep(REQUEST_SLEEP)

            return response

        except Exception as exc:

            last_error = exc

            if attempt < REQUEST_RETRY:

                time.sleep(
                    attempt * 0.8
                )

    print(
        f"      API ERROR: {url}"
    )

    print(
        f"      {last_error}"
    )

    return None


def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:

    response = http_get(
        url,
        params,
    )

    if response is None:
        return None

    try:

        return response.json()

    except Exception as exc:

        print(
            f"      JSON ERROR: {url}: {exc}"
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
            "universe.json root "
            "must be dict"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.json stocks "
            "must be dict"
        )

    active: Dict[str, Dict[str, Any]] = {}

    for key, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"Universe item "
                f"{key} must be dict"
            )

        symbol = item.get(
            "symbol"
        )

        if symbol != key:

            raise RuntimeError(
                f"Universe key/symbol "
                f"mismatch: "
                f"{key} != {symbol}"
            )

        status = item.get(
            "status"
        )

        if status != "active":
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
# FIELD HELPERS
# ============================================================

def normalize_field(
    value: Any,
) -> str:

    return (
        str(value)
        .strip()
        .replace(" ", "")
        .replace("\n", "")
        .replace("\r", "")
    )


def find_field(
    fields: Iterable[Any],
    exact: Iterable[str] = (),
    contains: Iterable[str] = (),
) -> Optional[int]:

    values = [
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
            values
        ):

            if field == candidate:

                return index

    for candidate in contains_values:

        for index, field in enumerate(
            values
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
# TWSE - INSTITUTIONAL
# ============================================================

def twse_institutional(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/fund/T86"
    )

    params = {
        "response": "json",
        "date": gdate.replace(
            "-",
            "",
        ),
        "selectType": "ALLBUT0999",
    }

    data = get_json(
        url,
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

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if not isinstance(
        fields,
        list,
    ):

        return result

    if not isinstance(
        rows,
        list,
    ):

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
            "外陸資買賣超股數",
        ],
        contains=[
            "外陸資",
            "買賣超股數",
        ],
    )

    trust_index = find_field(
        fields,
        contains=[
            "投信",
            "買賣超股數",
        ],
    )

    dealer_index = find_field(
        fields,
        contains=[
            "自營商",
            "買賣超股數",
        ],
    )

    if code_index is None:

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

        if (
            foreign is not None
            and trust is not None
            and dealer is not None
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

    return result


# ============================================================
# TPEx - INSTITUTIONAL
# ============================================================

def tpex_institutional(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    urls = [
        (
            "https://www.tpex.org.tw/"
            "web/stock/3insti/"
            "daily_trade/"
            "3itrade_hedge_result.php"
        ),
        (
            "https://www.tpex.org.tw/"
            "web/stock/3insti/"
            "daily_trade/"
            "3itrade.php"
        ),
    ]

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": roc_date(gdate),
    }

    for url in urls:

        data = get_json(
            url,
            params,
        )

        if not isinstance(
            data,
            dict,
        ):

            continue

        rows = data.get(
            "aaData"
        )

        if not isinstance(
            rows,
            list,
        ):

            continue

        result: Dict[
            str,
            Dict[str, Any]
        ] = {}

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

            numbers = []

            for value in row[1:]:

                number = safe_float(
                    value
                )

                numbers.append(
                    number
                )

            # TPEx report is published in
            # buy / sell / net groups.
            #
            # Instead of relying on a guessed
            # absolute column position, detect
            # the net columns from the published
            # group layout.
            #
            # Known official layout:
            # foreign group
            # investment trust group
            # dealer group
            #
            # The report contains three net
            # columns in sequence.

            net_values = []

            for index, value in enumerate(
                numbers
            ):

                if value is None:
                    continue

                # net columns generally follow
                # buy/sell pairs.
                if index >= 2:

                    previous_1 = numbers[
                        index - 1
                    ]

                    previous_2 = numbers[
                        index - 2
                    ]

                    if (
                        previous_1 is not None
                        and previous_2 is not None
                    ):

                        expected = (
                            previous_1
                            - previous_2
                        )

                        if abs(
                            expected - value
                        ) < 0.5:

                            net_values.append(
                                value
                            )

            if len(net_values) >= 3:

                foreign = net_values[0]
                trust = net_values[1]
                dealer = net_values[-1]

            else:

                # Official report unavailable
                # or layout changed.
                #
                # Do not fabricate values.
                foreign = None
                trust = None
                dealer = None

            total = None

            if (
                foreign is not None
                and trust is not None
                and dealer is not None
            ):

                total = (
                    foreign
                    + trust
                    + dealer
                )

            if (
                foreign is None
                and trust is None
                and dealer is None
            ):

                continue

            result[code] = {
                "date": gdate,
                "foreign_net": foreign,
                "trust_net": trust,
                "dealer_net": dealer,
                "total_net": total,
            }

        if result:

            return result

    return {}


# ============================================================
# TWSE - MARGIN
# ============================================================

def twse_margin(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/marginTrading/"
        "MI_MARGN"
    )

    params = {
        "response": "json",
        "date": gdate.replace(
            "-",
            "",
        ),
        "selectType": "ALL",
    }

    data = get_json(
        url,
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

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if not isinstance(
        fields,
        list,
    ):

        return result

    if not isinstance(
        rows,
        list,
    ):

        return result

    code_index = find_field(
        fields,
        exact=[
            "股票代號",
            "證券代號",
            "代號",
        ],
    )

    margin_balance_index = find_field(
        fields,
        exact=[
            "融資餘額",
            "今日餘額",
        ],
        contains=[
            "融資",
            "今日餘額",
        ],
    )

    short_balance_index = find_field(
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
                    margin_balance_index,
                ),
            "short_balance":
                row_number(
                    row,
                    short_balance_index,
                ),
            "offset_volume":
                row_number(
                    row,
                    offset_index,
                ),
        }

    return result


# ============================================================
# TPEx - MARGIN
# ============================================================

def tpex_margin(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    urls = [
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/"
            "tpex_mainboard_margin"
        ),
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/"
            "tpex_esb_margin"
        ),
    ]

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for url in urls:

        data = get_json(
            url
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

            margin = None
            short = None
            offset = None

            for key, value in item.items():

                field = normalize_field(
                    key
                )

                if (
                    "融資餘額" in field
                    or "MarginBalance" in field
                    or "margin_balance" in field
                ):

                    margin = safe_float(
                        value
                    )

                if (
                    "融券餘額" in field
                    or "ShortBalance" in field
                    or "short_balance" in field
                ):

                    short = safe_float(
                        value
                    )

                if (
                    "資券相抵" in field
                    or "資券互抵" in field
                    or "Offset" in field
                    or "offset" in field
                ):

                    offset = safe_float(
                        value
                    )

            result[code] = {
                "date": gdate,
                "margin_balance": margin,
                "short_balance": short,
                "offset_volume": offset,
            }

    return result


# ============================================================
# TWSE - VOLUME
# ============================================================

def twse_volume(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.twse.com.tw/"
        "exchangeReport/MI_INDEX"
    )

    params = {
        "response": "json",
        "date": gdate.replace(
            "-",
            "",
        ),
        "type": "ALL",
    }

    data = get_json(
        url,
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

    tables = data.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):

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
        ):

            continue

        if not isinstance(
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

    return result


# ============================================================
# TPEx - VOLUME
# ============================================================

def tpex_volume(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/"
        "tpex_mainboard_daily_close_quotes"
    )

    data = get_json(
        url
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

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if not isinstance(
        rows,
        list,
    ):

        return result

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

        volume = None

        for key, value in item.items():

            field = normalize_field(
                key
            ).lower()

            if (
                "成交股數" in field
                or "tradingshares" in field
                or field == "volume"
            ):

                volume = safe_float(
                    value
                )

                break

        if volume is not None:

            result[code] = {
                "date": gdate,
                "volume": volume,
            }

    return result


# ============================================================
# TWSE - DAY TRADE
# ============================================================

def twse_day_trade(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    """
    TWSE official day-trading data.

    The official daily report provides
    day-trading shares per security.

    day_trade_rate is calculated as:

        day_trade_volume / total_volume * 100

    because the official per-security
    report gives the day-trading volume,
    while the market quote gives total
    traded volume.
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/trading/"
        "TWTB4U"
    )

    params = {
        "response": "json",
        "date": gdate.replace(
            "-",
            "",
        ),
        "selectType": "ALL",
    }

    data = get_json(
        url,
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

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if not isinstance(
        fields,
        list,
    ):

        return result

    if not isinstance(
        rows,
        list,
    ):

        return result

    code_index = find_field(
        fields,
        exact=[
            "證券代號",
            "股票代號",
            "標的代碼",
        ],
    )

    day_trade_index = find_field(
        fields,
        contains=[
            "當日沖銷交易成交股數",
            "當沖成交股數",
            "當沖股數",
        ],
    )

    if code_index is None:

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
            day_trade_index,
        )

        result[code] = {
            "date": gdate,
            "day_trade_volume":
                day_volume,
        }

    return result


# ============================================================
# TPEx - DAY TRADE
# ============================================================

def tpex_day_trade(
    gdate: str,
) -> Dict[str, Dict[str, Any]]:

    """
    TPEx official day-trading statistics.

    If official response cannot be parsed,
    return empty result.

    NEVER fabricate rate.
    """

    candidates = [
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/"
            "tpex_intraday_trading_statistics"
        ),
    ]

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for url in candidates:

        data = get_json(
            url
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

                if (
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

            result[code] = {
                "date": gdate,
                "day_trade_volume":
                    day_volume,
                "day_trade_rate":
                    rate,
            }

    return result


# ============================================================
# OFFICIAL DAY SNAPSHOT
# ============================================================

def fetch_day(
    gdate: str,
    market: str,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    if market == "TWSE":

        institutional = (
            twse_institutional(
                gdate
            )
        )

        margin = (
            twse_margin(
                gdate
            )
        )

        volume = (
            twse_volume(
                gdate
            )
        )

        day_trade = (
            twse_day_trade(
                gdate
            )
        )

    elif market == "TPEX":

        institutional = (
            tpex_institutional(
                gdate
            )
        )

        margin = (
            tpex_margin(
                gdate
            )
        )

        volume = (
            tpex_volume(
                gdate
            )
        )

        day_trade = (
            tpex_day_trade(
                gdate
            )
        )

    else:

        raise ValueError(
            f"Unsupported market: "
            f"{market}"
        )

    codes = (
        set(institutional)
        | set(margin)
        | set(volume)
        | set(day_trade)
    )

    for code in codes:

        record = {
            "date": gdate,
            "market": market,
            "institutional":
                institutional.get(
                    code
                ),
            "margin":
                margin.get(
                    code
                ),
            "volume":
                volume.get(
                    code
                ),
            "day_trade":
                day_trade.get(
                    code
                ),
        }

        # Calculate TWSE rate from
        # official day-trade volume / volume.
        if market == "TWSE":

            volume_record = (
                record["volume"]
            )

            day_record = (
                record["day_trade"]
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
# TRADING DATE DISCOVERY
# ============================================================

def discover_dates(
    market: str,
    end_date: date,
    required: int = REQUIRED_20D,
) -> List[str]:

    """
    Discover actual published trading dates.

    A date is considered valid only when
    the official institutional endpoint
    returns data.

    Therefore weekends, holidays and
    unpublished dates are not counted.
    """

    found: List[str] = []

    current = end_date

    checked = 0

    max_checked = LOOKBACK_CALENDAR_DAYS

    while (
        checked < max_checked
        and len(found) < required
    ):

        gdate = current.isoformat()

        print(
            f"      {market} "
            f"institutional date "
            f"{gdate}"
        )

        if market == "TWSE":

            data = twse_institutional(
                gdate
            )

        else:

            data = tpex_institutional(
                gdate
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

    """
    Return:

        {
            symbol: {
                date: daily_record
            }
        }
    """

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
# SUM INSTITUTIONAL
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

    if len(records) == 0:

        return result

    for field in (
        "foreign_net",
        "trust_net",
        "dealer_net",
    ):

        values = []

        for record in records:

            value = record.get(
                field
            )

            if value is None:

                values = []
                break

            values.append(
                value
            )

        if values:

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

    dates_5d = (
        available_dates[:REQUIRED_5D]
    )

    dates_20d = (
        available_dates[:REQUIRED_20D]
    )

    latest = records.get(
        latest_date
    )

    latest_institutional = None
    latest_margin = None
    latest_volume = None
    latest_day_trade = None

    if isinstance(
        latest,
        dict,
    ):

        latest_institutional = (
            latest.get(
                "institutional"
            )
        )

        latest_margin = (
            latest.get(
                "margin"
            )
        )

        latest_volume = (
            latest.get(
                "volume"
            )
        )

        latest_day_trade = (
            latest.get(
                "day_trade"
            )
        )

    institutional_records_5d = []

    institutional_records_20d = []

    for gdate in dates_5d:

        record = records.get(
            gdate
        )

        if not isinstance(
            record,
            dict,
        ):

            continue

        institutional = (
            record.get(
                "institutional"
            )
        )

        if isinstance(
            institutional,
            dict,
        ):

            institutional_records_5d.append(
                institutional
            )

    for gdate in dates_20d:

        record = records.get(
            gdate
        )

        if not isinstance(
            record,
            dict,
        ):

            continue

        institutional = (
            record.get(
                "institutional"
            )
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
        len(institutional_records_5d)
        == REQUIRED_5D
    ):

        institutional_5d = (
            sum_institutional(
                institutional_records_5d
            )
        )

    institutional_20d = None

    if (
        len(institutional_records_20d)
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

    day_trade_rate = None

    if isinstance(
        latest_day_trade,
        dict,
    ):

        day_trade_rate = (
            latest_day_trade.get(
                "day_trade_rate"
            )
        )

    volume = None

    if isinstance(
        latest_volume,
        dict,
    ):

        volume = (
            latest_volume.get(
                "volume"
            )
        )

    return {
        "symbol": code,
        "full_symbol": meta.get(
            "full_symbol"
        ),
        "name": meta.get(
            "name"
        ),
        "market": meta.get(
            "market"
        ),
        "type": meta.get(
            "type"
        ),
        "instrument_type": meta.get(
            "instrument_type"
        ),
        "status": "active",
        "listed_date": meta.get(
            "listed_date"
        ),

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
                    if institutional_5d
                    else {
                        "foreign_net":
                            None,
                        "trust_net":
                            None,
                        "dealer_net":
                            None,
                        "total_net":
                            None,
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
                    if institutional_20d
                    else {
                        "foreign_net":
                            None,
                        "trust_net":
                            None,
                        "dealer_net":
                            None,
                        "total_net":
                            None,
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
                (
                    latest_day_trade.get(
                        "day_trade_volume"
                    )
                    if isinstance(
                        latest_day_trade,
                        dict,
                    )
                    else None
                ),
        },

        "data_quality": {
            "current_institutional":
                isinstance(
                    latest_institutional,
                    dict,
                ),

            "institutional_5d":
                (
                    len(
                        institutional_records_5d
                    )
                    == REQUIRED_5D
                ),

            "institutional_20d":
                (
                    len(
                        institutional_records_20d
                    )
                    == REQUIRED_20D
                ),

            "margin":
                isinstance(
                    latest_margin,
                    dict,
                ),

            "volume":
                volume is not None,

            "day_trade":
                day_trade_rate is not None,

            "valid":
                (
                    isinstance(
                        latest_institutional,
                        dict,
                    )
                    or isinstance(
                        latest_margin,
                        dict,
                    )
                    or volume is not None
                    or day_trade_rate is not None
                ),
        },
    }


# ============================================================
# OUTPUT VALIDATION
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
                f"{code}: chip item "
                "must be dict"
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
                f"{code}: inactive "
                "entered chip"
            )

        if item.get(
            "market"
        ) != universe[code].get(
            "market"
        ):

            raise RuntimeError(
                f"{code}: market mismatch"
            )

        if item.get(
            "type"
        ) != universe[code].get(
            "type"
        ):

            raise RuntimeError(
                f"{code}: type mismatch"
            )


# ============================================================
# BUILD
# ============================================================

def build() -> Dict[str, Any]:

    universe = load_universe()

    print("=" * 72)
    print(
        "FETCH CHIP "
        f"{VERSION}"
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

    today = taiwan_today()

    print()
    print(
        "STEP 1 - DISCOVER "
        "OFFICIAL TRADING DATES"
    )

    twse_dates = []

    if twse_codes:

        twse_dates = discover_dates(
            "TWSE",
            today,
            REQUIRED_20D,
        )

    tpex_dates = []

    if tpex_codes:

        tpex_dates = discover_dates(
            "TPEX",
            today,
            REQUIRED_20D,
        )

    if twse_codes and not twse_dates:

        print(
            "WARNING: "
            "TWSE has no official "
            "institutional dates."
        )

    if tpex_codes and not tpex_dates:

        print(
            "WARNING: "
            "TPEX has no official "
            "institutional dates."
        )

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    print()
    print(
        "STEP 2 - FETCH OFFICIAL "
        "HISTORY"
    )

    twse_history = {}

    if twse_dates:

        twse_history = (
            fetch_market_history(
                "TWSE",
                twse_dates,
            )
        )

    tpex_history = {}

    if tpex_dates:

        tpex_history = (
            fetch_market_history(
                "TPEX",
                tpex_dates,
            )
        )

    # --------------------------------------------------------
    # LATEST DATE
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
            "No official trading date "
            "was found."
        )

    latest_trade_date = max(
        latest_dates
    )

    # --------------------------------------------------------
    # BUILD STOCKS
    # --------------------------------------------------------

    print()
    print(
        "STEP 3 - BUILD CHIP"
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

            history = (
                twse_history
            )

            dates = twse_dates

        else:

            history = (
                tpex_history
            )

            dates = tpex_dates

        if not dates:

            # Still create the active
            # Universe record, but no
            # fabricated market data.
            stock = {
                "symbol":
                    code,

                "full_symbol":
                    meta.get(
                        "full_symbol"
                    ),

                "name":
                    meta.get(
                        "name"
                    ),

                "market":
                    market,

                "type":
                    meta.get(
                        "type"
                    ),

                "instrument_type":
                    meta.get(
                        "instrument_type"
                    ),

                "status":
                    "active",

                "listed_date":
                    meta.get(
                        "listed_date"
                    ),

                "latest_trade_date":
                    latest_trade_date,

                "institutional": {
                    "date":
                        latest_trade_date,

                    "foreign_net":
                        None,

                    "trust_net":
                        None,

                    "dealer_net":
                        None,

                    "total_net":
                        None,

                    "5d": {
                        "trading_days":
                            0,
                        "dates": [],
                        "foreign_net":
                            None,
                        "trust_net":
                            None,
                        "dealer_net":
                            None,
                        "total_net":
                            None,
                    },

                    "20d": {
                        "trading_days":
                            0,
                        "dates": [],
                        "foreign_net":
                            None,
                        "trust_net":
                            None,
                        "dealer_net":
                            None,
                        "total_net":
                            None,
                    },
                },

                "margin": {
                    "date":
                        latest_trade_date,
                    "margin_balance":
                        None,
                    "short_balance":
                        None,
                    "offset_volume":
                        None,
                },

                "trading": {
                    "date":
                        latest_trade_date,
                    "volume":
                        None,
                    "day_trade_rate":
                        None,
                    "day_trade_volume":
                        None,
                },

                "data_quality": {
                    "current_institutional":
                        False,
                    "institutional_5d":
                        False,
                    "institutional_20d":
                        False,
                    "margin":
                        False,
                    "volume":
                        False,
                    "day_trade":
                        False,
                    "valid":
                        False,
                },
            }

        else:

            stock = build_stock_record(
                code,
                meta,
                history,
                dates[0],
            )

        if stock[
            "data_quality"
        ].get(
            "valid"
        ):

            valid_count += 1

        stocks[code] = stock

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
                for item in universe.values()
                if item.get("type")
                == "STOCK"
            ),

        "etf_count":
            sum(
                1
                for item in universe.values()
                if item.get("type")
                == "ETF"
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
                    (
                        "https://www.twse.com.tw/"
                        "rwd/zh/fund/T86"
                    ),

                "margin":
                    (
                        "https://www.twse.com.tw/"
                        "rwd/zh/marginTrading/"
                        "MI_MARGN"
                    ),

                "volume":
                    (
                        "https://www.twse.com.tw/"
                        "exchangeReport/MI_INDEX"
                    ),

                "day_trade":
                    (
                        "https://www.twse.com.tw/"
                        "rwd/zh/trading/TWTB4U"
                    ),
            },

            "TPEX": {
                "institutional":
                    (
                        "https://www.tpex.org.tw/"
                        "web/stock/3insti/"
                        "daily_trade/"
                        "3itrade_hedge_result.php"
                    ),

                "margin":
                    (
                        "https://www.tpex.org.tw/"
                        "openapi/v1/"
                        "tpex_mainboard_margin"
                    ),

                "volume":
                    (
                        "https://www.tpex.org.tw/"
                        "openapi/v1/"
                        "tpex_mainboard_daily_close_quotes"
                    ),

                "day_trade":
                    (
                        "https://www.tpex.org.tw/"
                        "openapi/v1/"
                        "tpex_intraday_trading_statistics"
                    ),
            },
        },

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
        },

        "stocks":
            stocks,
    }

    # --------------------------------------------------------
    # FINAL VALIDATION
    # --------------------------------------------------------

    print()
    print(
        "STEP 4 - VALIDATE"
    )

    validate_output(
        payload,
        universe,
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
        f"✓ Valid: "
        f"{valid_count}"
    )

    print(
        f"✓ Invalid: "
        f"{len(universe) - valid_count}"
    )

    # --------------------------------------------------------
    # SPECIAL SAFETY CHECK
    # --------------------------------------------------------

    forbidden = []

    for code in stocks:

        if code not in universe:

            forbidden.append(
                code
            )

        elif universe[code].get(
            "status"
        ) != "active":

            forbidden.append(
                code
            )

    if forbidden:

        raise RuntimeError(
            "Forbidden symbols "
            "detected: "
            f"{forbidden[:20]}"
        )

    # --------------------------------------------------------
    # DATA SANITY
    # --------------------------------------------------------

    for code, item in stocks.items():

        institutional = item.get(
            "institutional",
            {}
        )

        d5 = institutional.get(
            "5d",
            {}
        )

        d20 = institutional.get(
            "20d",
            {}
        )

        dates5 = d5.get(
            "dates",
            []
        )

        dates20 = d20.get(
            "dates",
            []
        )

        if len(dates5) > REQUIRED_5D:

            raise RuntimeError(
                f"{code}: "
                "5D date overflow"
            )

        if len(dates20) > REQUIRED_20D:

            raise RuntimeError(
                f"{code}: "
                "20D date overflow"
            )

        if len(
            set(dates5)
        ) != len(dates5):

            raise RuntimeError(
                f"{code}: "
                "duplicate 5D dates"
            )

        if len(
            set(dates20)
        ) != len(dates20):

            raise RuntimeError(
                f"{code}: "
                "duplicate 20D dates"
            )

    return payload


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

        # ----------------------------------------------------
        # IMPORTANT:
        # Do NOT overwrite existing chip.json when
        # the entire official data collection failed.
        # ----------------------------------------------------

        if (
            payload.get(
                "valid_count",
                0
            )
            <= 0
        ):

            print()
            print(
                "ERROR: "
                "No valid official "
                "chip data."
            )

            print(
                "Existing chip.json "
                "will NOT be overwritten."
            )

            return 1

        atomic_write_json(
            OUTPUT_FILE,
            payload,
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
            f"{payload['universe_count']}"
        )

        print(
            f"Valid: "
            f"{payload['valid_count']}"
        )

        print(
            f"Invalid: "
            f"{payload['invalid_count']}"
        )

        print(
            f"Latest trade date: "
            f"{payload['latest_trade_date']}"
        )

        print("=" * 72)

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

        return 1


if __name__ == "__main__":

    raise SystemExit(
        main()
    )