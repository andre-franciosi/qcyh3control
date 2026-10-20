#!/usr/bin/env python3
"""Build the .zip bundle required to publish the extension on extensions.gnome.org.

The GNOME Extensions store expects a zip whose root contains metadata.json,
extension.js and prefs.js (no extra top-level folder). This script produces
that archive in dist/.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_SOURCE = ROOT / "gnome-extension"
DIST = ROOT / "dist"
FILES = ("metadata.json", "extension.js", "prefs.js", "bluez.js", "rcsp.js")


def main() -> int:
    metadata = json.loads((EXTENSION_SOURCE / "metadata.json").read_text(encoding="utf-8"))
    uuid = metadata["uuid"]
    version = metadata.get("version", 1)

    DIST.mkdir(exist_ok=True)
    archive = DIST / f"{uuid}.v{version}.shell-extension.zip"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in FILES:
            source = EXTENSION_SOURCE / name
            if not source.is_file():
                print(f"Missing required file: {source}")
                return 1
            bundle.write(source, arcname=name)

    print(f"Built {archive}")
    print("Upload this file at https://extensions.gnome.org/upload/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
