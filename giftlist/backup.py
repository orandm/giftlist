"""Nightly backup: python -m giftlist.backup  (keeps the newest 14 copies)."""

from __future__ import annotations

import glob
import os
import sqlite3
import sys
from datetime import datetime

KEEP = 14


def backup(db_path: str, out_dir: str, keep: int = KEEP) -> str:
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, f"giftlist-{datetime.now():%Y%m%d-%H%M%S}.db")
    src, dst = sqlite3.connect(db_path), sqlite3.connect(target)
    with dst:
        src.backup(dst)  # consistent even while the app is writing
    src.close()
    dst.close()
    for old in sorted(glob.glob(os.path.join(out_dir, "giftlist-*.db")))[:-keep]:
        os.remove(old)
    return target


if __name__ == "__main__":
    data = os.environ.get("DATA_DIR", "./data")
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(data, "backups")
    print(backup(os.path.join(data, "giftlist.db"), out))
