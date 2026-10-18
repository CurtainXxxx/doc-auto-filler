"""Keep tests independent of developer secrets and cloud services."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["STORAGE_BACKEND"] = "local"
os.environ["PGDATABASE_URL"] = ""
