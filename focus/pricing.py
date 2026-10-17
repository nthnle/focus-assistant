"""What a model call cost, from the prices in focus.env.

Open-model prices depend on the provider, so they are settings, not a table:

    FOCUS_MODEL_PRICE=0.15,0.60        US$ per million tokens in,out for FOCUS_MODEL
    FOCUS_CHAT_MODEL_PRICE=0.075,0.30  the same for FOCUS_CHAT_MODEL

focus.env is readable by root only, so only the services see these. They
work the cost out when they make the call and save it with the token counts;
`focus status`, run from a normal shell, just adds up what was saved.
"""

from __future__ import annotations

import os


def prices() -> dict[str, tuple[float, float]]:
    table = {}
    for model_var, price_var in (("FOCUS_MODEL", "FOCUS_MODEL_PRICE"),
                                 ("FOCUS_CHAT_MODEL", "FOCUS_CHAT_MODEL_PRICE")):
        model, raw = os.environ.get(model_var), os.environ.get(price_var, "")
        try:
            tin, tout = (float(x) for x in raw.split(","))
        except ValueError:
            continue
        if model:
            table[model] = (tin, tout)
    return table


def cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    price = prices().get(model)
    if price is None:
        return None
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000


def priced(record: dict, model: str, tokens_in: int, tokens_out: int) -> dict:
    """`record` with "usd" added when the model's price is known."""
    usd = cost(model, tokens_in, tokens_out)
    if usd is not None:
        record["usd"] = round(usd, 8)
    return record
