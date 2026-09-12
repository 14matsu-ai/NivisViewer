# Browser horizontal thumbnail gaps — 2026-09-11

This records the accepted horizontal-margin reduction. The subsequent
independent spacing/settings fix is documented in `BROWSER_SPACING_SETTINGS.md`;
it retains this compact default, but replaces the ineffective Qt spacing
setting with explicit grid-pitch gaps and corrects filename padding painting.

## Cause and narrow change

The real offscreen QListView cells already touched: their measured horizontal
item-rectangle gap was zero. `BrowserGridProfile.horizontal_margin` nevertheless
added 4 / 20 / 20 / 44 / 72 / 104 logical pixels to the thumbnail width as density
increased. Centering the fixed-size thumbnail split this extra title allowance
between its left and right sides. That was unused inter-thumbnail space, not
source-image letterboxing, rating stars, or an associated-icon backing.

Only the five wider profile margins change in production: all six profiles now
use the existing Extra Compact allowance of **4 logical pixels total** (2 per
side). The existing `BrowserGridMetrics` still owns every rectangle:

`cell width = frame width + 4 + 2 × explicit cell padding`

No alternate layout, config migration, font reduction, source-image shrink,
image decoding, source traversal, or rendering-policy change was added.
The title uses the narrower cell and existing elision/wrapping; its font and
hidden/one-line/two-line choice are unchanged. Long names can elide sooner.
Canonical names, full-name tooltip/status/copy paths, and item identity remain
unchanged. Frame, title and rating hit targets remain usable within each cell.

## Measured before/after

Synthetic 149-logical-pixel long-edge portrait `1:sqrt(2)`, 105 × 149 frame,
one-line filename, zero explicit cell padding, Qt 6.11.2, 100% DPI:

| Density | Frame gap before → after | Fit image-pixel gap before → after |
| --- | --- | --- |
| Extra Compact | 4 → 4 | 12 → 12 |
| Compact | 20 → 4 | 28 → 12 |
| Medium | 20 → 4 | 28 → 12 |
| Standard | 44 → 4 | 52 → 12 |
| Comfortable | 72 → 4 | 80 → 12 |
| Large | 104 → 4 | 112 → 12 |

The pixel measurements use generated solid-color images matched to the Fit
content rectangle. Fit retains its existing 4-pixel inset per side; center crop
retains its 1-pixel inset. Source-specific aspect-fit letterboxing is separate
and intentionally remains (tested using a narrow generated image).

For Medium in a **744-pixel viewport**: cell width 125 → 109, columns 5 → 6,
image width 97 → 97, row pitch 160 → 160, vertical frame gap 11 → 11, vertical
image-pixel gap 19 → 19. Right remainder 119 → 90. No horizontal scrollbar.
Vertical frame/row geometry remains exact at every tested DPI. At fractional
DPI, existing physical-pixel rounding of a new x-origin may change Fit's
available width by one physical pixel and move each aspect-fit vertical edge
by at most one physical pixel; no resampling/snapping algorithm was changed.

Resize measurements use view widths 452, 760, 761 and 927 (the viewport excludes
frame/scrollbar width). Qt leaves the remainder at the right edge, not between
columns. Its existing strict boundary can wrap a column at an exact cell-width
multiple, leaving one cell's worth of remainder. That existing Qt behavior was
measured and retained, not worked around with a new layout. Likewise, in this
fixed-grid IconMode configuration, Qt ignores `spacing()` for actual cell pitch;
both old and new real-cell measurements demonstrate that. Explicit cell padding
continues to affect both axes; this task does not redesign the spacing setting.

## Verification and scope

- 117 passed: new horizontal measurements plus Browser grid, Medium preset and
  Browser window regressions.
- 114 passed: Browser navigation/history/viewport restoration, thumbnail display
  resampling, Sprint 16 selection/layout and Medium preset regressions (the
  Medium group overlaps the earlier run).
- 31 focused tests passed at each 100/125/150/200% DPI in separate processes:
  real cell rectangles, before/after delegate raster pixels, six densities,
  four widths, explicit padding, all filename modes, thumbnail/title clicks,
  Right/Down navigation, rating/associated-icon bounds, and horizontal clipping.

Before-state rendering uses a test-only delegate subclass restoring only the
old margin constants; layout and painting otherwise use production code.
Every image and path fixture is synthetic/temporary. No private image was opened
or analyzed. More columns can naturally make more items visible; the existing
visible-thumbnail scheduling/cache authorities continue to govern their work.
No new decode/request/rescan mechanism was introduced.

Production change: `app/browser_item_delegate.py`. Tests:
`tests/test_browser_horizontal_geometry.py`, `tests/test_browser_grid.py`, and
`tests/test_browser_window.py`. Documentation: this file and `ARCHITECTURE.md`.
Recent license cleanup and all unrelated work remain intact. No real app,
native input, external GUI, dependency install, portable build, commit or push.
