#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Universe Builder V1.0

目的：
    建立完整台灣市場 Universe。

包含：
    1. TWSE 上市普通股票
    2. TPEx 上櫃普通股票
    3. TWSE ETF
    4. TPEx ETF
    5. ETF 類型分類
       - equity
       - bond
       - other

輸出：
    Data/universe.json

重要：
    本程式只負責建立 Universe。
    不抓歷史價格。
    不計算 MACD / KD / RSI。
    不建立 UI。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "1.0.0"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"
OUTPUT_FILE = DATA_DIR / "universe.json"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


# ============================================================
# HTTP
# ============================================================

def http_get(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: int = TIMEOUT,
) -> requests.Response:
    """
    統一 HTTP GET。
    """

    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=timeout,
    )

    response.raise_for_status()

    return response


# ============================================================
# 工具
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\ufeff", "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_symbol(value: Any) -> str:
    """
    股票代號標準化。

    只接受台股常見的 4~6 位數字代號。
    """

    text = clean_text(value)

    match = re.search(r"(\d{4,6})", text)

    if not match:
        return ""

    return match.group(1)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_item(
    symbol: str,
    name: str,
    market: str,
    security_type: str,
    category: str,
    source: str,
) -> dict[str, Any]:

    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "security_type": security_type,
        "category": category,
        "currency": "TWD",
        "source": source,
        "active": True,
    }


# ============================================================
# TWSE
# ============================================================

def get_twse_stocks() -> list[dict[str, Any]]:
    """
    取得 TWSE 上市股票。

    使用 TWSE 公開 JSON API，
    不依賴 pandas.read_html / lxml。
    """

    print("🔎 取得 TWSE 上市股票...")

    url = (
        "https://openapi.twse.com.tw/"
        "v1/opendata/t187ap03_L"
    )

    response = http_get(url)

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError("TWSE 股票 API 回傳格式錯誤")

    result: list[dict[str, Any]] = []

    for row in data:

        if not isinstance(row, dict):
            continue

        symbol = normalize_symbol(
            row.get("公司代號")
            or row.get("有價證券代號")
            or row.get("代號")
        )

        name = clean_text(
            row.get("公司簡稱")
            or row.get("有價證券名稱")
            or row.get("名稱")
        )

        if not symbol:
            continue

        if not name:
            continue

        result.append(
            make_item(
                symbol=symbol,
                name=name,
                market="TWSE",
                security_type="stock",
                category="listed_stock",
                source="TWSE",
            )
        )

    return result


# ============================================================
# TPEx 上櫃股票
# ============================================================

def get_tpex_stocks() -> list[dict[str, Any]]:
    """
    取得 TPEx 上櫃股票。

    使用 TPEx 公開 API。

    注意：
    TPEx API 格式可能因官方調整而變化，
    因此解析採取多格式容錯。
    """

    print("🔎 取得 TPEx 上櫃股票...")

    urls = [
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_mainboard_peratio"
        ),
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_mainboard_quotes"
        ),
    ]

    last_error: Exception | None = None

    for url in urls:

        try:

            response = http_get(url)

            data = response.json()

            if not isinstance(data, list):
                continue

            result: list[dict[str, Any]] = []

            for row in data:

                if not isinstance(row, dict):
                    continue

                symbol = normalize_symbol(
                    row.get("SecuritiesCompanyCode")
                    or row.get("Code")
                    or row.get("SecuritiesCode")
                    or row.get("有價證券代號")
                    or row.get("代號")
                )

                name = clean_text(
                    row.get("CompanyName")
                    or row.get("Name")
                    or row.get("SecuritiesCompanyName")
                    or row.get("有價證券名稱")
                    or row.get("名稱")
                )

                if not symbol:
                    continue

                if not name:
                    continue

                result.append(
                    make_item(
                        symbol=symbol,
                        name=name,
                        market="TPEX",
                        security_type="stock",
                        category="otc_stock",
                        source="TPEx",
                    )
                )

            if result:
                return result

        except Exception as exc:
            last_error = exc

    if last_error:
        print(f"   ⚠️ TPEx 股票 API 取得失敗：{last_error}")

    return []


# ============================================================
# TWSE ETF
# ============================================================

def get_twse_etf() -> list[dict[str, Any]]:
    """
    取得 TWSE ETF。

    ETF 不應與普通股票混為一談。
    """

    print("🔎 取得 TWSE ETF...")

    urls = [
        "https://openapi.twse.com.tw/v1/opendata/t187ap46_L",
        "https://openapi.twse.com.tw/v1/opendata/t187ap47_L",
    ]

    result: list[dict[str, Any]] = []

    for url in urls:

        try:

            response = http_get(url)

            data = response.json()

            if not isinstance(data, list):
                continue

            for row in data:

                if not isinstance(row, dict):
                    continue

                symbol = normalize_symbol(
                    row.get("證券代號")
                    or row.get("有價證券代號")
                    or row.get("代號")
                )

                name = clean_text(
                    row.get("證券名稱")
                    or row.get("有價證券名稱")
                    or row.get("名稱")
                )

                if not symbol or not name:
                    continue

                category = classify_etf(
                    symbol,
                    name,
                )

                result.append(
                    make_item(
                        symbol=symbol,
                        name=name,
                        market="TWSE",
                        security_type="etf",
                        category=category,
                        source="TWSE",
                    )
                )

        except Exception as exc:
            print(f"   ⚠️ TWSE ETF API 失敗：{exc}")

    return result


# ============================================================
# TPEx ETF
# ============================================================

def get_tpex_etf() -> list[dict[str, Any]]:
    """
    取得 TPEx ETF。

    不使用 pandas.read_html。
    """

    print("🔎 取得 TPEx ETF...")

    urls = [
        "https://www.tpex.org.tw/openapi/v1/tpex_etf",
        "https://www.tpex.org.tw/openapi/v1/tpex_etf_list",
    ]

    result: list[dict[str, Any]] = []

    for url in urls:

        try:

            response = http_get(url)

            data = response.json()

            if not isinstance(data, list):
                continue

            for row in data:

                if not isinstance(row, dict):
                    continue

                symbol = normalize_symbol(
                    row.get("SecuritiesCompanyCode")
                    or row.get("Code")
                    or row.get("SecuritiesCode")
                    or row.get("有價證券代號")
                    or row.get("代號")
                )

                name = clean_text(
                    row.get("CompanyName")
                    or row.get("Name")
                    or row.get("SecuritiesCompanyName")
                    or row.get("有價證券名稱")
                    or row.get("名稱")
                )

                if not symbol or not name:
                    continue

                category = classify_etf(
                    symbol,
                    name,
                )

                result.append(
                    make_item(
                        symbol=symbol,
                        name=name,
                        market="TPEX",
                        security_type="etf",
                        category=category,
                        source="TPEx",
                    )
                )

        except Exception as exc:
            print(f"   ⚠️ TPEx ETF API 失敗：{exc}")

    return result


# ============================================================
# ETF 分類
# ============================================================

BOND_KEYWORDS = [
    "債券",
    "債",
    "公債",
    "公司債",
    "投資級",
    "高收益",
    "非投資等級",
    "金融債",
    "短期債",
    "長天期債",
    "美債",
    "美國債",
    "國債",
    "政府債",
    "地方政府債",
    "新興市場債",
    "美元債",
    "歐元債",
    "日圓債",
    "新興債",
    "非投資級債",
    "收益債",
]


EQUITY_KEYWORDS = [
    "台灣50",
    "台灣",
    "科技",
    "半導體",
    "電子",
    "金融",
    "高股息",
    "低波",
    "ESG",
    "永續",
    "Smart",
    "AI",
    "電動車",
    "5G",
    "元大",
    "國泰",
    "富邦",
    "中信",
    "復華",
    "群益",
    "兆豐",
    "第一金",
    "統一",
    "野村",
    "凱基",
    "聯邦",
    "大華",
]


def classify_etf(
    symbol: str,
    name: str,
) -> str:

    text = f"{symbol} {name}"

    for keyword in BOND_KEYWORDS:

        if keyword in text:
            return "bond_etf"

    for keyword in EQUITY_KEYWORDS:

        if keyword in text:
            return "equity_etf"

    return "other_etf"


# ============================================================
# 去重
# ============================================================

def deduplicate(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    result: dict[tuple[str, str], dict[str, Any]] = {}

    for item in items:

        key = (
            item["market"],
            item["symbol"],
        )

        if key not in result:

            result[key] = item

    return list(result.values())


# ============================================================
# 驗證
# ============================================================

def validate_universe(
    items: list[dict[str, Any]],
) -> None:

    listed_stocks = [
        x for x in items
        if x["category"] == "listed_stock"
    ]

    otc_stocks = [
        x for x in items
        if x["category"] == "otc_stock"
    ]

    listed_etf = [
        x for x in items
        if x["market"] == "TWSE"
        and x["security_type"] == "etf"
    ]

    otc_etf = [
        x for x in items
        if x["market"] == "TPEX"
        and x["security_type"] == "etf"
    ]

    bond_etf = [
        x for x in items
        if x["category"] == "bond_etf"
    ]

    print()
    print("============================================================")
    print("Universe 驗證")
    print("============================================================")

    print(f"上市股票：{len(listed_stocks)}")
    print(f"上櫃股票：{len(otc_stocks)}")
    print(f"上市 ETF：{len(listed_etf)}")
    print(f"上櫃 ETF：{len(otc_etf)}")
    print(f"債券 ETF：{len(bond_etf)}")
    print(f"Universe 總數：{len(items)}")

    errors: list[str] = []

    if len(listed_stocks) < 500:
        errors.append(
            f"上市股票數量異常：{len(listed_stocks)}"
        )

    if len(otc_stocks) < 500:
        errors.append(
            f"上櫃股票數量異常：{len(otc_stocks)}"
        )

    if len(listed_etf) < 20:
        errors.append(
            f"上市 ETF 數量異常：{len(listed_etf)}"
        )

    if len(otc_etf) < 5:
        errors.append(
            f"上櫃 ETF 數量異常：{len(otc_etf)}"
        )

    if len(bond_etf) < 1:
        errors.append(
            "沒有偵測到任何債券型 ETF"
        )

    if len(items) < 1100:
        errors.append(
            f"Universe 總數過低：{len(items)}"
        )

    if errors:

        print()
        print("❌ Universe 驗證失敗")

        for error in errors:
            print(f"   ❌ {error}")

        raise RuntimeError(
            "Universe incomplete"
        )

    print()
    print("✅ Universe 驗證成功")


# ============================================================
# JSON
# ============================================================

def write_universe(
    items: list[dict[str, Any]],
) -> None:

    listed_stocks = sum(
        1
        for x in items
        if x["category"] == "listed_stock"
    )

    otc_stocks = sum(
        1
        for x in items
        if x["category"] == "otc_stock"
    )

    listed_etf = sum(
        1
        for x in items
        if x["market"] == "TWSE"
        and x["security_type"] == "etf"
    )

    otc_etf = sum(
        1
        for x in items
        if x["market"] == "TPEX"
        and x["security_type"] == "etf"
    )

    bond_etf = sum(
        1
        for x in items
        if x["category"] == "bond_etf"
    )

    payload = {
        "version": VERSION,
        "generated_at": now_iso(),
        "source": "TWSE / TPEx official open data",
        "market": "TW",
        "total": len(items),
        "listed_stocks": listed_stocks,
        "otc_stocks": otc_stocks,
        "listed_etf": listed_etf,
        "otc_etf": otc_etf,
        "bond_etf": bond_etf,
        "items": sorted(
            items,
            key=lambda x: (
                x["market"],
                x["security_type"],
                x["symbol"],
            ),
        ),
    }

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temporary_file.open(
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

    temporary_file.replace(
        OUTPUT_FILE
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    start = time.time()

    print()
    print("================================================================")
    print("台股 AI 選股系統")
    print("build_universe.py V1.0")
    print("================================================================")
    print()

    print("📁 Repository：", ROOT)
    print("📁 Output：", OUTPUT_FILE)
    print()

    try:

        print("============================================================")
        print("建立完整台灣市場 Universe")
        print("============================================================")

        # --------------------------------------------------------
        # TWSE
        # --------------------------------------------------------

        twse_stocks = get_twse_stocks()

        print(
            f"   TWSE 上市股票：{len(twse_stocks)}"
        )

        if len(twse_stocks) < 500:
            raise RuntimeError(
                "TWSE 上市股票數量異常"
            )

        # --------------------------------------------------------
        # TPEx
        # --------------------------------------------------------

        tpex_stocks = get_tpex_stocks()

        print(
            f"   TPEx 上櫃股票：{len(tpex_stocks)}"
        )

        if len(tpex_stocks) < 500:
            raise RuntimeError(
                "TPEx 上櫃股票數量異常"
            )

        # --------------------------------------------------------
        # ETF
        # --------------------------------------------------------

        twse_etf = get_twse_etf()

        print(
            f"   TWSE ETF：{len(twse_etf)}"
        )

        if len(twse_etf) < 20:
            raise RuntimeError(
                "TWSE ETF 數量異常"
            )

        tpex_etf = get_tpex_etf()

        print(
            f"   TPEx ETF：{len(tpex_etf)}"
        )

        if len(tpex_etf) < 5:
            raise RuntimeError(
                "TPEx ETF 數量異常"
            )

        # --------------------------------------------------------
        # 合併
        # --------------------------------------------------------

        all_items = (
            twse_stocks
            + tpex_stocks
            + twse_etf
            + tpex_etf
        )

        all_items = deduplicate(
            all_items
        )

        # --------------------------------------------------------
        # 驗證
        # --------------------------------------------------------

        validate_universe(
            all_items
        )

        # --------------------------------------------------------
        # 寫入
        # --------------------------------------------------------

        write_universe(
            all_items
        )

        elapsed = time.time() - start

        print()
        print("================================================================")
        print("✅ build_universe.py V1.0 完成")
        print("================================================================")

        print(
            f"Universe：{len(all_items)}"
        )

        print(
            f"輸出：{OUTPUT_FILE}"
        )

        print(
            f"耗時：{elapsed:.2f} 秒"
        )

        print("================================================================")

        return 0

    except Exception as exc:

        print()
        print("================================================================")
        print("❌ build_universe.py V1.0 執行失敗")
        print("================================================================")

        print(
            f"錯誤：{exc}"
        )

        print()
        print(
            "⚠️ 不產生錯誤 Universe。"
        )

        print("================================================================")

        return 1


if __name__ == "__main__":
    sys.exit(main())