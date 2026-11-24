"""Every User-Agent the package sends is a generic client name: what the program does, nothing else. The checker reads
source text with `ast` (it never imports the scanned modules). Each rule is tested both ways: the scan rejects the
bad shapes and accepts the good one, and the real package scan must also have SEEN the known constants, so a scan that
sees nothing cannot pass."""
import ast
import re
from pathlib import Path

PROJECT = "valedia"
PACKAGE = Path(__file__).resolve().parents[2] / "valedia_trader"
_FORM = re.compile(r"^[a-z]+(-[a-z]+)*/[0-9]+\.[0-9]+$")


def _name_is_ua(name):
    return name in ("_UA", "UA") or "USER_AGENT" in name


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def seen_user_agents(source):
    """Every User-Agent string value in `source`: a module-level constant named `_UA`, `UA` or holding USER_AGENT, and a
    string value of the key "User-Agent" in a dict literal."""
    tree = ast.parse(source)
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, value = [node.target.id], node.value
        else:
            continue
        if any(_name_is_ua(n) for n in names) and _str(value) is not None:
            found.append(_str(value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if _str(k) == "User-Agent" and _str(v) is not None:
                    found.append(_str(v))
    return found


def breaks_rule(value):
    low = value.lower()
    return (not _FORM.match(value) or PROJECT in low or "@" in value or "http" in low or "www" in low
            or value.count("/") != 1)


def check_source(source):
    """Every User-Agent value in `source` that breaks the rule."""
    return [v for v in seen_user_agents(source) if breaks_rule(v)]


# ---- the real package ----

KNOWN = {
    "knowledge/k3_calendar.py": ["calendar-and-notice-reader/1.0"],
    "knowledge/k4_venue.py": ["calendar-and-notice-reader/1.0"],
    "venues/polymarket_data.py": ["market-data-reader/1.0"],
    "venues/kalshi_data.py": ["market-data-reader/1.0"],
    "data_and_fees/second_quote.py": ["second-quote-check/1.0"],
    "data_and_fees/company_facts.py": ["market-data-reader/1.0"],
    "research_loop/chatter_loop.py": ["comment-and-price-collector/1.0"],
}


def _scan_package():
    seen = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        values = seen_user_agents(path.read_text(encoding="utf-8"))
        if values:
            seen[path.relative_to(PACKAGE).as_posix()] = values
    return seen


def test_no_file_under_the_package_breaks_the_rule():
    bad = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        found = check_source(path.read_text(encoding="utf-8"))
        if found:
            bad[path.relative_to(PACKAGE).as_posix()] = found
    assert bad == {}


def test_the_scan_saw_the_seven_constants_that_exist_today():
    seen = _scan_package()
    for rel, values in KNOWN.items():
        assert rel in seen, rel
        for v in values:
            assert v in seen[rel], (rel, v)
    assert sum(len(v) for r, v in seen.items() if r in KNOWN) >= 7


# ---- two-way: the checker itself ----

def test_rejects_the_old_value_assigned_to_ua():
    assert check_source('_UA = "valedia-client/1.0"\n') == ["valedia-client/1.0"]


def test_rejects_a_value_with_an_at_sign():
    assert check_source('USER_AGENT = "reader/1.0 (someone@example.invalid)"\n') != []
    assert check_source('UA = "name@host/1.0"\n') == ["name@host/1.0"]


def test_rejects_the_project_name_in_capitals_as_a_dict_value():
    src = 'h = {"User-Agent": "VALEDIA/1.0", "Accept": "x"}\n'
    assert check_source(src) == ["VALEDIA/1.0"]


def test_rejects_a_url():
    assert check_source('_UA = "https://example.invalid/1.0"\n') != []
    assert check_source('_UA = "www.example.invalid/1.0"\n') != []


def test_rejects_an_extra_slash_and_a_bad_version():
    assert check_source('_UA = "a/b/1.0"\n') != []
    assert check_source('_UA = "reader/1"\n') != []
    assert check_source('_UA = "Reader/1.0"\n') != []


def test_accepts_a_generic_name():
    assert check_source('_UA = "market-data-reader/1.0"\n') == []
    assert check_source('h = {"User-Agent": "market-data-reader/1.0"}\n') == []
    assert check_source('MY_USER_AGENT = "calendar-and-notice-reader/1.0"\n') == []


def test_finds_each_shape_and_ignores_the_rest():
    src = ('_UA = "a-b/1.0"\nUA = "c/2.0"\nX_USER_AGENT = "d/3.0"\nh = {"User-Agent": "e/4.0"}\n'
           'OTHER = "bad@x/1.0"\ng = {"Accept": "bad@x/1.0"}\nh2 = {"User-Agent": some_name}\n')
    assert sorted(seen_user_agents(src)) == ["a-b/1.0", "c/2.0", "d/3.0", "e/4.0"]
