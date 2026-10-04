# Bundled license texts

`scripts/collect_licenses.py` copies license and notice files from the exact
installed distributions into this directory before a release build.

The generated manifest and every copied text require a manual audit before a
public release. No license text is inferred or synthesized.

The project itself is AGPL-3.0-or-later; see root `LICENSE` and
`PROJECT_LICENSE.md`. This does not relicense third-party texts in this folder.

`Qt/6.11.2/` is a curated, version-matched supplement from official upstream
sources. Its `SOURCES.json` records hashes/URLs; the collector verifies and
includes it without network access, only for matching installed versions.
Wheel metadata's open-source license expression and the wheel's limited
commercial-reference file inventory are recorded separately. Neither implies
a commercial license purchase is necessary. This supplement is not a complete
Qt third-party attribution or Corresponding Source package.

The collector also retains the installed CPython `LICENSE.txt` when available,
records per-file SHA-256 and release-pin mismatches, and preserves different
notices sharing a basename under hash-suffixed names. It does not delete older
notices or certify the actual frozen binary inventory. `--strict` only enforces
installed runtime/license-file presence; it is not a publication compliance gate.
Run into a new temporary output folder when auditing a new release, then review
the notices appropriate to that exact build. Do not publish an accumulated
development license folder without that review.

See `docs/RELEASE_LICENSE_AUDIT.md` for the inspected artifact inventory and
unresolved Qt source/module, Python/native, and release-preparation obligations.

`brackettree/` (0.2.5) and `loguru/` (0.7.3) supplement license texts omitted
from their wheels and source distributions. Each `SOURCES.json` records the
official upstream source and checksum; the collector checks the exact version
and checksum before using these texts without network access.

`ZipPlaFork/` is not generated from an installed distribution. It preserves
the exact `license/AGPL.txt` and `license/About.txt` files from fixed revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b` for the Viewer processing
structure provenance recorded in `docs/ZIPPLAFORK_COMPARISON.md`.
