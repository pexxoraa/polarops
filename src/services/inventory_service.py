VALID_INVENTORY_REASONS = {
    "resupply": "Resupply",
    "field consumption": "Field consumption",
    "transfer correction": "Transfer correction",
    "damaged / lost": "Damaged / lost",
    "stock count correction": "Stock count correction",
}


def resolve_inventory_adjustment(current_quantity: float, entered_value: float, reason: str) -> tuple[float, float, str]:
    """Return (actual_delta, new_quantity, canonical_reason).

    User-entered values are interpreted by reason:
    - Resupply: positive amount added.
    - Field consumption: positive amount removed.
    - Damaged / lost: positive amount removed.
    - Transfer correction: signed delta (+ adds, - removes).
    - Stock count correction: entered value is the physical counted quantity.
    """
    current = float(current_quantity or 0)
    value = float(entered_value)
    key = str(reason or "").strip().casefold()
    canonical = VALID_INVENTORY_REASONS.get(key)
    if not canonical:
        raise ValueError("Select a valid inventory adjustment reason")

    if canonical == "Resupply":
        actual_delta = abs(value)
        new_quantity = current + actual_delta
    elif canonical in ("Field consumption", "Damaged / lost"):
        actual_delta = -abs(value)
        new_quantity = current + actual_delta
    elif canonical == "Transfer correction":
        actual_delta = value
        new_quantity = current + actual_delta
    else:  # Stock count correction
        if value < 0:
            raise ValueError("Counted quantity cannot be negative")
        new_quantity = value
        actual_delta = new_quantity - current

    if new_quantity < 0:
        raise ValueError("Inventory cannot be negative")

    return actual_delta, new_quantity, canonical
