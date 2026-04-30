import sys
from pathlib import Path

# Add app/ to sys.path for all tests so they can import app modules directly
# without each test file repeating this boilerplate.
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
