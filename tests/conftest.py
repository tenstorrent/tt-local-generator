import sys
import pytest
from pathlib import Path

# Add app/ to sys.path for all tests so they can import app modules directly
# without each test file repeating this boilerplate.
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


@pytest.fixture(autouse=True)
def _isolate_pipeline_store(tmp_path, monkeypatch):
    """Redirect PipelineStore index to tmp_path for every test.

    Prevents test runs from writing fake run records into the production
    pipeline-index.json, which would corrupt the app's startup state.

    This fixture runs before each test's own monkeypatching, establishing
    a safe default. Tests that do their own pipeline_store monkeypatching
    will override this redirect for their specific tmp_path, which is fine.
    """
    import pipeline_store as _ps
    safe_dir = tmp_path / "pipeline_store"
    safe_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_ps, "_INDEX_PATH", safe_dir / "pipeline-index.json")
    monkeypatch.setattr(_ps, "_RUNS_DIR", safe_dir)
