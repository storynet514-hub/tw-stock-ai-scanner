import json
import csv
import os
from collections import Counter


INPUT_FILE = "Data/prices.json"
OUTPUT_JSON = "Data/qualified_stocks.json"
OUTPUT_CSV = "Data/qualified_stocks.csv"


CONDITION_KEYS = [
    "macd_golden_cross",
    "rsi_above_50",
    "kd_golden_cross",
    "volume_expand",
    "above_ma20",
    "ma20_up",
]


def is_stock(item):
    """
    僅保留一般股票。
    ETF 不列入本次 6/6 選股。
    """
    security_type = str(
        item.get("security_type", "")
    ).upper()

    category = str(
        item.get("category", "")
    ).upper()

    asset_type = str(
        item.get("asset_type", "")
    ).upper()

    text = " ".join(
        [
            security_type,
            category,
            asset_type,
        ]
    )

    if "ETF" in text:
        return False

    return True


def get_conditions(item):
    conditions = item.get("conditions", {})

    return {
        key: bool(conditions.get(key, False))
        for key in CONDITION_KEYS
    }


def main():

    print("=" * 70)
    print("台股 6/6 選股完整驗證")
    print("=" * 70)

    if not os.path.exists(INPUT_FILE):
        raise SystemExit(
            f"ERROR: 找不到 {INPUT_FILE}"
        )

    with open(
        INPUT_FILE,
        "r",
        encoding="utf-8"
    ) as f:
        data = json.load(f)

    stocks = data.get("stocks", [])

    if not isinstance(stocks, list):
        raise SystemExit(
            "ERROR: stocks 不是陣列"
        )

    print(f"原始 stocks 筆數：{len(stocks)}")

    stock_items = [
        item
        for item in stocks
        if isinstance(item, dict)
        and is_stock(item)
    ]

    print(
        f"排除 ETF 後個股筆數："
        f"{len(stock_items)}"
    )

    qualified = []

    distribution = Counter()

    missing_conditions = Counter()

    for item in stock_items:

        conditions = get_conditions(item)

        passed = sum(
            1
            for value in conditions.values()
            if value
        )

        distribution[passed] += 1

        if passed == 6:

            qualified_item = {
                "code": item.get("code"),
                "name": item.get("name"),
                "price": item.get("price"),
                "rsi": item.get("rsi"),
                "kd_k": item.get("kd_k"),
                "kd_d": item.get("kd_d"),
                "macd": item.get("macd"),
                "macd_signal": item.get(
                    "macd_signal"
                ),
                "volume": item.get("volume"),
                "volume_ma5": item.get(
                    "volume_ma5"
                ),
                "ma20": item.get("ma20"),
                "ai_score": item.get(
                    "ai_score"
                ),
                "signal": item.get("signal"),
                "conditions": conditions,
            }

            qualified.append(
                qualified_item
            )

        else:

            for key, value in conditions.items():

                if not value:
                    missing_conditions[key] += 1

    # ------------------------------------------------------------
    # 排序
    # ------------------------------------------------------------

    qualified.sort(
        key=lambda x: (
            -(x.get("ai_score") or 0),
            x.get("code") or ""
        )
    )

    # ------------------------------------------------------------
    # 輸出 JSON
    # ------------------------------------------------------------

    output = {
        "verification_date": data.get(
            "date"
        ),
        "source_file": INPUT_FILE,
        "total_stocks": len(stock_items),
        "qualified_count": len(qualified),
        "required_conditions": CONDITION_KEYS,
        "distribution": {
            f"{i}/6": distribution.get(i, 0)
            for i in range(7)
        },
        "missing_condition_counts": dict(
            missing_conditions
        ),
        "qualified_stocks": qualified,
    }

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # ------------------------------------------------------------
    # 輸出 CSV
    # ------------------------------------------------------------

    fieldnames = [
        "code",
        "name",
        "price",
        "rsi",
        "kd_k",
        "kd_d",
        "macd",
        "macd_signal",
        "volume",
        "volume_ma5",
        "ma20",
        "ai_score",
        "signal",
    ]

    with open(
        OUTPUT_CSV,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for item in qualified:

            writer.writerow({
                key: item.get(key)
                for key in fieldnames
            })

    # ------------------------------------------------------------
    # 顯示結果
    # ------------------------------------------------------------

    print()
    print("=" * 70)
    print("6/6 完整符合結果")
    print("=" * 70)

    print(
        f"完整符合 6/6："
        f"{len(qualified)} 檔"
    )

    print()

    if qualified:

        for index, item in enumerate(
            qualified,
            start=1
        ):

            print(
                f"{index:>3}. "
                f"{item.get('code', ''):<8} "
                f"{item.get('name', ''):<12} "
                f"股價={item.get('price')} "
                f"RSI={item.get('rsi')} "
                f"AI={item.get('ai_score')}"
            )

    else:

        print("沒有任何個股達到 6/6。")

    # ------------------------------------------------------------
    # 分布
    # ------------------------------------------------------------

    print()
    print("=" * 70)
    print("0/6 ～ 6/6 分布")
    print("=" * 70)

    for i in range(7):

        print(
            f"{i}/6："
            f"{distribution.get(i, 0)} 檔"
        )

    # ------------------------------------------------------------
    # 各條件未通過數
    # ------------------------------------------------------------

    print()
    print("=" * 70)
    print("各條件未通過數")
    print("=" * 70)

    for key in CONDITION_KEYS:

        print(
            f"{key:<24} "
            f"{missing_conditions.get(key, 0)}"
        )

    print()
    print("=" * 70)
    print("輸出完成")
    print("=" * 70)

    print(
        f"JSON：{OUTPUT_JSON}"
    )

    print(
        f"CSV ：{OUTPUT_CSV}"
    )


if __name__ == "__main__":
    main()
