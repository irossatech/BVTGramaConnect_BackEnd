"""The OpenAPI schema builds without errors, so Swagger and generated client code stay right
(audit BE_01, BE-API-001)."""

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_schema_has_no_errors_and_no_component_clash():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("PYTEST_VERSION", None)  # build it as the server would
    result = subprocess.run(
        [sys.executable, "manage.py", "spectacular", "--validate", "--format", "openapi-json"],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    errors = re.search(r"Errors:\s+(\d+)", result.stderr)
    assert errors is None or errors.group(1) == "0", result.stderr[-3000:]
    assert "identical names" not in result.stderr
    assert "unable to guess serializer" not in result.stderr
