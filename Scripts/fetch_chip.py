#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V6.0 全市場版

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
本版本資料邏輯
============================================================

每次 GitHub Actions 執行：

1. 讀取 Data/universe.json
2. 對全市場股票逐檔取得 CMoney 主力進出頁面
3. 只解析「日期」與「買賣超」
4. 當次直接取得最多 20 個交易日資料
5. 依日期排序
6. 使用當次抓取的資料直接計算：
   1D / 5D / 10D / 20D

不使用上一版 chip.json 補資料。

不做跨日歷史累積。

因此：

如果 CMoney 該股票本身提供 20 個交易日：
    → main_force_20d 正常產生

如果 CMoney 該股票本身只有不足 20 個交易日：
    → main_force_20d = None

============================================================
資料取得原則
============================================================

只使用 CMoney 已存在的：

Desktop URL
Mobile URL

不：

探測 API
猜 pagination
使用 API
使用 URL 延伸資料
使用其他籌碼欄位
使用歷史 chip.json 補足資料

當 Desktop 頁面與 Mobile 頁面都可以取得資料時：

選擇「有效買賣超資料筆數較多」的結果。

目的只有：
避免其中一個頁面只回傳 10 筆而另一個頁面
實際提供較完整資料時被錯誤採用。

============================================================
全市場
============================================================

讀取：

Data/universe.json

使用：

listed_stocks
otc_stocks

或 universe.json 中的股票 items。

不固定測試股票。

============================================================
輸出
============================================================

Data/chip.json

每次直接以當次 CMoney 資料重新產生。

不依賴上一版 chip.json。

============================================================
"""

from __future__ import annotations

import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "V6.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

CHIP_FILE = DATA_DIR / "chip.json"

UNIVERSE_FILE = DATA_DIR / "universe.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.5

MAX_HISTORY = 20


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

def request_cmoney_pages(
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

    pages = []

    for url in urls:

        try:

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

            pages.append({
                "html": html,
                "url": url,
            })

        except Exception as exc:

            log(
                f"⚠️ CMoney 頁面取得失敗："
                f"{url}"
            )

            log(
                f"   原因：{exc}"
            )

    if not pages:

        raise RuntimeError(
            "CMoney Desktop / Mobile 頁面皆無法取得"
        )

    return pages


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

    target_tables = []

    # ========================================================
    # 第一階段：
    # 找真正同時具有：
    #
    # 日期
    # 買賣超
    #
    # 的 table
    # ========================================================

    for table in tables:

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        table_indexes = None

        for header_row in rows[:15]:

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

                table_indexes = (
                    date_index,
                    force_index
                )

                break

        if table_indexes is not None:

            target_tables.append(
                (
                    table,
                    table_indexes[0],
                    table_indexes[1]
                )
            )

    # ========================================================
    # 沒找到
    # ========================================================

    if not target_tables:

        return []

    best_result = []

    # ========================================================
    # 解析所有符合條件的 table
    # ========================================================

    for (
        table,
        best_date_index,
        best_force_index
    ) in target_tables:

        rows = table.find_all(
            "tr"
        )

        for row in rows:

            cells = row.find_all(
                ["th", "td"]
            )

            if len(cells) <= max(
                best_date_index,
                best_force_index
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
                values[best_date_index]
            )

            if not date_value:
                continue

            force_value = parse_number(
                values[best_force_index]
            )

            if force_value is None:
                continue

            best_result.append({
                "date": date_value,
                "main_force": force_value,
            })

    # ========================================================
    # 去重
    # ========================================================

    unique = {}

    for row in best_result:

        unique[
            row["date"]
        ] = row["main_force"]

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

    # ========================================================
    # 一次只保留最近20個交易日
    # ========================================================

    return result[:MAX_HISTORY]


# ============================================================
# 取得 CMoney 當次資料
# ============================================================

def fetch_current_history(
    session,
    symbol
):

    pages = request_cmoney_pages(
        session,
        symbol
    )

    candidates = []

    for page in pages:

        html = page["html"]
        url = page["url"]

        history = parse_cmoney_main_force(
            html
        )

        log(
            f"CMoney 頁面：{url}"
        )

        log(
            f"有效「買賣超」："
            f"{len(history)} 筆"
        )

        if history:

            log(
                "✓ 已確認資料來源欄位：買賣超"
            )

        candidates.append({
            "history": history,
            "source_url": url,
        })

    # ========================================================
    # 選擇有效資料較多的頁面
    #
    # 不使用歷史資料補足
    # 不使用 API
    # 不猜 pagination
    # ========================================================

    candidates.sort(
        key=lambda item: len(
            item["history"]
        ),
        reverse=True
    )

    best = candidates[0]

    return (
        best["history"],
        best["source_url"]
    )


# ============================================================
# 讀取 Universe
# ============================================================

def load_universe():

    section(
        "讀取台股 Universe"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到 Universe："
            f"{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "universe.json 頂層不是 object"
        )

    stocks = []

    # ========================================================
    # 主要格式：
    #
    # listed_stocks
    # otc_stocks
    # ========================================================

    listed = data.get(
        "listed_stocks",
        []
    )

    otc = data.get(
        "otc_stocks",
        []
    )

    if isinstance(
        listed,
        list
    ):

        stocks.extend(
            listed
        )

    if isinstance(
        otc,
        list
    ):

        stocks.extend(
            otc
        )

    # ========================================================
    # 如果 Universe 使用 items
    # ========================================================

    if not stocks:

        items = data.get(
            "items",
            []
        )

        if isinstance(
            items,
            list
        ):

            stocks = items

    if not stocks:

        raise RuntimeError(
            "universe.json 沒有有效股票資料"
        )

    result = []

    seen = set()

    for item in stocks:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = str(
            item.get(
                "symbol",
                item.get(
                    "code",
                    ""
                )
            )
        ).strip()

        if not symbol:
            continue

        symbol = re.sub(
            r"\.TW$|\.TWO$",
            "",
            symbol,
            flags=re.IGNORECASE
        )

        if symbol in seen:
            continue

        seen.add(symbol)

        name = str(
            item.get(
                "name",
                ""
            )
        ).strip()

        market = str(
            item.get(
                "market",
                ""
            )
        ).strip()

        result.append({
            "symbol": symbol,
            "name": name,
            "market": market,
        })

    if not result:

        raise RuntimeError(
            "Universe 沒有任何有效股票"
        )

    log(
        f"Universe 有效股票："
        f"{len(result)}"
    )

    return result


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
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
        "history_count": len(values),
    }

    if len(values) >= 1:

        result["main_force_1d"] = round(
            values[0],
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

    section(
        f"CMoney 主力買賣超："
        f"{symbol} {name}"
    )

    # --------------------------------------------------------
    # 直接取得當次資料
    # --------------------------------------------------------

    history, source_url = (
        fetch_current_history(
            session,
            symbol
        )
    )

    log(
        f"採用資料來源："
        f"{source_url}"
    )

    log(
        f"當次有效歷史："
        f"{len(history)} 筆"
    )

    # --------------------------------------------------------
    # 計算
    # --------------------------------------------------------

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
        f"歷史筆數："
        f"{len(history)}"
    )

    if len(history) >= 20:

        log(
            "✓ 已取得完整20個交易日"
        )

    else:

        log(
            "ℹ️ CMoney 當次資料不足20個交易日"
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
# 建立失敗紀錄
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

def fetch_all(
    universe
):

    section(
        "開始 CMoney 主力買賣超更新"
    )

    log(
        "本版本為全市場 Universe 模式"
    )

    log(
        f"讀取：{UNIVERSE_FILE}"
    )

    log(
        "只使用 CMoney 首頁「買賣超」"
    )

    log(
        "不探測 API"
    )

    log(
        "不猜 pagination"
    )

    log(
        "不使用上一版 chip.json 補足20D"
    )

    log(
        "20D = 當次直接取得的最近20個交易日"
    )

    log(
        f"本次掃描股票：{len(universe)}"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    complete = 0
    partial = 0
    insufficient = 0

    total = len(universe)

    success_requests = 0
    failed_requests = 0

    for index, stock in enumerate(
        universe,
        start=1
    ):

        symbol = stock["symbol"]
        name = stock["name"]

        log(
            f"[{index}/{total}] "
            f"{symbol} {name}"
        )

        try:

            record = fetch_stock(
                session,
                stock
            )

            results[symbol] = record

            success_requests += 1

            status = record["status"]

            if status == "complete":

                complete += 1

            elif status.startswith(
                "partial"
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

            results[symbol] = record

            failed_requests += 1

            insufficient += 1

        time.sleep(
            REQUEST_DELAY
        )

    log("")
    log(
        f"掃描完成："
        f"{len(results)}/{total}"
    )

    log(
        f"成功請求："
        f"{success_requests}"
    )

    log(
        f"失敗請求："
        f"{failed_requests}"
    )

    return (
        results,
        complete,
        partial,
        insufficient,
        success_requests,
        failed_requests
    )


# ============================================================
# Validate
# ============================================================

def validate(
    results,
    universe
):

    section(
        "最終資料驗證"
    )

    if len(results) != len(universe):

        raise RuntimeError(
            "輸出股票數量與 Universe 不一致"
        )

    valid_1d = 0
    valid_5d = 0
    valid_10d = 0
    valid_20d = 0

    for stock in universe:

        symbol = stock["symbol"]

        if symbol not in results:

            raise RuntimeError(
                f"缺少股票：{symbol}"
            )

        record = results[symbol]

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

        # ----------------------------------------------------
        # 確認當次 history 與 1D/5D/10D/20D 計算一致
        # ----------------------------------------------------

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

        if len(history) > MAX_HISTORY:

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
        f"Universe 股票："
        f"{len(universe)}"
    )

    log(
        f"輸出股票："
        f"{len(results)}"
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

    if valid_20d > 0:

        log(
            f"✓ 已取得完整20D："
            f"{valid_20d}"
        )

    else:

        log(
            "⚠️ 目前沒有股票取得完整20D"
        )

    log(
        "✓ 資料來源欄位驗證完成"
    )

    log(
        "✓ 1D / 5D / 10D / 20D 計算驗證完成"
    )

    log(
        "✓ 未使用 5日集中 / 20日集中 / 家數差"
    )

    log(
        "✓ 未使用上一版 chip.json 累積20D"
    )


# ============================================================
# Save
# ============================================================

def save_chip(
    results,
    universe,
    complete,
    partial,
    insufficient,
    success_requests,
    failed_requests
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
            "full_market",

        "universe_count":
            len(universe),

        "statistics": {

            "complete":
                complete,

            "partial":
                partial,

            "insufficient":
                insufficient,

            "valid_1d":
                sum(
                    1
                    for record in results.values()
                    if record.get(
                        "main_force_1d"
                    ) is not None
                ),

            "valid_5d":
                sum(
                    1
                    for record in results.values()
                    if record.get(
                        "main_force_5d"
                    ) is not None
                ),

            "valid_10d":
                sum(
                    1
                    for record in results.values()
                    if record.get(
                        "main_force_10d"
                    ) is not None
                ),

            "valid_20d":
                valid_20d,

            "success_requests":
                success_requests,

            "failed_requests":
                failed_requests,
        },

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
                "每次直接取得CMoney當次最近20個交易日資料",

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
            "chip.json stocks 不是 object"
        )

    if len(
        verify_stocks
    ) != len(universe):

        raise RuntimeError(
            "chip.json 股票數量與 Universe 不一致"
        )

    # --------------------------------------------------------
    # 驗證所有股票
    # --------------------------------------------------------

    for stock in universe:

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

        if not isinstance(
            history,
            list
        ):

            raise RuntimeError(
                f"{symbol} history 格式錯誤"
            )

        if len(history) > MAX_HISTORY:

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

            if record.get(field) != periods.get(field):

                raise RuntimeError(
                    f"{symbol} {field} "
                    "寫入後驗證失敗"
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
        f"{len(universe)}"
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
        "20D：當次直接取得最近20日買賣超加總"
    )

    log(
        "模式：全市場台股 Universe"
    )

    log(
        "禁止：5日集中 / 20日集中 / 家數差"
    )

    try:

        # ----------------------------------------------------
        # Universe
        # ----------------------------------------------------

        universe = load_universe()

        # ----------------------------------------------------
        # 抓取
        # ----------------------------------------------------

        (
            results,
            complete,
            partial,
            insufficient,
            success_requests,
            failed_requests
        ) = fetch_all(
            universe
        )

        # ----------------------------------------------------
        # 驗證
        # ----------------------------------------------------

        validate(
            results,
            universe
        )

        # ----------------------------------------------------
        # 儲存
        # ----------------------------------------------------

        save_chip(
            results,
            universe,
            complete,
            partial,
            insufficient,
            success_requests,
            failed_requests
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
            f"Universe 股票："
            f"{len(universe)}"
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
            f"❌ fetch_chip.py {VERSION} 執行失敗"
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
