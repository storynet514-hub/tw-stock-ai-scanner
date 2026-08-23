#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V6.0.0

============================================================
目的
============================================================
建立 Data/universe.json

本版本重點：
1. TWSE / TPEX 股票池來源分離
2. 禁止空白股票名稱進入 universe.json
3. TWSE 主 API 失敗時，使用官方 ISIN 清單作為名稱/股票池備援
4. 保留既有 universe.json，不因 API timeout 被錯誤覆蓋
5. 固定驗證：
   2337 = 旺宏
   2426 = 鼎元
   2368 = 金像電
   3081 = 聯亞
6. 任何固定標的名稱錯誤 → Build FAILED
7. 不允許重複 symbol
8. 不允許空 symbol
9. 不允許空 name
10. 產生完整統計：
    TWSE / TPEX / Stock / ETF / Total
11. Atomic Write
12. 新資料驗證完成後才覆蓋正式 universe.json

============================================================
重要
============================================================
本程式只負責建立 universe。

不負責：
- 三大法人
- 全券商分點
- 主力買賣超
- 當沖
- 資券
- 股價
- RSI
- MACD
- KD

上述資料由後續 fetch_chip.py / fetch_data.py 處理。
============================================================
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


VERSION = "V6.0.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
OUTPUT_FILE = DATA_DIR / "universe.json"
BACKUP_FILE = DATA_DIR / "universe.json.bak"

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, text/plain, text/csv, "
        "text/html, */*"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.twse.com.tw/",
}

# ============================================================
# 安全門檻
# ============================================================

MIN_TWSE = 700
MIN_TPEX = 300
MIN_TOTAL = 1200

# ============================================================
# 固定驗證標的
# ============================================================

REQUIRED_TEST_STOCKS = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}


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
# 基本工具
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    # 移除 BOM
    text = text.replace("\ufeff", "")

    # 清理空白
    text = text.strip()

    return text


def clean_symbol(value: Any) -> str:
    text = clean_text(value)

    text = text.upper()

    text = text.replace(".TW", "")
    text = text.replace(".TWO", "")

    # 僅保留代號本體
    match = re.search(r"\b(\d{4,6})\b", text)

    if match:
        return match.group(1)

    return ""


def is_stock_symbol(symbol: str) -> bool:
    return bool(
        re.fullmatch(r"\d{4}", symbol)
    )


def is_etf_symbol(symbol: str) -> bool:
    """
    台股 ETF 常見以 00 開頭。

    這裡只做類型分類，不作為名稱來源。
    """
    return bool(
        re.fullmatch(r"00\d{2,4}", symbol)
    )


def classify_type(symbol: str, raw_type: str = "") -> str:
    raw = clean_text(raw_type).lower()

    if "etf" in raw:
        return "ETF"

    if is_etf_symbol(symbol):
        return "ETF"

    return "Stock"


def classify_market(
    raw_market: str,
    full_symbol: str = "",
) -> str:
    raw = clean_text(raw_market).upper()
    full = clean_text(full_symbol).upper()

    if "TPEX" in raw or "TPEx".upper() in raw:
        return "TPEX"

    if "TWO" in full:
        return "TPEX"

    if ".TWO" in full:
        return "TPEX"

    return "TWSE"


# ============================================================
# HTTP
# ============================================================

def http_get(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = MAX_RETRIES,
) -> requests.Response:
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):

        try:
            log(
                f"  HTTP GET attempt "
                f"{attempt}/{retries}"
            )

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=timeout,
            )

            response.raise_for_status()

            return response

        except Exception as exc:
            last_error = exc

            log(
                f"  ⚠️ attempt {attempt} 例外："
                f"{exc}"
            )

            if attempt < retries:
                time.sleep(1.5 * attempt)

    raise RuntimeError(
        f"取得資料失敗：{last_error}"
    )


# ============================================================
# JSON / CSV Parser
# ============================================================

def parse_json_response(
    response: requests.Response,
) -> Any:
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(
            f"JSON 解析失敗：{exc}"
        )


def decode_csv_text(
    response: requests.Response,
) -> str:
    raw = response.content

    encodings = [
        "utf-8-sig",
        "utf-8",
        "big5",
        "cp950",
    ]

    for encoding in encodings:

        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode(
        "utf-8",
        errors="replace",
    )


# ============================================================
# 1. TWSE 主 API
# ============================================================

def fetch_twse_primary(
    session: requests.Session,
) -> List[Dict[str, str]]:
    section("取得 TWSE 上市股票")

    url = (
        "https://openapi.twse.com.tw/"
        "v1/opendata/t187ap03_L"
    )

    try:
        response = http_get(
            session,
            url,
        )

        payload = parse_json_response(response)

        if not isinstance(payload, list):
            raise RuntimeError(
                "TWSE API 回傳格式不是 list"
            )

        results: List[Dict[str, str]] = []

        for row in payload:

            if not isinstance(row, dict):
                continue

            symbol = clean_symbol(
                row.get("公司代號")
                or row.get("Code")
                or row.get("證券代號")
                or row.get("有價證券代號")
            )

            name = clean_text(
                row.get("公司名稱")
                or row.get("Name")
                or row.get("證券名稱")
                or row.get("有價證券名稱")
            )

            if not symbol:
                continue

            if not (
                is_stock_symbol(symbol)
                or is_etf_symbol(symbol)
            ):
                continue

            results.append({
                "symbol": symbol,
                "name": name,
                "market": "TWSE",
                "type": classify_type(
                    symbol,
                    str(
                        row.get("證券種類")
                        or row.get("類型")
                        or ""
                    ),
                ),
                "full_symbol": (
                    f"{symbol}.TW"
                ),
                "source": "TWSE_PRIMARY",
            })

        log(
            f"✓ TWSE 主 API 回傳 "
            f"{len(results)} 筆"
        )

        return results

    except Exception as exc:

        log(
            f"⚠️ TWSE 主 API 取得失敗："
            f"{exc}"
        )

        return []


# ============================================================
# 2. TWSE 官方 ISIN 清單
#
# 用於：
# - 主 API timeout 時建立 TWSE 股票池
# - 補空白名稱
# - 驗證上市市場
# ============================================================

def fetch_twse_isin(
    session: requests.Session,
) -> List[Dict[str, str]]:
    section("取得 TWSE 官方 ISIN 股票清單")

    url = (
        "https://isin.twse.com.tw/"
        "isin/C_public.jsp"
    )

    params = {
        "strMode": "2",
    }

    try:
        response = http_get(
            session,
            url,
            params=params,
        )

        text = response.content.decode(
            "utf-8",
            errors="ignore",
        )

        # 如果沒有中文，嘗試 Big5
        if "上市" not in text and "TWSE" not in text:
            try:
                text = response.content.decode(
                    "big5",
                    errors="ignore",
                )
            except Exception:
                pass

        # ----------------------------------------------------
        # 先嘗試 HTML table parser
        # ----------------------------------------------------

        rows: List[Dict[str, str]] = []

        try:
            from html.parser import HTMLParser

            class TableParser(HTMLParser):

                def __init__(self) -> None:
                    super().__init__()
                    self.in_td = False
                    self.current: List[str] = []
                    self.row: List[str] = []
                    self.rows: List[List[str]] = []

                def handle_starttag(
                    self,
                    tag: str,
                    attrs: List[Tuple[str, Optional[str]]],
                ) -> None:
                    tag = tag.lower()

                    if tag == "td":
                        self.in_td = True
                        self.current = []

                    elif tag == "tr":
                        self.row = []

                def handle_data(
                    self,
                    data: str,
                ) -> None:
                    if self.in_td:
                        self.current.append(data)

                def handle_endtag(
                    self,
                    tag: str,
                ) -> None:
                    tag = tag.lower()

                    if tag == "td":
                        value = clean_text(
                            "".join(self.current)
                        )

                        self.row.append(value)
                        self.current = []
                        self.in_td = False

                    elif tag == "tr":

                        if self.row:
                            self.rows.append(
                                self.row
                            )

            parser = TableParser()
            parser.feed(text)

            for row in parser.rows:

                if len(row) < 4:
                    continue

                first = clean_text(row[0])

                # 常見格式：
                # 代號　名稱
                match = re.match(
                    r"^(\d{4,6})\s+(.+)$",
                    first,
                )

                if not match:
                    continue

                symbol = match.group(1)
                name = clean_text(
                    match.group(2)
                )

                market_text = " ".join(
                    row
                ).upper()

                if "TWSE LISTED" not in market_text:
                    continue

                if not name:
                    continue

                if not (
                    is_stock_symbol(symbol)
                    or is_etf_symbol(symbol)
                ):
                    continue

                rows.append({
                    "symbol": symbol,
                    "name": name,
                    "market": "TWSE",
                    "type": classify_type(
                        symbol
                    ),
                    "full_symbol": (
                        f"{symbol}.TW"
                    ),
                    "source": "TWSE_ISIN",
                })

        except Exception as exc:
            log(
                f"  ⚠️ HTML parser 異常：{exc}"
            )

        # ----------------------------------------------------
        # 若 HTML parser 沒抓到，使用文字 regex
        # ----------------------------------------------------

        if not rows:

            for line in text.splitlines():

                line = clean_text(line)

                match = re.search(
                    r"(\d{4,6})\s+(.+?)"
                    r"(?:TW\d{10}|TWSE LISTED)",
                    line,
                    flags=re.IGNORECASE,
                )

                if not match:
                    continue

                symbol = clean_symbol(
                    match.group(1)
                )

                name = clean_text(
                    match.group(2)
                )

                if not symbol or not name:
                    continue

                rows.append({
                    "symbol": symbol,
                    "name": name,
                    "market": "TWSE",
                    "type": classify_type(
                        symbol
                    ),
                    "full_symbol": (
                        f"{symbol}.TW"
                    ),
                    "source": "TWSE_ISIN",
                })

        # 去重
        dedup: Dict[str, Dict[str, str]] = {}

        for item in rows:
            symbol = item["symbol"]

            if symbol not in dedup:
                dedup[symbol] = item

        results = list(
            dedup.values()
        )

        log(
            f"✓ TWSE 官方 ISIN 清單取得 "
            f"{len(results)} 筆"
        )

        return results

    except Exception as exc:

        log(
            f"⚠️ TWSE ISIN 取得失敗："
            f"{exc}"
        )

        return []


# ============================================================
# 3. TPEx 官方 Mainboard HTML
# ============================================================

def fetch_tpex_mainboard(
    session: requests.Session,
) -> List[Dict[str, str]]:
    section("取得 TPEX 上櫃股票")

    url = (
        "https://www.tpex.org.tw/"
        "en-us/mainboard/listed/company.html"
    )

    try:
        response = http_get(
            session,
            url,
        )

        text = response.content.decode(
            "utf-8",
            errors="ignore",
        )

        results: List[Dict[str, str]] = []

        # ----------------------------------------------------
        # Markdown/HTML 常見：
        # Code | Name
        # ----------------------------------------------------

        pattern = re.compile(
            r"(?:^|\|)\s*"
            r"(\d{4,6})\s*"
            r"\|\s*"
            r"([^|\n]+?)"
            r"\s*(?:\||$)"
        )

        for match in pattern.finditer(text):

            symbol = clean_symbol(
                match.group(1)
            )

            name = clean_text(
                match.group(2)
            )

            if not symbol or not name:
                continue

            if not (
                is_stock_symbol(symbol)
                or is_etf_symbol(symbol)
            ):
                continue

            # 排除欄位名稱
            if symbol.lower() in {
                "code",
                "stock",
            }:
                continue

            results.append({
                "symbol": symbol,
                "name": name,
                "market": "TPEX",
                "type": classify_type(
                    symbol
                ),
                "full_symbol": (
                    f"{symbol}.TWO"
                ),
                "source": "TPEX_MAINBOARD",
            })

        # ----------------------------------------------------
        # HTML fallback
        # ----------------------------------------------------

        if not results:

            from html.parser import HTMLParser

            class SimpleParser(
                HTMLParser
            ):

                def __init__(self) -> None:
                    super().__init__()
                    self.in_td = False
                    self.cells: List[str] = []
                    self.row: List[str] = []
                    self.rows: List[List[str]] = []

                def handle_starttag(
                    self,
                    tag: str,
                    attrs: List[Tuple[str, Optional[str]]],
                ) -> None:
                    if tag.lower() == "td":
                        self.in_td = True
                        self.cells = []

                    elif tag.lower() == "tr":
                        self.row = []

                def handle_data(
                    self,
                    data: str,
                ) -> None:
                    if self.in_td:
                        self.cells.append(data)

                def handle_endtag(
                    self,
                    tag: str,
                ) -> None:
                    tag = tag.lower()

                    if tag == "td":
                        self.row.append(
                            clean_text(
                                "".join(
                                    self.cells
                                )
                            )
                        )
                        self.cells = []
                        self.in_td = False

                    elif tag == "tr":
                        if self.row:
                            self.rows.append(
                                self.row
                            )

            parser = SimpleParser()
            parser.feed(text)

            for row in parser.rows:

                if len(row) < 2:
                    continue

                for idx, cell in enumerate(row):

                    symbol = clean_symbol(
                        cell
                    )

                    if not is_stock_symbol(
                        symbol
                    ):
                        continue

                    if idx + 1 >= len(row):
                        continue

                    name = clean_text(
                        row[idx + 1]
                    )

                    if not name:
                        continue

                    results.append({
                        "symbol": symbol,
                        "name": name,
                        "market": "TPEX",
                        "type": classify_type(
                            symbol
                        ),
                        "full_symbol": (
                            f"{symbol}.TWO"
                        ),
                        "source": (
                            "TPEX_MAINBOARD"
                        ),
                    })

                    break

        # 去重
        dedup: Dict[
            str,
            Dict[str, str]
        ] = {}

        for item in results:

            symbol = item["symbol"]

            if symbol not in dedup:
                dedup[symbol] = item

        results = list(
            dedup.values()
        )

        log(
            f"✓ TPEX 官方公司清單取得 "
            f"{len(results)} 筆"
        )

        return results

    except Exception as exc:

        log(
            f"⚠️ TPEX 官方清單取得失敗："
            f"{exc}"
        )

        return []


# ============================================================
# 4. TPEX 3081 等名稱補正
#
# 不把單一標的硬塞成股票池。
# 這裡只允許官方來源補正。
# ============================================================

def merge_sources(
    primary: Iterable[Dict[str, str]],
    fallback: Iterable[Dict[str, str]],
    *,
    market: str,
) -> List[Dict[str, str]]:

    merged: Dict[
        str,
        Dict[str, str]
    ] = {}

    # 主來源優先
    for item in primary:

        symbol = clean_symbol(
            item.get("symbol", "")
        )

        if not symbol:
            continue

        name = clean_text(
            item.get("name", "")
        )

        if not name:
            continue

        merged[symbol] = {
            **item,
            "symbol": symbol,
            "name": name,
            "market": market,
        }

    # fallback 補缺
    for item in fallback:

        symbol = clean_symbol(
            item.get("symbol", "")
        )

        if not symbol:
            continue

        name = clean_text(
            item.get("name", "")
        )

        if not name:
            continue

        existing = merged.get(symbol)

        if existing is None:

            merged[symbol] = {
                **item,
                "symbol": symbol,
                "name": name,
                "market": market,
            }

        elif not clean_text(
            existing.get("name", "")
        ):

            existing["name"] = name
            existing["source"] = (
                item.get("source")
                or "FALLBACK"
            )

    return list(
        merged.values()
    )


# ============================================================
# 5. 使用既有 universe 作為安全 fallback
# ============================================================

def load_existing_universe() -> List[
    Dict[str, str]
]:
    if not OUTPUT_FILE.exists():
        return []

    try:

        with OUTPUT_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:
            payload = json.load(f)

        items = (
            payload.get("items", [])
            if isinstance(payload, dict)
            else payload
        )

        if not isinstance(items, list):
            return []

        results = []

        for item in items:

            if not isinstance(item, dict):
                continue

            symbol = clean_symbol(
                item.get("symbol")
                or item.get("code")
                or ""
            )

            name = clean_text(
                item.get("name")
                or ""
            )

            if not symbol or not name:
                continue

            results.append({
                "symbol": symbol,
                "full_symbol": (
                    item.get("full_symbol")
                    or (
                        f"{symbol}."
                        f"{'TWO' if item.get('market') == 'TPEX' else 'TW'}"
                    )
                ),
                "name": name,
                "market": (
                    item.get("market")
                    or "TWSE"
                ),
                "type": (
                    item.get("type")
                    or classify_type(symbol)
                ),
                "source": "EXISTING",
            })

        return results

    except Exception as exc:

        log(
            f"⚠️ 既有 universe 讀取失敗："
            f"{exc}"
        )

        return []


# ============================================================
# 6. 最終正規化
# ============================================================

def normalize_universe(
    items: Iterable[Dict[str, str]],
) -> List[Dict[str, str]]:

    final: Dict[
        str,
        Dict[str, str]
    ] = {}

    for item in items:

        symbol = clean_symbol(
            item.get("symbol", "")
        )

        name = clean_text(
            item.get("name", "")
        )

        if not symbol:
            continue

        if not name:
            # 空名稱直接丟棄
            continue

        market = classify_market(
            item.get("market", ""),
            item.get("full_symbol", ""),
        )

        sec_type = classify_type(
            symbol,
            item.get("type", ""),
        )

        suffix = (
            "TWO"
            if market == "TPEX"
            else "TW"
        )

        final[symbol] = {
            "symbol": symbol,
            "full_symbol": (
                f"{symbol}.{suffix}"
            ),
            "name": name,
            "market": market,
            "type": sec_type,
        }

    result = list(
        final.values()
    )

    result.sort(
        key=lambda x: (
            x["market"],
            x["symbol"],
        )
    )

    return result


# ============================================================
# 7. 固定標的驗證
# ============================================================

def verify_required_stocks(
    items: List[Dict[str, str]],
) -> None:

    section("固定測試股票驗證")

    mapping = {
        item["symbol"]: item
        for item in items
    }

    errors: List[str] = []

    for symbol, expected_name in (
        REQUIRED_TEST_STOCKS.items()
    ):

        item = mapping.get(symbol)

        if item is None:

            errors.append(
                f"{symbol}: 股票不存在"
            )

            print(
                f"❌ {symbol} "
                f"{expected_name}：不存在"
            )

            continue

        actual_name = clean_text(
            item.get("name", "")
        )

        market = item.get(
            "market",
            "",
        )

        print(
            f"{symbol} | "
            f"預期：{expected_name} | "
            f"實際：{actual_name} | "
            f"市場：{market}"
        )

        if actual_name != expected_name:

            errors.append(
                f"{symbol}: "
                f"預期={expected_name} "
                f"實際={actual_name}"
            )

    if errors:

        log("")
        log(
            "❌ 固定股票驗證失敗"
        )

        for error in errors:
            log(
                f"   {error}"
            )

        raise RuntimeError(
            "固定測試股票驗證失敗"
        )

    log("")
    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "全部通過"
    )


# ============================================================
# 8. Universe 完整性驗證
# ============================================================

def verify_universe(
    items: List[Dict[str, str]],
) -> Dict[str, int]:

    section("Universe 完整性驗證")

    if not items:
        raise RuntimeError(
            "Universe 為空"
        )

    symbols = set()

    duplicate_symbols = []

    empty_name = []

    invalid_symbol = []

    for item in items:

        symbol = clean_symbol(
            item.get("symbol", "")
        )

        name = clean_text(
            item.get("name", "")
        )

        if not symbol:

            invalid_symbol.append(
                item
            )

            continue

        if symbol in symbols:

            duplicate_symbols.append(
                symbol
            )

        symbols.add(symbol)

        if not name:

            empty_name.append(
                symbol
            )

    if duplicate_symbols:

        raise RuntimeError(
            "發現重複股票代號："
            + ", ".join(
                duplicate_symbols[:20]
            )
        )

    if invalid_symbol:

        raise RuntimeError(
            "發現無效股票代號"
        )

    if empty_name:

        raise RuntimeError(
            "發現空白股票名稱："
            + ", ".join(
                empty_name[:20]
            )
        )

    twse = sum(
        1
        for item in items
        if item["market"] == "TWSE"
    )

    tpex = sum(
        1
        for item in items
        if item["market"] == "TPEX"
    )

    stock = sum(
        1
        for item in items
        if item["type"] == "Stock"
    )

    etf = sum(
        1
        for item in items
        if item["type"] == "ETF"
    )

    total = len(items)

    log(
        f"TWSE：{twse}"
    )

    log(
        f"TPEX：{tpex}"
    )

    log(
        f"Stock：{stock}"
    )

    log(
        f"ETF：{etf}"
    )

    log(
        f"Total：{total}"
    )

    # 安全門檻
    if twse < MIN_TWSE:

        raise RuntimeError(
            f"TWSE 數量不足："
            f"{twse} < {MIN_TWSE}"
        )

    if tpex < MIN_TPEX:

        raise RuntimeError(
            f"TPEX 數量不足："
            f"{tpex} < {MIN_TPEX}"
        )

    if total < MIN_TOTAL:

        raise RuntimeError(
            f"Total 數量不足："
            f"{total} < {MIN_TOTAL}"
        )

    log("")
    log(
        "✓ Universe 數量安全門檻通過"
    )

    return {
        "twse_count": twse,
        "tpex_count": tpex,
        "stock_count": stock,
        "etf_count": etf,
        "total_count": total,
    }


# ============================================================
# 9. Atomic Write
# ============================================================

def atomic_write(
    payload: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    # 先備份既有版本
    if OUTPUT_FILE.exists():

        try:

            import shutil

            shutil.copy2(
                OUTPUT_FILE,
                BACKUP_FILE,
            )

            log(
                f"✓ 已建立備份："
                f"{BACKUP_FILE}"
            )

        except Exception as exc:

            log(
                f"⚠️ 備份失敗：{exc}"
            )

    with temp_file.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            payload,
            f,
            ensure_ascii=False,
            indent=2,
        )

        f.write("\n")

    temp_file.replace(
        OUTPUT_FILE
    )


# ============================================================
# 10. 主程式
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        f"BASE_DIR：{BASE_DIR}"
    )

    log(
        f"DATA_DIR：{DATA_DIR}"
    )

    log(
        f"OUTPUT：{OUTPUT_FILE}"
    )

    section("安全門檻")

    log(
        f"TWSE >= {MIN_TWSE}"
    )

    log(
        f"TPEX >= {MIN_TPEX}"
    )

    log(
        f"Total >= {MIN_TOTAL}"
    )

    existing = load_existing_universe()

    log(
        f"既有 universe："
        f"{len(existing)} stocks"
    )

    session = requests.Session()

    # ========================================================
    # TWSE
    # ========================================================

    twse_primary = fetch_twse_primary(
        session
    )

    twse_isin = fetch_twse_isin(
        session
    )

    # 主來源 + 官方 ISIN fallback
    twse_items = merge_sources(
        twse_primary,
        twse_isin,
        market="TWSE",
    )

    log(
        f"TWSE 合併後："
        f"{len(twse_items)} 筆"
    )

    # ========================================================
    # TPEX
    # ========================================================

    tpex_items = fetch_tpex_mainboard(
        session
    )

    # ========================================================
    # 如果某一市場取得異常
    # ========================================================

    if len(twse_items) < MIN_TWSE:

        log("")
        log(
            "⚠️ TWSE 新資料低於安全門檻"
        )

        existing_twse = [
            item
            for item in existing
            if item.get("market") == "TWSE"
        ]

        if len(existing_twse) >= MIN_TWSE:

            log(
                "✓ 使用既有 TWSE universe "
                "作為安全 fallback"
            )

            twse_items = existing_twse

        else:

            log(
                "❌ 沒有足夠的既有 TWSE "
                "資料可 fallback"
            )

    if len(tpex_items) < MIN_TPEX:

        log("")
        log(
            "⚠️ TPEX 新資料低於安全門檻"
        )

        existing_tpex = [
            item
            for item in existing
            if item.get("market") == "TPEX"
        ]

        if len(existing_tpex) >= MIN_TPEX:

            log(
                "✓ 使用既有 TPEX universe "
                "作為安全 fallback"
            )

            tpex_items = existing_tpex

        else:

            log(
                "❌ 沒有足夠的既有 TPEX "
                "資料可 fallback"
            )

    # ========================================================
    # 合併
    # ========================================================

    section("合併 TWSE + TPEX")

    combined = (
        list(twse_items)
        + list(tpex_items)
    )

    items = normalize_universe(
        combined
    )

    log(
        f"合併後有效標的："
        f"{len(items)}"
    )

    # ========================================================
    # 固定標的驗證
    # ========================================================

    try:

        verify_required_stocks(
            items
        )

    except Exception as exc:

        section(
            "BUILD UNIVERSE FAILED"
        )

        log(
            f"ERROR：{exc}"
        )

        log(
            "✓ 不覆蓋既有 "
            "universe.json"
        )

        return 1

    # ========================================================
    # 完整性驗證
    # ========================================================

    try:

        stats = verify_universe(
            items
        )

    except Exception as exc:

        section(
            "BUILD UNIVERSE FAILED"
        )

        log(
            f"ERROR：{exc}"
        )

        log(
            "✓ 不覆蓋既有 "
            "universe.json"
        )

        return 1

    # ========================================================
    # 建立輸出
    # ========================================================

    generated_at = (
        datetime.now()
        .strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    payload = {
        "schema_version": VERSION,
        "generated_at": generated_at,

        "universe_count": (
            stats["total_count"]
        ),

        "statistics": {
            "twse": stats["twse_count"],
            "tpex": stats["tpex_count"],
            "stock": stats["stock_count"],
            "etf": stats["etf_count"],
            "total": stats["total_count"],
        },

        "items": items,
    }

    # ========================================================
    # 最後輸出前再檢查一次
    # ========================================================

    section(
        "最終輸出前安全檢查"
    )

    if payload["universe_count"] != len(
        payload["items"]
    ):

        log(
            "❌ universe_count 與 items "
            "數量不一致"
        )

        return 1

    for item in payload["items"]:

        if not item["symbol"]:

            log(
                "❌ 發現空白 symbol"
            )

            return 1

        if not item["name"]:

            log(
                f"❌ 發現空白 name："
                f"{item['symbol']}"
            )

            return 1

    log(
        "✓ symbol 全部有效"
    )

    log(
        "✓ name 全部非空白"
    )

    log(
        "✓ 無重複 symbol"
    )

    log(
        "✓ 固定測試標的全部正確"
    )

    # ========================================================
    # 寫入
    # ========================================================

    section(
        "寫入 Data/universe.json"
    )

    try:

        atomic_write(
            payload
        )

    except Exception as exc:

        section(
            "BUILD UNIVERSE FAILED"
        )

        log(
            f"ERROR：寫入失敗：{exc}"
        )

        return 1

    elapsed = (
        time.time() - start_time
    )

    # ========================================================
    # 最終結果
    # ========================================================

    section(
        "BUILD UNIVERSE SUCCESS"
    )

    log(
        f"TWSE："
        f"{stats['twse_count']}"
    )

    log(
        f"TPEX："
        f"{stats['tpex_count']}"
    )

    log(
        f"Stock："
        f"{stats['stock_count']}"
    )

    log(
        f"ETF："
        f"{stats['etf_count']}"
    )

    log(
        f"Total："
        f"{stats['total_count']}"
    )

    log("")
    log(
        "固定測試股票："
    )

    for symbol, name in (
        REQUIRED_TEST_STOCKS.items()
    ):

        item = next(
            x
            for x in items
            if x["symbol"] == symbol
        )

        log(
            f"  {symbol} "
            f"{item['name']} "
            f"{item['market']}"
        )

    log("")
    log(
        f"✓ universe.json 已更新"
    )

    log(
        f"✓ build_universe.py "
        f"{VERSION} 完成"
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