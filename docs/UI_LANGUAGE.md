# Japanese / English UI language and Settings gear

## User contract

Open **設定 → 一般 → 表示言語 / Language** (English:
**Settings → General → 表示言語 / Language**). The language caption is always
bilingual, independent of the current UI language.
The only choices are `日本語` and `English`. Japanese remains the default;
missing, unsupported, and malformed saved values fall back to Japanese.

The existing `ConfigManager` persists `ui_language` as `ja` or `en`. Apply and
OK save through the existing Settings path; Cancel discards unapplied edits
and does not undo an earlier Apply. A note beside the control explains that
the change takes effect after restarting NivisViewer. Saving the setting does
not partially switch already-open windows or asynchronous messages.

English covers app-owned Browser/Viewer menus, late-created context menus,
Settings labels/options/help, dialogs, status strings, and error messages.
Filenames, paths, user bookmark names, search queries/MRU, history values,
internal IDs, and file-operation clipboard payloads are not translated.
Generated file/folder names also retain their existing naming behavior.
Operating-system dialogs, Shell content, and external backend error details
remain controlled by their original provider. This does not promise that
every Windows-owned surface follows the application language.

## Translation authority

`app/i18n.py` installs one application-owned `QTranslator` at controller
startup, immediately after loading configuration and before constructing
windows. Explicit `tr()` calls use the `NivisViewer` Qt translation context.
Japanese source strings are paired with English in `app/translations_en.py`.
No runtime widget-tree replacement, translated-text action dispatch, second
configuration store, or dependency was added.

Format dynamic values after translation:

```python
tr("{count} 個の項目", count=count)
```

Use a named argument for each value and preserve its formatting specification
in the catalog. User text containing braces is an argument, never a format
template. Existing shared option catalogs are translated when consumed by UI,
not at module import; their persisted IDs and ordering are unchanged.
Use `disambiguation` when one Japanese source has different meanings, such as
navigation `移動` (Go) versus file-operation `移動` (Move), or the resampling
headings `縮小` / `拡大` (Downscaling / Upscaling).

Unknown catalog entries return Qt's not-found value (`None`), preserving source
fallback and standard Qt button captions. Returning an empty translation would
erase those captions. `initialize_ui_language()` cannot replace an already
initialized process language when settings are applied or another controller
is constructed. The explicit install helper also supports isolated tests.

English captions were measured in the existing 620 × 680 logical-pixel Settings
window. A few long captions were shortened; English forms allow long rows to
wrap. Japanese layout and fonts are unchanged. The General tab is appended to
the existing tab order rather than moving or duplicating earlier settings.

## Gear icon

`app/menu_icons.py` draws a small vector gear using the active palette, with
disabled and highlighted colors and device-pixel-ratio-aware pixmaps. It uses
no emoji, font glyph, downloaded asset, or additional dependency. Browser's
existing Settings action and Viewer's existing Settings menu retain their text,
identity, and trigger behavior; no toolbar or extra button was introduced.

Qt's ordinary menu-bar drawing may choose the icon instead of its caption.
A scoped `QProxyStyle` paints both the gear and text. Its icon box is
`round(0.8 * fontMetrics.height())` logical pixels (minimum 1), with a gap of
`max(2, round(0.2 * fontMetrics.height()))`; the reserved slot is box plus gap.
The gear's vector ink occupies approximately 5/6 of the box, or 2/3 of the
font line height. This replaces the original fixed 16-pixel box / 20-pixel
slot. The box is vertically centered with at most half a logical pixel of
integer placement rounding, and vector painting uses its geometric center.
Browser retains its previous compact 4-by-6-pixel item padding.
The fullscreen menu clone uses the same style and existing shared actions.
The proxy owns its own base style, not the application's style object.

## Changed files

New implementation and tests:

- `app/i18n.py`, `app/translations_en.py`, `app/menu_icons.py`
- `tests/test_ui_language.py`, this document

Startup, config, and primary UI integration:

- `main.py`, `app/application_controller.py`, `app/config_manager.py`
- `app/settings_dialog.py`, `app/browser_window.py`, `app/viewer_window.py`
- `app/fullscreen_chrome.py`

Explicit presentation/error translation calls (existing authorities retained):

- `app/archive_backend.py`, `app/book_session.py`, `app/bookmark_model.py`
- `app/browser_location_bar.py`, `app/browser_model.py`, `app/browser_rating_filter_widget.py`
- `app/browser_scanner.py`, `app/chunked_file_copier.py`, `app/diagnostics_dialog.py`
- `app/fallback_background_editor.py`, `app/ffmpeg_thumbnail_backend.py`
- `app/file_conflict_dialog.py`, `app/file_operation_artifact.py`, `app/file_operation_panel.py`
- `app/file_operation_plan.py`, `app/file_operation_queue.py`, `app/file_operation_service.py`
- `app/file_properties_dialog.py`, `app/folder_bookmark_model.py`, `app/history_model.py`
- `app/image_source.py`, `app/logging_setup.py`, `app/metadata_store.py`, `app/page_model.py`
- `app/pdf_backend.py`, `app/pdf_image_source.py`, `app/rating_rename_service.py`
- `app/seven_zip_locator.py`, `app/sidebar_layout.py`, `app/single_instance.py`
- `app/startup_restore.py`, `app/system_file_opener.py`, `app/text_preview_provider.py`
- `app/thumbnail_disk_cache.py`, `app/thumbnail_provider.py`, `app/viewer_presentation_state.py`
- `app/viewer_widget.py`, `app/windows_file_registration.py`, `app/windows_filename.py`
- `app/windows_recycle_bin.py`, `app/winrar_locator.py`, `app/zip_raster_book_runtime.py`

No ZipPlaFork implementation was newly ported for this localization task;
existing ZipPlaFork notices and comparison provenance remain intact.

## Offscreen verification (2026-09-10)

Python 3.11.9, PySide6 / Qt 6.11.2, Windows, `QT_QPA_PLATFORM=offscreen`.

- 222 passed: localization, configuration, Settings, filename context menu,
  Browser file operations/window/chrome/page count, file-operation service,
  large-progress projection, Windows filename rules.
- 137 passed: Viewer window/fullscreen, ZIP raster runtime/integration, and
  navigation follow-ups.
- 142 passed: application controller, book session, archive/image sources,
  file-operation worker/close/fallback/Viewer integration, registration, and
  single-instance boundaries.
- Two additional bilingual late-dialog/progress cases passed after those runs:
  **503 distinct cases total** across the focused groups.
- Final localization suite: **23 passed at each of 100/125/150/200% DPI**, in
  fresh processes. Tests hold the same 620 × 680 logical Settings window at
  every scale; otherwise the offscreen plugin's tiny physical screen shrinks
  the dialog, conflating DPI scaling with an unusually small display.
- Tests cover Apply/OK/Cancel/restart behavior, invalid-value fallback, late
  menus, real offscreen menu event-loop copy/search actions, Japanese user data,
  plain-text clipboard, option IDs, dynamic errors/status/progress, caption
  completeness/format contracts, all Settings tabs, and light/dark gear paint.
- Syntax parsing passed for 126 Python files; `git diff --check` passed.

The existing navigation benchmark used nine generated 2400 × 3600 high-detail
JPEG pages in a temporary ZIP, a 1280 × 800 viewport, and a 256 MiB cache. It
completed forward, reverse, immediate reversal, ping-pong, and rapid-final
navigation with zero terminal errors. Sample request-to-offscreen-paint times:
forward 1.051 ms, reverse 0.992 ms, reversal 1.313 ms, ping-pong median 0.959 ms,
rapid-final 1.630 ms; cold immediate target 63.422 ms. These are one synthetic
regression run, not a native compositor measurement or performance claim.

No real application launch, native input, external GUI, private-image inspection,
portable rebuild, commit, push, or destructive Git command was performed.
Native Windows theme rendering, dialogs, and external clipboard interoperability
remain unverified by these offscreen tests.

### Small visual follow-up

Only `app/menu_icons.py`, `app/settings_dialog.py`, `tests/test_ui_language.py`,
and this document changed for the subsequent smaller-gear / bilingual-caption
request. No language choices, persistence, restart behavior, font, or unrelated
menu geometry changed. The shared menu style applies the sizing rule to Browser,
Viewer, and the fullscreen menu clone.

With the offscreen fixture's 12-logical-pixel menu font height, the box is now
10 × 10 rather than 16 × 16, and the slot is 12 rather than 20 logical pixels.
The approximately 13.3-pixel old vector extent becomes approximately 8.3 pixels
before antialiasing. This is a fixture measurement, not a native Windows font
measurement; real menu fonts supply their own metrics.

Focused checks: **30 passed at each 100/125/150/200% DPI** in fresh offscreen
processes. They compare new versus old raster alpha bounds, verify centering,
light/dark contrast, compact/noncompact menu text and width, unchanged plain
menu sizing and Browser chrome height, the exact bilingual caption, and existing
language Apply/OK/Cancel/restart behavior. Syntax and diff checks passed.
