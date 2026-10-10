"""Launch the ReviewRing dashboard: .venv/Scripts/python main.py"""

import sys
from pathlib import Path

# pyproject.toml has no build backend yet, so the src/ package is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from reviewring.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
