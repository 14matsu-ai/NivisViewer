# TODO21 — shared shortcut help audit

2026-09-19. Scope: every function/chord in `app/shortcuts_help.py`, plus the
separately authorized Browser Delete menu placement. TODO19 remains awaiting
user-machine feedback; no further startup optimization was attempted.

## Findings and fixes

Two actual mismatches were reproduced by sending Qt events to a focused Viewer:

1. `-` from a fitted image could enlarge it: `zoom_out` multiplied the retained
   manual zoom (initially 1.0), rather than the displayed fit scale. For the
   synthetic book fitted at about 0.452, it jumped to 0.870 instead of 0.393.
   Both `zoom_in` and `zoom_out` now start at the current displayed scale,
   consistent with Ctrl+wheel. Repeated manual zoom remains multiplicative.
2. The `+` QShortcut accepted unmodified Plus and keypad Plus but not the
   Shift-modified Plus event required by ordinary keyboard layouts. Added the
   `Shift++` spelling for the SAME zoom-in command. Existing `+` and `=` alias
   remain. Event tests verify all three modifier variants/alias and detect
   duplicate QAction/QShortcut registrations.

Intentional priority rules were retained and clarified in Japanese/English help:
overflowing manual/actual-size images pan before arrow-key page navigation;
Shift uses a larger pan step. Esc cancels magnifier/gesture before leaving
fullscreen. No unrelated shortcut or valid interaction was redesigned.

## Displayed chord → route → precondition → observed result

All rows refer to Viewer/canvas focus unless noted. Route names are methods in
`app/viewer_window.py`, `app/viewer_widget.py`, or `app/slideshow_keys.py`.
Actions also retain their usual Qt WindowShortcut scope.

| Help entry | Binding / event path | Required state | Observed result |
| --- | --- | --- | --- |
| Right | canvas filter → `_handle_navigation_key_event` → `next_page`; QAction fallback | Book open; no higher-priority pan | Next display unit, passed |
| Left | same filter → `previous_page`; QAction fallback | Not first unit; no pan | Previous unit, passed |
| Alt+Left / Alt+Right | history QActions → `go_back_in_page_history` / `go_forward_in_page_history` | Committed back/forward history and post-paint action state | Back/forward through painted pages, passed |
| Space / PageDown | filter/QShortcut → `next_page_or_scroll` | Book open | Vertical scroll first; next page when no scroll is possible, passed |
| Backspace / PageUp | filter/QShortcut → `previous_page_or_scroll` | Book open | Upward scroll first; previous page otherwise, passed |
| Shift+Right | filter/QAction → `next_one_page` | No higher-priority pan | Advances one page even in spread mode, passed |
| Shift+Left | filter/QAction → `previous_one_page` | No higher-priority pan | Moves one page back in spread mode, passed |
| Arrow pan explanation | `ViewerWidget.can_pan_with_key` / `pan_with_key` before page action | Manual/actual-size content overflows; movement possible | Image movement wins; Shift step is larger, passed. At a bound, page action can proceed |
| Home / End | canvas filter/QActions → first/last page | Book open | First/last page, passed; also tested with slider/page-list focus |
| G | QAction → `go_to_page_dialog` | Book open | Real QInputDialog opens; Enter commits selected page, passed |
| D | QShortcut → `dispatch_command(TOGGLE_SPREAD)` | Viewer window active | Single/spread toggles both ways, passed |
| Shift+R | QShortcut → `TOGGLE_READING_DIRECTION` | Viewer window active | Binding direction, config and checked actions update; bare R inert, passed (TODO20 retained) |
| F | QAction → `TOGGLE_FULLSCREEN` | Viewer window active | Enters/leaves fullscreen, passed |
| Esc | QShortcut → `_handle_escape` | Magnifier, gesture or fullscreen active | Magnifier/gesture cancelled first; next Esc leaves fullscreen, passed |
| Z | QAction → `toggle_magnifier` | Displayed image; usable canvas position | Magnifier enabled/disabled, passed |
| + / - / Ctrl+Wheel | QShortcuts → zoom methods; `ViewerWidget.wheelEvent` for wheel | Image displayed | Correct relative zoom after fix; Ctrl+wheel does not turn page. Shift-Plus/keypad Plus and existing equals alias passed |
| 0 | QShortcut → `FIT_WINDOW` → `set_fit_mode` | Manual zoom or other fit mode | Returns to fit-window, passed |
| S | `SlideshowKeys` application filter, limited to eligible Viewer descendants | Book open; non-editor focus; no modal/popup | Toggles on release, passed |
| Held 1–9 + S, either order | `SlideshowKeys` held-key state | Simultaneously held keys | All nine digits in both orders start corresponding seconds; repeat/release rules passed |
| Shift+S, Enter | `SlideshowKeys` → interval chooser | Eligible Viewer focus | Real interval dialog; Enter starts, cancel preserves interval, passed |
| B / Ctrl+B | QShortcuts → `toggle_current_bookmark` | Current book/page | Adds/removes current page bookmark, passed |
| Ctrl+PageDown / Ctrl+PageUp | QActions → next/previous book → controller adjacent-book service | Adjacent book available | Real synthetic sibling ZIP open in both directions, passed |
| Ctrl+C | Copy QAction → `copy_current_image` | Committed image exists | Offscreen clipboard contains current image, passed |
| Ctrl+Shift+C | QAction → `copy_current_image_path` | Book/page available | Clipboard contains the current display path, passed |
| Ctrl+Alt+C | QAction → `copy_current_view` | Book/page available | Clipboard contains viewport-sized pixmap, passed |
| Ctrl+I | QAction → `show_page_info` | Book/page available | Correct page-info content including source dimensions, passed; message presentation captured without blocking |
| Double Click | canvas double-click event → pointer confirmation → fullscreen command | Eligible canvas point; no active blocking interaction | Fullscreen toggle, passed |

`_navigation_key_targets` intentionally includes canvas/central area, not editors
or the virtual page list. Slider/list native Home/End navigation and window-wide
Shift+R were exercised with actual child-control focus. Editable controls kept
letters and Ctrl+C; modal non-editor focus blocked the parent shortcut. Slideshow
eligibility separately excludes editors, modal dialogs and popups. Exact modified
R chords and the three copy chords remain distinct. No duplicate active shortcut
strings were found. Auto-repeat is not globally disabled: existing QShortcut
behavior is retained; slideshow's held-key filter suppresses repeat by design.

An initial history test sent the next chord before queued post-paint action
enablement was settled. The test now waits for the committed frame AND action
states to match committed history. No history production code was changed.

## Verification

Synthetic six-page 800×1200 PNG-in-ZIP books and sibling ZIPs, isolated profile,
Qt offscreen. QTest key/click/drag delivery and QApplication.sendEvent wheel/key
delivery go through the actual widgets and Qt shortcut routing. Direct calls
are used only to establish special states (such as overflowing zoom), not as
substitutes for the audited chord. This does not certify every native Windows
keyboard layout or native fullscreen appearance.

Fresh-process audit group: 74 passed (8.15 s):

* `tests/test_viewer_shortcut_audit.py`: 22 cases.
* `tests/test_viewer_binding_shortcut.py`: 10 cases.
* `tests/test_viewer_slideshow.py`: 38 cases, extended from sampled digits to 1–9.
* `tests/test_settings_help.py`: 4 cases; JA/EN Settings and Viewer help match,
  now 27 lines including the added priority explanation.

Syntax and scoped whitespace checks passed. Required large-image offscreen
forward/reverse/direction-reversal/ping-pong/rapid-input evaluation is recorded
in `out/todo21-shortcuts-navigation.json`.
It completed with terminal errors 0: forward 1.079 ms, reverse 1.058,
direction reversal 1.515, ping-pong median 1.052, rapid final 1.762.
After strengthening the editor Plus/Minus and fullscreen round-trip assertions,
the corresponding two 3-case subsets were rerun and passed in fresh processes.

## Separate user request: Delete menu placement

Browser item context menu now ends:

`Rating / Tags → separator → Delete → separator → Properties`.

Only creation order/separators changed. The existing recycle callback,
`selection_count > 0 and not busy` enablement and selection handling remain.
ZIP stays isolated above Cut. The exact-layout test also includes the already
implemented Tags item that its old expectation omitted.

Layout/name-copy tests passed together (2). Four existing recycle tests passed
in separate fresh processes: confirmation/selection refresh, single folder,
mixed selection, and default-Yes acceptance. An initial combined
`-k 'context_menu or recycle'` run stopped making progress after one passing test
and was interrupted; it is NOT reported as a passing suite. The first stalled
recycle test and the other selected recycle tests passed independently. No
speculative fix for that combined-run behavior was included.

No native GUI/input, private media, new dependencies, build, commit or push.
