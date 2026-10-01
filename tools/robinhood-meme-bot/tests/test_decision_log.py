import json
import threading

from test_app import make_bot

from rh_meme_bot.app import main
from rh_meme_bot.decision_log import DecisionLog, filter_records, read_records


def test_append_is_one_complete_line_each_even_concurrently(tmp_path):
    log = DecisionLog(str(tmp_path / "sub" / "d.jsonl"))
    big = {"raw": {"blob": "x" * 20000}}

    def worker(i):
        for j in range(10):
            log.append({"i": i, "j": j, **big})

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    lines = (tmp_path / "sub" / "d.jsonl").read_text().splitlines()
    assert len(lines) == 50 and all(json.loads(x)["raw"] for x in lines)


def test_read_skips_corrupt_lines_and_missing_file(tmp_path):
    assert read_records(str(tmp_path / "nope.jsonl")) == []
    p = tmp_path / "d.jsonl"
    p.write_text('{"a": 1}\nnot json\n[1,2]\n{"b": 2}\n')
    assert read_records(str(p)) == [{"a": 1}, {"b": 2}]


def _populate(tmp_path):
    bot, _, _ = make_bot(tmp_path)
    bot.run_once()
    return str(tmp_path / "dec.jsonl")


def test_filter_records(tmp_path):
    recs = read_records(_populate(tmp_path))
    assert len(filter_records(recs, action="buy")) == 1
    assert len(filter_records(recs, action="skip")) == 1
    assert len(filter_records(recs, action="hold")) == 0
    assert len(filter_records(recs, final="skip")) == 1
    assert len(filter_records(recs, last=1)) == 1
    assert filter_records(recs, last=1)[0] == recs[-1]


def test_review_cli(tmp_path, capsys):
    path = _populate(tmp_path)
    assert main(["review", "--file", path, "--action", "skip"]) == 0
    out = capsys.readouterr().out
    assert "model=skip" in out and "-> SKIP" in out and "failed:" in out
    assert "model=buy" not in out and "1 record(s)" in out
    main(["review", "--file", path])
    assert "2 record(s)" in capsys.readouterr().out
    main(["review", "--file", path, "--action", "buy", "--json"])
    first = capsys.readouterr().out.splitlines()[0]
    rec = json.loads(first)
    assert rec["final_action"] == "buy" and rec["raw"] and rec["gates"]
    main(["review", "--file", path, "--last", "1"])
    assert "1 record(s)" in capsys.readouterr().out
