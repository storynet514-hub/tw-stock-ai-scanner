#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
debug_chip_targets.py V1.0

目的：
    精確診斷指定股票在 fetch_chip.py 的
    Universe → CMoney Request → Response → Parser
    哪一層發生問題。

本程式：
    ✓ 只測 2337、2426
    ✓ 讀取 Data/universe.json
    ✓ 不修改任何資料
    ✓ 不寫入 chip.json
    ✓ 不跑 1985 檔
    ✓ 不建立任何輸出檔
    ✓ 只發指定股票的 HTTP Request

診斷層級：

    Layer 1  Universe
    Layer 2  Symbol normalization
    Layer 3  CMoney URL
    Layer 4  HTTP Request
    Layer 5  HTTP Response
    Layer 6  HTML / table detection
    Layer 7  Header detection
    Layer 8  日期欄位
    Layer 9  買賣超欄位
    Layer 10 1/5/10 日數值解析
"""

import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime

import requests


# ============================================================
# 基本設定
# ============================================================

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "Data" / "universe.json"

TARGETS = {
    "2337": "旺宏",
    "2426": "鼎元",
}

TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


# ============================================================
# 輸出工具
# ============================================================

def line(char="=", n=70):
    print(char * n)


def section(title):
    print()
    line("=")
    print(title)
    line("=")


def ok(msg):
    print(f"✓ {msg}")


def fail(msg):
    print(f"✗ {msg}")


def warn(msg):
    print(f"⚠️ {msg}")


# ============================================================
# Symbol normalization
# ============================================================

def normalize_symbol(value):
    """
    模擬 fetch_chip.py 的基本 symbol 正規化。
    """
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    # 2337.TW → 2337
    value = re.sub(r"\.(TW|TWO)$", "", value, flags=re.IGNORECASE)

    # 只接受台股股票代號
    if not re.fullmatch(r"\d{4,6}", value):
        return None

    return value


# ============================================================
# Universe
# ============================================================

def load_universe():

    section("Layer 1：讀取 Universe")

    print(f"Universe：{UNIVERSE_FILE}")

    if not UNIVERSE_FILE.exists():
        fail("Data/universe.json 不存在")
        return None

    try:
        with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        fail(f"Universe JSON 讀取失敗：{e}")
        return None

    print(f"JSON root type：{type(data).__name__}")

    if not isinstance(data, dict):
        fail("Universe root 不是 dict")
        return None

    items = data.get("items")

    if not isinstance(items, list):
        fail("Universe items 不是 list")
        return None

    print(f"Universe items：{len(items)}")

    found = {}

    for idx, item in enumerate(items):

        if not isinstance(item, dict):
            continue

        candidates = []

        for key in ("code", "symbol", "ticker"):
            if key in item:
                candidates.append(
                    (key, str(item.get(key)).strip())
                )

        for target in TARGETS:

            for key, raw in candidates:

                normalized = normalize_symbol(raw)

                if normalized == target:

                    found.setdefault(target, []).append(
                        {
                            "index": idx,
                            "source": key,
                            "raw": raw,
                            "item": item,
                        }
                    )

    return found


# ============================================================
# Print Universe result
# ============================================================

def inspect_universe(found):

    section("Layer 1：2337 / 2426 Universe 精確檢查")

    result = {}

    for symbol, name in TARGETS.items():

        print()
        print(f"目標：{symbol} {name}")

        matches = found.get(symbol, [])

        if not matches:
            fail("Universe 找不到")
            result[symbol] = None
            continue

        # 去除同一 item 因 code / symbol 造成的重複
        unique = {}

        for m in matches:
            idx = m["index"]
            unique[idx] = m

        matches = list(unique.values())

        print(f"找到 Universe item：{len(matches)} 筆")

        for m in matches:

            idx = m["index"]
            item = m["item"]

            print()
            print(f"items[{idx}]")
            print(f"來源欄位：{m['source']}")
            print(f"原始值：{m['raw']}")

            print("完整 item：")

            for k, v in item.items():
                print(f"  {k} = {v!r}")

        result[symbol] = matches[0]

        ok(f"{symbol} 已通過 Universe")

    return result


# ============================================================
# CMoney URL
# ============================================================

def build_urls(symbol):

    return [
        f"https://www.cmoney.tw/forum/stock/{symbol}?s=main-force",
        f"https://mobile.cmoney.tw/forum/stock/{symbol}?s=main-force",
    ]


# ============================================================
# HTTP Request
# ============================================================

def request_cmoney(symbol):

    section(f"Layer 3-5：CMoney Request / Response → {symbol}")

    urls = build_urls(symbol)

    for attempt, url in enumerate(urls, start=1):

        print()
        print(f"[Attempt {attempt}]")
        print(f"URL：{url}")

        start = time.time()

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            elapsed = time.time() - start

            print(f"耗時：{elapsed:.2f} 秒")
            print(f"HTTP status：{response.status_code}")
            print(f"Final URL：{response.url}")
            print(f"Content-Type：{response.headers.get('Content-Type')}")
            print(f"Content-Length header：{response.headers.get('Content-Length')}")

            html = response.text

            print(f"Response bytes：{len(response.content):,}")
            print(f"Response chars：{len(html):,}")

            if response.history:
                print("Redirect history：")
                for r in response.history:
                    print(
                        f"  {r.status_code} "
                        f"{r.url} → {response.url}"
                    )

            if response.status_code != 200:
                warn(
                    f"HTTP {response.status_code}，"
                    f"嘗試下一個 URL"
                )
                continue

            if not html.strip():
                warn("HTTP 200 但 Response 是空的")
                continue

            ok("CMoney HTTP Request 成功")

            return {
                "url": url,
                "final_url": response.url,
                "status": response.status_code,
                "html": html,
                "response": response,
            }

        except requests.exceptions.Timeout:
            fail(f"Request timeout：{TIMEOUT} 秒")

        except requests.exceptions.RequestException as e:
            fail(f"Request exception：{type(e).__name__}: {e}")

        except Exception as e:
            fail(f"未知錯誤：{type(e).__name__}: {e}")

    return None


# ============================================================
# HTML 基本診斷
# ============================================================

def inspect_html(html, symbol):

    section(f"Layer 6：HTML / Table Detection → {symbol}")

    if not html:
        fail("HTML 為空")
        return None

    print(f"HTML length：{len(html):,}")

    lower = html.lower()

    checks = {
        "<html": "<html",
        "<table": "<table",
        "<thead": "<thead",
        "<tbody": "<tbody",
        "買賣超": "買賣超",
        "主力": "主力",
        "main-force": "main-force",
        "cmoney": "cmoney",
    }

    print()
    print("關鍵字檢查：")

    for label, needle in checks.items():

        count = lower.count(needle.lower())

        if count > 0:
            ok(f"{label}：{count} 次")
        else:
            warn(f"{label}：0 次")

    # --------------------------------------------------------
    # 找 table
    # --------------------------------------------------------

    tables = re.findall(
        r"<table\b[^>]*>(.*?)</table>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    print()
    print(f"偵測到 table 數量：{len(tables)}")

    if not tables:

        fail("HTML 中完全找不到 <table>")

        # 印出前 1000 chars 協助判斷
        print()
        print("Response 前 1000 字元：")
        print("-" * 70)
        print(html[:1000])
        print("-" * 70)

        return None

    ok(f"找到 {len(tables)} 個 HTML table")

    return tables


# ============================================================
# Table Parser
# ============================================================

def strip_html(text):

    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
        flags=re.DOTALL,
    )

    text = text.replace("&nbsp;", " ")

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_tables(tables, symbol):

    section(f"Layer 7-10：Table Parser → {symbol}")

    for table_index, table in enumerate(tables):

        print()
        print(f"Table #{table_index}")

        rows = re.findall(
            r"<tr\b[^>]*>(.*?)</tr>",
            table,
            flags=re.IGNORECASE | re.DOTALL,
        )

        print(f"rows：{len(rows)}")

        if not rows:
            warn("沒有 tr")
            continue

        parsed_rows = []

        for row_index, row in enumerate(rows):

            cells = re.findall(
                r"<t[hd]\b[^>]*>(.*?)</t[hd]>",
                row,
                flags=re.IGNORECASE | re.DOTALL,
            )

            values = [
                strip_html(cell)
                for cell in cells
            ]

            if values:
                parsed_rows.append(values)

        if not parsed_rows:
            warn("沒有解析出任何 cell")
            continue

        # 顯示前 8 rows
        print()
        print("前 8 rows：")

        for i, values in enumerate(parsed_rows[:8]):

            print(f"  row[{i}] = {values}")

        # ----------------------------------------------------
        # Header detection
        # ----------------------------------------------------

        header_index = None
        header = None

        for i, row in enumerate(parsed_rows):

            joined = " ".join(row)

            if (
                "買賣超" in joined
                or "主力" in joined
                or "日期" in joined
            ):
                header_index = i
                header = row
                break

        print()

        if header is None:

            warn(
                f"Table #{table_index}："
                "找不到日期／主力／買賣超 header"
            )

            continue

        ok(
            f"找到候選 Header："
            f"row[{header_index}]"
        )

        print(f"Header：{header}")

        # ----------------------------------------------------
        # Column detection
        # ----------------------------------------------------

        date_col = None
        force_col = None

        for i, value in enumerate(header):

            normalized = value.replace(
                " ", ""
            ).replace(
                "\n", ""
            )

            if "日期" in normalized:
                date_col = i

            if (
                "買賣超" in normalized
                or "主力買賣超" in normalized
                or normalized == "主力"
            ):
                force_col = i

        print()

        print(f"日期欄：{date_col}")
        print(f"買賣超欄：{force_col}")

        if date_col is None:
            fail("找不到日期欄")
        else:
            ok(f"日期欄 = {date_col}")

        if force_col is None:
            fail("找不到買賣超欄")
        else:
            ok(f"買賣超欄 = {force_col}")

        # ----------------------------------------------------
        # Data rows
        # ----------------------------------------------------

        if date_col is None or force_col is None:
            continue

        data_rows = parsed_rows[
            header_index + 1:
        ]

        print()
        print(f"Header 後資料列：{len(data_rows)}")

        extracted = []

        for row in data_rows:

            if (
                len(row) <= date_col
                or len(row) <= force_col
            ):
                continue

            date_value = row[date_col]
            force_value = row[force_col]

            if not date_value:
                continue

            extracted.append(
                (
                    date_value,
                    force_value,
                )
            )

        print()
        print("解析出的日期 / 買賣超：")

        for date_value, force_value in extracted[:15]:

            print(
                f"  日期={date_value!r} "
                f"買賣超={force_value!r}"
            )

        if extracted:

            ok(
                f"Table #{table_index} 成功解析 "
                f"{len(extracted)} 筆資料"
            )

            # ------------------------------------------------
            # 嘗試數值
            # ------------------------------------------------

            print()
            print("數值轉換測試：")

            numeric_values = []

            for date_value, force_value in extracted:

                cleaned = (
                    str(force_value)
                    .replace(",", "")
                    .replace("+", "")
                    .strip()
                )

                match = re.search(
                    r"-?\d+(?:\.\d+)?",
                    cleaned,
                )

                if match:

                    try:
                        value = float(match.group(0))
                        numeric_values.append(value)

                        print(
                            f"  {date_value} "
                            f"→ {value}"
                        )

                    except ValueError:
                        pass

            if numeric_values:

                ok(
                    f"成功解析數值："
                    f"{len(numeric_values)} 筆"
                )

                print()
                print("★★★★★ Parser PASS ★★★★★")

                return {
                    "table_index": table_index,
                    "header_index": header_index,
                    "date_col": date_col,
                    "force_col": force_col,
                    "rows": extracted,
                    "values": numeric_values,
                }

            fail("有資料列，但無法解析買賣超數值")

    fail(
        "所有 table 都無法完成 "
        "日期 + 買賣超解析"
    )

    return None


# ============================================================
# 單一股票完整診斷
# ============================================================

def debug_target(symbol, name, universe_info):

    section(
        f"開始診斷：{symbol} {name}"
    )

    print(
        f"時間："
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    if not universe_info:

        fail(
            f"{symbol} 根本不在 Universe"
        )

        return False

    ok(
        f"{symbol} 已存在 Universe"
    )

    item = universe_info["item"]

    print()
    print("Universe item：")

    for key, value in item.items():
        print(f"  {key} = {value!r}")

    # --------------------------------------------------------
    # Symbol normalization
    # --------------------------------------------------------

    section(
        f"Layer 2：Symbol Normalization → {symbol}"
    )

    raw_candidates = []

    for key in ("code", "symbol", "ticker"):

        if key in item:

            raw = item.get(key)

            normalized = normalize_symbol(raw)

            print()
            print(f"欄位：{key}")
            print(f"raw：{raw!r}")
            print(f"normalized：{normalized!r}")

            if normalized == symbol:
                ok(
                    f"{key} 正規化後 = {symbol}"
                )
            else:
                warn(
                    f"{key} 正規化後 != {symbol}"
                )

            raw_candidates.append(
                (key, normalized)
            )

    normalized = normalize_symbol(
        item.get("code")
        or item.get("symbol")
    )

    if normalized != symbol:

        fail(
            f"Symbol normalization FAIL："
            f"{normalized!r}"
        )

        return False

    ok(
        f"Symbol normalization PASS：{symbol}"
    )

    # --------------------------------------------------------
    # CMoney
    # --------------------------------------------------------

    result = request_cmoney(symbol)

    if result is None:

        fail(
            f"{symbol}：CMoney Request 全部失敗"
        )

        return False

    html = result["html"]

    # --------------------------------------------------------
    # HTML
    # --------------------------------------------------------

    tables = inspect_html(
        html,
        symbol
    )

    if tables is None:

        fail(
            f"{symbol}：HTTP 成功，但 HTML 沒有可解析 table"
        )

        return False

    # --------------------------------------------------------
    # Parser
    # --------------------------------------------------------

    parsed = parse_tables(
        tables,
        symbol
    )

    if parsed is None:

        fail(
            f"{symbol}：CMoney response 存在，"
            "但 parser FAIL"
        )

        return False

    ok(
        f"{symbol}：CMoney + Parser 全部 PASS"
    )

    return True


# ============================================================
# Main
# ============================================================

def main():

    section(
        "台股 AI 選股系統 debug_chip_targets.py V1.0"
    )

    print("本程式只測試：2337 旺宏、2426 鼎元")
    print("不跑 1985 檔")
    print("不寫 chip.json")
    print("不修改任何資料")
    print("只發指定股票的 CMoney HTTP Request")

    # --------------------------------------------------------
    # Load Universe
    # --------------------------------------------------------

    found = load_universe()

    if found is None:
        sys.exit(1)

    universe_results = inspect_universe(found)

    # --------------------------------------------------------
    # Debug targets
    # --------------------------------------------------------

    results = {}

    for symbol, name in TARGETS.items():

        info = universe_results.get(symbol)

        results[symbol] = debug_target(
            symbol,
            name,
            info
        )

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    section("最終診斷結果")

    for symbol, name in TARGETS.items():

        if results.get(symbol):
            ok(
                f"{symbol} {name}："
                "Universe + CMoney + Parser PASS"
            )
        else:
            fail(
                f"{symbol} {name}："
                "存在至少一層 FAIL"
            )

    print()
    line("=")
    print("診斷完成")
    line("=")

    print()
    print("判讀方式：")
    print()
    print("1. Universe FAIL")
    print("   → Universe 問題")
    print()
    print("2. HTTP Request FAIL")
    print("   → CMoney URL / 網路 / HTTP 問題")
    print()
    print("3. HTTP 200 + table = 0")
    print("   → CMoney 回傳內容改變，或拿到非預期頁面")
    print()
    print("4. 找不到 Header")
    print("   → CMoney HTML 結構 / parser 問題")
    print()
    print("5. 找到 Header 但找不到買賣超欄")
    print("   → parser 欄位辨識問題")
    print()
    print("6. 有資料但數值解析失敗")
    print("   → parser 數值格式問題")
    print()
    print("7. Parser PASS")
    print("   → 2337 / 2426 的 CMoney 抓取本身正常")
    print("   → 必須繼續查 fetch_chip.py 後面的資料寫入 / 合併流程")


if __name__ == "__main__":
    main()
