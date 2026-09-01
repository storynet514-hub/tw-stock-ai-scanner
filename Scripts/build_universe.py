def normalize_security_type(
    value: Any,
) -> Optional[str]:

    text = normalize_text(value)

    if not text:
        return None

    # 官方普通股票類型
    if text == "STOCKS":
        return "STOCK"

    # 保留相容性：若官方來源出現單數 STOCK，也接受
    if text == "STOCK":
        return "STOCK"

    # 官方 ETF
    if text == "ETF":
        return "ETF"

    # 其他官方商品全部拒絕
    return None