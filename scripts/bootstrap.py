"""Prepare this checkout's own local data; never execute source-provided code."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    manifest = json.loads((ROOT / "bootstrap-manifest.json").read_text())

    def local_path(value):
        path = ROOT / value
        if not isinstance(value, str) or not path.resolve().is_relative_to(ROOT):
            raise ValueError("Path outside this checkout")
        return path

    for item in manifest["files"]:
        source, target = local_path(item["source"]), local_path(item["target"])
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("Bundled example fingerprint changed: " + item["source"])
        if target.is_symlink():
            raise ValueError("Local data symlink refused")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError(
                    "Preserve changed local data; use a separate checkout: " + item["target"]
                )
        else:
            shutil.copyfile(source, target)
    for step in manifest["commands"]:
        if step["argv"] != ["scripts/acquire.py"]:
            raise ValueError("Only the fixed acquisition script is allowed")
        if not local_path(step["ready_file"]).is_file():
            subprocess.run([sys.executable, *step["argv"]], cwd=ROOT, check=True)
    (ROOT / "data/verification").mkdir(parents=True, exist_ok=True)
    print("Local bootstrap complete. Data remains excluded from Git; no model calls were made.")


if __name__ == "__main__":
    main()
