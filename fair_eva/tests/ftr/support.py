"""Constants and helpers shared by the FTR tests (all of them offline)."""

from pathlib import Path

from pyshacl import validate
from rdflib import Graph

from fair_eva.tests.plugins.dummy.plugin import PID

PLUGIN = "dummy"
API_ENDPOINT = "http://x"  # never contacted: the network is blocked in tests

# Shapes of the FTR vocabulary, release 1.3.0 (see the README next to them)
SHACL_DIR = Path(__file__).resolve().parent.parent / "data" / "ftr_shacl" / "1.3.0"


def shacl_report(data, shapes, inference="none"):
    """Validate ``data`` (a Graph) against one of the shape files of :data:`SHACL_DIR`.

    Each file is used on its own: they all define the same shape IRIs
    (``http://www.example.org/me#TestShape``...), so loading them into one graph would
    merge unrelated shapes (see docs/ftr_ostrails.md).

    Returns ``(conforms, report_text)``.
    """
    conforms, _, text = validate(
        data_graph=data,
        shacl_graph=Graph().parse(str(SHACL_DIR / shapes), format="turtle"),
        inference=inference,
    )
    return conforms, text


__all__ = ["API_ENDPOINT", "PID", "PLUGIN", "SHACL_DIR", "shacl_report"]
