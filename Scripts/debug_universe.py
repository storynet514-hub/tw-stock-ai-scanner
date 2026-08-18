#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
debug_universe.py V1.0

============================================================
用途
============================================================

只驗證：

Data/universe.json
        ↓
load_universe()
        ↓
2337 / 2426 是否成功進入股票 Universe

本程式：

✓ 不呼叫 CMoney
✓ 不發 HTTP Request
✓ 不修改任何 JSON
✓ 不建立 chip.json
✓ 不執行 fetch_chip.py
✓ 不執行價格抓取
✓ 不跑 1985 檔股票

只做 Universe 結構與篩選條件 Debug。

============================================================
重點驗證股票
============================================================

2337 旺宏
2426 鼎元

============================================================
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# ============================================================
# 基本設定
# ============================================================

VERSION = "V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

TARGETS = {
    "2337": "旺宏",
    "2426": "鼎元",
}


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):

    log("")
    log("=" * 70)
    log(title)
    log("=" * 70)


# ============================================================
# 顯示 item
# ============================================================

def dump_item(item, index):

    log("")
    log(f"原始 Universe item #{index + 1}")
    log("-" * 70)

    if not isinstance(item, dict):

        log(f"型別：{type(item).__name__}")
        log(f"內容：{repr(item)}")
        return

    for key, value in item.items():

        log(
            f"{key!r}: {value!r}"
        )


# ============================================================
# symbol 正規化
#
# 完全模擬 fetch_chip.py load_universe()
# ============================================================

def normalize_symbol(item):

    if not isinstance(item, dict):

        return {
            "ok": False,
            "reason": "item 不是 dict",
            "symbol_raw": None,
            "symbol_normalized": None,
        }

    # --------------------------------------------------------
    # fetch_chip.py 原本邏輯
    # --------------------------------------------------------

    symbol = item.get("code")

    source_field = "code"

    if symbol is None:

        symbol = item.get("symbol")
        source_field = "symbol"

    if symbol is None:

        return {
            "ok": False,
            "reason": "code 與 symbol 都不存在",
            "symbol_raw": None,
            "symbol_normalized": None,
            "source_field": source_field,
        }

    symbol_raw = str(symbol).strip()

    symbol = symbol_raw.upper()

    symbol = re.sub(
        r"\.(TW|TWO)$",
        "",
        symbol
    )

    # --------------------------------------------------------
    # fetch_chip.py 原本 Regex
    # --------------------------------------------------------

    if not re.fullmatch(
        r"[A-Z0-9]{4,6}",
        symbol
    ):

        return {
            "ok": False,
            "reason": (
                "Regex 不符合 "
                r"[A-Z0-9]{4,6}"
            ),
            "symbol_raw": symbol_raw,
            "symbol_normalized": symbol,
            "source_field": source_field,
        }

    return {
        "ok": True,
        "reason": "通過",
        "symbol_raw": symbol_raw,
        "symbol_normalized": symbol,
        "source_field": source_field,
    }


# ============================================================
# 讀取 Universe
# ============================================================

def load_raw_universe():

    section(
        "1. 讀取 Data/universe.json"
    )

    log(
        f"檔案：{UNIVERSE_FILE}"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到 Universe：{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    log(
        f"JSON 根節點型別："
        f"{type(data).__name__}"
    )

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 根節點不是 dict"
        )

    log(
        "JSON 根節點 keys："
        f"{list(data.keys())}"
    )

    items = data.get("items")

    log(
        f"items 型別："
        f"{type(items).__name__}"
    )

    if not isinstance(items, list):

        raise RuntimeError(
            "universe.json 的 items 不是 list"
        )

    log(
        f"原始 items 數量：{len(items)}"
    )

    return data, items


# ============================================================
# 全 Universe Debug
# ============================================================

def debug_all_items(items):

    section(
        "2. 模擬 fetch_chip.py 的 Universe 篩選"
    )

    seen = set()

    accepted = []

    rejected = []

    duplicate = []

    for index, item in enumerate(items):

        result = normalize_symbol(item)

        if not result["ok"]:

            rejected.append({
                "index": index,
                "item": item,
                "result": result,
            })

            continue

        symbol = result[
            "symbol_normalized"
        ]

        if symbol in seen:

            duplicate.append({
                "index": index,
                "symbol": symbol,
                "item": item,
            })

            continue

        seen.add(symbol)

        accepted.append({
            "index": index,
            "symbol": symbol,
            "item": item,
            "result": result,
        })

    log(
        f"原始 items：{len(items)}"
    )

    log(
        f"成功進入 Universe："
        f"{len(accepted)}"
    )

    log(
        f"被 Regex / 欄位規則排除："
        f"{len(rejected)}"
    )

    log(
        f"重複 symbol 被排除："
        f"{len(duplicate)}"
    )

    log(
        f"最終有效 Universe："
        f"{len(seen)}"
    )

    # --------------------------------------------------------
    # 顯示所有被排除項目
    # --------------------------------------------------------

    if rejected:

        log("")
        log(
            "被篩掉的項目："
        )

        for item in rejected:

            log(
                f"  #{item['index'] + 1} "
                f"{item['result']}"
            )

    if duplicate:

        log("")
        log(
            "重複 symbol："
        )

        for item in duplicate:

            log(
                f"  #{item['index'] + 1} "
                f"{item['symbol']}"
            )

    return accepted, rejected, duplicate


# ============================================================
# 精確搜尋目標股票
# ============================================================

def find_target_in_raw_items(
    items,
    target
):

    matches = []

    for index, item in enumerate(items):

        if not isinstance(item, dict):
            continue

        # ----------------------------------------------------
        # 直接檢查所有可能欄位
        # ----------------------------------------------------

        code = item.get("code")
        symbol = item.get("symbol")

        values = []

        if code is not None:
            values.append(
                ("code", str(code).strip())
            )

        if symbol is not None:
            values.append(
                ("symbol", str(symbol).strip())
            )

        for field, value in values:

            normalized = value.upper()

            normalized = re.sub(
                r"\.(TW|TWO)$",
                "",
                normalized
            )

            if normalized == target:

                matches.append({
                    "index": index,
                    "field": field,
                    "value": value,
                    "item": item,
                })

    return matches


# ============================================================
# 目標股票精確 Debug
# ============================================================

def debug_target(
    items,
    accepted,
    target,
    expected_name
):

    section(
        f"3. 精確 Debug：{target} {expected_name}"
    )

    log(
        f"目標代號：{target}"
    )

    log(
        f"預期名稱：{expected_name}"
    )

    # --------------------------------------------------------
    # 第一層：原始 universe.json
    # --------------------------------------------------------

    raw_matches = find_target_in_raw_items(
        items,
        target
    )

    log("")
    log(
        "[Layer 1] 原始 universe.json"
    )

    if not raw_matches:

        log(
            f"❌ {target} 根本不存在於 "
            f"universe.json items"
        )

        log(
            "→ 問題發生在 universe 建立階段"
        )

        return {
            "raw": False,
            "accepted": False,
        }

    log(
        f"✓ 找到 {len(raw_matches)} 筆原始資料"
    )

    for match in raw_matches:

        log("")
        log(
            f"位置：items[{match['index']}]"
        )

        log(
            f"來源欄位："
            f"{match['field']}"
        )

        log(
            f"原始值："
            f"{match['value']!r}"
        )

        dump_item(
            match["item"],
            match["index"]
        )

        # ----------------------------------------------------
        # 模擬 fetch_chip.py
        # ----------------------------------------------------

        result = normalize_symbol(
            match["item"]
        )

        log("")
        log(
            "[Layer 2] fetch_chip.py "
            "symbol 正規化"
        )

        log(
            f"symbol_raw："
            f"{result.get('symbol_raw')!r}"
        )

        log(
            f"symbol_normalized："
            f"{result.get('symbol_normalized')!r}"
        )

        log(
            f"結果："
            f"{'✓ 通過' if result['ok'] else '❌ 排除'}"
        )

        log(
            f"原因："
            f"{result['reason']}"
        )

        if not result["ok"]:

            log("")
            log(
                "❌ 問題定位："
                "fetch_chip.py 的 Universe 篩選"
            )

            return {
                "raw": True,
                "accepted": False,
            }

    # --------------------------------------------------------
    # 第三層：最終 accepted Universe
    # --------------------------------------------------------

    log("")
    log(
        "[Layer 3] fetch_chip.py 最終 Universe"
    )

    accepted_matches = [
        item
        for item in accepted
        if item["symbol"] == target
    ]

    if not accepted_matches:

        log(
            f"❌ {target} 已存在原始 Universe，"
            f"但沒有進入最終 Universe"
        )

        log(
            "→ 問題發生在 load_universe() "
            "的去重 / 篩選流程"
        )

        return {
            "raw": True,
            "accepted": False,
        }

    log(
        f"✓ {target} 成功進入最終 Universe"
    )

    for item in accepted_matches:

        log(
            f"最終位置："
            f"accepted index = "
            f"{accepted.index(item)}"
        )

        log(
            f"symbol："
            f"{item['symbol']}"
        )

        log(
            f"name："
            f"{item['item'].get('name', '')}"
        )

        log(
            f"market："
            f"{item['item'].get('market', '')}"
        )

    log("")
    log(
        f"✓✓✓ {target} Universe 驗證通過"
    )

    return {
        "raw": True,
        "accepted": True,
    }


# ============================================================
# 檢查 2337 / 2426 是否可能因市場欄位被排除
# ============================================================

def debug_market_field(
    items
):

    section(
        "4. 檢查 2337 / 2426 的 market 欄位"
    )

    for target in TARGETS:

        matches = find_target_in_raw_items(
            items,
            target
        )

        if not matches:

            log(
                f"{target}：❌ 不存在"
            )

            continue

        for match in matches:

            item = match["item"]

            log("")
            log(
                f"{target}："
            )

            log(
                f"  code   = "
                f"{item.get('code')!r}"
            )

            log(
                f"  symbol = "
                f"{item.get('symbol')!r}"
            )

            log(
                f"  name   = "
                f"{item.get('name')!r}"
            )

            log(
                f"  market = "
                f"{item.get('market')!r}"
            )

            log(
                f"  type   = "
                f"{item.get('type')!r}"
            )


# ============================================================
# 最終結論
# ============================================================

def final_report(
    results
):

    section(
        "5. 最終 Debug 結論"
    )

    all_ok = True

    for target, result in results.items():

        if (
            result["raw"]
            and result["accepted"]
        ):

            log(
                f"✓ {target} "
                f"{TARGETS[target]}："
                f"存在於 Universe，"
                f"且成功通過 load_universe()"
            )

        elif result["raw"]:

            log(
                f"❌ {target} "
                f"{TARGETS[target]}："
                f"存在於 universe.json，"
                f"但被 load_universe() 排除"
            )

            all_ok = False

        else:

            log(
                f"❌ {target} "
                f"{TARGETS[target]}："
                f"根本不在 universe.json"
            )

            all_ok = False

    log("")

    if all_ok:

        log(
            "=============================================="
        )

        log(
            "✓ 2337 / 2426 都通過 Universe"
        )

        log(
            "→ Universe 不是問題"
        )

        log(
            "→ 下一層應直接檢查 fetch_chip.py"
            " 的 CMoney request / parse"
        )

        log(
            "=============================================="
        )

    else:

        log(
            "=============================================="
        )

        log(
            "❌ Universe 層存在問題"
        )

        log(
            "→ 先修 Universe"
        )

        log(
            "→ 暫時不要再跑 fetch_chip.py"
        )

        log(
            "=============================================="
        )


# ============================================================
# Main
# ============================================================

def main():

    section(
        f"台股 AI 選股系統 "
        f"debug_universe.py {VERSION}"
    )

    log(
        "本程式只讀 universe.json"
    )

    log(
        "不呼叫 CMoney"
    )

    log(
        "不發任何 HTTP Request"
    )

    log(
        "不修改任何資料"
    )

    log(
        f"Universe：{UNIVERSE_FILE}"
    )

    try:

        # ----------------------------------------------------
        # Layer 1
        # ----------------------------------------------------

        _, items = load_raw_universe()

        # ----------------------------------------------------
        # Layer 2
        # ----------------------------------------------------

        (
            accepted,
            rejected,
            duplicate
        ) = debug_all_items(
            items
        )

        # ----------------------------------------------------
        # Layer 3
        # ----------------------------------------------------

        results = {}

        for target, name in TARGETS.items():

            results[target] = debug_target(
                items,
                accepted,
                target,
                name
            )

        # ----------------------------------------------------
        # Market
        # ----------------------------------------------------

        debug_market_field(
            items
        )

        # ----------------------------------------------------
        # Final
        # ----------------------------------------------------

        final_report(
            results
        )

        return 0

    except Exception as exc:

        log("")
        log(
            "=============================================="
        )

        log(
            "❌ debug_universe.py 執行失敗"
        )

        log(
            f"原因：{exc}"
        )

        log(
            "=============================================="
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
