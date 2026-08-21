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

需求只有一個：

「本次執行直接取得 CMoney 的 20 個交易日買賣超」

不使用：

- 上一版 chip.json 補足 20D
- 跨執行日歷史累積
- 5日集中
- 20日集中
- 家數差
- 其他籌碼欄位
- API 探測
- 猜測 API
- Universe
- 全市場掃描

CMoney 目前首頁 HTML 只直接呈現 10 筆。

因此本版本會：

1. 取得 CMoney 主力進出首頁
2. 解析首頁「日期 + 買賣超」
3. 檢查頁面本身提供的「查看更多」相關連結
4. 檢查頁面本身提供的延伸資料
5. 檢查 HTML / script 中已存在的結構化資料
6. 只接受真正的「日期 + 買賣超」
7. 合併同一股票本次取得的資料
8. 依日期去重
9. 依日期由新到舊排序
10. 最多取 20 個交易日
11. >= 20 筆才計算 main_force_20d

重要：

這不是歷史累積。

每次執行都必須重新取得本次 20D。

如果 CMoney 本次實際只提供 10 筆，
main_force_20d 保持 None。

============================================================
固定測試
============================================================

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
from urllib.parse import (
    urljoin,
    urlparse,
)

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

    return (
        text
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
        .replace("\u3000", "")
        .strip()
    )


# ============================================================
# 嚴格判斷「買賣超」
# ============================================================

def is_main_force_header(text):

    return (
        normalize_header(text)
        == "買賣超"
    )


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
# 首頁
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

    raise last_error or RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# 找欄位
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

        if (
            date_index is None
            and normalized == "日期"
        ):

            date_index = index

        if (
            force_index is None
            and normalized == "買賣超"
        ):

            force_index = index

    return (
        date_index,
        force_index
    )


# ============================================================
# 解析 HTML table
# ============================================================

def parse_cmoney_tables(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    results = []

    for table in soup.find_all("table"):

        rows = table.find_all("tr")

        if not rows:
            continue

        target_header_position = None

        date_index = None

        force_index = None

        # ----------------------------------------------------
        # 找目標 header
        # ----------------------------------------------------

        for position, row in enumerate(
            rows[:20]
        ):

            cells = row.find_all(
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

            di, fi = find_column_indexes(
                headers
            )

            if (
                di is not None
                and fi is not None
            ):

                target_header_position = (
                    position
                )

                date_index = di

                force_index = fi

                break

        if target_header_position is None:
            continue

        # ----------------------------------------------------
        # 解析資料列
        # ----------------------------------------------------

        for row in rows[
            target_header_position + 1:
        ]:

            cells = row.find_all(
                ["th", "td"]
            )

            if len(cells) <= max(
                date_index,
                force_index
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
                values[date_index]
            )

            if not date_value:
                continue

            force_value = parse_number(
                values[force_index]
            )

            if force_value is None:
                continue

            results.append({
                "date": date_value,
                "main_force": force_value,
            })

    return results


# ============================================================
# 解析 HTML 中已存在的結構化資料
#
# 注意：
# 不建立 API。
# 不猜 API。
# 只讀 CMoney 已經送回來的 HTML。
# ============================================================

def parse_embedded_data(html):

    results = []

    # --------------------------------------------------------
    # 方式一：
    # 找 HTML 中所有日期 + 買賣超附近的資料
    # --------------------------------------------------------

    date_pattern = (
        r"(20\d{2}[/-]\d{1,2}[/-]\d{1,2})"
    )

    # --------------------------------------------------------
    # script / JSON / data attribute
    # --------------------------------------------------------

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    script_texts = []

    for script in soup.find_all("script"):

        text = script.string

        if text:
            script_texts.append(text)

        else:
            text = script.get_text(
                " ",
                strip=False
            )

            if text:
                script_texts.append(text)

    # --------------------------------------------------------
    # 只處理明確包含「買賣超」的 script
    # --------------------------------------------------------

    for text in script_texts:

        if "買賣超" not in text:
            continue

        # ----------------------------------------------------
        # 找日期附近的數字
        #
        # 這裡不接受任意數字。
        # 必須是日期附近的資料。
        # ----------------------------------------------------

        for match in re.finditer(
            date_pattern,
            text
        ):

            raw_date = match.group(1)

            date_value = normalize_date(
                raw_date
            )

            if not date_value:
                continue

            start = match.start()

            end = min(
                len(text),
                match.end() + 500
            )

            fragment = text[
                start:end
            ]

            # ------------------------------------------------
            # 優先找：
            #
            # 買賣超 : 數字
            # 買賣超":數字
            # ------------------------------------------------

            patterns = [
                r"買賣超[^0-9+\-]{0,30}"
                r"([-+]?\d[\d,]*(?:\.\d+)?)",

                r"買賣超.{0,80}?"
                r"([-+]?\d[\d,]*(?:\.\d+)?)",
            ]

            found = None

            for pattern in patterns:

                value_match = re.search(
                    pattern,
                    fragment,
                    re.S
                )

                if value_match:

                    found = parse_number(
                        value_match.group(1)
                    )

                    if found is not None:
                        break

            if found is None:
                continue

            results.append({
                "date": date_value,
                "main_force": found,
            })

    return results


# ============================================================
# 找「查看更多」相關頁面
#
# 不猜 API。
# 只使用 CMoney HTML 本身提供的：
#
# href
# data-href
# data-url
# data-link
# onclick
#
# ============================================================

def find_more_links(
    html,
    source_url
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    candidates = []

    # --------------------------------------------------------
    # 所有 anchor
    # --------------------------------------------------------

    for tag in soup.find_all(
        ["a", "button"]
    ):

        text = tag.get_text(
            " ",
            strip=True
        )

        attributes = []

        for attr in [
            "href",
            "data-href",
            "data-url",
            "data-link",
            "onclick",
        ]:

            value = tag.get(attr)

            if value:
                attributes.append(
                    str(value)
                )

        combined = (
            text
            + " "
            + " ".join(attributes)
        )

        if not any(
            keyword in combined
            for keyword in [
                "查看更多",
                "更多",
                "more",
                "More",
            ]
        ):

            continue

        for attribute in attributes:

            # ------------------------------------------------
            # 找完整 URL
            # ------------------------------------------------

            urls = re.findall(
                r"https?://[^\s\"'<>]+",
                attribute
            )

            for url in urls:

                candidates.append(url)

            # ------------------------------------------------
            # 找 href/path
            # ------------------------------------------------

            if (
                attribute.startswith("/")
                or attribute.startswith("?")
            ):

                candidates.append(
                    urljoin(
                        source_url,
                        attribute
                    )
                )

    # --------------------------------------------------------
    # 去重 + 僅允許 CMoney
    # --------------------------------------------------------

    result = []

    seen = set()

    source_host = urlparse(
        source_url
    ).netloc

    for url in candidates:

        url = url.strip()

        if not url:
            continue

        parsed = urlparse(url)

        if parsed.netloc:
            if (
                parsed.netloc
                not in {
                    source_host,
                    "www.cmoney.tw",
                    "mobile.cmoney.tw",
                }
            ):
                continue

        if url in seen:
            continue

        seen.add(url)

        result.append(url)

    return result


# ============================================================
# 合併本次資料
# ============================================================

def merge_current_data(
    *datasets
):

    combined = {}

    for dataset in datasets:

        for row in dataset:

            if not isinstance(
                row,
                dict
            ):
                continue

            date = normalize_date(
                row.get("date")
            )

            value = parse_number(
                row.get("main_force")
            )

            if not date:
                continue

            if value is None:
                continue

            combined[date] = float(
                value
            )

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in combined.items()
    ]

    result.sort(
        key=lambda row: datetime.strptime(
            row["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    return result[:TARGET_HISTORY]


# ============================================================
# 計算 1D / 5D / 10D / 20D
# ============================================================

def calculate_periods(history):

    values = [
        float(row["main_force"])
        for row in history
        if row.get("main_force") is not None
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
        "history_count": len(values),
    }

    if len(values) >= 1:

        result["main_force_1d"] = round(
            sum(values[:1]),
            2
        )

    if len(values) >= 5:

        result["main_force_5d"] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result["main_force_10d"] = round(
            sum(values[:10]),
            2
        )

    if len(values) >= 20:

        result["main_force_20d"] = round(
            sum(values[:20]),
            2
        )

    return result


# ============================================================
# Status
# ============================================================

def get_status(periods):

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

    section(
        f"CMoney 主力買賣超："
        f"{symbol} {name}"
    )

    # --------------------------------------------------------
    # 首頁
    # --------------------------------------------------------

    html, source_url = (
        request_cmoney_page(
            session,
            symbol
        )
    )

    homepage_data = (
        parse_cmoney_tables(
            html
        )
    )

    log(
        f"CMoney 首頁有效「買賣超」："
        f"{len(homepage_data)} 筆"
    )

    if homepage_data:

        log(
            "✓ 已確認資料來源欄位：買賣超"
        )

    else:

        log(
            "❌ 首頁沒有找到有效"
            "「日期 + 買賣超」"
        )

    # --------------------------------------------------------
    # HTML 已嵌入資料
    # --------------------------------------------------------

    embedded_data = (
        parse_embedded_data(
            html
        )
    )

    if embedded_data:

        log(
            "✓ HTML 內存在額外結構化"
            "「買賣超」資料："
            f"{len(embedded_data)} 筆"
        )

    # --------------------------------------------------------
    # CMoney 頁面自己提供的查看更多
    # --------------------------------------------------------

    more_links = find_more_links(
        html,
        source_url
    )

    if more_links:

        log(
            "✓ 找到 CMoney 頁面提供的"
            "延伸資料連結："
            f"{len(more_links)} 個"
        )

    # --------------------------------------------------------
    # 嘗試 CMoney 自己提供的延伸頁面
    #
    # 最多補到 20 筆。
    # --------------------------------------------------------

    extension_data = []

    for index, url in enumerate(
        more_links,
        start=1
    ):

        if len(
            merge_current_data(
                homepage_data,
                embedded_data,
                extension_data
            )
        ) >= TARGET_HISTORY:

            break

        try:

            extension_html = request_url(
                session,
                url
            )

            page_data = (
                parse_cmoney_tables(
                    extension_html
                )
            )

            page_embedded = (
                parse_embedded_data(
                    extension_html
                )
            )

            before = len(
                extension_data
            )

            extension_data.extend(
                page_data
            )

            extension_data.extend(
                page_embedded
            )

            after = len(
                extension_data
            )

            log(
                f"延伸頁面 [{index}]："
                f"新增候選資料 "
                f"{after - before} 筆"
            )

        except Exception as exc:

            log(
                f"ℹ️ 延伸頁面無法讀取："
                f"{exc}"
            )

    # --------------------------------------------------------
    # 最終只使用本次取得資料
    # --------------------------------------------------------

    history = merge_current_data(
        homepage_data,
        embedded_data,
        extension_data
    )

    log(
        f"本次最終取得有效交易日："
        f"{len(history)}"
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

    log(
        f"本次歷史筆數："
        f"{len(history)}"
    )

    if len(history) >= 20:

        log(
            "✓ 已取得完整 20 個交易日"
        )

    else:

        log(
            "ℹ️ 本次 CMoney 實際只取得 "
            f"{len(history)} 筆"
        )

    return {
        "symbol": symbol,
        "name": name,
        "market": stock["market"],

        "source": "CMoney",

        "source_url": source_url,

        "source_field": "買賣超",

        "main_force_1d":
            periods["main_force_1d"],

        "main_force_5d":
            periods["main_force_5d"],

        "main_force_10d":
            periods["main_force_10d"],

        "main_force_20d":
            periods["main_force_20d"],

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
# 失敗紀錄
# ============================================================

def build_error_record(
    stock,
    error
):

    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "market": stock["market"],

        "source": "CMoney",

        "source_url":
            CMONEY_URL.format(
                symbol=stock["symbol"]
            ),

        "source_field": "買賣超",

        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,

        "history_count": 0,

        "status": "insufficient",

        "history": [],

        "error": str(error),
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
        "不使用歷史 chip.json 累積"
    )

    log(
        "不使用 5日集中 / 20日集中 / 家數差"
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

        log(
            f"[{index}/{total}] "
            f"{stock['symbol']} "
            f"{stock['name']}"
        )

        try:

            record = fetch_stock(
                session,
                stock
            )

            results[
                stock["symbol"]
            ] = record

            if (
                record["main_force_20d"]
                is not None
            ):

                complete += 1

            elif (
                record["main_force_10d"]
                is not None
            ):

                partial += 1

            else:

                insufficient += 1

        except Exception as exc:

            log(
                f"❌ {stock['symbol']} "
                f"取得失敗：{exc}"
            )

            results[
                stock["symbol"]
            ] = build_error_record(
                stock,
                exc
            )

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

def validate(results):

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
                f"缺少測試股票：{symbol}"
            )

        record = results[symbol]

        history = record.get(
            "history",
            []
        )

        if not isinstance(
            history,
            list
        ):

            raise RuntimeError(
                f"{symbol} history 格式錯誤"
            )

        # ----------------------------------------------------
        # 確認資料真的只有本次抓取
        # ----------------------------------------------------

        if len(history) > TARGET_HISTORY:

            raise RuntimeError(
                f"{symbol} 超過20筆資料"
            )

        periods = calculate_periods(
            history
        )

        if (
            record.get(
                "main_force_1d"
            )
            is not None
        ):

            valid_1d += 1

        if (
            record.get(
                "main_force_5d"
            )
            is not None
        ):

            valid_5d += 1

        if (
            record.get(
                "main_force_10d"
            )
            is not None
        ):

            valid_10d += 1

        if (
            record.get(
                "main_force_20d"
            )
            is not None
        ):

            valid_20d += 1

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
            for stock in TEST_STOCKS
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
                "本次執行直接取得CMoney當次20個交易日資料",

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
                20,

            "note":
                "不使用跨執行日歷史累積",
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
    # 寫入後驗證
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
            "chip.json stocks 不是 object"
        )

    if len(
        verify_stocks
    ) != len(TEST_STOCKS):

        raise RuntimeError(
            "chip.json 股票數量錯誤"
        )

    for stock in TEST_STOCKS:

        symbol = stock["symbol"]

        if symbol not in verify_stocks:

            raise RuntimeError(
                f"chip.json 缺少 {symbol}"
            )

        record = verify_stocks[symbol]

        history = record.get(
            "history",
            []
        )

        if len(history) > TARGET_HISTORY:

            raise RuntimeError(
                f"{symbol} history 超過20筆"
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
                    "寫入後驗證失敗"
                )

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
        "20D：本次直接取得20個交易日"
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

        (
            results,
            complete,
            partial,
            insufficient
        ) = fetch_all()

        validate(
            results
        )

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
            for record in results.values()
            if record.get(
                "main_force_20d"
            ) is not None
        )

        log("")
        log("=" * 72)

        log(
            f"✓ fetch_chip.py {VERSION} 完成"
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
            f"❌ fetch_chip.py {VERSION} "
            "執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        if CHIP_FILE.exists():

            log(
                "⚠️ 保留既有 chip.json"
            )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
