<div align="center">
  <img src="docs/hero.jpg" width="780" alt="ChatGPT's voice input, as a standalone app: vox">
  <p><b>Hold a key, speak, and the text appears in any app.</b><br>
  Dictation for macOS and Windows on your ChatGPT account. No API key.</p>
  <a href="https://github.com/ziyangliu-666/vox/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/ziyangliu-666/vox?style=flat-square&label=release&color=111111"></a>
  <a href="https://github.com/ziyangliu-666/vox/releases"><img alt="Downloads" src="https://img.shields.io/github/downloads/ziyangliu-666/vox/total?style=flat-square&color=111111"></a>
  <a href="https://github.com/ziyangliu-666/vox/actions/workflows/build.yml"><img alt="Build" src="https://img.shields.io/github/actions/workflow/status/ziyangliu-666/vox/build.yml?branch=main&style=flat-square&label=build"></a>
  <img alt="macOS 12+" src="https://img.shields.io/badge/macOS-12%2B-111111?style=flat-square&logo=apple&logoColor=white">
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-111111?style=flat-square&logo=windows&logoColor=white">
  <p><a href="https://github.com/ziyangliu-666/vox/releases/latest"><b>Download for macOS · Windows</b></a></p>
</div>

## Features

**Hold to talk, double-tap for hands-free.** Hold the hotkey while you speak. Double-tap it to keep listening; press once more to stop.

<img src="docs/dictate.webp" width="420" alt="The flow bar: listening, then the pasted text">

**A flow bar that stays out of the way.** Drag it to any screen edge. Guides show where it lands; it snaps to the middle or the thirds.

<img src="docs/drag.webp" width="560" alt="Dragging the flow bar to the left edge and back">

**Every transcript, searchable.**

<img src="docs/history.png" width="560" alt="History view in light and dark mode">

**Any key you like.** Pick a single key or a combination. Light and dark mode. Chinese, English, and mixed speech.

<img src="docs/window.png" width="560" alt="Main window in light and dark mode">

## Install

- **macOS 12+, Apple silicon:** open `vox-<version>.dmg` and drag vox to Applications. On first launch, allow it in System Settings › Privacy & Security › **Open Anyway**.
- **Windows 10/11:** run `vox-<version>.exe`. If SmartScreen warns, click **More info › Run anyway**.

Then click **Log in with ChatGPT**, allow the microphone, and hold **Right ⌥** (Windows: **Right Ctrl**).

vox updates itself: when a new release is out, the window footer shows **Update to x.y.z**. On macOS, permissions carry over between versions.

## FAQ

**Is this an official ChatGPT app?** No. vox uses the private endpoint behind the dictation button on chatgpt.com. It can change without notice, and automated use breaks the ChatGPT terms of use. Use it at your own risk.

**Where does my audio go?** To ChatGPT only, from a chatgpt.com page that vox keeps open out of sight. vox never reads your login. Transcripts stay on your computer.

**Why Accessibility and Input Monitoring?** To see the hotkey while you work in another app, and to paste into it.

## Build

```sh
uv run vox                  # run from source
packaging/build_mac.sh      # dist/vox.app, dist/vox.dmg
packaging/build_win.ps1     # dist\vox.exe
```

Pushing a `v*` tag builds both platforms and publishes a release.
