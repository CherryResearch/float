import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(backend_dir))
