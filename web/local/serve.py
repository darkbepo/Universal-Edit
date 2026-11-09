#!/usr/bin/env python3
"""Serve the Media Converter web app on this computer only (macOS / Linux / any Python 3).

Usage: python3 serve.py      then open the printed address. Press Ctrl+C to stop.
"""

import http.server
import mimetypes
import os
import sys
import webbrowser
from functools import partial

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.path.endswith((".html", "/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


def main() -> int:
    if not os.path.isfile(os.path.join(ROOT, "index.html")):
        print("Can't find the 'app' folder next to this script.")
        return 1
    for port in range(8790, 8800):
        try:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=ROOT))
            break
        except OSError:
            continue
    else:
        print("No free port between 8790 and 8799.")
        return 1
    url = f"http://localhost:{port}/"
    print(f"\n  Media Converter is running at {url}\n  Your files stay on this computer. Press Ctrl+C to stop.\n")
    if not os.environ.get("UMC_NO_BROWSER"):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
