"""Turn token counts into dollars.

Prices are USD per million tokens, from each vendor's pricing page (checked 2026-10-18).
Prices change: update this table when they do. Unknown models cost None (not 0),
so a missing price is visible instead of silently looking free.
"""

from decimal import Decimal

# model id -> (input $/MTok, output $/MTok)
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-fable-5-1": (Decimal("10.00"), Decimal("50.00")),
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    # Haiku 5.5's price applies to prompts up to 100K tokens; ours are far smaller.
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    # Gemini free tier: no charge (rate-limited; Google may use inputs to improve
    # its products). If you enable billing, replace these with the paid prices.
    "gemini-3.8-flash": (Decimal(0), Decimal(0)),
    "gemini-3.6-flash": (Decimal(0), Decimal(0)),
    "gemini-3.5-flash-lite": (Decimal(0), Decimal(0)),
    "gemini-2.5-flash": (Decimal(0), Decimal(0)),
}

MILLION = Decimal(1_000_000)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    prices = PRICES.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    cost = (input_tokens * input_price + output_tokens * output_price) / MILLION
    return cost.quantize(Decimal("0.000001"))
