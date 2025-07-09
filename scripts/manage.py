"""Manage only TraceLab's stack and its fixed, bounded local host bridge."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "data/bridge"
CONTROL = ROOT / "data/bridge-control"


def live_pid():
    path = CONTROL / "pid.json"
    if not path.exists() or path.is_symlink():
        return None
    record = json.loads(path.read_text())
    pid = record.get("pid")
    if type(pid) is not int or pid < 2 or record.get("script") != str(ROOT / "bridge.py"):
        return None
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True
    )
    # Refuse a stale pid reused by an unrelated process.
    return pid if result.returncode == 0 and str(ROOT / "bridge.py") in result.stdout else None


def compose(*args):
    environment = os.environ.copy()
    environment["TRACELAB_UID"] = str(os.getuid())
    command = ["docker-compose"] if shutil.which("docker-compose") else ["docker", "compose"]
    subprocess.run([*command, *args], cwd=ROOT, env=environment, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["up", "stop", "status", "logs", "bridge"])
    action = parser.parse_args().action
    if action in {"up", "bridge"}:
        if not (ROOT / "data/prepared/manifest.json").is_file():
            raise SystemExit("Run uv run python scripts/acquire.py before starting")
        if action == "up":
            compose("up", "-d", "--build")
            print("Local application started. Model bridge is optional: sh stack.sh bridge")
            return
        BRIDGE.mkdir(parents=True, exist_ok=True, mode=0o700)
        CONTROL.mkdir(parents=True, exist_ok=True, mode=0o700)
        if live_pid() is None:
            with (CONTROL / "service.log").open("ab") as log:
                subprocess.Popen(
                    [sys.executable, str(ROOT / "bridge.py")],
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and live_pid() is None:
                time.sleep(0.1)
            if live_pid() is None:
                raise SystemExit(
                    "TraceLab app started but host bridge failed; "
                    "inspect data/bridge-control/service.log"
                )
        print("TraceLab: http://127.0.0.1:8285 ; bounded public-only cookllm bridge is running")
    elif action == "stop":
        pid = live_pid()
        if pid:
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline and live_pid() is not None:
                time.sleep(0.1)
            if live_pid() is not None:
                raise SystemExit("Bridge still stopping; no unrelated process was signalled")
        compose("stop")
        print("TraceLab stack/bridge stopped; local data and named volume retained")
    elif action == "status":
        compose("ps")
        print(json.dumps({"bridge_pid": live_pid()}))
    else:
        compose("logs", "--tail=100", "app")


if __name__ == "__main__":
    main()
