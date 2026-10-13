"""vox: hold a key, speak, and the text appears in the focused app.

With no arguments, start the desktop app. The flags are for development.
"""

import argparse
import sys
from pathlib import Path


def transcribe_file(win, path: str) -> None:
    try:
        # Allow time for a first login in the window.
        win.wait_until_ready(timeout_s=600)
        print(win.transcribe(Path(path).read_bytes()))
    except Exception as exc:
        print(f"vox: transcription failed: {exc}", file=sys.stderr)
    finally:
        win.window.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(prog="vox", description=__doc__.splitlines()[0])
    parser.add_argument("--file", metavar="WAV",
                        help="transcribe WAV through ChatGPT, print the text, and exit")
    parser.add_argument("--demo", metavar="WAV",
                        help="start the app; after login, play WAV through the flow bar once")
    args = parser.parse_args()

    if args.file:
        from vox import chatgpt

        chatgpt.run(lambda win: transcribe_file(win, args.file))
    else:
        from vox import app

        app.main(args.demo)


if __name__ == "__main__":
    main()
