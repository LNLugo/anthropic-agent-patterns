"""Prices come from a file with a source and a date for every model. Nothing is assumed.

    book = PriceBook.load()                      # 11_finops/prices.yaml
    book.cost("gpt-6-luna", input_tokens=728, output_tokens=39)   # 9.23e-05
    book.cost("claude-sonnet-5-5", 728, 39)      # raises Unpriced: not in the file
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

DEFAULT = Path(__file__).resolve().parents[1] / "prices.yaml"


class Unpriced(KeyError):
    """The model is not in the price file. Its cost is unknown, not zero."""

    def __init__(self, model: str | None):
        super().__init__(model)
        self.model = model

    def __str__(self) -> str:
        return f"no price for model {self.model!r}; add it to prices.yaml with its source"


@dataclass(frozen=True)
class Price:
    model: str
    input: float                 # USD per 1M tokens
    cached_input: float
    output: float
    source: str
    checked: date
    long_context_above: int | None = None
    long_input_multiplier: float = 1.0
    long_output_multiplier: float = 1.0

    def cost(self, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
        """One request. input_tokens includes the cached ones; output_tokens includes reasoning."""
        if not 0 <= cached_input_tokens <= input_tokens or output_tokens < 0:
            raise ValueError(f"bad token counts: input {input_tokens}, cached {cached_input_tokens}, output {output_tokens}")
        mi = mo = 1.0
        if self.long_context_above is not None and input_tokens > self.long_context_above:
            mi, mo = self.long_input_multiplier, self.long_output_multiplier  # for the whole request
        fresh = input_tokens - cached_input_tokens
        return (fresh * self.input * mi + cached_input_tokens * self.cached_input * mi
                + output_tokens * self.output * mo) / 1_000_000


class PriceBook:
    def __init__(self, prices: dict[str, Price], currency: str = "USD"):
        self.prices, self.currency = prices, currency

    @classmethod
    def load(cls, path: str | Path = DEFAULT) -> "PriceBook":
        raw = yaml.safe_load(Path(path).read_text())
        prices = {}
        for name, p in (raw.get("models") or {}).items():
            missing = [k for k in ("input", "cached_input", "output", "source", "checked") if k not in p]
            if missing:
                raise ValueError(f"{name}: missing {', '.join(missing)} in {path}")
            lc = p.get("long_context") or {}
            checked = p["checked"] if isinstance(p["checked"], date) else date.fromisoformat(str(p["checked"]))
            prices[name] = Price(name, float(p["input"]), float(p["cached_input"]), float(p["output"]),
                                 p["source"], checked, lc.get("above_input_tokens"),
                                 float(lc.get("input_multiplier", 1.0)), float(lc.get("output_multiplier", 1.0)))
        return cls(prices, raw.get("currency", "USD"))

    def __contains__(self, model: str | None) -> bool:
        return model in self.prices

    def price(self, model: str | None) -> Price:
        try:
            return self.prices[model]
        except KeyError:
            raise Unpriced(model) from None

    def cost(self, model: str | None, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
        return self.price(model).cost(input_tokens, output_tokens, cached_input_tokens)
