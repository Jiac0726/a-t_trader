from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

# Compatibility entrypoint for deployments that still point at app/dashboard.py.
# Always hand off to the current full-market Streamlit app at repository root.
ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)
os.chdir(ROOT)

runpy.run_path(str(ROOT / "streamlit_app.py"), run_name="__main__")
