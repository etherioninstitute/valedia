from valedia_trader.quant_engine import formula_registry, wiring as W


def test_every_formula_is_wired_or_has_a_stated_reason():
    rows = W.wiring()
    assert len(rows) == 50 == len(formula_registry())
    s = W.summary(rows)
    assert s["unwired_ids"] == [], f"implemented but used nowhere and not explained: {s['unwired_ids']}"
    assert s["wired"] + s["not_applicable"] == 50 and s["wired"] >= 40


def test_the_not_applicable_list_cannot_go_stale():
    rows = {r["formula"]: r for r in W.wiring()}
    assert set(W.NOT_APPLICABLE) <= set(rows)
    used_anyway = [fid for fid in W.NOT_APPLICABLE if rows[fid]["used_in"]]
    assert used_anyway == [], f"now used; remove from NOT_APPLICABLE: {used_anyway}"
    assert all(len(reason) > 20 for reason in W.NOT_APPLICABLE.values())


def test_a_use_needs_the_level_module_not_just_a_matching_word(tmp_path):
    pkg = tmp_path / "valedia_trader"; (pkg / "quant_engine").mkdir(parents=True); (pkg / "x").mkdir()
    (pkg / "x" / "a.py").write_text("variance = 3  # a local name, no engine import\n")
    (pkg / "x" / "b.py").write_text("from valedia_trader.quant_engine import level1_probability as l1\nv = l1.variance([1, 2])\n")
    rows = {r["formula"]: r for r in W.wiring(pkg)}
    assert list(rows["1.3"]["used_in"]) == ["x/b.py"]
    assert "42 of 50" not in W.render(list(rows.values())) and "wired" in W.render(list(rows.values()))
