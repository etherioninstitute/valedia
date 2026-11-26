"""Offline tests for the read-only Kalshi index websocket client: every connection is an injected fake."""
import asyncio
import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.http11 import Response

from valedia_trader.research_loop.btc_updown_logger import ENABLED_FROM, NotYetEnabled
from valedia_trader.venues import kalshi_index_ws as kis

KEY_ID = "TEST-KEY-ID"
UTC = timezone.utc
AFTER = ENABLED_FROM + timedelta(days=1)
RECV = int(datetime(2026, 12, 5, 12, 0, 0, tzinfo=UTC).timestamp() * 1000)


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def ed_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def creds(rsa_key):
    return kis.Credentials(KEY_ID, rsa_key)


@pytest.fixture(autouse=True)
def no_jitter(monkeypatch):
    monkeypatch.setattr(kis, "_jitter", lambda: 0.0)


# ------------------------------------------------------------------ fakes

class StepClock:
    """A timezone-aware clock that moves `step` seconds on every reading."""
    def __init__(self, start=AFTER, step=1.0):
        self.t, self.step, self.calls = start, step, 0

    def __call__(self):
        now = self.t
        self.t += timedelta(seconds=self.step)
        self.calls += 1
        return now


class FakeWS:
    """Answers recv() from a script: a str/bytes is returned, an exception is raised; when it runs out the socket closes."""
    def __init__(self, script):
        self.script, self.sent, self.closed = list(script), [], False

    async def recv(self):
        if not self.script:
            raise ConnectionClosedOK(None, None)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def send(self, text):
        self.sent.append(text)

    async def close(self):
        self.closed = True


class FakeConnect:
    """Each call takes the next outcome: a FakeWS is returned, an exception is raised; when out, Ctrl-C ends the run."""
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    async def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.outcomes:
            raise KeyboardInterrupt()
        item = self.outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Sleeps:
    def __init__(self):
        self.values = []

    async def __call__(self, seconds):
        self.values.append(seconds)


def status_error(code):
    return InvalidStatus(Response(code, "Too Many Requests", Headers(), b""))


def value_msg(seq=42, sid=1, received_at=RECV, value="68000.12", with_15min=False, sending=True):
    msg = {
        "index_id": "BRTI",
        "received_at": received_at,
        "data": json.dumps({"type": "value", "id": "BRTI", "time": received_at - 20, "value": value}),
        "avg_60s_data": {"value": "68000.12000000", "window_size": 3,
                         "window_start_ts_ms": received_at - 60000, "window_end_ts_exclusive": received_at},
    }
    if with_15min:
        msg["last_60s_windowed_average_15min"] = {"value": "67999.50000000", "window_size": 60,
                                                  "window_start_ts_ms": received_at - 60000,
                                                  "window_end_ts_exclusive": received_at}
    obj = {"type": "cfbenchmarks_value", "sid": sid, "seq": seq, "msg": msg}
    if sending:
        obj["sending_ts_ms"] = received_at + 5
    return obj


def j(obj):
    return json.dumps(obj)


def run(tmp_path, outcomes, creds, *, clock=None, sleeps=None, **kw):
    connect = FakeConnect(outcomes)
    sleeps = sleeps if sleeps is not None else Sleeps()
    summary = kis.stream(tmp_path / "ticks", clock=clock or StepClock(), connect=connect, credentials=creds,
                         sleep=sleeps, **kw)
    return summary, connect, sleeps


def lines(tmp_path):
    out = []
    for p in sorted((tmp_path / "ticks").glob("kalshi_index_ticks-*.jsonl")):
        out += [json.loads(x) for x in p.read_text().splitlines()]
    return out


# ------------------------------------------------------------------ signing and headers

def test_rsa_signature_verifies_under_digest_length_and_not_under_the_max_salt(rsa_key):
    ts = 1764936000123
    sig = base64.b64decode(kis.sign(rsa_key, ts, kis.WS_PATH))
    message = f"{ts}GET/trade-api/ws/v2".encode()
    pub = rsa_key.public_key()
    pub.verify(sig, message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
               hashes.SHA256())
    # verify() treats PSS.MAX_LENGTH as "detect the salt", so it cannot show the difference; an explicit length can:
    # the digest length (32) verifies, the largest salt this 2048-bit key allows (256 - 32 - 2 = 222) does not.
    pub.verify(sig, message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
    with pytest.raises(InvalidSignature):
        pub.verify(sig, message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=222), hashes.SHA256())


def test_ed25519_signature_verifies(ed_key):
    ts = 1764936000123
    sig = base64.b64decode(kis.sign(ed_key, str(ts), kis.WS_PATH))
    ed_key.public_key().verify(sig, f"{ts}GET/trade-api/ws/v2".encode())


def test_unsupported_key_type_is_refused():
    with pytest.raises(TypeError):
        kis.sign(object(), 1, kis.WS_PATH)


def test_header_shape_and_query_stripped_from_signed_path(ed_key):
    ts = 1764936000123
    headers = kis.build_headers(KEY_ID, ed_key, ts, "/trade-api/ws/v2?channel=x&y=1")
    assert set(headers) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-SIGNATURE", "KALSHI-ACCESS-TIMESTAMP"}
    assert headers["KALSHI-ACCESS-KEY"] == KEY_ID
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1764936000123" and isinstance(headers["KALSHI-ACCESS-TIMESTAMP"], str)
    ed_key.public_key().verify(base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]), f"{ts}GET/trade-api/ws/v2".encode())
    assert kis.WS_URL == "wss://external-api-ws.kalshi.com/trade-api/ws/v2"


def test_stream_connects_to_the_url_with_fresh_signed_headers(tmp_path, creds, rsa_key):
    _, connect, _ = run(tmp_path, [FakeWS([ConnectionClosedOK(None, None)]), FakeWS([])], creds)
    assert [c[0] for c in connect.calls] == [kis.WS_URL] * 3      # two fakes, then the Ctrl-C outcome
    stamps = [c[1]["additional_headers"]["KALSHI-ACCESS-TIMESTAMP"] for c in connect.calls]
    assert len(set(stamps)) == 3                                   # re-signed with a new timestamp on every connect
    for _, kw in connect.calls:
        h = kw["additional_headers"]
        rsa_key.public_key().verify(
            base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), f"{h['KALSHI-ACCESS-TIMESTAMP']}GET{kis.WS_PATH}".encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        assert h["KALSHI-ACCESS-KEY"] == KEY_ID


# ------------------------------------------------------------------ credentials

def test_missing_key_id_names_the_variable_only():
    with pytest.raises(kis.CredentialError) as err:
        kis.load_credentials({})
    assert "KALSHI_API_KEY_ID" in str(err.value)


def test_unreadable_key_file_names_the_variable_not_the_values(tmp_path):
    env = {"KALSHI_API_KEY_ID": "SECRET-ID-VALUE", "KALSHI_PRIVATE_KEY_PATH": str(tmp_path / "missing.pem")}
    with pytest.raises(kis.CredentialError) as err:
        kis.load_credentials(env)
    assert "KALSHI_PRIVATE_KEY_PATH" in str(err.value)
    assert "SECRET-ID-VALUE" not in str(err.value) and "missing.pem" not in str(err.value)


def test_garbage_key_file_does_not_quote_its_content(tmp_path):
    pem = tmp_path / "k.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----\nSECRETBODY\n-----END PRIVATE KEY-----\n")
    with pytest.raises(kis.CredentialError) as err:
        kis.load_credentials({"KALSHI_API_KEY_ID": "SECRET-ID-VALUE", "KALSHI_PRIVATE_KEY_PATH": str(pem)})
    assert "SECRETBODY" not in str(err.value) and "SECRET-ID-VALUE" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_credentials_load_from_a_key_file_and_repr_hides_them(tmp_path, rsa_key):
    pem = tmp_path / "k.pem"
    pem.write_bytes(rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    got = kis.load_credentials({"KALSHI_API_KEY_ID": KEY_ID, "KALSHI_PRIVATE_KEY_PATH": str(pem)})
    assert got.key_id == KEY_ID
    assert KEY_ID not in repr(got) and "private" not in repr(got).lower()
    assert kis.DEFAULT_KEY_PATH == "~/.hermes/secrets/kalshi_private_key.pem"


# ------------------------------------------------------------------ the date gate

def test_gate_refuses_before_enabled_from_with_no_credential_read_and_no_connect(tmp_path, monkeypatch):
    reads, connect = [], FakeConnect([FakeWS([])])
    monkeypatch.setattr(kis, "load_credentials", lambda *a, **k: reads.append("default") or pytest.fail("read"))
    before = ENABLED_FROM - timedelta(seconds=1)
    with pytest.raises(NotYetEnabled):
        kis.stream(tmp_path / "ticks", clock=lambda: before, connect=connect, credentials=None, sleep=Sleeps())
    with pytest.raises(NotYetEnabled):
        kis.stream(tmp_path / "ticks", clock=lambda: before, connect=connect,
                   credentials=lambda: reads.append("injected"), sleep=Sleeps())
    assert connect.calls == [] and reads == []
    assert not (tmp_path / "ticks").exists()


def test_gate_refuses_a_naive_clock(tmp_path, creds):
    connect = FakeConnect([])
    with pytest.raises(Exception, match="timezone-aware"):
        kis.stream(tmp_path / "ticks", clock=lambda: datetime(2027, 1, 1), connect=connect, credentials=creds)
    assert connect.calls == []


def test_gate_opens_at_enabled_from_exactly(tmp_path, creds):
    connect = FakeConnect([])
    kis.stream(tmp_path / "ticks", clock=lambda: ENABLED_FROM, connect=connect, credentials=creds, sleep=Sleeps())
    assert len(connect.calls) == 1


# ------------------------------------------------------------------ the send guard

@pytest.mark.parametrize("channel", ["user_orders", "fill", "orderbook_delta", "ticker", ""])
def test_guard_refuses_other_channels(channel):
    with pytest.raises(kis.SendRefused):
        kis.guard_frame({"id": 1, "cmd": "subscribe", "params": {"channels": [channel], "index_ids": ["BRTI"]}})
    with pytest.raises(kis.SendRefused):
        kis.guard_frame({"id": 1, "cmd": "subscribe",
                         "params": {"channels": ["cfbenchmarks_value", channel], "index_ids": ["BRTI"]}})


def test_guard_allows_the_index_channels():
    for ch in ("cfbenchmarks_value", "cfbenchmarks_value_5hz"):
        text = kis.guard_frame({"id": 1, "cmd": "subscribe", "params": {"channels": [ch], "index_ids": ["BRTI"]}})
        assert json.loads(text)["params"]["channels"] == [ch]
    ok = {"id": 2, "cmd": "update_subscription", "params": {"sid": 1, "action": "subscribe_indices", "index_ids": ["all"]}}
    assert kis.guard_frame(ok)
    assert kis.guard_frame({"id": 3, "cmd": "update_subscription", "params": {"sid": 1, "action": "indexlist"}})
    assert kis.guard_frame({"id": 4, "cmd": "unsubscribe", "params": {"sids": [1]}})


@pytest.mark.parametrize("frame", [
    {"id": 1, "cmd": "create_" + "order", "params": {}},
    {"id": 1, "cmd": "update_subscription", "params": {"sid": 1, "action": "add_markets", "market_tickers": ["X"]}},
    {"id": 1, "cmd": "update_subscription", "params": {"sid": 1, "action": "indexlist", "index_ids": ["BRTI"]}},
    {"id": 1, "cmd": "update_subscription", "params": {"sid": 1, "action": "subscribe_indices"}},
    {"id": 1, "cmd": "subscribe", "params": {"channels": ["cfbenchmarks_value"], "index_ids": ["BRTI"], "x": 1}},
    {"id": 1, "cmd": "subscribe", "params": {"channels": ["cfbenchmarks_value"]}},
    {"id": 1, "cmd": "unsubscribe", "params": {"sids": ["1"]}},
    {"id": 0, "cmd": "unsubscribe", "params": {"sids": [1]}},
    {"cmd": "unsubscribe", "params": {"sids": [1]}},
    {"id": 1, "cmd": "subscribe", "params": "x"},
    "subscribe",
])
def test_guard_refuses_everything_else(frame):
    with pytest.raises(kis.SendRefused):
        kis.guard_frame(frame)


@pytest.mark.parametrize("bad", ["brti", "B", "A" * 33, "BRTI\n", "BR TI", "BRTI;X", "", 5, None])
def test_guard_checks_index_ids(bad):
    with pytest.raises(kis.SendRefused):
        kis.guard_frame({"id": 1, "cmd": "subscribe", "params": {"channels": ["cfbenchmarks_value"], "index_ids": [bad]}})


def test_stream_refuses_a_forbidden_channel_before_credentials_and_connect(tmp_path, monkeypatch):
    reads, connect = [], FakeConnect([])
    for bad in ("user_orders", "fill", "orderbook_delta"):
        with pytest.raises(kis.SendRefused):
            kis.stream(tmp_path / "ticks", channel=bad, clock=StepClock(), connect=connect,
                       credentials=lambda: reads.append(1))
    with pytest.raises(kis.SendRefused):
        kis.stream(tmp_path / "ticks", index_ids=["bad id"], clock=StepClock(), connect=connect,
                   credentials=lambda: reads.append(1))
    assert reads == [] and connect.calls == []


def test_the_subscribe_frame_goes_out_through_the_guard(tmp_path, creds):
    ws = FakeWS([KeyboardInterrupt()])
    run(tmp_path, [ws], creds, index_ids=("BRTI", "ETHUSD_RTI"))
    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0]) == {"id": 1, "cmd": "subscribe", "params": {
        "channels": ["cfbenchmarks_value"], "index_ids": ["BRTI", "ETHUSD_RTI"]}}
    ws2 = FakeWS([KeyboardInterrupt()])
    run(tmp_path, [ws2], creds, channel="cfbenchmarks_value_5hz")
    assert json.loads(ws2.sent[0])["params"]["channels"] == ["cfbenchmarks_value_5hz"]


# ------------------------------------------------------------------ the parser

def test_parse_the_documented_example_with_the_fifteen_minute_average():
    tick = kis.parse_value_message(value_msg(with_15min=True))
    assert tick.index_id == "BRTI"
    assert tick.value == Decimal("68000.12") and isinstance(tick.value, Decimal)
    assert tick.source_time_ms == RECV - 20 and tick.received_at_ms == RECV and tick.sending_ts_ms == RECV + 5
    assert (tick.sid, tick.seq) == (1, 42)
    assert tick.avg_60s == kis.WindowAvg(Decimal("68000.12000000"), 3, RECV - 60000, RECV)
    assert str(tick.avg_60s.value) == "68000.12000000"
    assert tick.last_15min_avg == kis.WindowAvg(Decimal("67999.50000000"), 60, RECV - 60000, RECV)
    assert tick.data == json.dumps({"type": "value", "id": "BRTI", "time": RECV - 20, "value": "68000.12"})
    with pytest.raises(Exception):
        tick.seq = 1        # frozen


def test_parse_without_the_fifteen_minute_average_or_sending_time():
    tick = kis.parse_value_message(value_msg(sending=False))
    assert tick.last_15min_avg is None and tick.sending_ts_ms is None and tick.avg_60s is not None


def value_msg_5hz(seq=7, sid=2, received_at=RECV, value="68000.12"):
    """The 5 Hz message: the 1 Hz envelope with the type changed and no averages."""
    obj = value_msg(seq=seq, sid=sid, received_at=received_at, value=value)
    obj["type"] = "cfbenchmarks_value_5hz"
    del obj["msg"]["avg_60s_data"]
    return obj


def test_a_one_hertz_message_without_avg_60s_data_is_malformed(tmp_path, creds):
    msg = value_msg()
    del msg["msg"]["avg_60s_data"]
    with pytest.raises(kis.MalformedFrame):
        kis.parse_value_message(msg)
    msg["msg"]["avg_60s_data"] = None
    with pytest.raises(kis.MalformedFrame):
        kis.parse_value_message(msg)
    gone = value_msg(seq=2)
    del gone["msg"]["avg_60s_data"]
    summary, _, _ = run(tmp_path, [FakeWS([j(gone), j(value_msg(seq=3)), KeyboardInterrupt()])], creds)
    assert summary["malformed"] == 1 and summary["ticks"] == 1 and len(lines(tmp_path)) == 1


def test_the_five_hertz_message_parses_with_no_avg_60s():
    tick = kis.parse_value_message(value_msg_5hz())
    assert tick.avg_60s is None and tick.last_15min_avg is None
    assert tick.index_id == "BRTI" and tick.value == Decimal("68000.12")
    assert (tick.sid, tick.seq) == (2, 7) and tick.received_at_ms == RECV and tick.source_time_ms == RECV - 20
    assert tick.sending_ts_ms == RECV + 5


def test_the_five_hertz_message_takes_the_fifteen_minute_average_when_present():
    msg = value_msg_5hz()
    msg["msg"]["last_60s_windowed_average_15min"] = {
        "value": "67999.50000000", "window_size": 60, "window_start_ts_ms": RECV - 60000,
        "window_end_ts_exclusive": RECV}
    tick = kis.parse_value_message(msg)
    assert tick.avg_60s is None
    assert tick.last_15min_avg == kis.WindowAvg(Decimal("67999.50000000"), 60, RECV - 60000, RECV)


def test_a_five_hertz_stream_records_ticks_and_both_index_list_replies_are_handled(tmp_path, creds):
    script = [j({"type": "cfbenchmarks_value_indexlist", "msg": {"index_ids": ["BRTI"]}}),
              j({"type": "cfbenchmarks_value_5hz_indexlist", "msg": {"index_ids": ["BRTI"]}}),
              j(value_msg_5hz(seq=1)), j(value_msg_5hz(seq=2)), KeyboardInterrupt()]
    summary, _, _ = run(tmp_path, [FakeWS(script)], creds, channel="cfbenchmarks_value_5hz")
    assert summary["ticks"] == 2 and summary["malformed"] == 0 and summary["errors"] == []
    rows = lines(tmp_path)
    assert [r["channel"] for r in rows] == ["cfbenchmarks_value_5hz"] * 2
    assert rows[0]["avg_60s"] is None and rows[0]["last_15min_avg"] is None and rows[0]["value"] == "68000.12"


def test_decimal_precision_is_kept(tmp_path, creds):
    big = "68000.123456789012345678"
    run(tmp_path, [FakeWS([j(value_msg(value=big)), KeyboardInterrupt()])], creds)
    assert lines(tmp_path)[0]["value"] == big


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(type="other"),
    lambda m: m["msg"].update(data="not json"),
    lambda m: m["msg"].update(data="[1]"),
    lambda m: m["msg"].update(data=5),
    lambda m: m["msg"].update(data=j({"time": RECV, "value": 68000.12})),     # a float is refused
    lambda m: m["msg"].update(data=j({"time": RECV, "value": "NaN"})),
    lambda m: m["msg"].update(data=j({"time": RECV, "value": "1E+999999999"})),
    lambda m: m["msg"].update(data=j({"value": "1"})),
    lambda m: m["msg"].update(index_id="brti"),
    lambda m: m["msg"].pop("received_at"),
    lambda m: m["msg"].update(received_at="x"),
    lambda m: m["msg"].update(received_at=10 ** 30),
    lambda m: m["msg"]["avg_60s_data"].update(value="abc"),
    lambda m: m["msg"].pop("avg_60s_data"),
    lambda m: m.pop("seq"),
    lambda m: m.update(sid=True),
    lambda m: m.update(msg=[]),
])
def test_parse_refuses_malformed_frames(mutate):
    msg = value_msg()
    mutate(msg)
    with pytest.raises(kis.MalformedFrame):
        kis.parse_value_message(msg)


# ------------------------------------------------------------------ counting inside a run

def test_malformed_frames_are_counted_and_skipped(tmp_path, creds):
    bad_value = value_msg(seq=3)
    bad_value["msg"]["data"] = j({"time": RECV, "value": "oops"})
    script = [j({"type": "subscribed", "id": 1, "msg": {"channel": "cfbenchmarks_value", "sid": 1}}),
              "this is not json", b"\xff\xfe", "[]", j({"type": "cfbenchmarks_value", "msg": {}}), j(bad_value),
              j({"type": "ok", "sid": 1, "seq": 9}),
              j({"type": "cfbenchmarks_value_indexlist", "msg": {"index_ids": ["BRTI"]}}),
              j(value_msg(seq=1)), KeyboardInterrupt()]
    summary, _, _ = run(tmp_path, [FakeWS(script)], creds)
    assert summary["malformed"] == 5 and summary["ticks"] == 1 and summary["gaps"] == 0
    assert len(lines(tmp_path)) == 1


def test_odd_frames_are_malformed_and_the_connection_and_the_next_tick_survive(tmp_path, creds):
    script = [j({"type": [1]}), j({"type": {"a": 1}}), "[" * 100_000, j(value_msg(seq=1)), KeyboardInterrupt()]
    summary, connect, _ = run(tmp_path, [FakeWS(script)], creds)
    assert summary["malformed"] == 3 and summary["ticks"] == 1
    assert summary["reconnects"] == 0 and summary["errors"] == [] and len(connect.calls) == 1


def test_a_deeply_nested_data_string_is_malformed_not_a_crash():
    msg = value_msg()
    msg["msg"]["data"] = "[" * 100_000
    with pytest.raises(kis.MalformedFrame):
        kis.parse_value_message(msg)
    with pytest.raises(kis.MalformedFrame):
        kis.parse_value_message({"type": [1]})


def test_a_sequence_jump_is_a_gap(tmp_path, creds):
    script = [j(value_msg(seq=s)) for s in (1, 2, 5, 6)] + [j(value_msg(seq=1, sid=2)), KeyboardInterrupt()]
    summary, _, _ = run(tmp_path, [FakeWS(script)], creds)
    assert summary["ticks"] == 5 and summary["gaps"] == 1 and summary["missed"] == 2
    assert summary["reconnects"] == 0


def test_a_new_connection_starts_the_sequence_again(tmp_path, creds):
    first = FakeWS([j(value_msg(seq=1)), j(value_msg(seq=2)), ConnectionClosedOK(None, None)])
    second = FakeWS([j(value_msg(seq=1)), j(value_msg(seq=2)), KeyboardInterrupt()])
    summary, _, _ = run(tmp_path, [first, second], creds)
    assert summary["ticks"] == 4 and summary["gaps"] == 0 and summary["reconnects"] == 1


def test_server_error_replies_are_recorded_with_code_and_message(tmp_path, creds):
    script = [j({"id": 1, "type": "error", "msg": {"code": 6, "msg": "Already subscribed"}}), KeyboardInterrupt()]
    summary, _, _ = run(tmp_path, [FakeWS(script)], creds)
    assert {"kind": "server_error", "code": 6, "message": "Already subscribed"} in summary["errors"]
    assert summary["malformed"] == 0


# ------------------------------------------------------------------ reconnect and backoff

def test_reconnect_after_a_close_and_after_429s_with_exponential_backoff(tmp_path, creds):
    outcomes = [FakeWS([]),                                   # closes at once, no data
                status_error(429), status_error(429),
                FakeWS([j(value_msg(seq=1)), KeyboardInterrupt()])]
    summary, connect, sleeps = run(tmp_path, outcomes, creds)
    assert len(connect.calls) == 4
    assert sleeps.values == [1.0, 2.0, 4.0]
    assert summary["reconnects"] == 3 and summary["ticks"] == 1
    assert [e["kind"] for e in summary["errors"]] == ["connection_closed", "handshake_429", "handshake_429"]


def test_backoff_restarts_after_every_connection_that_delivered_data(tmp_path, creds):
    # Three refused handshakes (1, 2, 4), then three connections that each deliver a tick and then close: each of those
    # closes is a fresh first failure, so the wait is 1 s again. Without the reset it would keep doubling (8, 16, 32).
    outcomes = [status_error(429)] * 3 + [FakeWS([j(value_msg(seq=1))]) for _ in range(3)]
    summary, connect, sleeps = run(tmp_path, outcomes, creds)
    assert sleeps.values == [1.0, 2.0, 4.0, 1.0, 1.0, 1.0]
    assert summary["ticks"] == 3 and summary["reconnects"] == 6 and len(connect.calls) == 7


@pytest.mark.parametrize("reply", [
    {"type": "subscribed", "id": 1, "msg": {"channel": "cfbenchmarks_value", "sid": 1}},
    {"id": 1, "type": "error", "msg": {"code": 6, "msg": "Already subscribed"}},
    {"type": "ok", "id": 1},
])
def test_a_reply_without_a_tick_does_not_restart_the_backoff(tmp_path, creds, reply):
    # The server takes the handshake, answers, and closes, three times: the wait must keep doubling.
    outcomes = [FakeWS([j(reply)]) for _ in range(3)]
    summary, _, sleeps = run(tmp_path, outcomes, creds)
    assert sleeps.values == [1.0, 2.0, 4.0]
    assert summary["ticks"] == 0


def test_backoff_doubles_to_a_sixty_second_cap():
    assert [kis.backoff_delay(n) for n in range(1, 10)] == [1, 2, 4, 8, 16, 32, 60, 60, 60]


def test_jitter_only_shortens_and_stays_within_a_quarter(monkeypatch):
    monkeypatch.setattr(kis, "_jitter", lambda: 0.999999)
    for n in range(1, 10):
        base = min(60.0, 2.0 ** (n - 1))
        assert 0.75 * base <= kis.backoff_delay(n) <= base


def test_a_plain_sleep_function_is_accepted(tmp_path, creds):
    seen = []
    connect = FakeConnect([OSError("down")])
    kis.stream(tmp_path / "ticks", clock=StepClock(), connect=connect, credentials=creds, sleep=seen.append)
    assert seen == [1.0]


def test_stream_ends_at_max_seconds_and_closes_the_socket(tmp_path, creds):
    ws = FakeWS([j(value_msg(seq=n)) for n in range(1, 11)])
    summary, connect, _ = run(tmp_path, [ws], creds, clock=StepClock(step=1.0), max_seconds=3.5)
    assert len(connect.calls) == 1 and ws.closed
    assert 1 <= summary["ticks"] < 10 and summary["reconnects"] == 0


def test_max_seconds_wins_over_a_long_backoff(tmp_path, creds):
    sleeps = Sleeps()
    summary, connect, _ = run(tmp_path, [OSError("down")] * 5, creds, clock=StepClock(step=1.0), sleeps=sleeps,
                              max_seconds=3.5)
    assert all(s <= 3.5 for s in sleeps.values) and len(connect.calls) < 5


def test_a_cancellation_closes_the_socket_and_returns_the_summary(tmp_path, creds):
    # a real Ctrl-C under asyncio.run arrives as a cancellation of the main task, not as KeyboardInterrupt
    ws = FakeWS([j(value_msg(seq=1)), j(value_msg(seq=2)), asyncio.CancelledError()])
    summary, connect, sleeps = run(tmp_path, [ws], creds)
    assert ws.closed and summary["ticks"] == 2 and summary["reconnects"] == 0 and summary["errors"] == []
    assert len(connect.calls) == 1 and sleeps.values == []
    assert len(lines(tmp_path)) == 2


def test_a_real_task_cancellation_closes_the_socket_and_keeps_the_ticks(tmp_path, creds):
    class BlockingWS(FakeWS):
        async def recv(self):
            if self.script:
                return self.script.pop(0)
            await asyncio.Event().wait()        # waits for ever, like an idle socket

    ws = BlockingWS([j(value_msg(seq=1))])
    connect = FakeConnect([ws])
    sleeps = Sleeps()

    async def drive():
        run_ = kis._Run(kis.TickStore(tmp_path / "ticks"), ["BRTI"], kis.CHANNEL_VALUE, StepClock(), connect, creds,
                        None, sleeps, AFTER)
        task = asyncio.ensure_future(run_.main())
        for _ in range(200):
            if run_.ticks:
                break
            await asyncio.sleep(0)
        assert run_.ticks == 1 and not task.done()
        task.cancel()
        await task              # main ends quietly on a real cancellation
        return run_.summary()

    summary = asyncio.run(drive())
    assert ws.closed and summary["ticks"] == 1 and summary["errors"] == [] and sleeps.values == []
    assert len(lines(tmp_path)) == 1


def test_ctrl_c_closes_cleanly_and_returns_the_summary(tmp_path, creds):
    ws = FakeWS([j(value_msg(seq=1)), KeyboardInterrupt()])
    summary, _, sleeps = run(tmp_path, [ws], creds)
    assert ws.closed and summary["ticks"] == 1 and sleeps.values == []


def test_max_seconds_must_be_positive(tmp_path, creds):
    for bad in (0, -1, True, "5"):
        with pytest.raises(ValueError):
            kis.stream(tmp_path / "ticks", clock=StepClock(), connect=FakeConnect([]), credentials=creds, max_seconds=bad)


# ------------------------------------------------------------------ the store

def test_store_writes_one_line_per_tick_dated_by_utc_received_time(tmp_path, creds):
    late = int(datetime(2026, 12, 5, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000)
    early = late + 1
    script = [j(value_msg(seq=1, received_at=late)), j(value_msg(seq=2, received_at=early, with_15min=True)),
              KeyboardInterrupt()]
    run(tmp_path, [FakeWS(script)], creds)
    names = sorted(p.name for p in (tmp_path / "ticks").iterdir())
    assert names == ["kalshi_index_ticks-2026-12-05.jsonl", "kalshi_index_ticks-2026-12-06.jsonl"]
    rows = lines(tmp_path)
    assert [r["seq"] for r in rows] == [1, 2]
    assert rows[1]["last_15min_avg"]["window_size"] == 60 and rows[0]["last_15min_avg"] is None
    assert rows[0]["value"] == "68000.12" and rows[0]["channel"] == "cfbenchmarks_value"
    assert rows[0]["avg_60s"]["value"] == "68000.12000000"
    assert json.loads(rows[0]["data"])["time"] == late - 20


def test_store_path_and_line_content(tmp_path):
    store = kis.TickStore(tmp_path / "ticks")
    assert store.folder == Path(os.path.realpath(tmp_path / "ticks"))
    tick = kis.parse_value_message(value_msg(with_15min=True))
    path = store.append(tick, "cfbenchmarks_value")
    assert path == store.folder / "kalshi_index_ticks-2026-12-05.jsonl"
    assert path.read_text() == json.dumps(tick.as_json("cfbenchmarks_value"), separators=(",", ":"), sort_keys=True) + "\n"
    assert json.loads(path.read_text())["last_15min_avg"]["value"] == "67999.50000000"
    assert kis._REPO_ROOT not in path.parents


@pytest.mark.parametrize("where", ["root", "inside", "relative", "empty", "dotdot"])
def test_store_and_stream_refuse_a_folder_in_the_repo(where, tmp_path, creds):
    folder = {"root": kis._REPO_ROOT, "inside": kis._REPO_ROOT / "data" / "x", "relative": "ticks", "empty": "",
              "dotdot": str(kis._REPO_ROOT / "tests" / ".." / "x")}[where]
    with pytest.raises(ValueError):
        kis.TickStore(folder)
    reads, connect = [], FakeConnect([])
    with pytest.raises(ValueError):
        kis.stream(folder, clock=StepClock(), connect=connect, credentials=lambda: reads.append(1) or creds)
    assert reads == [] and connect.calls == []


def test_store_refuses_a_symlink_into_the_repo(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(kis._REPO_ROOT)
    with pytest.raises(ValueError):
        kis.TickStore(link / "ticks")


def test_a_failed_write_stops_the_run_instead_of_reconnecting_forever(tmp_path, creds):
    (tmp_path / "ticks").write_text("a file where the folder should be")
    with pytest.raises(kis.StoreError):
        run(tmp_path, [FakeWS([j(value_msg(seq=1))])], creds)


# ------------------------------------------------------------------ no secret leaks

def test_no_secret_reaches_the_summary_an_error_or_the_jsonl(tmp_path, creds):
    seen = {}

    class Leaky(FakeConnect):
        async def __call__(self, url, **kwargs):
            sig = kwargs["additional_headers"]["KALSHI-ACCESS-SIGNATURE"]
            seen.setdefault("sigs", []).append(sig)
            if len(self.calls) == 0:
                self.calls.append((url, kwargs))
                raise OSError(f"cannot reach host with {KEY_ID} and {sig}")
            return await super().__call__(url, **kwargs)

    connect = Leaky([FakeWS([j({"id": 1, "type": "error", "msg": {"code": 9, "msg": f"bad key {KEY_ID}"}}),
                             j(value_msg(seq=1)), KeyboardInterrupt()])])
    summary = kis.stream(tmp_path / "ticks", clock=StepClock(), connect=connect, credentials=creds, sleep=Sleeps())
    blob = json.dumps(summary) + "".join(p.read_text() for p in (tmp_path / "ticks").iterdir())
    blob += repr(creds) + str(creds)
    assert KEY_ID not in blob
    for sig in seen["sigs"]:
        assert sig not in blob
    assert summary["ticks"] == 1 and len(summary["errors"]) == 2


def test_a_missing_credential_stops_before_any_connect(tmp_path):
    def boom():
        raise kis.CredentialError("KALSHI_API_KEY_ID is not set")
    connect = FakeConnect([FakeWS([])])
    with pytest.raises(kis.CredentialError, match="KALSHI_API_KEY_ID"):
        kis.stream(tmp_path / "ticks", clock=StepClock(), connect=connect, credentials=boom)
    assert connect.calls == []
    with pytest.raises(kis.CredentialError):
        kis.stream(tmp_path / "ticks", clock=StepClock(), connect=connect, credentials=object())


# ------------------------------------------------------------------ the module source

def test_the_module_has_no_rest_url_requests_import_or_trade_words():
    src = Path(kis.__file__).read_text().lower()
    assert not re.search(r"https?://", src)
    assert not re.search(r"^\s*(import|from)\s+(requests|urllib|httpx|aiohttp)", src, re.M)
    for word in ("create_" + "order", "cancel_" + "order", "portfolio", "order", "fill", "balance", "position",
                 "/trade-api/v2", "user_"):
        assert word not in src, word
    assert src.count(".send(") == 1          # one place where a frame leaves the module, and it is guarded
    assert "guard_frame(frame)" in src


# ------------------------------------------------------------------ the command line

def test_cli_prints_only_the_summary(monkeypatch, capsys, tmp_path):
    got = {}

    def fake_stream(folder, **kw):
        got.update(folder=folder, **kw)
        return {"ticks": 3, "gaps": 0, "missed": 0, "malformed": 0, "reconnects": 1, "errors": []}

    monkeypatch.setattr(kis, "stream", fake_stream)
    code = kis.main(["stream", "--out-dir", str(tmp_path), "--index", "BRTI", "--index", "ETHUSD_RTI", "--5hz",
                     "--seconds", "5"])
    out = capsys.readouterr()
    assert code == 0 and json.loads(out.out)["ticks"] == 3 and out.out.count("\n") == 1 and out.err == ""
    assert got["index_ids"] == ("BRTI", "ETHUSD_RTI") and got["channel"] == "cfbenchmarks_value_5hz"
    assert got["max_seconds"] == 5.0 and got["folder"] == str(tmp_path)


def test_cli_defaults_and_refusals(monkeypatch, capsys, tmp_path):
    seen = {}
    monkeypatch.setattr(kis, "stream", lambda folder, **kw: seen.update(kw) or {"ticks": 0})
    assert kis.main(["stream", "--out-dir", str(tmp_path)]) == 0
    assert seen["index_ids"] == ("BRTI",) and seen["channel"] == "cfbenchmarks_value" and seen["max_seconds"] is None
    capsys.readouterr()

    def refuse(folder, **kw):
        raise NotYetEnabled("disabled until x")
    monkeypatch.setattr(kis, "stream", refuse)
    assert kis.main(["stream", "--out-dir", str(tmp_path)]) == 2
    assert "NotYetEnabled" in capsys.readouterr().err


# ------------------------------------------------------------------ the live connect never logs the handshake

def test_the_live_connect_passes_a_logger_that_ignores_a_host_wide_debug(monkeypatch):
    import websockets
    seen = {}
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: seen.update(url=url, **kw) or "not-a-connection")
    names = ["", "websockets", "websockets.client"]
    old = {n: logging.getLogger(n).level for n in names}
    try:
        for n in names:
            logging.getLogger(n).setLevel(logging.DEBUG)
        assert kis._default_connect(kis.WS_URL, additional_headers={"X": "y"}) == "not-a-connection"
        assert seen["url"] == kis.WS_URL and seen["additional_headers"] == {"X": "y"}
        assert seen["logger"].isEnabledFor(logging.DEBUG) is False
        assert logging.getLogger("websockets").isEnabledFor(logging.DEBUG) is True    # the host setting was really on
    finally:
        for n, level in old.items():
            logging.getLogger(n).setLevel(level)
