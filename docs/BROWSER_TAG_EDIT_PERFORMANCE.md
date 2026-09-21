# Browser tag-edit latency (2026-09-21)

The right-click tag action changes a file's name synchronously. Before this
change, a single file also scanned every `library_items` row to relocate its
metadata, rebuilt and reset the entire Browser model, and then did another
reset after a normal Windows directory-watcher reconciliation. The watcher
event was observed in the synthetic offscreen run; it was not assumed.

The single-file metadata relocation now uses the indexed exact path. A single
rating/tag rename updates only its model row when the existing sort position
and filter membership remain valid. A reorder or filter change rebuilds the
visible rows with binary insertion and updates the position indices; it still
resets the Qt model so row count and selection stay consistent. Stable
multi-item edits update their rows without a reset. The directory watcher still scans to
detect external changes, but an equal scan result no longer resets the model
or starts a new thumbnail generation. Failed thumbnails still retry on an
unchanged filesystem notification. External additions and modifications are
reconciled normally. Multi-item edits that change order or filter membership
retain the batch reset behavior.

`out/tag_latency_compare.py` used three paired fresh offscreen processes per
variant, exporting HEAD `app/` as the baseline. Each run created an isolated
20- or 5,000-file synthetic folder and equally populated metadata database.
No image decoding, native input, personal files, or real app launch were used.
The Noop thumbnail provider records requests but performs no decode.

| Files | Metric | HEAD median | Changed median |
| ---: | --- | ---: | ---: |
| 20 | Tag call | 1.615 ms | 1.259 ms |
| 20 | Metadata relocation | 0.303 ms | 0.261 ms |
| 20 | Model rename update | 0.375 ms | 0.158 ms |
| 5,000 | Tag call | 59.528 ms | 1.276 ms |
| 5,000 | Metadata relocation | 5.643 ms | 0.273 ms |
| 5,000 | Model rename update | 47.343 ms | 0.153 ms |
| 5,000 | Watcher result model reset | 24.812 ms | none |

Both variants observed one directory-watcher scan after the rename. For 5,000
items, the old scan advanced the thumbnail generation once and the changed
equal-result scan did not. Visible thumbnail request attempts were 60 in both
variants. The scan itself still costs work on a worker and prepares a listing
for safe external-change reconciliation; this probe does not quantify a native
desktop's subjective right-click delay or large multi-selection throughput.
Focused tests cover exact-path metadata relocation, stable-row thumbnail
preservation, filter-changing reset, equal watcher reconciliation, failed
thumbnail retry, external additions, and ItemTagsDialog Apply/Cancel behavior.

A later 15,000-item offscreen probe compared the original model path with the
current reorder/filter and batch paths. A single reorder fell from about 177
to 36 ms and a filter-changing rename from about 224 to 27 ms. Stable batches
of 20 and 100 fell from about 196 to 17 ms and 242 to 75 ms, respectively.
The pure model update is about 7 ms; the remaining single-edit time includes
view state restoration, while the 100-item batch is dominated by file rename
and metadata work. Moving those operations to a worker would add cancellation
and lifetime complexity for little measured gain, so they remain synchronous.
These synthetic figures do not establish native desktop responsiveness.

## 2026-09-22: single ZIP tag badge on slower storage

The single-archive tag path still performed the successful filename rename,
profile-database relocation, navigation-history relocation, and only then the
model update that paints the tag badge. A fresh-process offscreen synthetic
ZIP trace took about 1.8 ms without injected delay. With separate 50 ms
delays around the rename and metadata relocation to model slower storage,
`dataChanged` occurred around 102 ms after the click and the call returned
around 102 ms. This shows that both synchronous operations can gate the
badge; it does not identify the relative cost on a particular user's HDD.

For a single archive tag edit, the successful rename now updates the model
and repaints its visible row before relocating profile metadata. The
navigation-history path is still updated synchronously. The Pillow image
detail probe is not drained for archive/PDF edits because it cannot own those
files; image and folder edits retain that handle-safety wait. The metadata
relocation still completes before `set_tags_for_paths` returns, preserving
bookmark/history consistency for subsequent actions. Multi-selection,
rating edits, and folder operation order remain unchanged.

Under the same injected 50 + 50 ms delays, `dataChanged` moved to about
51 ms, and a visible offscreen viewport paint occurred around 52 ms, before
metadata relocation finished and before the call returned around 103 ms.
The filename rename itself remains synchronous and can still delay the badge
on a slow or contended drive. An active archive thumbnail/page-count worker
can temporarily hold a Windows ZIP handle; this change does not claim to
remove that separate rename-failure condition. The test covers paint before
metadata migration, retained bookmark relocation, and omission of the
irrelevant image-probe wait. No ZIP payload is rewritten or decoded on the
UI thread by this change.
