"""Double-click launcher: reuse server or start it, then open local controls."""

import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8876"


def ready():
    try:
        with urlopen(URL + "/api/local/status", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


if not ready():
    logs = ROOT / "runs/local"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "server.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "balatro_ai.local_ui"],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    for _ in range(100):
        if ready():
            break
        if process.poll() is not None:
            raise RuntimeError("Server failed; see runs/local/server.log")
        time.sleep(0.2)
if ready():
    webbrowser.open(URL)
else:
    raise RuntimeError("Server not ready; see runs/local/server.log")
