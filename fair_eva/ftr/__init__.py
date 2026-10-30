"""FAIR Testing Resource (FTR) vocabulary support for FAIR EVA.

This package exposes FAIR EVA tests as resources of the OSTrails FTR vocabulary
(release 1.3.0) and serialises test executions as FTR test results. A *test* is
the pair ``plugin + indicator``: the same RDA indicator (the FTR *metric*) is
checked differently by every plugin. See ``docs/ftr_ostrails.md``.

Nothing in this package is specific to a repository or research domain.
"""
