"""Registry of the RDA indicators that FAIR EVA implements (the FTR *metrics*).

``indicators.json`` holds, for each indicator exposed by ``fair-api.yaml`` with
``x-indicator: True``, its RDA code, the FAIR principle it belongs to and the English
title and description published in ``docs/technical_implementation.md``. The docstrings
of the evaluator cannot be used for that: many of them are copies of other indicators.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Dict, Tuple

# Pattern used by the GO FAIR / FAIR principles IRIs, e.g. .../terms/F1 or .../terms/A1.1
# (taken from the "inDimension" example of the FTR specification, see docs/ftr_ostrails.md).
FAIR_PRINCIPLES_BASE = "https://w3id.org/fair/principles/terms/"

PRINCIPLE_NAMES = {
    "F": "Findable",
    "A": "Accessible",
    "I": "Interoperable",
    "R": "Reusable",
}


@dataclass(frozen=True)
class Indicator:
    id: str  # python/API name, e.g. rda_f1_01m
    code: str  # RDA code, e.g. RDA-F1-01M
    principle: str  # e.g. F1, A1.1
    title: str
    description: str

    @property
    def principle_uri(self):
        return FAIR_PRINCIPLES_BASE + self.principle

    @property
    def dimension_name(self):
        return PRINCIPLE_NAMES[self.principle[0]]


@lru_cache(maxsize=1)
def _load() -> Dict[str, Indicator]:
    text = (
        resources.files("fair_eva.ftr").joinpath("indicators.json").read_text("utf-8")
    )
    return {item["id"]: Indicator(**item) for item in json.loads(text)}


def all_indicators() -> Tuple[Indicator, ...]:
    return tuple(_load().values())


def get_indicator(indicator_id):
    """Return the :class:`Indicator` or ``None`` if it is not a known RDA indicator."""
    return _load().get(indicator_id)
