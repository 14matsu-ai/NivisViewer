# Browser tag filtering and cancel-key audit

2026-09-21. Existing uncommitted shortcut/settings work is preserved.

## Findings

- `_sync_browser_filter_controls` calls the quick-tag geometry/reflow path
  after filtering. `_reflow` used to hide every quick button and show it again,
  even when the visible set did not change. Hiding the focused button transfers
  keyboard focus. An added test now observes actual Hide events and requires
  zero Hide events during a tag toggle/Esc clear.
- The overflow menu used to clear and recreate its actions during the selected
  action's callback. The menu now retains existing actions, updates their state,
  and adds/removes/reorders only when the hidden registry entries change.
- QToolButton InstantPopup runs a nested menu loop. Offscreen tests reproduced
  `focusWidget() == None` after that loop, including for the grouped menu.
  Earlier fixes ran inside the action callback or menu hide signal, before the
  complete button/menu event returned. Their queued/50 ms retries could either
  finish prematurely or steal focus from a subsequent user action.
- Browser's application event filter used `isAncestorOf` for its cancel scope.
  This includes parented nonmodal QDialogs. Scope now requires the receiver's
  actual top-level `window()` to be the Browser. Modal/popup checks also guard
  the cancel handler itself.

## Implementation

- Preserve visible quick buttons through reflow. When an actual width change
  hides a focused quick button, move focus synchronously to visible overflow.
- Use `TagFilterMenuButton` for overflow and the grouped tag button. Its mouse
  and keyboard event handlers let Qt finish the InstantPopup loop first, then
  restore focus only if it is absent or still on that button/its own menu.
  An unrelated active window, another focused widget, or an open modal/popup
  prevents restoration.
- Remove the Browser tag-focus generations, pending state, 0/50 ms timers,
  recursive retries, and menu-aboutToHide focus callbacks. The activated signal
  again carries the original mouse-button value, not a QWidget in that slot.
- Restore TagFilterDialog focus synchronously after `exec()` returns; no queued
  focus callback remains to override the user's next action.
- Preserve configured cancel keys, ON/OFF filter-clear behavior, editing
  protection, and interaction -> filters -> clipboard cancellation priority.

## Baseline and limits

Read-only comparison with tag `1.0.7` (`f399db8`) shows that its Browser
eventFilter restricted filter Esc to list/viewport/rating. Its direct quick
buttons already had StrongFocus; the grouped tag button had no explicit focus
policy. The old hide/show reflow was already present. These facts explain the
fragile dependence on the surviving focus, but do not establish a complete
native Windows reproduction of the user's historical 1.0.7 behavior.

## Validation

Fresh-process offscreen run with existing `.venv311/Scripts/python.exe`:

```
pytest -q tests/test_browser_escape_filters.py tests/test_browser_tag_clicks.py tests/test_browser_tags.py tests/test_shortcut_settings_regressions.py tests/test_settings_help.py
119 passed
```

Coverage includes immediate direct-click Esc without a corrective setFocus,
empty filtered results, remapped T/Ctrl+K, grouped mouse/Space popup activation,
overflow selection and immediate focus transfers to list/tree/search, rapid
successive tags, other windows/modal editors, parented nonmodal dialogs, dialog
Enter/Apply/Esc and post-dialog focus retention, and stable button/action
identity. Python 3.11 was accessible with approved execution outside the shell
sandbox; the earlier claim that the runtime was absent was too broad.

`scripts/benchmark_zip_runtime_navigation.py` used 12 identical synthetic
3200x5000 JPEG pages in a temporary ZIP. All seven scenarios settled before
timeout, including cold forward, reversal, roundtrip, and rapid final input.
Report: `out/tag-escape-navigation-check.json` (local generated output).

This is offscreen evidence, not a claim that native Windows input was tested.
No build, commit, push, native input, or real application launch was performed.
