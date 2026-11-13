import pytest

from valedia_trader.core import secrets


@pytest.fixture(autouse=True)
def _no_tripwire_between_tests():
    """Every test starts and ends with no output guard and no remembered secret: the tripwire's
    state is process-wide, and any test that calls `secret()` with a long value turns it on."""
    secrets.remove_output_guard()
    secrets._forget_all()
    yield
    secrets.remove_output_guard()
    secrets._forget_all()
