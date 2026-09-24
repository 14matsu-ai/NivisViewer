# Continuous wheel at the cold-cache frontier

## Scope and observation

The supplied private ZIP was used only for dimensions/formats and archive,
decode, resize and navigation timing. No source images were displayed,
exported, described, classified or OCRed. Reports contain numbers and page
indexes, not archive member names. The ViewerWindow remained offscreen.

After settling the offscreen layout before opening the book, both cached and
continuous runs decoded exactly 25 pages once each: zero duplicate decodes,
zero cancellations and zero stale results. The initial probe did not settle
layout and misleadingly included startup resize-related decodes; its results
are not the final comparison.

The cache-ready case spends about 0.20 seconds scrolling, but that excludes
the preceding cache preparation. The uncached case must perform extraction,
decoding and resizing while navigating. Its decoder was already almost
continuously occupied. Replaying accumulated wheel input or adding an
automatic page-advance timer would not remove that work, and could change
the existing stop/reversal behavior. Those changes were not made.

## Selected change

Keep one raster decoder. Once its current JPEG payload is available, overlap
the next DEFLATED JPEG's ZIP extraction with current decode/resize. A separate
reader owns at most one encoded payload, capped at 32 MiB and charged against
the combined book budget and in-flight reservations. Stored ZIP entries and
other formats continue through their existing paths.

The first frame retains priority. Read-ahead starts only after continuous
warmup has been released. Direction reversal, cancellation, suspension,
layout invalidation and budget shrink discard or cooperatively cancel the
encoded work. Running work retains its reservation until it settles. Its
completion signal also lets retired runtimes finish cleanup when the raster
job has already ended. Consumed/cancelled futures break their callback cycle
so completed payloads do not wait for cyclic garbage collection.

The reader never decodes or publishes images. Existing source epochs,
request IDs, current-first raster scheduling and atomic GUI commits remain
responsible for presentation. No dependency was added.

## Same-book A/B measurement

`benchmark_continuous_cold_wheel.py --compare-read-ahead --repeats 3` alternates
enabled/disabled order. Single page, 2560 × 1440 offscreen window, 4 GiB book
budget, 8 ms wheel packets, all 25 pages. Each pass has an empty application
cache; the OS file cache can already be warm.

| Median, milliseconds | Disabled | Enabled |
| --- | ---: | ---: |
| Open through final continuous-scroll commit | 3020.46 | 2505.68 |
| Continuous scrolling after first commit | 2923.50 | 2383.69 |
| Open, wait for all cache, then scroll to end | 3085.65 | 2671.95 |

Continuous end-to-end completion improved about 17%; scrolling after the
first commit improved about 18%. All runs used 25 decode calls, and enabled
runs consumed 23 prefetched encoded payloads. Waiting for the entire cache
still makes the scrolling phase much faster. These are offscreen auxiliary
measurements, not a claim about native Windows input/paint latency.

A separate 12-page direct-navigation probe exercised forward, reverse,
roundtrip and rapid target changes. Three-pass medians for reverse were
408 ms disabled / 411 ms enabled, and roundtrip 237 / 240 ms: no clear gain
for those short sequences. Rapid direct seeks fluctuated between passes;
the instrumented follow-up confirmed zero encoded read-ahead starts during
that scenario, whose warmup is not released. The main measured benefit is
continuous traversal, not a guarantee that every cold single-page request
becomes faster. All these sequences reached the correct final target with
zero terminal errors.

## Validation

285 related regression tests passed, including nine new reader/source/runtime
cases covering single-slot ownership, failure retry, budget shrink, reversal,
suspension, cancellation, source close and late idle notification. A blocked
reader and decoder test proves actual read/decode overlap with only one
raster job and no GUI-thread decoding.

Generated 3200 × 5000 images were tested through offscreen ViewerWindow in
DEFLATED ZIP and Folder form, ten scenarios each: forward, reverse,
roundtrip, outside-cache return, and 0/4/8 ms wheel bursts. All completed,
with zero stale results and clean coordinator/runtime shutdown. The ZIP
reversal included one cancelled decode attempt; all 24 pages subsequently
decoded successfully. Syntax validation covered 160 Python files and
`git diff --check` passed.

Reproduction tools:

* `scripts/benchmark_continuous_cold_wheel.py --compare-read-ahead`: same-book
  cache-ready versus continuous traversal, alternating enabled/disabled runs.
* `scripts/benchmark_raster_overlap.py --compare-read-ahead`: one decoder,
  forward/reverse/roundtrip/rapid requests, encoded-read counts and final targets.
* `scripts/benchmark_raster_navigation_critical_path.py --deflated`: generated
  compressed ZIP and equivalent Folder through production navigation.

## Architecture comparison

| Option | Decision |
| --- | --- |
| ZipPlaFork | Retain as the single-worker decode/resize reference. Replacing Qt publication or moving to GDI+ is not justified by these measurements. |
| NivisViewer | Preserve the scheduler, source cache, generations and wheel frontier behavior; they did not produce duplicate work in the settled-layout comparison. |
| Hybrid | The earlier two-raster-worker experiment improved warmup but regressed cold reversal. It is now available as an explicit user option, not the default. |
| New design (selected here) | A separately bounded encoded-byte reader overlaps ZIP extraction with one decoder, without changing page-input semantics. |

No new ZipPla code or processing structure was copied in this follow-up.
The reference remains `himamon/ZipPlaFork` revision
`07955f5267e2fb92d6fc6e40fde2507d8fb07b3b`,
`source/ZipPla/ViewerForm.cs` and `source/ZipPla/ImageLoader.cs`.
Existing AGPL-3.0-or-later provenance, license text and copyright notices
remain in `ZIPPLAFORK_COMPARISON.md` and `licenses/ZipPlaFork/`.

## User-selectable parallelism

Settings → Viewer → Viewer memory now exposes two independent controls:

* Concurrent image loads: 1 (default/recommended) or 2, for Folder and ZIP.
* Parallel next-page ZIP extraction: enabled by default, or disabled.

The recommended combination is one decoder plus one encoded ZIP reader.
Choosing two image loads can use two decoders plus the bounded reader, but
is not recommended universally because earlier cold-reversal tests slowed
down. Four or more image loads have not been validated and are not offered.
The ZIP reader remains single-slot, not an unbounded file-read queue.

Both settings are persisted, normalized and included in the Viewer reset
scope. They apply when the next book is opened; changing settings does not
replace the current runtime or cancel its active work. The shared coordinator
enables/disables its second Viewer lane at that boundary, keeps owned work
until completion, and leaves Browser worker capacity unchanged. The generated
navigation benchmark now selects worker count through saved configuration
rather than replacing private BookSession fields.

Settings follow-up checks: the combined configuration/settings/session/raster/
language run passed 218 tests and failed three pre-existing translation
checks. The failures concern untranslated Browser/settings text and a fixed
catalog-count assertion of 1019 (HEAD already contains 1042 entries; this
change adds five translated entries, for 1047). No existing translations were
removed. The separate settings/Folder/ZIP integration run passed all 70 tests.
Syntax checks and whitespace validation passed. No native app was launched.
With the saved two-image-load option, the 3200 × 5000 DEFLATED ZIP/Folder
offscreen run completed all ten scenarios per source (forward, reversal,
roundtrip and rapid input), with zero stale results and successful shutdown
without remaining runtime tasks. ZIP decoded 121/121 attempts and Folder
167/167 across the scenarios. These figures include cache work and are not
a matched performance comparison against the earlier one-worker run.
