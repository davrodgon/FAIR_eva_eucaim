"""Offline plugin showing how a plugin uses the generic SHACL validation."""

import logging

import pandas as pd
from rdflib import Graph

from fair_eva.api.evaluator import EvaluatorBase
from fair_eva.shacl import ShaclError

logger = logging.getLogger("api.plugin.shacl_demo")

GOOD_ID = "good"

DATA = """
@prefix ex: <http://example.org/> .
ex:alice a ex:Person ; ex:name "Alice" .
"""
DATA_MISSING_NAME = """
@prefix ex: <http://example.org/> .
ex:bob a ex:Person .
"""


class Plugin(EvaluatorBase):
    def __init__(
        self, item_id, api_endpoint=None, lang="en", config=None, name=None, **kwargs
    ):
        super().__init__(item_id, api_endpoint, lang, config, name)
        self.metadata = pd.DataFrame(
            self.get_metadata(),
            columns=["metadata_schema", "element", "text_value", "qualifier"],
            dtype=object,
        )

    @staticmethod
    def get_ids(api_endpoint=None, pattern_to_query=""):
        return [GOOD_ID, "bad"]

    def get_metadata(self):
        return [["dc", "identifier", self.item_id, None]]

    def get_rdf_graph(self):
        text = DATA if self.item_id == GOOD_ID else DATA_MISSING_NAME
        return Graph().parse(data=text, format="turtle")

    def rda_r1_3_01m(self, **kwargs):
        """Metadata complies with the shapes configured for this plugin."""
        try:
            report = self.validate_shacl()
        except ShaclError as exc:
            return 0, [{"message": str(exc), "points": 0}]
        return report.as_indicator_result()
