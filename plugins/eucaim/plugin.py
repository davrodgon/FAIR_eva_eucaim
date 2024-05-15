#!/usr/bin/python
# -*- coding: utf-8 -*-

import ast
import json
import logging
import os
import sys
import api.utils as ut
import pandas as pd
from api.evaluator import ConfigTerms, Evaluator
from fdpclient import operations
from fdpclient.client import Client




logging.basicConfig(
    stream=sys.stdout, level=logging.DEBUG, format="'%(name)s:%(lineno)s' | %(message)s"
)

logger = logging.getLogger(os.path.basename(__file__))


class Plugin(Evaluator):
    """A class used to define FAIR indicators tests. For the EUCAIM FDP approach.

    Attributes
    ----------
    item_id : str
        Digital Object identifier, which can be a generic one (DOI, PID), or an internal (e.g. an
            identifier from the repo)

    oai_base : str
        Open Archives Initiative , This is the place in which the API will ask for the metadata. If you are working with  Digital CSIC http://digital.csic.es/dspace-oai/request

    lang : Language
    """

    def __init__(self, item_id, oai_base=None, lang="en"):
        plugin = "eucaim"
        super().__init__(item_id, oai_base, lang, plugin)
        # TO REDEFINE - WHICH IS YOUR PID TYPE?
        self.id_type = "internal"

        global _
        _ = super().translation()

        # You need a way to get your metadata in a similar format
        metadata_sample = self.get_metadata()
        self.metadata = pd.DataFrame(
            metadata_sample,
            columns=["metadata_schema", "element", "text_value", "qualifier"],
        )

        logger.debug("METADATA: %s" % (self.metadata))
        # Protocol for (meta)data accessing
        if len(self.metadata) > 0:
            self.access_protocols = ["http"]

        self.identifier_term = ast.literal_eval(self.config[plugin]["identifier_term"])
        self.terms_quali_generic = ast.literal_eval(
            self.config[plugin]["terms_quali_generic"]
        )
        self.terms_quali_disciplinar = ast.literal_eval(
            self.config[plugin]["terms_quali_disciplinar"]
        )
        self.terms_access = ast.literal_eval(self.config[plugin]["terms_access"])
        self.terms_cv = ast.literal_eval(self.config[plugin]["terms_cv"])
        self.supported_data_formats = ast.literal_eval(
            self.config[plugin]["supported_data_formats"]
        )
        self.terms_qualified_references = ast.literal_eval(
            self.config[plugin]["terms_qualified_references"]
        )
        self.terms_relations = ast.literal_eval(self.config[plugin]["terms_relations"])
        self.terms_license = ast.literal_eval(self.config[plugin]["terms_license"])
        self.metadata_schemas = ast.literal_eval(
            self.config[plugin]["metadata_schemas"]
        )
        self.metadata_quality = 100  # Value for metadata balancing

    def get_metadata(self):
        metadata_sample = []
        eml_schema = "eucaim"
        final_url = self.oai_base + "/resources/details/" + self.item_id

        error_in_metadata = False
        headers = {
            "accept": "application/json",
        }

        # create a client with base URL
        client = Client('http://fdp3.healthdataportal.eu')
        # read metadata, return a RDF graph
        r = client.read_dataset('ec1121de-5d99-4c81-92fb-273419b50386')
        fdp_json = r.serialize(format="json-ld")
        print(fdp_json)
        if not fdp_json:
            msg = (
                "Error: empty metadata received from FDP: %s"
                % final_url
            )
            error_in_metadata = True
        if error_in_metadata:
            logger.error(msg)
            raise Exception(msg)

        for s, p, o in r:
            print("Subject:", s)
            print("Predicate:", p)
            print("Object:", o)
            metadata_sample.append([eml_schema, s, p, o])
        return metadata_sample

    @ConfigTerms(term_id="identifier_term")
    def rda_f1_01m(self, **kwargs):
        """Indicator RDA-F1-01M: Metadata is identified by a persistent identifier.

        This indicator is linked to the following principle: F1 (meta)data are assigned a globally
        unique and eternally persistent identifier. More information about that principle can be found
        here.

        This indicator evaluates whether or not the metadata is identified by a persistent identifier.
        A persistent identifier ensures that the metadata will remain findable over time, and reduces
        the risk of broken links.

        Parameters
        ----------
        identifier_term : dict
            A dictionary with metadata information about the identifier/s used for the metadata (see ConfigTerms class for further details)

        Returns
        -------
        points
            - 0/100   if no persistent identifier is used  for the metadata
            - 100/100 if a persistent identifier is used for the metadata
        msg
            Message with the results or recommendations to improve this indicator
        """
        term_data = kwargs["identifier_term"]
        term_metadata = term_data["metadata"]

        id_list = term_metadata.text_value.values

        points, msg_list = self.eval_persistency(id_list, data_or_metadata="metadata")
        logger.debug(msg_list)

        return (points, msg_list)

    def rda_i1_02m(self):
        """Indicator RDA-A1-01M
        This indicator is linked to the following principle: I1: (Meta)data use a formal, accessible,
        shared, and broadly applicable language for knowledge representation. More information
        about that principle can be found here.

        This indicator focuses on the machine-understandability aspect of the metadata. This means
        that metadata should be readable and thus interoperable for machines without any
        requirements such as specific translators or mappings.

        Technical proposal:

        Parameters
        ----------
        item_id : str
            Digital Object identifier, which can be a generic one (DOI, PID), or an internal (e.g. an
            identifier from the repo)

        Returns
        -------
        points
            A number between 0 and 100 to indicate how well this indicator is supported
        msg
            Message with the results or recommendations to improve this indicator
        """
        # TO REDEFINE
        points = 0
        msg = "No machine-actionable metadata format found. OAI-PMH endpoint may help"
        return (points, msg)

    def rda_i1_02d(self):
        """Indicator RDA-A1-01M
        This indicator is linked to the following principle: I1: (Meta)data use a formal, accessible,
        shared, and broadly applicable language for knowledge representation. More information
        about that principle can be found here.

        This indicator focuses on the machine-understandability aspect of the data. This means that
        data should be readable and thus interoperable for machines without any requirements such
        as specific translators or mappings.

        Technical proposal:

        Parameters
        ----------
        item_id : str
            Digital Object identifier, which can be a generic one (DOI, PID), or an internal (e.g. an
            identifier from the repo)

        Returns
        -------
        points
            A number between 0 and 100 to indicate how well this indicator is supported
        msg
            Message with the results or recommendations to improve this indicator
        """
        return self.rda_i1_02m()

    def rda_r1_3_01m(self):
        """Indicator RDA-A1-01M
        This indicator is linked to the following principle: R1.3: (Meta)data meet domain-relevant
        community standards.

        This indicator requires that metadata complies with community standards.

        Technical proposal:

        Parameters
        ----------
        item_id : str
            Digital Object identifier, which can be a generic one (DOI, PID), or an internal (e.g. an
            identifier from the repo)

        Returns
        -------
        points
            A number between 0 and 100 to indicate how well this indicator is supported
        msg
            Message with the results or recommendations to improve this indicator
        """
        # TO REDEFINE
        points = 0
        msg = _(
            "Currently, this repo does not include community-bsed schemas. If you need to include yours, please contact."
        )
        return (points, msg)

    def rda_r1_3_01d(self):
        """Indicator RDA_R1.3_01D.

        Technical proposal:

        Parameters
        ----------
        item_id : str
            Digital Object identifier, which can be a generic one (DOI, PID), or an internal (e.g. an
            identifier from the repo)

        Returns
        -------
        points
            A number between 0 and 100 to indicate how well this indicator is supported
        msg
            Message with the results or recommendations to improve this indicator
        """
        # TO REDEFINE
        points = 0
        msg = _(
            "Currently, this repo does not include community-bsed schemas. If you need to include yours, please contact."
        )
        return (points, msg)