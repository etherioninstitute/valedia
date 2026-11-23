"""Suite-wide safety for the secret tripwire.

No test can reach the real secrets file. Before any test module is imported, VALEDIA_ENV_FILE points
at a path that does not exist inside a fresh temporary folder. Every attempt to open the real file,
or to resolve it from the process environment, is recorded, and the test that made it fails.

Every test also starts and ends with no output guard and no remembered secret: the tripwire's state
is process-wide, and any test that calls `secret()` with a long value turns it on."""
import functools
import os
import sys
import tempfile
from pathlib import Path

import pytest

_NO_SECRETS_DIR = Path(tempfile.mkdtemp(prefix="valedia-no-secrets-"))
os.environ["VALEDIA_ENV_FILE"] = str(_NO_SECRETS_DIR / "trading.env")      # never created
# No test reads the real Hermes profiles or runs the real LM Studio CLI (the placement guard does both).
os.environ["HERMES_HOME"] = str(_NO_SECRETS_DIR / "hermes-home")          # never created
os.environ["VALEDIA_LMS"] = str(_NO_SECRETS_DIR / "no-lms")               # never created

from valedia_trader.core import secrets  # noqa: E402

_REAL = str(secrets.DEFAULT_ENV_FILE)
_touches: list[str] = []                                                    # what happened, never a value


def _audit(event, args):
    if event == "open" and args:
        path = args[0]
        if isinstance(path, os.PathLike):
            path = os.fspath(path)
        if path == _REAL:
            _touches.append("opened")


sys.addaudithook(_audit)

_plain_env_file_path = secrets.env_file_path


@functools.wraps(_plain_env_file_path)
def _watched_env_file_path(environ=None):
    path = _plain_env_file_path(environ)
    if (environ is None or environ is os.environ) and str(path) == _REAL:
        _touches.append("resolved")
    return path


secrets.env_file_path = _watched_env_file_path


@pytest.fixture
def real_secret_file_touches():
    """Every time a test opened the real secrets file or resolved its path from the environment."""
    return _touches


@pytest.fixture(autouse=True)
def _no_tripwire_between_tests():
    secrets.remove_output_guard()
    secrets._forget_all()
    yield
    secrets.remove_output_guard()
    secrets._forget_all()
    assert not _touches, "a test reached the real secrets file (opened or resolved its path)"
