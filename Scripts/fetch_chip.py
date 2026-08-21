#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V6.0

============================================================
核心目的
============================================================

取得 CMoney「主力進出」頁面的：

「買賣超」

單位：
張

正數：
主力買超

負數：
主力賣超

============================================================
重要定義
============================================================

main_force_1d
    最近一個交易日主力買賣超

main_force_5d
    最近 5 個交易日「每日買賣超」加總

main_force_10d
    最近 10 個交易日「每日買賣超」加總

main_force_20d
    最近 20 個交易日「每日買賣超」加總

絕對禁止：

5日集中
20日集中
家數差
其他集中度欄位
其他籌碼欄位

============================================================
本次修正
============================================================

CMoney 主力進出首頁目前一次只回傳約 10 個交易日。

本版本：

1. 不使用上一版 chip.json 補足 20D
2. 不進行跨執行日歷史累積
3. 不將 10D 當成 20D
4. 只從本次 CMoney 資料取得 20D
5. 首頁取得 10 筆後，嘗試使用 CMoney
   頁面本身可能存在的延伸/分頁資料
   取得第 11～20 筆
6. 最終只有在本次取得資料 >= 20 筆時
   才產生 main_force_20d
7. 如果本次 CMoney 實際只能取得 10 筆，
   main_force_20d 保持 None
8. 不使用其他籌碼欄位補足資料

============================================================
重要
============================================================

本版本：

不讀 universe.json
不跑全市場
不探測 API
不猜 API pagination
不使用 URL 延伸資料來源
不使用其他欄位補足 20D

固定測試：

2337 旺宏
2426 鼎元
2368 金像電
3081 聯亞

============================================================
輸出
============================================================

Data/chip.json
"""

from __future__ import annotations

import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "V6.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.5

TARGET_HISTORY = 20


# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = [
    {
        "symbol": "2337",
        "name": "旺宏",
        "market": "TWSE",
    },
    {
        "symbol": "2426",
        "name": "鼎元",
        "market": "TWSE",
    },
    {
        "symbol": "2368",
        "name": "金像電",
        "market": "TWSE",
    },
    {
        "symbol": "3081",
        "name": "聯亞",
        "market": "TPEX",
    },
]


# ============================================================
# CMoney URL
# ============================================================

CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

CMONEY_MOBILE_URL = (
    "https://mobile.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)


# ============================================================
# Headers
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
    "Connection": "keep-alive",
}


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# Number
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(text).strip()

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("張", "")
        .strip()
    )

    if text.upper() in {
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "-",
        "--",
        "－",
        "—",
        "無",
    }:
        return None

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
        text
    )

    if not match:
        return None

    try:
        return float(match.group(0))
    except Exception:
        return None


# ============================================================
# 日期
# ============================================================

def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    patterns = [
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
    ]

    for pattern in patterns:

        match = re.fullmatch(
            pattern,
            text
        )

        if match:

            y, m, d = match.groups()

            try:

                dt = datetime(
                    int(y),
                    int(m),
                    int(d)
                )

                return dt.strftime(
                    "%Y/%m/%d"
                )

            except Exception:

                return None

    return None


# ============================================================
# Header normalize
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    text = (
        text
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
        .replace("\u3000", "")
        .strip()
    )

    return text


# ============================================================
# 嚴格判斷「買賣超」
# ============================================================

def is_main_force_header(text):

    header = normalize_header(text)

    if header == "買賣超":
        return True

    return False


# ============================================================
# Request
# ============================================================

def request_url(
    session,
    url
):

    response = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    html = response.text

    if not html:
        raise RuntimeError(
            "CMoney 回傳空白內容"
        )

    return html


# ============================================================
# 首頁 Request
# ============================================================

def request_cmoney_page(
    session,
    symbol
):

    urls = [
        CMONEY_URL.format(
            symbol=symbol
        ),
        CMONEY_MOBILE_URL.format(
            symbol=symbol
        ),
    ]

    last_error = None

    for url in urls:

        try:

            html = request_url(
                session,
                url
            )

            return html, url

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# 建立可能的頁面延伸 URL
#
# 注意：
# 這裡不是 API。
#
# 只對 CMoney 網頁本身嘗試常見的
# query-string 分頁形式。
#
# 如果 CMoney 沒有這些頁面，
# 程式會自然維持 10 筆。
# ============================================================

def build_page_candidates(
    source_url
):

    parsed = urlparse(
        source_url
    )

    query = parse_qs(
        parsed.query,
        keep_blank_values=True
    )

    candidates = []

    # --------------------------------------------------------
    # 常見 page 分頁
    # --------------------------------------------------------

    for page in range(2, 5):

        new_query = {
            key: values[:]
            for key, values in query.items()
        }

        new_query["page"] = [
            str(page)
        ]

        new_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(
                    new_query,
                    doseq=True
                ),
                parsed.fragment,
            )
        )

        candidates.append(
            new_url
        )

    # --------------------------------------------------------
    # 常見 p 分頁
    # --------------------------------------------------------

    for page in range(2, 5):

        new_query = {
            key: values[:]
            for key, values in query.items()
        }

        new_query["p"] = [
            str(page)
        ]

        new_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(
                    new_query,
                    doseq=True
                ),
                parsed.fragment,
            )
        )

        candidates.append(
            new_url
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = []

    seen = set()

    for url in candidates:

        if url in seen:
            continue

        seen.add(url)

        unique.append(url)

    return unique


# ============================================================
# 找日期欄與買賣超欄
# ============================================================

def find_column_indexes(headers):

    date_index = None

    force_index = None

    for index, header in enumerate(
        headers
    ):

        normalized = normalize_header(
            header
        )

        if date_index is None:

            if normalized == "日期":

                date_index = index

        if force_index is None:

            if is_main_force_header(
                normalized
            ):

                force_index = index

    return date_index, force_index


# ============================================================
# 嚴格解析 CMoney 表格
# ============================================================

def parse_cmoney_main_force(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    tables = soup.find_all(
        "table"
    )

    all_results = []

    # ========================================================
    # 每一個 table 分開判斷
    # ========================================================

    for table in tables:

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        target_header = None
        target_date_index = None
        target_force_index = None
        target_header_position = None

        # ----------------------------------------------------
        # 找真正包含
        #
        # 日期
        # 買賣超
        #
        # 的 header
        # ----------------------------------------------------

        for position, header_row in enumerate(
            rows[:15]
        ):

            cells = header_row.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            headers = [
                normalize_header(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            date_index, force_index = (
                find_column_indexes(
                    headers
                )
            )

            if (
                date_index is not None
                and force_index is not None
            ):

                target_header = headers

                target_date_index = (
                    date_index
                )

                target_force_index = (
                    force_index
                )

                target_header_position = (
                    position
                )

                break

        if target_header is None:
            continue

        # ----------------------------------------------------
        # 只解析 header 後面的資料列
        # ----------------------------------------------------

        for row in rows[
            target_header_position + 1:
        ]:

            cells = row.find_all(
                ["th", "td"]
            )

            if len(cells) <= max(
                target_date_index,
                target_force_index
            ):
                continue

            values = [
                cell.get_text(
                    " ",
                    strip=True
                )
                for cell in cells
            ]

            date_value = normalize_date(
                values[target_date_index]
            )

            if not date_value:
                continue

            force_value = parse_number(
                values[target_force_index]
            )

            if force_value is None:
                continue

            all_results.append({
                "date": date_value,
                "main_force": force_value,
            })

    # ========================================================
    # 去重
    # ========================================================

    unique = {}

    for row in all_results:

        date = row["date"]

        value = row["main_force"]

        unique[date] = value

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in unique.items()
    ]

    # ========================================================
    # 最新在前
    # ========================================================

    result.sort(
        key=lambda row: datetime.strptime(
            row["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    return result


# ============================================================
# 本次取得完整 20D
# ============================================================

def fetch_cmoney_20d(
    session,
    symbol
):

    section(
        f"CMoney 主力買賣超：{symbol}"
    )

    # --------------------------------------------------------
    # 第一次：
    # CMoney 主力進出首頁
    # --------------------------------------------------------

    html, source_url = request_cmoney_page(
        session,
        symbol
    )

    history = parse_cmoney_main_force(
        html
    )

    log(
        f"CMoney 首頁有效「買賣超」："
        f"{len(history)} 筆"
    )

    if history:

        log(
            "✓ 已確認資料來源欄位：買賣超"
        )

    # --------------------------------------------------------
    # 如果已經滿 20 筆
    # --------------------------------------------------------

    if len(history) >= TARGET_HISTORY:

        return (
            history[:TARGET_HISTORY],
            source_url
        )

    # --------------------------------------------------------
    # 首頁只有 10 筆時
    #
    # 嘗試 CMoney 網頁本身可能存在的
    # 分頁資料。
    #
    # 不使用 API。
    # 不猜 API。
    # 不使用其他欄位。
    # --------------------------------------------------------

    candidates = build_page_candidates(
        source_url
    )

    log(
        "首頁不足20筆，"
        "嘗試 CMoney 網頁分頁資料"
    )

    for page_index, page_url in enumerate(
        candidates,
        start=1
    ):

        if len(history) >= TARGET_HISTORY:
            break

        try:

            log(
                f"延伸頁面 {page_index}/"
                f"{len(candidates)}"
            )

            page_html = request_url(
                session,
                page_url
            )

            page_history = (
                parse_cmoney_main_force(
                    page_html
                )
            )

            log(
                f"延伸頁面有效「買賣超」："
                f"{len(page_history)} 筆"
            )

            if not page_history:
                continue

            # ------------------------------------------------
            # 合併本次 CMoney 資料
            # ------------------------------------------------

            combined = {}

            for row in history:

                combined[
                    row["date"]
                ] = row["main_force"]

            for row in page_history:

                combined[
                    row["date"]
                ] = row["main_force"]

            history = [
                {
                    "date": date,
                    "main_force": value,
                }
                for date, value
                in combined.items()
            ]

            history.sort(
                key=lambda row:
                datetime.strptime(
                    row["date"],
                    "%Y/%m/%d"
                ),
                reverse=True
            )

            history = history[
                :TARGET_HISTORY
            ]

            log(
                f"本次 CMoney 合併後："
                f"{len(history)} 筆"
            )

        except Exception as exc:

            log(
                f"ℹ️ 延伸頁面無有效資料："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # --------------------------------------------------------
    # 最終結果
    # --------------------------------------------------------

    history.sort(
        key=lambda row:
        datetime.strptime(
            row["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    history = history[
        :TARGET_HISTORY
    ]

    return (
        history,
        source_url
    )


# ============================================================
# 計算期間
# ============================================================

def calculate_periods(
    history
):

    values = [
        float(row["main_force"])
        for row in history
        if row.get(
            "main_force"
        ) is not None
    ]

    result = {

        "main_force_1d":
            None,

        "main_force_5d":
            None,

        "main_force_10d":
            None,

        "main_force_20d":
            None,

        "history_count":
            len(values),
    }

    if len(values) >= 1:

        result[
            "main_force_1d"
        ] = round(
            values[0],
            2
        )

    if len(values) >= 5:

        result[
            "main_force_5d"
        ] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result[
            "main_force_10d"
        ] = round(
            sum(values[:10]),
            2
        )

    if len(values) >= 20:

        result[
            "main_force_20d"
        ] = round(
            sum(values[:20]),
            2
        )

    return result


# ============================================================
# Status
# ============================================================

def get_status(
    periods
):

    if periods.get(
        "main_force_20d"
    ) is not None:

        return "complete"

    if periods.get(
        "main_force_10d"
    ) is not None:

        return "partial_20d"

    if periods.get(
        "main_force_5d"
    ) is not None:

        return "partial_10d"

    if periods.get(
        "main_force_1d"
    ) is not None:

        return "partial_5d"

    return "insufficient"


# ============================================================
# 取得單一股票
# ============================================================

def fetch_stock(
    session,
    stock
):

    symbol = stock["symbol"]

    name = stock["name"]

    log(
        f"{symbol} {name}"
    )

    history, source_url = (
        fetch_cmoney_20d(
            session,
            symbol
        )
    )

    log(
        f"本次取得有效歷史："
        f"{len(history)}/{TARGET_HISTORY} 筆"
    )

    periods = calculate_periods(
        history
    )

    status = get_status(
        periods
    )

    log(
        f"主力1日："
        f"{periods['main_force_1d']}"
    )

    log(
        f"主力5日："
        f"{periods['main_force_5d']}"
    )

    log(
        f"主力10日："
        f"{periods['main_force_10d']}"
    )

    log(
        f"主力20日："
        f"{periods['main_force_20d']}"
    )

    if len(history) >= TARGET_HISTORY:

        log(
            "✓ 本次已取得完整20個交易日"
        )

    else:

        log(
            f"ℹ️ 本次 CMoney 僅取得 "
            f"{len(history)} 個交易日，"
            f"因此20D保持 None"
        )

    return {

        "symbol":
            symbol,

        "name":
            name,

        "market":
            stock["market"],

        "source":
            "CMoney",

        "source_url":
            source_url,

        "source_field":
            "買賣超",

        "main_force_1d":
            periods[
                "main_force_1d"
            ],

        "main_force_5d":
            periods[
                "main_force_5d"
            ],

        "main_force_10d":
            periods[
                "main_force_10d"
            ],

        "main_force_20d":
            periods[
                "main_force_20d"
            ],

        "history_count":
            len(history),

        "status":
            status,

        "history":
            history,

        "error":
            None,
    }


# ============================================================
# 建立失敗紀錄
# ============================================================

def build_error_record(
    stock,
    error
):

    return {

        "symbol":
            stock["symbol"],

        "name":
            stock["name"],

        "market":
            stock["market"],

        "source":
            "CMoney",

        "source_url":
            CMONEY_URL.format(
                symbol=stock["symbol"]
            ),

        "source_field":
            "買賣超",

        "main_force_1d":
            None,

        "main_force_5d":
            None,

        "main_force_10d":
            None,

        "main_force_20d":
            None,

        "history_count":
            0,

        "status":
            "insufficient",

        "history":
            [],

        "error":
            str(error),
    }


# ============================================================
# Fetch all
# ============================================================

def fetch_all():

    section(
        "開始 CMoney 主力買賣超更新"
    )

    log(
        "本版本為固定測試模式"
    )

    log(
        "不讀 universe.json"
    )

    log(
        "不跑全市場 Universe"
    )

    log(
        "固定測試："
        "2337 / 2426 / 2368 / 3081"
    )

    log(
        "只使用 CMoney「買賣超」"
    )

    log(
        "不使用上一版 chip.json 補資料"
    )

    log(
        "不跨執行日累積歷史"
    )

    log(
        "目標：本次取得20個交易日"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    complete = 0

    partial = 0

    insufficient = 0

    total = len(TEST_STOCKS)

    for index, stock in enumerate(
        TEST_STOCKS,
        start=1
    ):

        symbol = stock["symbol"]

        name = stock["name"]

        log(
            ""
        )

        log(
            f"[{index}/{total}] "
            f"{symbol} {name}"
        )

        try:

            record = fetch_stock(
                session,
                stock
            )

            results[
                symbol
            ] = record

            if (
                record[
                    "main_force_20d"
                ] is not None
            ):

                complete += 1

            elif (
                record[
                    "main_force_10d"
                ] is not None
            ):

                partial += 1

            else:

                insufficient += 1

        except Exception as exc:

            log(
                f"❌ {symbol} 取得失敗："
                f"{exc}"
            )

            record = build_error_record(
                stock,
                exc
            )

            results[
                symbol
            ] = record

            insufficient += 1

        time.sleep(
            REQUEST_DELAY
        )

    return (
        results,
        complete,
        partial,
        insufficient
    )


# ============================================================
# Validate
# ============================================================

def validate(
    results
):

    section(
        "最終資料驗證"
    )

    if len(results) != len(
        TEST_STOCKS
    ):

        raise RuntimeError(
            "輸出股票數量錯誤"
        )

    valid_1d = 0

    valid_5d = 0

    valid_10d = 0

    valid_20d = 0

    for stock in TEST_STOCKS:

        symbol = stock["symbol"]

        if symbol not in results:

            raise RuntimeError(
                f"缺少測試股票："
                f"{symbol}"
            )

        record = results[
            symbol
        ]

        if record.get(
            "main_force_1d"
        ) is not None:

            valid_1d += 1

        if record.get(
            "main_force_5d"
        ) is not None:

            valid_5d += 1

        if record.get(
            "main_force_10d"
        ) is not None:

            valid_10d += 1

        if record.get(
            "main_force_20d"
        ) is not None:

            valid_20d += 1

        history = record.get(
            "history",
            []
        )

        if not isinstance(
            history,
            list
        ):

            raise RuntimeError(
                f"{symbol} history "
                f"格式錯誤"
            )

        if len(history) > TARGET_HISTORY:

            raise RuntimeError(
                f"{symbol} history "
                f"超過20筆"
            )

        periods = calculate_periods(
            history
        )

        for field in [

            "main_force_1d",

            "main_force_5d",

            "main_force_10d",

            "main_force_20d",

        ]:

            actual = record.get(
                field
            )

            expected = periods.get(
                field
            )

            if actual != expected:

                raise RuntimeError(
                    f"{symbol} {field} "
                    f"計算驗證失敗："
                    f"actual={actual}, "
                    f"expected={expected}"
                )

        # ----------------------------------------------------
        # 最重要的驗證：
        #
        # 20D 有值時，必須真的有20筆
        # ----------------------------------------------------

        if (
            record.get(
                "main_force_20d"
            ) is not None
            and len(history) < 20
        ):

            raise RuntimeError(
                f"{symbol} 出現虛假的20D："
                f"history只有"
                f"{len(history)}筆"
            )

    log(
        f"測試股票："
        f"{len(TEST_STOCKS)}"
    )

    log(
        f"有效主力1D："
        f"{valid_1d}"
    )

    log(
        f"有效主力5D："
        f"{valid_5d}"
    )

    log(
        f"有效主力10D："
        f"{valid_10d}"
    )

    log(
        f"有效主力20D："
        f"{valid_20d}"
    )

    if valid_20d == len(
        TEST_STOCKS
    ):

        log(
            "✓ 四檔全部已有完整20D"
        )

    else:

        log(
            "ℹ️ 本次沒有足夠的20個"
            "CMoney「買賣超」交易日資料"
        )

    log(
        "✓ 資料來源欄位驗證完成"
    )

    log(
        "✓ 1D / 5D / 10D / 20D "
        "計算驗證完成"
    )

    log(
        "✓ 未使用5日集中 / "
        "20日集中 / 家數差"
    )


# ============================================================
# Save
# ============================================================

def save_chip(
    results,
    complete,
    partial,
    insufficient
):

    section(
        "寫入 Data/chip.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    now = datetime.now()

    valid_20d = sum(
        1
        for record in results.values()
        if record.get(
            "main_force_20d"
        ) is not None
    )

    output = {

        "schema_version":
            VERSION,

        "generated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "data_date":
            now.strftime(
                "%Y-%m-%d"
            ),

        "source":
            "CMoney",

        "universe_mode":
            "fixed_test_4",

        "universe_count":
            len(TEST_STOCKS),

        "test_symbols": [

            stock["symbol"]

            for stock
            in TEST_STOCKS

        ],

        "definition": {

            "main_force":
                "CMoney 主力進出之買賣超",

            "source_field":
                "買賣超",

            "main_force_1d":
                "最近1個交易日主力買賣超",

            "main_force_5d":
                "最近5個交易日每日主力買賣超加總",

            "main_force_10d":
                "最近10個交易日每日主力買賣超加總",

            "main_force_20d":
                "最近20個交易日每日主力買賣超加總",

            "history_method":
                "每次執行直接取得CMoney當次歷史資料",

            "unit":
                "張",

            "positive":
                "主力買超",

            "negative":
                "主力賣超",

            "forbidden_fields": [

                "5日集中",

                "20日集中",

                "家數差",

            ],
        },

        "history_accumulation": {

            "enabled":
                False,

            "target_days":
                TARGET_HISTORY,

            "current_valid_20d_stocks":
                valid_20d,

            "note":
                "不跨執行日累積；20D必須由本次取得之CMoney買賣超資料計算",
        },

        "statistics": {

            "complete":
                complete,

            "partial":
                partial,

            "insufficient":
                insufficient,

            "valid_20d":
                valid_20d,

        },

        "stocks":
            results,
    }

    temp_file = CHIP_FILE.with_suffix(
        ".json.tmp"
    )

    # --------------------------------------------------------
    # 寫入暫存
    # --------------------------------------------------------

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 寫入後重新讀取驗證
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    if not isinstance(
        verify,
        dict
    ):

        raise RuntimeError(
            "chip.json 頂層不是 object"
        )

    verify_stocks = verify.get(
        "stocks"
    )

    if not isinstance(
        verify_stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks "
            "不是 object"
        )

    if len(
        verify_stocks
    ) != len(TEST_STOCKS):

        raise RuntimeError(
            "chip.json 股票數量錯誤"
        )

    # --------------------------------------------------------
    # 驗證四檔股票
    # --------------------------------------------------------

    for stock in TEST_STOCKS:

        symbol = stock["symbol"]

        if symbol not in verify_stocks:

            raise RuntimeError(
                f"chip.json 缺少 "
                f"{symbol}"
            )

        record = verify_stocks[
            symbol
        ]

        history = record.get(
            "history",
            []
        )

        if not isinstance(
            history,
            list
        ):

            raise RuntimeError(
                f"{symbol} history "
                f"格式錯誤"
            )

        periods = calculate_periods(
            history
        )

        for field in [

            "main_force_1d",

            "main_force_5d",

            "main_force_10d",

            "main_force_20d",

        ]:

            if (
                record.get(field)
                != periods.get(field)
            ):

                raise RuntimeError(
                    f"{symbol} {field} "
                    f"寫入後驗證失敗"
                )

    # --------------------------------------------------------
    # 原子替換
    # --------------------------------------------------------

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 寫入成功"
    )

    log(
        f"輸出股票數："
        f"{len(TEST_STOCKS)}"
    )

    log(
        f"完整20D："
        f"{valid_20d}"
    )

    log(
        f"輸出檔案："
        f"{CHIP_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 72)

    log(
        "台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )

    log("=" * 72)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    log(
        "資料來源：CMoney 主力進出"
    )

    log(
        "指定欄位：買賣超"
    )

    log(
        "20D：本次取得20個交易日"
        "每日買賣超加總"
    )

    log(
        "固定測試："
        "2337 / 2426 / 2368 / 3081"
    )

    log(
        "禁止：5日集中 / "
        "20日集中 / 家數差"
    )

    try:

        # ----------------------------------------------------
        # 抓取
        # ----------------------------------------------------

        (
            results,
            complete,
            partial,
            insufficient
        ) = fetch_all()

        # ----------------------------------------------------
        # 驗證
        # ----------------------------------------------------

        validate(
            results
        )

        # ----------------------------------------------------
        # 儲存
        # ----------------------------------------------------

        save_chip(
            results,
            complete,
            partial,
            insufficient
        )

        elapsed = (
            time.time()
            - start_time
        )

        valid_20d = sum(
            1
            for record
            in results.values()
            if record.get(
                "main_force_20d"
            ) is not None
        )

        log("")
        log("=" * 72)

        log(
            f"✓ fetch_chip.py "
            f"{VERSION} 完成"
        )

        log("=" * 72)

        log(
            f"測試股票："
            f"{len(TEST_STOCKS)}"
        )

        log(
            f"完整20D："
            f"{valid_20d}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出："
            f"{CHIP_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)

        log(
            f"❌ fetch_chip.py "
            f"{VERSION} 執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
