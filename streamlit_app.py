from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


# Streamlit Community Cloud executes the configured entry script directly.
# Keep the repository root on sys.path so the project's top-level packages
# (data, providers, scoring, backtest, storage, ...) resolve exactly as they do
# when the app is started from the repository root locally.
ROOT = Path(__file__).resolve().parent
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

# The app also uses a few project-relative paths such as config/watchlist.txt.
os.chdir(ROOT)

runpy.run_path(str(ROOT / "app" / "dashboard.py"), run_name="__main__")
