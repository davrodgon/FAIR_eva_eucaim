"""Reading the SHACL configuration from the (merged) FAIR EVA ``ConfigParser``.

Shape sets are declared in ``[shacl:<id>]`` sections and selected by id::

    [shacl:basic]
    source = shapes/basic.ttl
    format = turtle
    name = Basic resource shapes
    version = 1.2.0

    [my_plugin]
    shacl_sets = ['basic']
    shacl_ontologies = []

    [shacl]
    default_sets = ['basic']
    default_ontologies = []
    inference = none
    combine = separate

(comments must be on their own lines: the parser does not strip inline ones).
Selection precedence: explicit argument, then ``<plugin> / shacl_sets``, then
``[shacl] / default_sets``.  The same sections describe shapes and ontologies;
the role is given by where they are referenced.
"""

from __future__ import annotations

import ast
import configparser
import dataclasses
from importlib import resources
from typing import Any, Dict, List, Optional, Sequence

from fair_eva.shacl.errors import ShapesConfigError
from fair_eva.shacl.models import (
    COMBINE_MODES,
    COMBINE_SEPARATE,
    ShapesSpec,
    ValidationOptions,
)

SECTION_PREFIX = "shacl:"
GLOBAL_SECTION = "shacl"
PLUGIN_SETS_KEY = "shacl_sets"
PLUGIN_ONTOLOGIES_KEY = "shacl_ontologies"
DEFAULT_SETS_KEY = "default_sets"
DEFAULT_ONTOLOGIES_KEY = "default_ontologies"

_SET_KEYS = ("source", "format", "name", "version", "expected_sha256", "timeout")
_BOOL_OPTIONS = (
    "advanced",
    "allow_infos",
    "allow_warnings",
    "abort_on_first",
    "meta_shacl",
    "allow_empty_data",
)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def _parse_list(raw: str, where: str) -> List[str]:
    """Parse ``['a', 'b']`` (Python literal, as elsewhere in the config) or ``a, b``."""
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            value = ast.literal_eval(raw)
        except (ValueError, SyntaxError) as exc:
            raise ShapesConfigError("Invalid list in %s: %s" % (where, exc)) from exc
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(v, str) for v in value
        ):
            raise ShapesConfigError("%s must be a list of strings" % where)
        return [v.strip() for v in value if v.strip()]
    return [_unquote(v) for v in raw.replace("\n", ",").split(",") if v.strip()]


def _get(config: configparser.ConfigParser, section: str, key: str) -> Optional[str]:
    if config.has_section(section) and config.has_option(section, key):
        return config.get(section, key, raw=True)
    return None


def available_set_ids(config: configparser.ConfigParser) -> List[str]:
    """Ids of the ``[shacl:<id>]`` sections defined in ``config``."""
    return [
        s[len(SECTION_PREFIX) :]
        for s in config.sections()
        if s.startswith(SECTION_PREFIX)
    ]


def selected_ids(
    config: configparser.ConfigParser,
    plugin: Optional[str] = None,
    selection: Optional[Sequence[str]] = None,
    ontologies: bool = False,
) -> List[str]:
    """Ids selected for ``plugin`` (shapes, or ontologies if ``ontologies``).

    Returns an empty list when nothing is selected anywhere.
    """
    plugin_key, default_key = (
        (PLUGIN_ONTOLOGIES_KEY, DEFAULT_ONTOLOGIES_KEY)
        if ontologies
        else (PLUGIN_SETS_KEY, DEFAULT_SETS_KEY)
    )
    if selection is not None:
        return [str(s).strip() for s in selection if str(s).strip()]
    if plugin:
        raw = _get(config, plugin, plugin_key)
        if raw is not None:
            return _parse_list(raw, "[%s] %s" % (plugin, plugin_key))
    raw = _get(config, GLOBAL_SECTION, default_key)
    if raw is not None:
        return _parse_list(raw, "[%s] %s" % (GLOBAL_SECTION, default_key))
    return []


def spec_from_section(
    config: configparser.ConfigParser,
    set_id: str,
    base_dir: Optional[str] = None,
) -> ShapesSpec:
    """Build the :class:`ShapesSpec` described by ``[shacl:<set_id>]``."""
    section = SECTION_PREFIX + set_id
    if not config.has_section(section):
        known = ", ".join(available_set_ids(config)) or "none"
        raise ShapesConfigError(
            "Unknown SHACL shape set '%s' (no [%s] section; configured: %s)"
            % (set_id, section, known)
        )
    values = {key: config.get(section, key, raw=True) for key in config[section]}
    unknown = sorted(set(values) - set(_SET_KEYS))
    if unknown:
        raise ShapesConfigError(
            "Unknown key(s) in [%s]: %s (allowed: %s)"
            % (section, ", ".join(unknown), ", ".join(_SET_KEYS))
        )
    if not values.get("source", "").strip():
        raise ShapesConfigError("[%s] needs a 'source' (path or URL)" % section)
    fields: Dict[str, Any] = {k: _unquote(v) for k, v in values.items()}
    if "timeout" in fields:
        try:
            fields["timeout"] = float(fields["timeout"])
        except ValueError:
            raise ShapesConfigError("[%s] timeout must be a number" % section) from None
    return ShapesSpec(id=set_id, base_dir=base_dir, **fields)


def shapes_from_config(
    config: configparser.ConfigParser,
    plugin: Optional[str] = None,
    selection: Optional[Sequence[str]] = None,
    base_dir: Optional[str] = None,
) -> List[ShapesSpec]:
    """Shape set specs selected for ``plugin`` (see the module docstring).

    ``base_dir`` is where relative ``source`` paths are resolved (typically the
    plugin directory).
    """
    return [
        spec_from_section(config, set_id, base_dir)
        for set_id in selected_ids(config, plugin, selection)
    ]


def ontologies_from_config(
    config: configparser.ConfigParser,
    plugin: Optional[str] = None,
    selection: Optional[Sequence[str]] = None,
    base_dir: Optional[str] = None,
) -> List[ShapesSpec]:
    """Ontology document specs selected for ``plugin``."""
    return [
        spec_from_section(config, set_id, base_dir)
        for set_id in selected_ids(config, plugin, selection, ontologies=True)
    ]


def options_from_config(config: configparser.ConfigParser) -> ValidationOptions:
    """Validation options from the optional ``[shacl]`` section."""
    values: Dict[str, Any] = {}
    if config.has_section(GLOBAL_SECTION):
        section = config[GLOBAL_SECTION]
        if "inference" in section:
            values["inference"] = _unquote(config.get(GLOBAL_SECTION, "inference"))
        for key in _BOOL_OPTIONS:
            if key in section:
                try:
                    values[key] = config.getboolean(GLOBAL_SECTION, key)
                except ValueError as exc:
                    raise ShapesConfigError("[shacl] %s: %s" % (key, exc)) from exc
    return dataclasses.replace(ValidationOptions(), **values)


def combine_from_config(config: configparser.ConfigParser) -> str:
    """``separate`` (default) or ``merged`` from ``[shacl] combine``."""
    raw = _get(config, GLOBAL_SECTION, "combine")
    if raw is None:
        return COMBINE_SEPARATE
    mode = _unquote(raw).lower()
    if mode not in COMBINE_MODES:
        raise ShapesConfigError(
            "[shacl] combine must be one of %s, got %r"
            % (", ".join(COMBINE_MODES), raw)
        )
    return mode


def load_core_config() -> configparser.ConfigParser:
    """The core ``fair_eva/config.ini`` (without any plugin configuration)."""
    config = configparser.ConfigParser()
    config.read_string(resources.files("fair_eva").joinpath("config.ini").read_text())
    return config
