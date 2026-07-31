# Bundled license texts

`scripts/collect_licenses.py` copies license and notice files from the exact
installed distributions into this directory before a release build.

The generated manifest and every copied text require a manual audit before a
public release. No license text is inferred or synthesized.

`ZipPlaFork/` is not generated from an installed distribution. It preserves
the exact `license/AGPL.txt` and `license/About.txt` files from fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` for the Viewer processing
structure provenance recorded in `docs/ZIPPLAFORK_COMPARISON.md`.
