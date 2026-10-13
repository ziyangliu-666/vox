# vox design spec

vox has two surfaces:

- **Main window** (`vox/ui/index.html`): set-up and settings. **400 x 520 CSS px**, fixed size, native title bar.
- **Bar** (`vox/ui/hud.html`): a capsule at the bottom centre of the screen. It stays on screen and shows what vox is doing.

Shared tokens live in `vox/ui/style.css`. No build step, no network, system fonts only.

## Principles

1. **Show, do not explain.** Use keycaps, status dots, checks and motion. Labels have 1 to 4 words. Help text is one short line at most, and it goes away when its step is done.
2. **One thing at a time.** Set-up shows one open step. The other steps are one line each.
3. **The bar is the feedback.** It is small at rest. It grows only while you talk, and it shrinks back when the job is done.
4. **State comes from Python.** The page is a pure render of the last state. Only short flashes are local: Copied, key confirm, step pop, "Waiting…" and an open history row.
5. **One accent.** Green means "on" or "done". The primary button uses ink (black in light mode, white in dark mode), the same colour as the bar.

## Main window

### Views

The state picks the view; the user never picks it.

| View | Condition |
|---|---|
| Set-up | account is not `ready`, or a macOS permission is `missing`, or history is empty |
| Home | all of the above are done |

Set-up shows "Set up vox" and a numbered list. The first step that is not done is open. Steps after the open step always show as pending (number, grey title), also when their own condition is true, so the list reads in order. A step that turns done pops its check once. A switch between views fades in (320 ms).

### Set-up steps

| # | Pending title | Open content | Done title |
|---|---|---|---|
| 1 | Log in | depends on `account` (see below) | Connected + "Log out" |
| 2 | Allow access (macOS only) | one row per permission; meta "N to go" | Access on |
| 3 | Try it | "Hold [key] and speak" ("Press" in toggle mode); the key is clickable | (view switches to Home) |

On Windows the permission step is not shown, so "Try it" is step 2.

| `account` | Step 1 content |
|---|---|
| `logged_out` | Primary button "Log in with ChatGPT" |
| `logging_in` | Spinner + "Finish logging in in the ChatGPT window" |
| `learning` | Spinner + "Getting ready…" + a bar that fills in about 5 s and waits at 92 % |
| `error` | Warning icon + `account_error` in red (fallback "Could not log in.") + "Try again" (calls `login()`) |

### Permission rows

Each row has a status dot, the name, a grey sub-label, and an action. The longer reason is in the row tooltip.

| Permission | Sub-label |
|---|---|
| Microphone | To hear you |
| Accessibility | To type for you |
| Input Monitoring | To see the hotkey |

| Row state | Action area |
|---|---|
| missing or unknown | "Open Settings" |
| after "Open Settings" | small spinner + "Waiting…", until that permission changes in the state (stops after 30 s) |
| granted | green check; the dot and the check pop when the value changes to granted |

Dots: granted = accent, missing = danger, unknown = warn. `unknown` does not block the step. When the last permission turns granted, the step stays open for 0.9 s so the user sees the check.

### Home

1. Account row: green check, "Connected", "Log out".
2. Hotkey row: "Hotkey", a pencil "Change" button, keycaps. The row has a fixed height of 68 px, so capture does not move it.
3. If `hotkey.warning` is not null: one amber line with a warning icon under the row.
4. Mode: segmented "Hold to talk" / "Press to toggle".
5. Output: segmented "Paste into app" / "Copy only".
6. "Recent": up to 5 rows, newest first. Each row has the time, one line of text with an ellipsis, and a copy button. Click a row to show the full text (selectable); click again to close it.
7. Footer: "vox 0.2.0" on the left, a "Sounds" switch on the right (calls `set_sounds(bool)`).

The tallest home view (warning line and 5 rows) is 504 px. The window is 520 px, so nothing scrolls. An open history row can make the page scroll; that is a user action, so it is acceptable.

### Hotkey states

| State | Look |
|---|---|
| Idle | Keycaps. "Right ⌥" draws as one key: symbol top-right, "right" bottom-left. "Ctrl + Space" draws as two keys and a "+". The keycap and "Change" both start capture. |
| Capturing | A dashed slot "Press a key" with a blinking caret and a breathing border. An "esc" key button replaces "Change". Esc or the button calls `cancel_capture()`. |
| Confirm | A new label arrives: the key presses down and springs back, and a green ring expands from the row (700 ms). Cancel shows no animation. |

### Copy feedback

Click copy: the page calls `copy(text)`, the row turns light green, and the icon changes to a check plus "Copied" for 1.4 s.

## Bar (HUD)

The default window is **300 x 72 CSS px**: transparent, frameless, always on top, never focused. The capsule sits 14 px above the bottom edge of the window, centred. The capsule can be as wide as the window minus 28 px. A wider window gives done text more room with no other change.

The capsule is dark in light and dark mode, with white content. Its width animates to fit the content.

| Phase | Size | Content |
|---|---|---|
| `hidden` | 46 x 10, opacity 0 | nothing (the user turned the bar off) |
| `idle` | 46 x 10, opacity 0.9 | nothing; a light edge keeps it visible on dark wallpapers |
| `listening` (hold) | ~100 x 32 | 12 white bars driven by `level(x)` |
| `listening` (toggle) | ~265 x 32 | 8 bars + "[key] to stop · [esc] to cancel"; the keys are small chips |
| `transcribing` | ~100 x 32 | the same bars at 80 % white, in a low wave that travels left to right |
| `done` | up to max | green check + text + grey tag "Pasted" or "Copied" (from `output`) |
| `cancelled` | 40 x 32 | grey ✕; Python sets `idle` after about 0.6 s |
| `error` | fits content | amber warning + `message`; it stays until the next hotkey press |

Done text is 13 px and keeps its **end** visible ("…the last words"), so the user can check how a long or Chinese sentence finished. Error text uses a normal tail ellipsis. "Network error · Retry in menu" fits on one line at 300 px.

Level meter:

- Centre bars are tallest.
- At silence each bar is 4 px and breathes by up to 1.6 px, so the bar never looks frozen.
- Attack is fast (0.55 per frame) and release is slow (0.18 per frame). Speech onsets show at once and gaps fade.
- Maximum bar height is about 20 px.

## Colour tokens

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #ffffff | #1e1e21 | window |
| `--surface` | #f6f6f7 | #26262a | open step, hotkey row, row hover |
| `--surface-2` | #ececee | #303035 | segmented track, switch off |
| `--line` | #e4e4e7 | #36363c | dividers, key outline |
| `--text` | #18181b | #ededf0 | main text |
| `--text-2` | #5f6168 | #a6a8b0 | secondary text, copy icon |
| `--text-3` | #9a9ca3 | #8a8c94 | time, sub-labels, footer |
| `--ink` | #1b1b1f | #f2f2f4 | primary button, open step number |
| `--accent` | #12a150 | #34d17a | checks, granted, Copied, switch on |
| `--danger` | #d0362c | #ff6b60 | missing, login error |
| `--warn` | #c27a00 | #f2ac3c | unknown permission, hotkey warning |
| `--thumb` | #ffffff | #55555c + 1 px inset rgba(255,255,255,0.06) | segmented thumb |

Bar (both schemes): capsule rgba(22,22,25,0.94), idle rgba(28,28,32,0.72) with a 1 px rgba(255,255,255,0.38) edge, content #ffffff, hint 66 % white, tag 45 % white, chips 14 % white, check #34d17a, warning #ffb547, cancel ✕ 50 % white.

## Type

Font stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, "PingFang SC", "Microsoft YaHei UI", sans-serif`. The CJK faces come after the UI faces, so Latin text keeps the system look.

| Token | Size | Use |
|---|---|---|
| `--fs-xs` | 11 px | footer, step numbers, keycap side word, bar chips and tag |
| `--fs-sm` | 12 px | segmented labels, meta, sub-labels, bar hint |
| `--fs-md` | 13 px | body (base), bar done and error text |
| `--fs-lg` | 15 px | set-up title, keycap text, "Try it" line |

Weights: 400 body, 500 labels, 600 buttons and titles. Times use tabular numbers.

## Spacing and shape

- Grid: 4 px (`--s1` 4 to `--s6` 24). Window padding: 20 px.
- Heights: button 34, quiet button 26, segmented 32, step head 40, hotkey row 68, history row 36, keycap 40, switch 26 x 16.
- Radius: 6 (small buttons), 9 (buttons, segmented, rows), 12 (step, hotkey row), 999 (bar).
- History dividers are 1 px lines inset 4 px from each side, so they stay straight next to the row radius.

## Motion

| Token | Value | Use |
|---|---|---|
| `--t-fast` | 120 ms | colour, press, Copied text |
| `--t-base` | 180 ms | segmented thumb, switch, step reveal |
| `--t-slow` | 320 ms | view fade, step pop, check pop, row flash |
| bar morph | 150 ms `cubic-bezier(0.3,0.8,0.2,1)` | width, height, opacity of the capsule |
| `--ease-out` | `cubic-bezier(0.2,0.8,0.2,1)` | entrances |
| `--ease` | `cubic-bezier(0.4,0,0.2,1)` | state changes |

Bar enter and exit changes take 150 ms or less. With `prefers-reduced-motion`, animations and transitions drop to 1 ms. The level bars still move, because they are feedback.

## Mock mode

A page uses its built-in mock when no `window.pywebview` exists after 300 ms, or when the URL has `?mock`. The hash selects the state.

- `index.html`: `#first_run`, `#logged_out`, `#logging_in`, `#learning`, `#ready`, `#ready_full` (5 rows), `#ready_empty`, `#capturing`, `#permissions`, `#error`, `#warning`, `#win`. Login runs logging_in, learning (5 s), then ready. Capture takes a real key press. Open Settings grants after 1.5 s.
- `hud.html`: `#idle`, `#listening`, `#listening_silent`, `#listening_toggle`, `#transcribing`, `#done`, `#done_copy`, `#done_long_zh`, `#cancelled`, `#error`, `#error_retry`, `#hidden`. Each starts from idle, so the expand shows. Only mock mode paints a desktop backdrop.
