"""rondo unit tests. Run: python -m unittest discover -s scripts/tests"""

import sys
from pathlib import Path

# Make `import rondo` work whether tests are discovered from the repo root or
# from scripts/tests itself.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
