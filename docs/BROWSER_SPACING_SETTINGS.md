# Browser spacing settings — 2026-09-12

## Current-code findings and authority

This follow-up preserves the accepted horizontal-margin reduction documented
in `BROWSER_HORIZONTAL_SPACING.md`. It does not replay the older ZIP/fullscreen
attachment, touch licenses, or introduce a second layout/config authority.

The existing path was SettingsDialog → ConfigManager → BrowserWindow →
BrowserItemDelegate → BrowserGridMetrics → QListView gridSize and delegate
paint/hit rectangles. `item_spacing` reached QListView.setSpacing(), but Qt's
fixed-grid IconMode ignores that value. The control and its density-preset
checkbox were therefore cosmetic. Real cells touched at every value.

Filename gap and padding were not identical: gap moved the entire title region,
whereas padding increased its height by twice the value. However, `_paint_title`
started at title_rect.top() without the top padding. All extra padding therefore
appeared below the glyphs, contrary to the old “上下余白” label.

There is no hidden vertical title allowance in current BrowserGridMetrics.
The profile's legacy vertical_margin/title_lines values do not determine title
height. Height is font metrics × the explicit filename line count, plus explicit
gap/padding. Font ascent/descent inside a line is not removable layout padding.
The accepted 4 px horizontal allowance remains distinct from inter-item gaps;
Fit/Crop inset and source-aspect letterboxing also remain unchanged.

## Settings → Browser → 一覧表示 / Item list

| Japanese label | English label | Effect (logical px) |
| --- | --- | --- |
| 項目の横間隔: | Horizontal item gap: | Additional gap between item rectangles; width of thumbnail/title unchanged. |
| 項目の縦間隔: | Vertical item gap: | Additional gap below an item before the next row; title geometry unchanged. |
| 項目内余白（全辺）: | Item padding (all sides): | Existing cell padding; adds twice its value to cell width and height without shrinking the image. |
| 画像とファイル名の間隔: | Image-to-filename gap: | External gap between image frame and filename region. |
| ファイル名内余白（上下）: | Filename padding (top/bottom): | Internal padding above and below text inside the filename region. |

Tooltips explain each effect and that zero is the compact minimum. The two
filename controls are disabled when filenames are hidden, retaining their
stored values for re-enabling. The redundant single “サムネイル間隔” control and
“密度プリセットに従う” checkbox are removed. No new settings store is introduced.
Apply/OK use the existing ConfigManager save/signal path; the open Browser
updates without restarting. Cancel retains the existing Settings semantics.

All spacing defaults remain zero. Item x/y and image gap range: 0–32;
filename padding: 0–16 per side; cell padding: 0–12 per side.

## Geometry and measured behavior

`cell width = frame width + 4 + 2 × cell padding`

`cell height = frame height + 2 × cell padding + filename gap + title height`

`title height = font height × line count + 2 × filename padding`

For hidden filenames, gap/title height are zero. `grid_size` adds the independent
x/y gaps to `cell_size`. Delegate sizeHint and actual item/hit rectangles remain
cell-sized, with blank non-item space between them. Qt centers the cell within
its grid allocation; thumbnail/title/overlay rectangles remain relative to that
cell. `title_text_rect` in the existing metrics now owns internal text padding;
the painter starts at its top. No image-size, font, elision, wrapping, aspect or
resampling policy changed.

Measured synthetic Medium, 149 px portrait thumbnail, frame **105 × 149**,
one-line filename/font height **11**, 100% DPI:

| Independent change from zero | Cell size | Grid pitch | Horizontal frame gap | Title top | Text ink y (Ag) |
| --- | --- | --- | --- | --- | --- |
| None | 109 × 160 | 109 × 160 | 4 | 149 | 149–155 |
| Horizontal item gap 7 | 109 × 160 | 116 × 160 | 11 | 149 | unchanged within cell |
| Vertical item gap 9 | 109 × 160 | 109 × 169 | 4 | 149 | 149–155 |
| Image-to-name gap 6 | 109 × 166 | 109 × 166 | 4 | 155 | 155–161 |
| Filename padding 6, before fix | 109 × 172 | 109 × 172 | 4 | 149 | 149–155 |
| Filename padding 6, after fix | 109 × 172 | 109 × 172 | 4 | 149 | 155–161 |

Thus gap=6 and padding=6 move text down equally after correction, but are **not
duplicates**: gap adds 6 to the row height; padding adds 12 and leaves 6 inside
the filename region on each side. Selected title background follows that region.
An independent vertical item gap controls space outside it, including with names
hidden. Two-line and hidden modes are covered separately.

Matched synthetic Fit image-pixel width stays 97 at 100% DPI. Horizontal image
gap is 12 at zero and 19 with x=7. Row pitch is 160 at zero and 169 with y=9;
vertical frame gap is 11 → 20. Fractional-DPI snapping can shift raster edges
by a physical pixel; logical frame/cell dimensions remain exact.

## Saved-setting migration

ConfigManager migrates before merging defaults, for load and legacy Apply
updates. It removes old `browser_item_spacing_mode` / `browser_item_spacing`
and stores only `browser_item_spacing_x` / `browser_item_spacing_y`:

- Explicit legacy `custom` value N becomes x=N, y=N (validated 0–32).
  This honors the chosen amount, but intentionally makes it visible for the
  first time. Invalid custom values retain the old validation fallback of 2.
- Preset/missing/invalid mode becomes x=0, y=0, preserving the prior actual
  compact appearance rather than activating previously ignored density gaps.
- Explicit new-axis values win independently; migration is idempotent on save
  and reload. New profiles default to zero on both axes.
- Existing cell padding, filename gap/padding, text mode, density and thumbnail
  size retain their values. Nonzero filename padding now distributes correctly
  above/below text; total item height is unchanged, but glyphs move downward by
  that padding. Zero/default appearance is unchanged.

Only synthetic temporary JSON fixtures were inspected/loaded for migration;
no private profile or image folder was read.

## Restoration, cache and scheduling

Geometry changes use the existing capture/restore/token guards. Explicit
selection/current identity survives and existing minimum-scroll policy keeps
current visible. With fewer columns, current and the old first-visible path may
be more than a viewport apart; both cannot always remain visible. Without a
selection, the existing path anchor and pixel offset are restored exactly where
scrollbar limits allow. No sorting, filtering or history authority was added.

Visible metadata/thumbnail range calculation consumes the new grid pitch.
Real Qt measurements at viewport = 4 × pitch show **3**, not 4 columns: IconMode
wraps on an exact boundary. Browser passes width−1 to its existing range helper
for initial and scrolled requests, preventing actual visible rows being skipped
or demoted at these boundaries. No alternate scheduler is introduced.

Spacing updates do not invalidate thumbnail generation, render spec, cached
source images or display surfaces, and do not rescan. A changed visible set may
naturally request newly visible thumbnails through the existing priority/cache
path. Tests reject rescan/cache-clear/generation changes during Settings Apply.

## Verification

Focused tests use generated offscreen pixels, synthetic input and temporary
images/configs only. They cover independent x/y effects, text-region and painted
padding, all text modes, six densities, wrap boundaries, title/frame/rating hits,
keyboard navigation, English/Japanese Settings visibility/bindings, migration,
live Apply without thumbnail invalidation, selection and no-selection restore,
and visible-priority coverage.

- **65 passed per scale** at 100/125/150/200% in fresh processes: spacing and
  horizontal-geometry suites, including real Japanese/English Settings.
- **176 passed**: ConfigManager, Settings, UI language, Browser grid/window,
  Medium preset and thumbnail scheduler suites.
- **134 passed**: Browser navigation/history, async navigation, thumbnail
  resampling and wheel scrolling suites.
- Sprint 15 model/Browser tests passed within an **81-test** focused run;
  standalone Sprint 16: **17 passed**. Some groups overlap the DPI coverage.
- One combined 98-test run aborted in the Viewer fullscreen event filter
  during native Qt object lifecycle handling. This run is not counted as
  passing. The Browser group and standalone Sprint 16 passed separately;
  the broader process-lifetime issue is not diagnosed or fixed by this task.
- Syntax parsing: **11 passed**; `git diff --check` passed. The existing
  synthetic large-image navigation smoke completed with nine generated
  2400 × 3600 images, sequential/reverse/reversal/ping-pong/rapid navigation,
  and **0 terminal errors**. This is not a native performance claim.

## Files changed by this follow-up

- Production: `app/browser_grid_metrics.py`, `app/browser_item_delegate.py`,
  `app/browser_window.py`, `app/config_manager.py`, `app/settings_dialog.py`,
  `app/translations_en.py`.
- Tests: new `tests/test_browser_spacing.py`; extended
  `tests/test_browser_horizontal_geometry.py`; updated
  `tests/test_sprint15_browser_integration.py`, `tests/test_sprint15_models.py`,
  `tests/test_sprint16.py`.
- Documentation: this file, `docs/ARCHITECTURE.md`, and a follow-up pointer in
  `docs/BROWSER_HORIZONTAL_SPACING.md`.

Pre-existing changes in `tests/test_browser_grid.py` and
`tests/test_browser_window.py` are retained unchanged. The previously accepted
profile margin reduction and its geometry/documentation work remain intact.

No real app, native input, external GUI, private images, dependency change,
portable rebuild, commit or push. No ZipPlaFork code is ported in this follow-up.
