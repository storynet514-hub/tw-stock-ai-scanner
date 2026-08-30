#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/test_00838b.py

00838B TPEx 官方價格資料診斷
============================================================

目的
------------------------------------------------------------
1. 只測試 00838B
2. 只使用 TPEx 官方資料
3. 不使用 Yahoo
4. 不讀 Universe
5. 不呼叫正式 fetch_prices.py
6. 正確解析 TPEx 新版 JSON：
       response["tables"][].["data"]
7. 同時相容舊版：
       response["aaData"]
8. 確認 00838B 是否真的存在於官方行情資料
9. 顯示實際 response 結構與欄位
10. 顯示找到 00838B 時的 OHLCV
============================================================
"""

from __future__ import annotations

import json
import sys
import time

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "00838B"

TPEx_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "otc_quotes_no1430/"
    "stk_wn1430_result.php"
)

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 1.5


# ============================================================
# SESSION
# ============================================================

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
            "application/json,"
            "text/plain,"
            "*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Connection": "keep-alive",
    }
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
# TEXT
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(value: Any) -> Optional[str]:

    text = clean_text(value)

    if not text:
        return None

    for suffix in (
        ".TW",
        ".TWO",
        ".HK",
    ):
        if text.upper().endswith(suffix):
            text = text[
                :-len(suffix)
            ]
            break

    return text.strip() or None


# ============================================================
# ROC DATE
# ============================================================

def roc_date(
    date_text: str,
) -> str:

    dt = datetime.strptime(
        date_text,
        "%Y-%m-%d",
    )

    return (
        f"{dt.year - 1911:03d}/"
        f"{dt.month:02d}/"
        f"{dt.day:02d}"
    )


# ============================================================
# HTTP
# ============================================================

def fetch_json(
    date_text: str,
) -> tuple[int, str, Any]:

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": roc_date(date_text),
    }

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TPEx_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                )
            )

            data = response.json()

            return (
                response.status_code,
                content_type,
                data,
            )

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY
                )

    raise RuntimeError(
        "TPEx HTTP 失敗："
        f"{last_error}"
    )


# ============================================================
# EXTRACT TABLE DATA
# ============================================================

def extract_table_rows(
    data: Any,
) -> tuple[
    List[str],
    List[List[Any]],
    str,
]:

    if not isinstance(
        data,
        dict,
    ):
        return [], [], "invalid_root"

    # --------------------------------------------------------
    # 新版 TPEx JSON
    #
    # {
    #   "tables": [
    #       {
    #           "fields": [...],
    #           "data": [...]
    #       }
    #   ]
    # }
    # --------------------------------------------------------

    tables = data.get(
        "tables"
    )

    if isinstance(
        tables,
        list,
    ):

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

            if (
                isinstance(
                    rows,
                    list,
                )
                and isinstance(
                    fields,
                    list,
                )
            ):

                return (
                    [
                        clean_text(x)
                        for x in fields
                    ],
                    rows,
                    "tables[].data",
                )

    # --------------------------------------------------------
    # 舊版 / 其他格式
    # --------------------------------------------------------

    aa_data = data.get(
        "aaData"
    )

    if isinstance(
        aa_data,
        list,
    ):

        tables = data.get(
            "tables"
        )

        fields = []

        if (
            isinstance(
                tables,
                list,
            )
            and tables
            and isinstance(
                tables[0],
                dict,
            )
        ):

            fields = tables[0].get(
                "fields"
            ) or []

        return (
            [
                clean_text(x)
                for x in fields
            ],
            aa_data,
            "aaData",
        )

    return [], [], "no_supported_data"


# ============================================================
# FIND SYMBOL
# ============================================================

def find_symbol(
    rows: List[List[Any]],
    symbol: str,
) -> Optional[List[Any]]:

    target = normalize_symbol(
        symbol
    )

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        if not row:
            continue

        first = normalize_symbol(
            row[0]
        )

        if first == target:
            return row

    return None


# ============================================================
# FIELD MAP
# ============================================================

def build_field_map(
    fields: List[str],
) -> Dict[str, int]:

    result = {}

    for index, field in enumerate(
        fields
    ):

        result[
            clean_text(field)
        ] = index

    return result


# ============================================================
# GET FIELD
# ============================================================

def get_field(
    row: List[Any],
    field_map: Dict[str, int],
    names: tuple[str, ...],
) -> Any:

    for name in names:

        index = field_map.get(
            clean_text(name)
        )

        if (
            index is not None
            and index < len(row)
        ):

            return row[index]

    return None


# ============================================================
# PRINT RECORD
# ============================================================

def print_symbol_record(
    fields: List[str],
    row: List[Any],
) -> None:

    section(
        f"FOUND {SYMBOL}"
    )

    log(
        f"row length：{len(row)}"
    )

    log("")

    for index, value in enumerate(
        row
    ):

        field = (
            fields[index]
            if index < len(fields)
            else f"FIELD_{index}"
        )

        log(
            f"[{index:02d}] "
            f"{field} = {value}"
        )

    field_map = build_field_map(
        fields
    )

    log("")
    log(
        "NORMALIZED OHLCV"
    )

    close = get_field(
        row,
        field_map,
        (
            "收盤",
            "收盤 ",
        ),
    )

    open_price = get_field(
        row,
        field_map,
        (
            "開盤",
            "開盤 ",
        ),
    )

    high = get_field(
        row,
        field_map,
        (
            "最高",
            "最高 ",
        ),
    )

    low = get_field(
        row,
        field_map,
        (
            "最低",
            "最低 ",
        ),
    )

    volume = get_field(
        row,
        field_map,
        (
            "成交股數",
            "成交股數  ",
        ),
    )

    log(
        f"open：{open_price}"
    )

    log(
        f"high：{high}"
    )

    log(
        f"low：{low}"
    )

    log(
        f"close：{close}"
    )

    log(
        f"volume：{volume}"
    )


# ============================================================
# TEST ONE DATE
# ============================================================

def test_date(
    date_text: str,
) -> bool:

    section(
        f"TEST DATE：{date_text}"
    )

    log(
        f"TPEx DATE："
        f"{roc_date(date_text)}"
    )

    try:

        status_code, content_type, data = (
            fetch_json(
                date_text
            )
        )

    except Exception as exc:

        log(
            f"❌ HTTP exception：{exc}"
        )

        return False

    log("")
    log(
        "HTTP STATUS"
    )

    log(
        f"status_code："
        f"{status_code}"
    )

    log(
        f"content_type："
        f"{content_type}"
    )

    # --------------------------------------------------------
    # Root
    # --------------------------------------------------------

    log("")
    log(
        "JSON ROOT"
    )

    log(
        f"type："
        f"{type(data).__name__}"
    )

    if not isinstance(
        data,
        dict,
    ):

        log(
            "❌ JSON root 不是 dict"
        )

        return False

    log("")
    log(
        "ROOT KEYS"
    )

    for key in data.keys():

        log(
            f"  {key}"
        )

    # --------------------------------------------------------
    # Extract
    # --------------------------------------------------------

    fields, rows, source = (
        extract_table_rows(
            data
        )
    )

    log("")
    log(
        "DATA EXTRACTION"
    )

    log(
        f"來源結構：{source}"
    )

    log(
        f"fields："
        f"{len(fields)}"
    )

    log(
        f"rows："
        f"{len(rows)}"
    )

    if source == "no_supported_data":

        log(
            "❌ 找不到 tables[].data "
            "或 aaData"
        )

        log("")
        log(
            "完整 JSON："
        )

        print(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
        )

        return False

    # --------------------------------------------------------
    # Fields
    # --------------------------------------------------------

    if fields:

        log("")
        log(
            "FIELDS"
        )

        for index, field in enumerate(
            fields
        ):

            log(
                f"  [{index:02d}] "
                f"{field}"
            )

    # --------------------------------------------------------
    # Search
    # --------------------------------------------------------

    found = find_symbol(
        rows,
        SYMBOL,
    )

    log("")
    log(
        f"SEARCH SYMBOL：{SYMBOL}"
    )

    if found is None:

        log(
            f"❌ {SYMBOL} "
            "不在官方資料 rows 中"
        )

        # 顯示前 20 筆代號，確認 parser
        # 是否真的拿到行情資料。
        log("")
        log(
            "前 20 筆代號："
        )

        shown = 0

        for row in rows:

            if (
                isinstance(row, list)
                and row
            ):

                code = clean_text(
                    row[0]
                )

                if code:

                    log(
                        f"  {code}"
                    )

                    shown += 1

                if shown >= 20:
                    break

        return False

    print_symbol_record(
        fields,
        found,
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    section(
        "00838B TPEx OFFICIAL PRICE DIAGNOSTIC V2"
    )

    log(
        f"測試商品：{SYMBOL}"
    )

    log(
        f"Endpoint：{TPEx_URL}"
    )

    log(
        "資料來源：TPEx 官方"
    )

    log(
        "Yahoo：NO"
    )

    log(
        "Universe：NO"
    )

    log(
        "正式價格管線：NO"
    )

    # --------------------------------------------------------
    # 測試最近 10 個工作日
    # --------------------------------------------------------

    today = datetime(
        2026,
        8,
        28,
    )

    dates = []

    current = today

    while len(dates) < 10:

        if current.weekday() < 5:

            dates.append(
                current.strftime(
                    "%Y-%m-%d"
                )
            )

        current -= timedelta(
            days=1
        )

    found_dates = []

    for date_text in dates:

        try:

            found = test_date(
                date_text
            )

        except Exception as exc:

            log(
                f"❌ 測試失敗：{exc}"
            )

            found = False

        if found:

            found_dates.append(
                date_text
            )

        time.sleep(0.3)

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    section(
        "DIAGNOSTIC RESULT"
    )

    if found_dates:

        log(
            f"✓ 官方資料找到 {SYMBOL}"
        )

        log(
            "找到日期："
            + ", ".join(
                found_dates
            )
        )

        log("")
        log(
            "結論："
        )

        log(
            "00838B 確實存在於 TPEx "
            "官方行情 response。"
        )

        log(
            "先前 fetch_prices.py "
            "抓不到的主要原因是 parser "
            "讀取 data['aaData']，"
            "但目前 TPEx response 使用 "
            "data['tables'][].['data']。"
        )

        return 0

    log(
        f"❌ 最近測試日期沒有在 "
        f"TPEx 官方 tables[].data "
        f"找到 {SYMBOL}"
    )

    log("")
    log(
        "這代表目前仍需要進一步確認："
    )

    log(
        "1. 00838B 的實際商品分類"
    )

    log(
        "2. TPEx 官方對應的歷史行情 endpoint"
    )

    log(
        "3. 是否不是 stk_wn1430_result.php "
        "這個行情分類"
    )

    return 1


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        raise SystemExit(
            main()
        )

    except KeyboardInterrupt:

        log(
            "❌ 使用者中止"
        )

        raise SystemExit(
            130
        )

    except Exception as exc:

        log("")
        log(
            "========================================"
        )
        log(
            "DIAGNOSTIC FAILED"
        )
        log(
            "========================================"
        )
        log(
            f"❌ {exc}"
        )

        raise SystemExit(
            1
        )
