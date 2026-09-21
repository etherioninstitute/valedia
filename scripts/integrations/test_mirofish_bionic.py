#!/usr/bin/env python3
"""MIROFISH-BIONIC — embedding API detect + fence. No live orders.

Run: python3 test_mirofish_bionic.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/mirofish-offline/backend"))

import valedia_config as vc


def test_embedding_api_kind():
    from app.storage.embedding_service import (  # noqa: WPS433
        embedding_api_kind, embed_endpoint, parse_embeddings,
    )
    old = os.environ.pop("EMBEDDING_API", None)
    try:
        assert embedding_api_kind("http://127.0.0.1:1234") == "openai"
        assert embedding_api_kind("http://127.0.0.1:1234/v1") == "openai"
        assert embedding_api_kind("http://localhost:11435") == "ollama"
        assert embedding_api_kind("http://localhost:11435", api="openai") == "openai"
        assert embedding_api_kind("http://127.0.0.1:1234", api="ollama") == "ollama"
    finally:
        if old is not None:
            os.environ["EMBEDDING_API"] = old
    assert embed_endpoint("http://127.0.0.1:1234", "openai") == "http://127.0.0.1:1234/v1/embeddings"
    assert embed_endpoint("http://127.0.0.1:1234/v1", "openai") == "http://127.0.0.1:1234/v1/embeddings"
    assert embed_endpoint("http://localhost:11435", "ollama") == "http://localhost:11435/api/embed"
    vecs = parse_embeddings(
        {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]},
        2,
    )
    assert vecs == [[1.0, 0.0], [0.0, 1.0]]
    vecs2 = parse_embeddings({"embeddings": [[0.5]]}, 1)
    assert vecs2 == [[0.5]]


def test_healthcheck_helpers():
    path = os.path.join(ROOT, "scripts", "mirofish_healthcheck.py")
    src = open(path).read()
    assert "BIONIC = \"http://127.0.0.1:1234\"" in src
    assert "OLLAMA = \"http://127.0.0.1:11435\"" in src
    assert "MIROFISH_INFERENCE" in src
    assert ":11435" in src and ":1234" in src
    # script still allows ollama fallback; does not hard-require only :11435
    assert "FAIL if mode == \"ollama\" else \"WARN\"" in src or 'mode == "ollama"' in src


def test_hygiene_and_version():
    assert vc.VALEDIA_VERSION == "0.53.0", vc.VALEDIA_VERSION
    assert vc.MIROFISH_AUTO_RAISE is False
    assert os.environ.get("POLYMARKET_US_LIVE") in (None, "0", "false", "")
    sh = open(os.path.join(ROOT, "scripts", "valedia_models.sh")).read()
    assert "mirofish-inference" in sh
    assert "com.burton.ollama-mirofish" in sh
    assert "VENUE_WRITER" not in sh


if __name__ == "__main__":
    test_embedding_api_kind()
    test_healthcheck_helpers()
    test_hygiene_and_version()
    print("test_mirofish_bionic: PASS")
