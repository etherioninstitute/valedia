from valedia_trader import quant_engine


def test_all_fifty_formulas_are_implemented():
    reg = quant_engine.formula_registry()
    assert sorted(reg, key=lambda s: tuple(map(int, s.split(".")))) == [f"{a}.{b}" for a in range(1, 11) for b in range(1, 6)]
    for fid, (module, names) in reg.items():
        assert names and module == quant_engine.LEVELS[int(fid.split(".")[0])]
