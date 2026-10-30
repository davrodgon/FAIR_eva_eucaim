"""Offline plugin used to exercise the RDA endpoints in the tests."""

import logging

import pandas as pd

from fair_eva.api.evaluator import EvaluatorBase

logger = logging.getLogger("api.plugin.dummy")

PID = "10.1234/abcd.5678"


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
        return [PID, "other-id"]

    def get_metadata(self):
        return [
            ["dc", "identifier", self.item_id, None],
            ["dc", "title", "MyTitle", None],
            ["dc", "creator", "TheCreator", None],
        ]
