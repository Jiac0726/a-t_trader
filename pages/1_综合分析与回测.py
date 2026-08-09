from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)
os.chdir(ROOT)

runpy.run_path(str(ROOT / "app" / "dashboard.py"), run_name="__main__")
