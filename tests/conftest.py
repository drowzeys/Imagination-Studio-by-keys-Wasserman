"""Every test runs with a studio folder of its own: a test that imports the server without naming STUDIO_HOME must
never use (or move, app/studio_home.py) the person's real ~/ImaginationStudio or ~/TensorFoldStudio."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp(prefix="studio-test-")) / "ImaginationStudio"))
# nor walk the machine for models: the app starts that scan 20 s after it is imported (app/discover.py), and it would
# hold the scan's lock while tests/test_discover.py runs its own scans
os.environ.setdefault("STUDIO_DISCOVER_DELAY", "-1")
