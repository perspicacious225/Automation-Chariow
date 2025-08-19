def format_money(amount, currency: str | None) -> str:
    if amount is None:
        return ""
    try:
        amt = float(amount)
    except Exception:
        return str(amount)
    s = f"{int(amt):,}".replace(",", " ") if abs(amt - int(amt)) < 1e-6 else f"{amt:,.2f}".replace(",", " ")
    cur = (currency or "").upper()
    if cur in ("XOF", "FCFA", "CFA"):
        return f"{s} FCFA"
    if cur in ("USD", "$"):
        return f"${s}"
    if cur in ("EUR", "€"):
        return f"{s} €"
    return f"{s} {cur}".strip()