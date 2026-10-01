import pytest

from rh_meme_bot import nimble_pin


@pytest.fixture(autouse=True)
def _fresh_digest_cache():
    """The Nimble digest verification is cached per process; isolate every test."""
    nimble_pin.clear_cache()
    yield
    nimble_pin.clear_cache()
