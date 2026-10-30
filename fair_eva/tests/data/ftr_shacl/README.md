# SHACL shapes of the FAIR Testing Resource (FTR) vocabulary, release 1.3.0

Verbatim copies (byte for byte, not reformatted) of the SHACL shapes that the tests in
`fair_eva/tests/ftr/` use to validate the FTR output of FAIR EVA with pySHACL.

## Provenance

| | |
|---|---|
| Repository | <https://github.com/OSTrails/fair_testing_resource_vocabulary> |
| Release | 1.3.0 (directory `release/1.3.0/SHACL/`; the repository has no `v1.3.0` git tag, its tags stop at `v1.2.0`) |
| Commit | `26e50f717b6f3ef0526a508f89b9bd1aa9a1180d` (`main`, 2026-04-27, "cosmetic improvement") |
| Retrieved | 2026-10-30 (shallow clone). At that commit `development/SHACL/` is identical to `release/1.3.0/SHACL/`. |
| Authors (upstream) | Daniel Garijo, Mark Wilkinson, Pablo Alarcón, Esteban Gonzalez-Guardia, Tomasz Miksa, Lukas Arnhold, Allyson Lister, Elli Papadopoulou, Leonidas Pispiringas |
| License | Apache License 2.0, the `LICENSE` of the repository, copied here as [`LICENSE`](LICENSE) (the repository has no `NOTICE` file) |

## Files

SHA-256 of each file, equal to the one of the file in the upstream commit:

```
cef3c2fb936ff6700adb3d8f0a7694f69fea869af784596833e3bccbd6be0687  1.3.0/metric.shacl
1d98bbed492330b48b9a6acf4d73e676687e39daf60ef2a08c84396234ce5a66  1.3.0/test.shacl
90d2b5661f6784c4d38dc1a98af2af8605f2db8b53bd34870085775a17ba3b4b  1.3.0/testResult.shacl
a67259f32ab66a9fe0ae786d422f18cad84ddf06b5375b1c9ed500f55ad78868  1.3.0/testResultSet.shacl
c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4  LICENSE
```

Check them with `cd fair_eva/tests/data/ftr_shacl && sha256sum 1.3.0/*.shacl LICENSE`.

Only the shapes for what FAIR EVA produces are copied. `benchmark.shacl`,
`benchmarkScore.shacl` and `scoringAlgorithm.shacl` are not needed because FAIR EVA does
not model benchmarks yet.

## Not modified, but not perfect

The files are used as they are published. Their known problems (the same shape IRIs in every
file, `sh:nodeKind xsd:string` in `metric.shacl`, ...) are described in
[`docs/ftr_ostrails.md`](../../../../docs/ftr_ostrails.md). The tests work around them without touching
these files: each file is loaded on its own, and the test that validates metrics patches
`metric.shacl` in memory.

`.pre-commit-config.yaml` excludes this directory so that the hooks (for example the one that
adds a final newline, which `test.shacl` lacks) do not alter the files and their checksums.

## Updating

Copy the new files from the new release of the vocabulary, update this table (release,
commit, checksums) and run `python -m pytest fair_eva/tests/ftr`.
