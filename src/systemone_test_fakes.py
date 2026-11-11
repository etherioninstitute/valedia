"""Scoped fake of local Ollama for System One tests (not a test module).

Installs a fake /api/tags + /api/version getter on the client, a per-test log
path, and restores both afterwards, so combined pytest runs cannot leak a fake
between modules. Script mode: call setup() first and teardown() last.
"""
import os

FULL = "24e550a16a70" + "9" * 52  # Burton's nimble:latest digest, full-length stand-in


class Scoped:
    def __init__(self, s1, log):
        self.s1, self.log, self._saved = s1, log, (None, None)

    def use(self, digest=FULL):
        """Fake nimble:latest with this digest and drop the digest cache."""
        self.s1._get = lambda url, timeout: (
            {"models": [{"name": "nimble:latest", "digest": digest}]} if url.endswith("/api/tags")
            else {"version": "0.35.0"})
        self.s1._reset_digest()

    def setup(self):
        self._saved = (self.s1._get, os.environ.get("VALEDIA_SYSTEMONE_LOG"))
        os.environ["VALEDIA_SYSTEMONE_LOG"] = self.log
        self.use()

    def teardown(self):
        self.s1._get, old = self._saved
        os.environ.pop("VALEDIA_SYSTEMONE_LOG", None)
        if old is not None:
            os.environ["VALEDIA_SYSTEMONE_LOG"] = old
        self.s1._reset_digest()

    def fixture(self):
        """Autouse pytest fixture (None when pytest is absent: script mode)."""
        try:
            import pytest
        except ImportError:
            return None

        @pytest.fixture(autouse=True)
        def _scoped_fakes():
            self.setup()
            yield
            self.teardown()
        return _scoped_fakes
