"""Double-click entry point: start the app on this laptop and open the browser.

Data lives in a ClinCurate folder in the user's home directory unless
CLINCURATE_HOME says otherwise. The server listens on 127.0.0.1 only, so no
other computer can reach it.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

PREFERRED_PORT = 8765


def data_dir() -> Path:
    return Path(os.environ.get("CLINCURATE_HOME") or Path.home() / "ClinCurate")


def free_port(preferred: int = PREFERRED_PORT) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def main() -> None:
    from werkzeug.serving import make_server

    from .app import create_app

    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    app = create_app(home / "clincurate.db")
    port = free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    url = f"http://127.0.0.1:{port}/"
    print(f"ClinCurate is running at {url}\nData folder: {home}\nClose with the Quit link in the app.", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
