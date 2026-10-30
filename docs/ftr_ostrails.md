# FAIR EVA and the OSTrails FTR vocabulary

FAIR EVA describes its tests and publishes the outcome of running them with the
[FAIR Testing Resource (FTR) vocabulary](https://github.com/OSTrails/fair_testing_resource_vocabulary)
of OSTrails, release **1.3.0**, and its output validates with pySHACL against the SHACL
shapes of that release. This page records the design decisions, the field-by-field mapping
and the points where the specification is unclear or inconsistent.

Source of truth: the `release/1.3.0` folder of the vocabulary repository (commit
`26e50f7`), read locally (`ontology.ttl`, `SHACL/*.shacl`, `ftr_context.jsonld`,
`api/open_api_description.yaml` and `sections/*.html`). Nothing here comes from the
OSTrails web site, which was not reachable.

## 1. Decision: a *test* is `plugin + indicator`

| FTR concept | FAIR EVA |
|---|---|
| `ftr:Metric` | An RDA indicator (`rda_f1_01m`, "RDA-F1-01M"), the same for every plugin |
| `ftr:Test` | The pair **plugin + indicator** (`dummy` + `rda_f1_01m`) |
| `ftr:TestResult` | The outcome of one test on one resource |
| `ftr:TestResultSet` | The outcomes of several tests of one plugin on one resource, from one request |
| `ftr:TestExecutionActivity` | One execution (for a result set, the whole request) |

Why the pair and not the indicator alone: the indicator says *what* is checked, but *how*
depends on the repository, which is what a plugin knows (which metadata terms carry the
identifier, the licence, the access protocol...). The specification supports this reading:

* A `Test` is a "service, formed by an API and associated piece of code that implements a
  Metric" (`README.md`, `ontology.ttl` `ftr:Test` comment). It is a `dcat:DataService` and a
  `prov:Agent` (`ontology.ttl`), i.e. something with its own endpoint that can be associated
  with an execution (`prov:wasAssociatedWith`, `testResult.shacl` `:TestExecutionActivityShape`).
* Test and metric are related many-to-one: a test points to the metric it implements with
  `sio:SIO_000233` (`test.shacl` `:TestShape`, `sh:minCount 1`) and a metric lists its
  implementations with `sio:SIO_000234` (`metric.shacl` `:MetricShape`, no `maxCount`). Several
  plugin tests implementing the same indicator is therefore the normal case.
* A test has its own `dcat:version` (exactly one, `test.shacl`), `ftr:status`,
  `doap:repository`, `dcat:endpointURL` and `dcat:endpointDescription`, so each pair can be
  versioned and called independently.

Rejected alternatives: *one test per indicator with the plugin as a parameter* (the endpoint
and the code that implements the metric would not be those of the test, and results of
different repositories would look like the same test); *one test per plugin* (a test would
implement many metrics, and results could not be per indicator).

## 2. Resources and IRIs

Everything hangs from `[ftr] base_uri` in `config.ini` (provisional default
`https://example.org/PROVISIONAL/fair-eva`). FAIR EVA never chooses persistent identifiers.

| Resource | IRI |
|---|---|
| Test | `{base}/test/{plugin}/{indicator}` |
| Metric | `{base}/metric/{indicator}` |
| Test result / set / execution | `{base}/result/{uuid}`, `{base}/resultset/{uuid}`, `{base}/execution/{uuid}` |
| Suggestion | `{result}/suggestion/{n}` |
| Assessed resource | URL as is; DOI/handle/ARK/PURL as its resolver URL; otherwise `{base}/resource/{plugin}/{id}` |

The UUIDs are version 5 UUIDs of (test, resource, end time of the execution), so the same
execution always yields the same IRIs. The original identifier is always kept in
`dcterms:identifier` of the assessed resource.

## 3. Mapping

### Test (`test.shacl`) and metric (`metric.shacl`)

| FTR property | Value |
|---|---|
| `rdf:type ftr:Test`, `dcterms:identifier` | Test IRI (as a string, see D4) |
| `dcterms:title`, `dcterms:description` | Indicator code and title, plus the plugin; indicator description (from `docs/technical_implementation.md`, packaged in `fair_eva/ftr/indicators.json`) |
| `sio:SIO_000233` | Metric IRI |
| `dcat:endpointURL` | `{endpoint_base_url}/assess/test/{plugin}/{indicator}` |
| `dcat:endpointDescription` | `{endpoint_base_url}/openapi.json` |
| `dcat:landingPage` | `{endpoint_base_url}/tests/{plugin}/{indicator}` |
| `dcat:version` | `fair_eva-<installed version>` (D10) |
| `ftr:status` | `"Active"@en` |
| `dcterms:license` | `[ftr] test_license` |
| `dcat:contactPoint` | `[ftr] contact_*` as `vcard:Organization` or `vcard:Individual` |
| `dcterms:creator`, `dcterms:publisher` | `[ftr] creator_*`, `publisher_*`, only if configured (D8) |
| `doap:repository`, `ftr:supportedBy` | `[ftr] code_repository`, `tool_uri` |
| `dpv:isApplicableFor` | `[ftr] applicable_for`, only if configured (can differ per plugin) |
| `dcat:keyword`, `vivo:abbreviation` | FAIR, RDA, indicator code, FAIR dimension, plugin |
| Metric: `dqv:inDimension` | `https://w3id.org/fair/principles/terms/{principle}` (D13) |
| Metric: `sio:SIO_000234` | The test of every installed plugin |
| Metric: `dcat:landingPage` | `https://doi.org/10.15497/rda00050`, the RDA model cited in `docs/index.md` |

### Test result (`testResult.shacl`, `testResultSet.shacl`)

| FAIR EVA (JSON of `/rda/*`) | FTR |
|---|---|
| `test_status` (`pass`, `fail`, `indeterminate`) | `prov:value`; an indicator that raised an exception (HTTP 422, `name: ERROR`) is `indeterminate` (D9) |
| `points` / `score.earned` (0-100) | `ftr:completion`, rounded to an integer (D1) |
| `msg` (list of `{message, points}`) | `ftr:log` (one line per check, `[points/100] message`), `dcterms:description`, and one `ftr:suggestion` per message that earned less than 100 points (D2) |
| `evaluator_logs` | Lines of level INFO or higher emitted by the indicator, appended to `ftr:log` |
| `id` | `ftr:assessmentTarget`, `prov:used` |
| `repo` + indicator | `ftr:outputFromTest` (Test IRI); the `ftr:Test` is embedded in the result |
| (not recorded) | `prov:startedAtTime`, `prov:endedAtTime` of the activity and `prov:generatedAtTime` of the result |
| `color`, `score.total`, `lang`, `api_endpoint` | Not mapped (presentation, constant, input) |
| `x-level` weight, `x-principle` | Not mapped to results; the principle is the metric's `dqv:inDimension` |

`ftr:suggestion` is `ftr:GuidanceContext` with a title and a description. A result set has one
activity for the whole request plus one activity per member.

## 4. API

FAIR EVA serves an additional OpenAPI definition under `/v1.0/ftr` (`fair_eva/ftr/ftr-api.yaml`,
its own `openapi.json` and Swagger UI), shaped after
`release/1.3.0/api/open_api_description.yaml`. The `/v1.0/rda/*` endpoints and their JSON are
unchanged.

| Request | Returns |
|---|---|
| `GET /tests[?plugin=&testid=]`, `GET /tests/{plugin}/{indicator}` | `ftr:Test` descriptions |
| `GET /metrics[?metricid=]`, `GET /metrics/{indicator}` | `ftr:Metric` descriptions with their implementations |
| `POST /assess/test/{plugin}/{indicator}` `{"resource_identifier", "api_endpoint"?, "lang"?}` | `ftr:TestResult` |
| `POST /assess/plugin/{plugin}`, same body plus optional `"tests": [...]` | `ftr:TestResultSet` (all tests if `tests` is omitted) |

```bash
curl -H 'Accept: text/turtle' http://localhost:9090/v1.0/ftr/tests/dummy/rda_f1_01m
curl -X POST -H 'Content-Type: application/json' -H 'Accept: text/turtle' \
     -d '{"resource_identifier": "10.1234/abcd.5678", "api_endpoint": "https://repository.example/oai"}' \
     http://localhost:9090/v1.0/ftr/assess/test/dummy/rda_f1_01m
```

Representation: JSON-LD (default) or Turtle with `Accept: text/turtle` /
`Accept: application/ld+json`, or `?format=turtle|jsonld`. `application/json` is answered
with JSON-LD. Unsupported types get 406. The JSON-LD uses an inline context with the terms
of `ftr_context.jsonld` plus a few more (D5).

Unknown plugin or indicator is 404, a request that cannot start (no identifier, plugin
failed to initialise, e.g. no `api_endpoint`) is 400. A test that ran and failed, or crashed, is
200 with an FTR result.

## 5. Validation

```bash
python -m pytest fair_eva/tests/ftr          # offline; includes the SHACL checks
python -m pytest fair_eva/tests/ftr/test_shacl.py -v
```

`fair_eva/tests/ftr/test_shacl.py` builds the output for the `dummy` plugin (in memory, as
Turtle and as JSON-LD parsed back, and through the HTTP API) and validates it with
`pyshacl.validate(..., inference="none")` against `test.shacl`, `testResult.shacl` and
`testResultSet.shacl` for a passing, a failing and a crashed test, and for a result set. It
also checks that broken output (no suggestion, no licence, a float completion, a blank node,
...) is rejected. The shapes are vendored, with provenance and checksums, in
`fair_eva/tests/data/ftr_shacl/`.

Each shape file is validated **on its own**. They all declare shapes in the same namespace
(`http://www.example.org/me#`), so `:TestShape`, `:MetricShape`... in one file are the same
nodes as in the others; loading them together merges them (D7).

## 6. Deviations and open questions

D1. **`ftr:completion`.** `ontology.ttl` gives it range `xsd:float` and says 1 means pass and
values are in 0..1; `testResult.shacl` (`:TestResultShape`) requires `xsd:integer`; the examples in
`description-en.html` use `100`. FAIR EVA emits an integer 0-100 (its points), as the shape
demands.

D2. **A suggestion is mandatory.** `testResult.shacl` requires at least one `ftr:suggestion`
(`sh:minCount 1`) in every result, also for a pass; the passing example of
`description-en.html` has none. A fully satisfied result gets one placeholder suggestion
("No improvement is needed..."). Suggest relaxing the shape upstream.

D3. **Blank nodes are rejected.** `:TestExecutionActivityShape` and `:GuidanceShape` have
`sh:nodeKind sh:IRI`, yet the examples use blank nodes. FAIR EVA mints IRIs for them. The
first Turtle example of `description-en.html`, validated against `testResult.shacl`, fails for
this, for the missing `prov:endedAtTime` and `ftr:suggestion`, and for D4; its `ftr:Test`
fails `test.shacl` for lack of licence, metric and contact point.

D4. **`dcterms:identifier` is a string.** The shapes require `xsd:string` or language-tagged
literals; the examples use IRIs (`<urn:foops:...>`). FAIR EVA writes the IRI as a string.

D5. **SIO namespace.** The SHACL shapes and the Turtle examples use
`http://semanticscience.org/resource/SIO_000233`; `ontology.ttl` and the official
`ftr_context.jsonld` (and so `https://w3id.org/ftr/context`) use `https://...`. A document
built with the official context would not match `test.shacl` (the metric link would be
missing), so FAIR EVA uses `http` and embeds its own context instead of referencing the
official one. `ftr_context.jsonld` also maps `GuidanceElement` to `ftr:` while `ontology.ttl`
defines it in `fgv:`, and has the typo `haSoftwareImage`.

D6. **`metric.shacl` cannot be satisfied.** It declares `sh:nodeKind xsd:string` (not a node
kind) for `dcterms:title`, `dcterms:description` and `dcat:version`, which are mandatory, and
also for `ftr:status`, `dcat:keyword`, `vivo:abbreviation` and `vcard:organization-name`. Any
`ftr:Metric` is rejected. The test patches the shape in memory (`sh:or` of string and
langString, as the other files do) and passes; a strict `xfail` test checks the unpatched file,
so it will flag when upstream fixes it. The tests' descriptions do not type the metric node, so
`test.shacl` is not affected; typed metrics are only served by `/metrics`.

D7. **Shape IRIs clash across files.** See section 5. Loading the four files into one graph
makes the metric constraints of D6 apply to every test's metric, and no test validates.

D8. **`dcat:publisher`.** `test.shacl` (also `metric.shacl`, `benchmark.shacl`) checks
`dcat:publisher`, which DCAT does not define. The Metric example of `description-en.html`
uses `dcterms:publisher`, which is what FAIR EVA emits; the publisher is therefore not
validated.

D9. **Crashes.** The vocabulary documents `pass`/`fail` for `prov:value` (`ontology.ttl`) and
has `ftr:hasIndeterminateExpectation`/`Validation`. A test that could not run (network error,
bad configuration) is reported as `indeterminate`, not as `fail` as the `/rda/*` JSON does,
because it says nothing about the resource. This is a choice, not something in the spec.

D10. **Test version.** Plugins do not expose a version, so `dcat:version` is only the FAIR EVA
version (`fair_eva-1.5.0`). `[ftr] version` in a plugin's `config.ini` overrides it.

D11. **Licences are mandatory.** A test needs exactly one `dcterms:license`, a result at
least one (`test.shacl`, `testResult.shacl`). Provisional defaults: Apache-2.0 for tests (the
licence of `LICENSE`; `fair-api.yaml` says MIT, which is inconsistent) and CC BY 4.0 for results
(the one in the examples).

D12. **One log.** `testResultSet.shacl` limits `ftr:log` and `prov:value` to one value per
result, so all messages go in a single multi-line string.

D13. **FAIR principle IRIs** follow the single example of the specification
(`https://w3id.org/fair/principles/terms/A1` in `description-en.html`); the other terms
(`F1`, `A1.1`...) could not be checked offline.

D14. **Smaller inconsistencies**, not affecting FAIR EVA: the comment of `ftr:TestResult`
in `ontology.ttl` mentions `ftr:definedBy` (the property is `ftr:outputFromTest`);
`ftr:supportedBy` is declared both an object and a datatype property; `ftr:applicationArea`
has range `xsd:string` but `sh:nodeKind sh:IRI` (not used by FAIR EVA, which is domain
agnostic); the dates in the examples (`"Wed Aug 13 18:25:34 CEST 2025"^^xsd:date`) are not valid
`xsd:date`.

Not covered: benchmarks, scoring algorithms and benchmark scores (the weights `x-level` of
`fair-api.yaml` and the web score could be modelled as one), the plugin-specific
`data_01`/`data_02` tests, and `rda_all`.

## 7. Decisions that belong to the deployer

Set them in the `[ftr]` section of `config.ini` (plugin-level keys can also go in a plugin's
own `config.ini`). The defaults are placeholders:

1. **Persistent identifiers.** `base_uri` (and whether it will be registered, e.g. at
   w3id.org, and resolve to the descriptions), `endpoint_base_url` of the public API.
2. **Registration in OSTrails** of the tests, and whether FAIR EVA's metrics should point to
   metric IRIs already registered there instead of `{base}/metric/{indicator}`.
3. **Licences** of the tests and of the results (`test_license`, `result_license`).
4. **Contact point** (required), **publisher** and **creator**: organisation or person,
   name, email, IRI (ORCID, ROR).
5. **Software references**: `code_repository` (defaults to the upstream FAIR EVA repository)
   and `tool_uri`.
6. **Object types** each plugin applies to (`applicable_for`).
7. **Versioning** of tests and plugins, and the vocabulary of `ftr:status`.
8. **Execution errors**: `indeterminate` with HTTP 200 (D9), or `fail`.
9. **Privacy**: results contain the identifier of the assessed resource and the plugin's log
   messages; publish them only where that is acceptable.
10. Whether to model the RDA weights as an FTR benchmark and scoring algorithm, and whether to
    translate titles and descriptions (the registry has the Spanish texts in `docs/`).
