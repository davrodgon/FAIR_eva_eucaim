# SHACL validation

FAIR EVA core can validate RDF against [SHACL](https://www.w3.org/TR/shacl/) shapes and
turn the outcome into something an indicator can use: conformance, number of
violations and one message per focus node / path / severity.

The core is **generic**: it ships no shapes. The shapes are supplied by the user or by a
plugin, through configuration (a local path or a URL, in any common RDF serialisation).

Every report records **which shapes were used**: name, version and SHA-256 of each shapes
document, so a result can always be tied to the exact artefact that produced it (a name
such as "profile X" is not enough, because the same name can denote different versions).

The engine is [pyshacl](https://github.com/RDFLib/pySHACL) (a dependency of FAIR EVA).

## Configuration

Shape sets are declared in `[shacl:<id>]` sections of any `config.ini` (the core one or a
plugin's one; they are merged) and selected by id.

```ini
[shacl:basic]
# Local path or http(s) URL
source = shapes/basic.ttl
# Optional: turtle, json-ld, xml, n3, nt (guessed from the extension or the
# Content-Type when omitted)
format = turtle
# Optional metadata
name = Basic resource shapes
version = 1.2.0
# Optional pin: loading fails if the content has a different SHA-256
expected_sha256 = 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
# Optional download timeout in seconds (URLs)
timeout = 30

# Same keys; referenced as ontology below
[shacl:classes]
source = https://example.org/classes.ttl

# The plugin's own section
[my_plugin]
# Sets this plugin validates against, and optional ontology documents
shacl_sets = ['basic']
shacl_ontologies = ['classes']

# Optional global section
[shacl]
# Used when a plugin does not select any set
default_sets = ['basic']
default_ontologies = []
# separate | merged (see below)
combine = separate
# none | rdfs | owlrl | both
inference = none
# Booleans, all false by default: advanced, allow_warnings, allow_infos,
# abort_on_first, meta_shacl, allow_empty_data
allow_warnings = false
```

Comments must be on their own lines: the configuration parser does not strip inline `;` or
`#` comments.

Notes:

* Lists are written like the rest of the configuration (`['a', 'b']`); `a, b` also works.
* Selection precedence: explicit argument, then `shacl_sets` in the plugin section, then
  `default_sets` in `[shacl]`.
* A relative `source` is resolved against the directory of the module that defines the
  plugin class (so shapes can ship inside a plugin); with the Python API, against
  `ShapesSpec.base_dir` or the current directory.
* Every validation reads the file (or downloads the URL) and hashes it again, so the
  recorded hash is always that of the content used in that run. Only the *parsed* graph is
  kept in a small in-memory cache keyed by that hash (`clear_graph_cache()` empties it), so
  a long-running server does not re-parse unchanged shapes on every request. Graphs
  returned by `load_shapes` / `report.runs[i].shapes_graph` are shared: treat them as
  read-only.
* `owl:imports` found in a document are **not** followed (validation stays hermetic and
  reproducible). They are listed in the provenance (`declared_in_file.imports`). To use
  several related documents together, declare each as a set and use `combine = merged`.
* Ontology documents (`shacl_ontologies`) are handed to the engine as ontology graph, e.g.
  class hierarchies needed by `sh:class` together with `inference = rdfs`. Their provenance is
  recorded like that of the shapes.

## Python API

```python
from fair_eva.shacl import validate, ShapesSpec

report = validate(
    data_graph,                        # rdflib.Graph, or RDF text (+ data_format=...)
    [
        "shapes/basic.ttl",            # str: path or URL
        {"source": "https://example.org/extra.ttl", "version": "3", "id": "extra"},
        ShapesSpec(content=turtle_text, id="inline", name="Inline shapes"),
    ],
    ontologies=None,                   # same kinds of specs, optional
    combine="separate",                # or "merged"
    options={"inference": "rdfs"},     # ValidationOptions or dict
)

report.conforms                        # bool (all sets)
report.violations, report.warnings, report.infos
report.results                         # list of ShaclResult
report.filter(severity="Violation", focus_node=...)
report.group_by("focus_node")          # or "path", "severity", "shapes_id"
report.shapes_provenance               # list of ShapesProvenance
report.to_dict() / report.to_json()
points, msgs = report.as_indicator_result()   # (points, [{"message", "points"}])
```

* `separate` (default) validates each set on its own and attributes every result to its
  set (`result.shapes_id`). `merged` merges the sets into one shapes graph, needed when a
  set refers to shapes of another; results are then not attributed (`shapes_id` is `None`).
* Errors derive from `ShaclError`: `ShapesConfigError` (bad specification or
  configuration), `ShapesLoadError` (missing/unreadable/malformed shapes, download error,
  hash mismatch), `DataGraphError` (empty or unparsable data; an empty graph is rejected
  because it would conform vacuously, unless `allow_empty_data`), `ShaclRunError` (engine
  failure).
* `load_shapes(spec)` returns a `LoadedShapes` that can be passed to `validate` to reuse
  a document across many validations without reading/parsing it again. Its provenance is
  the one of load time.
* pyshacl memoises rendered blank nodes in a module-level dict that it never clears
  (it grows by a few KiB per validation in a long-running process); the core empties it
  after each run.
* For custom scoring, `report.runs[i].results_graph` and `.shapes_graph` expose the raw
  rdflib graphs (not serialised): e.g. follow `sh:sourceShape` back into the shapes graph.
  Blank node identifiers (`_:...`) in a report are local to that run.
* `as_indicator_result()` gives `points_if_conforms` (default 100) or `points_otherwise`
  (0), a first message with the verdict, one message per shapes document (name, version,
  SHA-256) and one per result (0 points).

## Report format and shapes provenance

`report.to_dict()` (also the body of the REST response):

```json
{
  "conforms": false,
  "summary": {"results": 1, "violations": 1, "warnings": 0, "infos": 0},
  "validated_at": "2026-10-30T12:19:08+00:00",
  "mode": "separate",
  "software": {"pyshacl": "0.40.2", "rdflib": "7.6.0", "fair_eva": "1.5.0", "python": "3.13.17"},
  "options": {"inference": "none", "advanced": false, "allow_infos": false,
              "allow_warnings": false, "abort_on_first": false, "meta_shacl": false,
              "allow_empty_data": false},
  "data": {"triples": 1},
  "shapes_provenance": [
    {
      "role": "shapes",
      "id": "person",
      "name": "Person shapes",
      "name_source": "config",
      "version": "1.0.0",
      "version_source": "config",
      "sha256": "8929726bed697f1513aa7d0c25828773265b7fa7ba4e8ed79b4d732c65429554",
      "hash_algorithm": "sha256",
      "hash_input": "raw-bytes",
      "source": "/opt/plugin/shapes/person.ttl",
      "source_type": "file",
      "final_url": null,
      "format": "turtle",
      "size_bytes": 306,
      "triples": 6,
      "declared_in_file": {"title": null, "version": null, "version_property": null, "imports": []},
      "discrepancies": [],
      "notes": []
    }
  ],
  "ontology_provenance": [],
  "runs": [{"shapes": ["person"], "conforms": false, "violations": 1, "warnings": 0, "infos": 0}],
  "results": [
    {
      "severity": "Violation",
      "focus_node": "http://example.org/bob",
      "path": "http://example.org/name",
      "message": "Less than 1 values on ex:bob->ex:name",
      "messages": ["Less than 1 values on ex:bob->ex:name"],
      "value": null,
      "source_shape": "_:n1",
      "constraint_component": "http://www.w3.org/ns/shacl#MinCountConstraintComponent",
      "shapes_id": "person"
    }
  ],
  "notes": []
}
```

`shapes_provenance` has one entry per shapes document (`ontology_provenance` has the same
structure for ontologies):

| field | meaning |
|---|---|
| `id`, `name`, `version` | identification of the set. |
| `name_source` | `config`, `file` (`dcterms:title`) or `id` (nothing else available). |
| `version_source` | `config`, `file` (`owl:versionInfo`, else `dcterms:hasVersion`) or `unspecified`. |
| `sha256` | SHA-256 of the document (see below). |
| `source`, `source_type`, `final_url` | where it was loaded from (`file`, `url` or `inline`); `final_url` is the URL after redirects. |
| `declared_in_file` | title, version, version property and `owl:imports` declared by the document's `owl:Ontology`. |
| `discrepancies` | list of `{"field", "config", "file"}` where the configuration and the file disagree. |
| `notes` | warnings about the document (ambiguous declarations, no SHACL terms...). |

### Where name and version come from

* **Version**: the configured `version` wins; otherwise the version declared by the file
  (`owl:versionInfo`, else `dcterms:hasVersion`, on the `owl:Ontology` resource); otherwise
  it is the literal string `"unspecified"`. A version is never inferred from the file name
  or invented.
* **Name**: configured `name`, else `dcterms:title` of the `owl:Ontology`, else the id.
* If the configuration and the file disagree, the configured value is reported **and** the
  difference is recorded in `discrepancies` (and logged). If a file declares ambiguous
  metadata (several values or several ontologies), it is ignored and a note is added.

### What the hash covers

`sha256` is the SHA-256 (lowercase hex) of the **raw bytes of the document exactly as they
were handed to the RDF parser**:

* local file: the content of the file;
* URL: the response body downloaded in this execution (after transport decoding such as
  gzip, before any charset decoding);
* inline content: the bytes given (`str` is encoded as UTF-8).

There is no decoding, newline normalisation or RDF canonicalisation, so it can be
reproduced with `sha256sum file.ttl`. The consequence is deliberate: the hash identifies the
*artefact*; re-serialising or re-formatting the same graph changes it. The fields
`hash_algorithm` / `hash_input` (`"raw-bytes"`) state this in every report. Use
`expected_sha256` to make a run fail if the shapes are not the ones you pinned.

`validated_at` is the UTC time of the validation; `software` records the versions of
pyshacl, rdflib, fair_eva and Python.

## Using it from a plugin

`EvaluatorBase` provides two optional methods:

* `get_rdf_graph()`: override it to return the item's RDF as an `rdflib.Graph`;
* `validate_shacl(data_graph=None, shape_sets=None, **kwargs)`: validates `data_graph`
  (default: `get_rdf_graph()`) against the sets selected for the plugin; returns a
  `ShaclReport` or raises `ShaclError`.

```ini
# plugin config.ini
[shacl:resource]
# Relative to the directory of plugin.py
source = shapes/resource.ttl
version = 1.0

[my_plugin]
shacl_sets = ['resource']
```

```python
from fair_eva.api.evaluator import EvaluatorBase
from fair_eva.shacl import ShaclError


class Plugin(EvaluatorBase):
    def get_rdf_graph(self):
        return self.rdf          # rdflib.Graph built by the plugin

    def rda_r1_3_01m(self):
        try:
            report = self.validate_shacl()
        except ShaclError as exc:
            return 0, [{"message": str(exc), "points": 0}]
        return report.as_indicator_result()
```

For other scoring rules, read the report instead of calling `as_indicator_result()`, e.g.
`report.violations`, `report.group_by("focus_node")` or `report.runs[0].results_graph`.
Reports are kept in memory only: if the provenance must appear in the output, include it in
the indicator messages (`as_indicator_result()` does it) or use `report.to_dict()`.

## REST endpoint

`POST /v1.0/shacl/validate`

```json
{"data": "<RDF text>", "format": "turtle", "repo": "my_plugin",
 "shape_sets": ["resource"], "ontologies": [], "combine": "separate"}
```

Only `data` is required. `repo` makes the plugin's configuration (and relative paths) apply;
without it only the core configuration is used. `shape_sets` / `ontologies` can only name
sets **configured on the server**: the request cannot bring its own shapes or URLs. The
response is the report above; errors are `{"code", "message"}` with 400 (bad request,
unknown set, bad data) or 500 (shapes that cannot be loaded, engine failure).
