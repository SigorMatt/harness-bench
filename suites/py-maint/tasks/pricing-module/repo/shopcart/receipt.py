"""Plain-text receipts for a cart."""

from .core import apply_discount, round_money


def format_receipt(cart, names=None):
    """Return a receipt for cart: one line per item, then the subtotal, discount, tax and total.

    names maps skus to the product names to print; a sku without one is printed as it is.
    """
    names = names or {}
    lines = [f"{names.get(item.sku, item.sku):<20}{item.quantity:>4} x {item.unit_price:>8} = {item.total:>9}"
             for item in cart.items]
    subtotal = cart.subtotal()
    lines.append(f"{'Subtotal':<36}{subtotal:>9}")
    discounted = apply_discount(subtotal, cart.discount_percent)
    if discounted != subtotal:
        lines.append(f"{'Discount':<36}{round_money(discounted - subtotal):>9}")
    total = cart.total()
    if total != discounted:
        lines.append(f"{'Tax':<36}{round_money(total - discounted):>9}")
    lines.append(f"{'Total':<36}{total:>9}")
    return "\n".join(lines) + "\n"
