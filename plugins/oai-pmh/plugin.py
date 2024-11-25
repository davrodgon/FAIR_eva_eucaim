#!/usr/bin/python
# -*- coding: utf-8 -*-
import logging
import sys
import urllib
import xml.etree.ElementTree as ET

import idutils
import pandas as pd
import requests

import api.utils as ut
from api.evaluator import EvaluatorBase

logging.basicConfig(
    stream=sys.stdout, level=logging.DEBUG, format="'%(name)s:%(lineno)s' | %(message)s"
)
logger = logging.getLogger("api.plugin")


class Plugin(EvaluatorBase):
    """A class used to define FAIR indicators tests. It contains all the references to
    all the tests. This is an example to be tailored to what your needs.

    Attributes
    ----------
    item_id : str
        Digital Object identifier, which can be a generic one (DOI, PID), or an internal (e.g. an identifier from the repo)
    api_endpoint : str
        Open Archives initiative , This is the place in which the API will ask for the metadata
    lang : Language
    """

    def __init__(
        self, item_id, api_endpoint=None, lang="en", config=None, name="oai-pmh"
    ):
        self.config = config
        self.name = name
        self.lang = lang
        self.api_endpoint = api_endpoint
        super().__init__(item_id, self.api_endpoint, self.lang, self.config, self.name)
        logger.debug("Using FAIR-EVA's plugin: %s" % self.name)
        global _
        _ = super().translation()
        # You need a way to get your metadata in a similar format
        metadata_sample = self.get_metadata()
        self.metadata = pd.DataFrame(
            metadata_sample,
            columns=["metadata_schema", "element", "text_value", "qualifier"],
        )

        logger.debug("METADATA: %s" % (self.metadata))

        if self.metadata is not None:
            if len(self.metadata) > 0:
                self.access_protocols = ["http", "oai-pmh"]

    def oai_check_record_url(self, oai_base, metadata_prefix, pid):
        endpoint_root = urllib.parse.urlparse(oai_base).netloc
        try:
            pid_type = idutils.detect_identifier_schemes(pid)[0]
        except Exception as e:
            pid_type = "internal"
            logging.error(e)
        if pid_type != "internal":
            oai_pid = idutils.normalize_pid(pid, pid_type)
        else:
            oai_pid = pid
        action = "?verb=GetRecord"

        test_id = "oai:%s:%s" % (endpoint_root, oai_pid)
        params = "&metadataPrefix=%s&identifier=%s" % (metadata_prefix, test_id)
        url_final = ""
        url = oai_base + action + params
        response = requests.get(url, verify=False, allow_redirects=True)
        logging.debug(
            "Trying ID v1: url: %s | status: %i" % (url, response.status_code)
        )
        error = 0
        for tags in ET.fromstring(response.text).findall(
            ".//{http://www.openarchives.org/OAI/2.0/}error"
        ):
            error = error + 1
        if error == 0:
            url_final = url

        test_id = "%s" % (oai_pid)
        params = "&metadataPrefix=%s&identifier=%s" % (metadata_prefix, test_id)

        url = oai_base + action + params
        logging.debug("Trying: " + url)
        response = requests.get(url, verify=False)
        error = 0
        for tags in ET.fromstring(response.text).findall(
            ".//{http://www.openarchives.org/OAI/2.0/}error"
        ):
            error = error + 1
        if error == 0:
            url_final = url

        test_id = "%s:%s" % (pid_type, oai_pid)
        params = "&metadataPrefix=%s&identifier=%s" % (metadata_prefix, test_id)

        url = oai_base + action + params
        logging.debug("Trying: " + url)
        response = requests.get(url, verify=False)
        error = 0
        for tags in ET.fromstring(response.text).findall(
            ".//{http://www.openarchives.org/OAI/2.0/}error"
        ):
            error = error + 1
        if error == 0:
            url_final = url

        test_id = "oai:%s:%s" % (
            endpoint_root,
            oai_pid[oai_pid.rfind(".") + 1 : len(oai_pid)],
        )
        params = "&metadataPrefix=%s&identifier=%s" % (metadata_prefix, test_id)

        url = oai_base + action + params
        logging.debug("Trying: " + url)
        response = requests.get(url, verify=False)
        error = 0
        for tags in ET.fromstring(response.text).findall(
            ".//{http://www.openarchives.org/OAI/2.0/}error"
        ):
            error = error + 1
        if error == 0:
            url_final = url

        test_id = "oai:%s:b2rec/%s" % (
            endpoint_root,
            oai_pid[oai_pid.rfind(".") + 1 : len(oai_pid)],
        )
        params = "&metadataPrefix=%s&identifier=%s" % (metadata_prefix, test_id)

        url = oai_base + action + params
        logging.debug("Trying: " + url)
        response = requests.get(url, verify=False)
        error = 0
        for tags in ET.fromstring(response.text).findall(
            ".//{http://www.openarchives.org/OAI/2.0/}error"
        ):
            error = error + 1
        if error == 0:
            url_final = url

        return url_final

    def oai_identify(self, oai_base):
        action = "?verb=Identify"
        return self.oai_request(oai_base, action)

    def oai_metadataFormats(self, oai_base):
        action = "?verb=ListMetadataFormats"
        xmlTree = self.oai_request(oai_base, action)
        metadataFormats = {}
        for e in xmlTree.findall(
            ".//{http://www.openarchives.org/OAI/2.0/}metadataFormat"
        ):
            metadataPrefix = e.find(
                "{http://www.openarchives.org/OAI/2.0/}metadataPrefix"
            ).text
            namespace = e.find(
                "{http://www.openarchives.org/OAI/2.0/}metadataNamespace"
            ).text
            metadataFormats[metadataPrefix] = namespace
        return metadataFormats

    def oai_get_metadata(self, url):
        logger.debug("Metadata from: %s" % url)
        oai = requests.get(url, verify=False, allow_redirects=True)
        try:
            xmlTree = ET.fromstring(oai.text)
        except Exception as e:
            logger.error("OAI_RQUEST: %s" % e)
            xmlTree = None
        return xmlTree

    def oai_request(self, oai_base, action):
        oai = requests.get(oai_base + action, verify=False)  # Peticion al servidor
        try:
            xmlTree = ET.fromstring(oai.text)
        except Exception as e:
            logging.error("OAI_RQUEST: %s" % e)
            xmlTree = ET.fromstring("<OAI-PMH></OAI-PMH>")
        return xmlTree

    def get_metadata(self):
        logger.debug("OAI_BASE IN evaluator: %s" % self.api_endpoint)
        if (
            self.api_endpoint is not None
            and self.api_endpoint != ""
            and self.metadata is None
        ):
            metadataFormats = self.oai_metadataFormats(self.api_endpoint)
            dc_prefix = ""
            for e in metadataFormats:
                if metadataFormats[e] == "http://www.openarchives.org/OAI/2.0/oai_dc/":
                    dc_prefix = e
            logger.debug("DC_PREFIX: %s" % dc_prefix)

            try:
                id_type = idutils.detect_identifier_schemes(self.item_id)[0]
            except Exception as e:
                id_type = "internal"

            logger.debug("Trying to get metadata")
            try:
                item_metadata = self.oai_get_metadata(
                    self.oai_check_record_url(
                        self.api_endpoint, dc_prefix, self.item_id
                    )
                ).find(".//{http://www.openarchives.org/OAI/2.0/}metadata")
            except Exception as e:
                logger.error("Problem getting metadata: %s" % e)
                item_metadata = ET.fromstring("<metadata></metadata>")
            data = []
            for tags in item_metadata.findall(".//"):
                metadata_schema = tags.tag[0 : tags.tag.rfind("}") + 1]
                element = tags.tag[tags.tag.rfind("}") + 1 : len(tags.tag)]
                text_value = tags.text
                qualifier = None
                data.append([metadata_schema, element, text_value, qualifier])

        return data
