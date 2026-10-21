import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = sorted((Path(__file__).parent / "js").glob("*.test.mjs"))
# The index script uses `Map.groupBy`, added in Node 21; 22 is the oldest maintained release with it.
MIN_NODE = 22


def node_or_skip() -> str:
    """Find a Node new enough for the index script, or skip the test.

    Returns:
        str: The path of `node`.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    if int(version.removeprefix("v").split(".")[0]) < MIN_NODE:
        pytest.skip(f"needs Node {MIN_NODE} or later, found {version}")
    return node


# Both sides of UTC, so a day taken in UTC instead of the browser's time zone fails in one of them.
@pytest.mark.parametrize("time_zone", ["Asia/Taipei", "America/Los_Angeles"])
def test_node_tests_pass(time_zone: str) -> None:
    node = node_or_skip()
    assert JS_TESTS, "no tests in tests/js"
    # The files are passed by name: Node 21 and later read a folder argument as a module path, not a folder of tests.
    result = subprocess.run(
        [node, "--test", *(str(path) for path in JS_TESTS)],
        capture_output=True,
        text=True,
        env={**os.environ, "TZ": time_zone},
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
