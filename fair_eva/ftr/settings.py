"""Deployment settings of the FTR support (``[ftr]`` section of ``config.ini``).

Persistent identifiers, licences and contact data are decisions of whoever deploys
FAIR EVA, so nothing here is hard-wired: every value comes from the ``[ftr]`` section
of ``fair_eva/config.ini`` (and, for some keys, of a plugin's own ``config.ini``).
The defaults below are PROVISIONAL placeholders that make the output well-formed.
"""

import configparser
import logging
from dataclasses import dataclass, field
from importlib import metadata, resources
from typing import Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("api.ftr")

SECTION = "ftr"

# PROVISIONAL defaults: they only exist so that the output is well-formed.
DEFAULT_BASE_URI = "https://example.org/PROVISIONAL/fair-eva"
# Used when [ftr] endpoint_base_url is empty and there is no HTTP request to derive it from
# (the API is served at http://localhost:9090/v1.0, see fair-api.yaml).
DEFAULT_ENDPOINT_BASE_URL = "http://localhost:9090/v1.0/ftr"
DEFAULT_TEST_LICENSE = "https://www.apache.org/licenses/LICENSE-2.0"
DEFAULT_RESULT_LICENSE = "http://creativecommons.org/licenses/by/4.0/"
DEFAULT_CONTACT_NAME = "PROVISIONAL contact: set [ftr] contact_name in config.ini"
DEFAULT_CODE_REPOSITORY = "https://github.com/IFCA-Advanced-Computing/FAIR_eva"

# Keys that a plugin's config.ini must not override: they are deployment-wide.
NOT_OVERRIDABLE = ("base_uri", "endpoint_base_url", "result_license")

AGENT_KINDS = ("organization", "individual")


@dataclass(frozen=True)
class Agent:
    """A vcard:Organization or vcard:Individual (contact point, creator, publisher)."""

    uri: str
    name: str
    kind: str = "organization"
    email: str = ""


@dataclass(frozen=True)
class FtrSettings:
    base_uri: str = DEFAULT_BASE_URI
    endpoint_base_url: str = ""  # empty: derived from the HTTP request when possible
    test_license: str = DEFAULT_TEST_LICENSE
    result_license: str = DEFAULT_RESULT_LICENSE
    contact: Agent = field(
        default_factory=lambda: Agent(
            uri=DEFAULT_BASE_URI + "/contact", name=DEFAULT_CONTACT_NAME
        )
    )
    publisher: Optional[Agent] = None
    creator: Optional[Agent] = None
    code_repository: str = DEFAULT_CODE_REPOSITORY
    tool_uri: str = DEFAULT_CODE_REPOSITORY
    applicable_for: Tuple[str, ...] = ()
    version: str = ""

    def __post_init__(self):
        # Frozen dataclass: normalise through object.__setattr__
        object.__setattr__(self, "base_uri", self.base_uri.rstrip("/"))
        object.__setattr__(
            self, "endpoint_base_url", self.endpoint_base_url.rstrip("/")
        )
        if not self.version:
            object.__setattr__(self, "version", default_version())


def default_version():
    """Version of the tests: the FAIR EVA version (plugins expose no version)."""
    try:
        return "fair_eva-%s" % metadata.version("fair_eva")
    except metadata.PackageNotFoundError:
        return "fair_eva-unknown"


def _require_iri(key, value):
    parsed = urlparse(value)
    if not parsed.scheme or not (parsed.netloc or parsed.path):
        raise ValueError("[ftr] %s must be an absolute IRI, got %r" % (key, value))
    return value


def _optional_iri(key, value):
    return _require_iri(key, value) if value else ""


def _agent(section, prefix, base_uri, required):
    uri = section.get(prefix + "_uri", "").strip()
    name = section.get(prefix + "_name", "").strip()
    email = section.get(prefix + "_email", "").strip()
    kind = section.get(prefix + "_type", "organization").strip().lower()
    if kind not in AGENT_KINDS:
        raise ValueError(
            "[ftr] %s_type must be one of %s, got %r" % (prefix, AGENT_KINDS, kind)
        )
    if not (uri or name or email) and not required:
        return None
    if not uri:
        uri = "%s/%s" % (base_uri.rstrip("/"), prefix)
    if email and not email.startswith("mailto:"):
        email = "mailto:" + email
    return Agent(
        uri=_require_iri(prefix + "_uri", uri), name=name, kind=kind, email=email
    )


def settings_from_section(section):
    """Build :class:`FtrSettings` from a mapping with the keys of the ``[ftr]`` section."""
    base_uri = _require_iri(
        "base_uri", section.get("base_uri", DEFAULT_BASE_URI).strip()
    )
    contact = _agent(section, "contact", base_uri, required=True)
    if not contact.name:
        contact = Agent(
            uri=contact.uri,
            name=DEFAULT_CONTACT_NAME,
            kind=contact.kind,
            email=contact.email,
        )
    code_repository = _require_iri(
        "code_repository",
        section.get("code_repository", DEFAULT_CODE_REPOSITORY).strip(),
    )
    applicable_for = tuple(
        _require_iri("applicable_for", v)
        for v in section.get("applicable_for", "").replace(",", " ").split()
    )
    return FtrSettings(
        base_uri=base_uri,
        endpoint_base_url=_optional_iri(
            "endpoint_base_url", section.get("endpoint_base_url", "").strip()
        ),
        test_license=_require_iri(
            "test_license", section.get("test_license", DEFAULT_TEST_LICENSE).strip()
        ),
        result_license=_require_iri(
            "result_license",
            section.get("result_license", DEFAULT_RESULT_LICENSE).strip(),
        ),
        contact=contact,
        publisher=_agent(section, "publisher", base_uri, required=False),
        creator=_agent(section, "creator", base_uri, required=False),
        code_repository=code_repository,
        tool_uri=_require_iri(
            "tool_uri", section.get("tool_uri", "").strip() or code_repository
        ),
        applicable_for=applicable_for,
        version=section.get("version", "").strip(),
    )


def _read_package_config(parser, package):
    try:
        text = resources.files(package).joinpath("config.ini").read_text()
    except (ModuleNotFoundError, FileNotFoundError, OSError) as e:
        logger.debug("No config.ini in package <%s>: %s" % (package, e))
        return False
    parser.read_string(text)
    return True


def load_settings(plugin=None, plugin_package=None):
    """Load the FTR settings.

    Parameters
    ----------
    plugin : str, optional
        Plugin name. When given, the ``[ftr]`` section of the plugin's own
        ``config.ini`` (if any) overrides the main one, except for the
        deployment-wide keys in :data:`NOT_OVERRIDABLE`.
    plugin_package : str, optional
        Namespace package that contains the plugins (``fair_eva.plugin``).
    """
    main = configparser.ConfigParser(interpolation=None)
    _read_package_config(main, "fair_eva")
    section = dict(main[SECTION]) if main.has_section(SECTION) else {}

    if plugin and plugin_package:
        plugin_parser = configparser.ConfigParser(interpolation=None)
        _read_package_config(plugin_parser, "%s.%s" % (plugin_package, plugin))
        if plugin_parser.has_section(SECTION):
            overrides = {
                k: v
                for k, v in plugin_parser[SECTION].items()
                if k not in NOT_OVERRIDABLE
            }
            section.update(overrides)
    return settings_from_section(section)
