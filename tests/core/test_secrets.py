import os

import pytest

from valedia_trader.core import secrets as s


def test_parse_and_load_respects_existing_values(tmp_path):
    f = tmp_path / "trading.env"
    f.write_text("# comment\nexport A_KEY='abc123'\nB_SECRET=\"xyz\"\nbad line\n9BAD=1\nC=\n")
    os.chmod(f, 0o600)
    env = {"B_SECRET": "already-set"}
    assert s.load_env(f, environ=env) == ["A_KEY", "C"]
    assert env == {"B_SECRET": "already-set", "A_KEY": "abc123", "C": ""}
    assert s.load_env(f, environ=env, override=True) == ["A_KEY", "B_SECRET", "C"] and env["B_SECRET"] == "xyz"
    assert s.load_env(tmp_path / "missing.env", environ={}) == []


def test_world_readable_file_is_refused(tmp_path):
    f = tmp_path / "trading.env"
    f.write_text("A_KEY=topsecretvalue\n")
    os.chmod(f, 0o644)
    with pytest.raises(s.SecretError) as err:
        s.load_env(f, environ={})
    assert "chmod 600" in str(err.value) and "topsecretvalue" not in str(err.value)


def test_strict_lookup_names_the_variable_not_the_value(tmp_path):
    env = {"VALEDIA_ENV_FILE": str(tmp_path / "x.env"), "PRESENT": "v"}
    assert s.secret("PRESENT", env) == "v" and s.env_file_path(env) == tmp_path / "x.env"
    with pytest.raises(s.SecretError, match="MISSING_KEY"):
        s.secret("MISSING_KEY", env)
    assert s.env_file_path({}) == s.DEFAULT_ENV_FILE and s.DEFAULT_ENV_FILE.name == "trading.env"
