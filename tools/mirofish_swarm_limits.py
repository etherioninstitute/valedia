"""Every cap and time limit of a MiroFish swarm profile, derived in one place from measured constants (TMF2, 2026-11-15).

Pure functions only: no file, process, model or network access. The stage tool takes its swarm numbers from derive(), so a
number in the chain cannot silently be too small for another. Each constant says where it was measured. "The stopped run"
is valedia-native-20261114-swarm48-b (run1/calls and its simulation.log); "the 4-agent run" is
valedia-native-20261108-report64-coverage.
"""
from __future__ import annotations
import math

# ---- The model and the machine -----------------------------------------------------------------------------------------
WINDOW = 32768                # model context, tokens: gateway Limits.context and the metadata receipt (context 32768)
READ_TOKENS_PER_S = 188.0     # prompt tokens read a second: fit over 288 completed calls of the stopped run (R2 0.97)
WRITE_TOKENS_PER_S = 23.6     # reply tokens written a second: same fit
OVERSHOOT_S = 27.0            # the largest amount any of those 288 calls took beyond the fitted line
RATIO_MEASURED = 1.131        # real prompt tokens per agent-library token: 217 completed agent turns of the stopped run
RATIO_ASSUMED = 1.25          # the same ratio assumed for safety (a feed is JSON-heavy and may tokenise worse)
OVERHEAD_LARGE = 1750         # tools + template tokens on the platform with the larger tool list (stopped run)
OVERHEAD_SMALL = 830          # the same on the platform with the smaller tool list (stopped run)
BYTES_PER_TOKEN_MAX = 5.28    # request bytes per real prompt token, largest over the 225 agent requests of the stopped run
# The largest size any single token of the frozen Qwen vocabulary (tokenizer.json, 248,077 entries) can take once written by
# json.dumps with its default ASCII escaping, counting each of the token's bytes at its worst: a control byte 6 (\u0001), a
# quote, backslash or common control 2, other ASCII 1, a byte of a non-ASCII character 3 (a 2-byte character becomes 6 bytes,
# a 4-byte one 12). Computed from the vocabulary itself (test_mirofish_swarm_limits.py), so it bounds every text the model
# writes, whatever its language: a reply of N tokens is at most 144 N bytes in the worker's event line.
ESCAPED_BYTES_PER_TOKEN = 144

# ---- Fixed by the reviewed code (not tunable here) ----------------------------------------------------------------------
PLATFORMS = 2                 # twitter and reddit run side by side (run_parallel_simulation.py, platform 'parallel')
CALLER_IN_FLIGHT = 1          # caller calls are serial: parallel_profile_count 1 (mirofish_native_run.py:317), one report thread
NATIVE_PROBE_OUTPUT = 512     # the two compatibility probes reserve 512 each (gateway ledger of every run)
HOST_SAMPLE_S = 0.4           # the host samples memory every 0.4 s (mirofish_native_startup.py)
HISTORY_RECORD_BYTES = 1219   # mean bytes per resource-history record: 21,488,517 bytes / 17,627 records (stopped run)
CLEANUP_S = 120               # gateway cleanup headroom (Limits.cleanup_seconds); the run keeps it before its deadline
GATEWAY_MESSAGES = 128        # the gateway's bound on the message list of one chat call (validate_chat)
HELPER_REQUEST_CAP = 262144   # the SDK helper refuses a request line longer than this, outside its try (mirofish_native_model.py:130)
RECENT_ACTIONS = 50           # run_state.json keeps this many recent actions with full arguments (simulation_runner.py:133)
# Worker events (mirofish_native_run.py, native_worker -> WorkerChannel.request): one JSON line per stage on a pipe; the
# observer refuses a line longer than its buffer limit (line 261). Everything in an event besides the recent actions' texts
# has a fixed shape: ids, counts, hashes, flags, timestamps, and the run state's own scalar fields.
ACTION_FIXED_BYTES = 512      # one recent action without its texts: keys, ids, round, platform, type, timestamp, flags (largest seen 231)
STATE_FIXED_BYTES = 8192      # the wait event without its recent actions: run state scalars, platform log evidence, wrapper (largest seen 1,566)
FIXED_EVENT_BYTES = 65536     # graph_gate, prepare, start, report and a failure event, which carry no agent text (largest seen 4,582)
EVENT_STEP = 2**20            # the event limit rounds up to a whole MiB

# ---- The lead's rulings (TMF2 and addendum 1) ---------------------------------------------------------------------------
AGENT_REPLY_CAP = 1024        # an agent turn's reply cap (raised from 512 on 2026-11-11; longest seen 738)
CALLER_REPLY_CAP = 4096       # personas, ontology, config and report text (2,560 was 96% used: 2,467)
IN_FLIGHT_PER_PLATFORM = 1    # one call in flight per platform: dispatch is serial, more in flight only adds waiting
MARGIN = 1.10                 # every derived time limit: the worst single call plus 10%,
STEP_S = 30                   # rounded up to a multiple of 30 s,
CLIENT_LEAD_S = 30            # and a client timeout is at least 30 s longer than the gateway limit in front of it
HELPER_MARGIN_BYTES = 4096    # the gateway's request limit sits this far below the SDK helper's request-line cap (addendum 2, R5):
                              # the helper re-wraps the body with its action, id, identifier and deadline (about 120 bytes)
REQUEST_BYTES = HELPER_REQUEST_CAP - HELPER_MARGIN_BYTES   # 258,048; still covers a full-window prompt at the measured bytes per token
DURATION_MARGIN = 1.20        # the wall clock is the ceiling estimate plus 20%,
WALL_STEP_S = 1800            # rounded up to a whole half hour
CHAT_MARGIN = 1.50            # model calls allowed: the expected count plus 50%, rounded up to a multiple of 32
OUTPUT_STEP = 100000          # the output budget rounds up to a multiple of 100,000 tokens

# ---- Measured run shape (the stopped run unless said otherwise) ---------------------------------------------------------
PERSONA_LIBRARY_MEDIAN = 1106     # agent system message, library tokens (median; largest 1,684)
PERSONA_LIBRARY_MAX = 1684
FEED_LIBRARY_MEDIAN = 1513        # one sliced feed message, library tokens (median; 90th percentile 5,326; largest 6,690)
FEED_LIBRARY_WORST = (2418, 6477, 6690)   # the largest feeds seen, in the order they arrived
AGENT_REPLY_MEAN = 152            # mean agent-turn reply tokens over 216 completed agent calls
TOOL_RESULT_LIBRARY = 30          # a tool result record such as {"success": true, "post_id": 7}, library tokens (about)
CALLS_PER_TURN = 110 / 104        # model calls per agent turn: about 110 calls for 104 agent turns a round (52 agents x 2 platforms)
GROWTH = 52 / 48                  # configured agents per named actor: the graph built 52 agents from the 48 names
SETUP_GRAPH_S = 173               # graph-building model time: 11 calls at the 1,500 cap (stopped run, speed fit)
SETUP_CALLER_CALL_S = 57          # one persona/ontology/config call: 3,359 s over 59 calls at the 2,560 cap
SETUP_OTHER_CALLER_CALLS = 7      # caller calls besides personas in setup (59 calls for 52 personas)
SETUP_FIXED_CHATS = 20            # setup chats besides personas: 2 probes, 11 graph, 7 ontology/config (78 setup calls - 52 - embeddings)
REPORT_CHATS = 30                 # report-phase chats: 29 in the 4-agent run
REPORT_MEASURED_S = 393           # report phase, 4-agent run (native_terminal 12:06:43 to fresh_report_validated 12:13:16)
REPORT_SCALE_ASSUMED = 3          # a swarm's report reads more actions; assumed three times the 4-agent figure (never measured)
START_S = 15                      # model load, probes and graph prerequisite (about 13 s in every run)


def ceil_to(value, step):
    return int(math.ceil(value / step - 1e-12) * step)


def worst_call_seconds(reply_cap):
    """The ticket's worst single call: a full window of prompt read, the whole reply cap written, plus the largest overshoot."""
    return (WINDOW - reply_cap) / READ_TOKENS_PER_S + reply_cap / WRITE_TOKENS_PER_S + OVERSHOOT_S


def with_margin(seconds):
    return ceil_to(MARGIN * seconds, STEP_S)


def window_need(budget, reply_cap=AGENT_REPLY_CAP):
    """Real tokens one agent call can need at the assumed ratio: its whole memory budget, tools and template, and its reply."""
    return math.ceil(RATIO_ASSUMED * budget) + OVERHEAD_LARGE + reply_cap


def largest_budget(reply_cap=AGENT_REPLY_CAP):
    return math.floor((WINDOW - OVERHEAD_LARGE - reply_cap) / RATIO_ASSUMED)


def call_seconds(prompt_real, reply):
    return prompt_real / READ_TOKENS_PER_S + reply / WRITE_TOKENS_PER_S


def expected_round_seconds(budget, round_number):
    """Expected time of one agent call in round 1, 2, 3...: persona plus every feed so far, capped by the memory budget."""
    record = AGENT_REPLY_MEAN + TOOL_RESULT_LIBRARY
    library = min(budget, PERSONA_LIBRARY_MEDIAN + round_number * (FEED_LIBRARY_MEDIAN + record))
    prompt = RATIO_MEASURED * library + (OVERHEAD_LARGE + OVERHEAD_SMALL) / 2
    return call_seconds(prompt, AGENT_REPLY_MEAN)


def ceiling_call_seconds(budget):
    """One agent call with its memory full at the assumed ratio on the larger platform, at the mean reply."""
    return call_seconds(RATIO_ASSUMED * budget + OVERHEAD_LARGE, AGENT_REPLY_MEAN)


def action_text_tokens():
    """The most model-written text one recorded action can carry. A quote carries its own text twice (content and
    quote_content, each at most one agent reply), the quoted post (a seed post can be as long as a caller reply) and two
    names (the agent's and the quoted author's, each at most a caller reply: names come from persona replies)."""
    return 2 * AGENT_REPLY_CAP + 3 * CALLER_REPLY_CAP


def wait_event_bound():
    """The largest worker 'wait' event line: the run state with its recent actions at their largest, every token at its worst."""
    return STATE_FIXED_BYTES + RECENT_ACTIONS * (action_text_tokens() * ESCAPED_BYTES_PER_TOKEN + ACTION_FIXED_BYTES)


def worker_event_bytes():
    """The observer's event buffer limit: the largest event line plus its newline, with the 10% margin, in whole MiB."""
    return ceil_to(MARGIN * (max(wait_event_bound(), FIXED_EVENT_BYTES) + 1), EVENT_STEP)


def derive(profile):
    """All numbers for one swarm profile from its closed inputs: actors, agents_bound, rounds, memory_budget. Expected
    figures use the named actors times the measured growth; ceilings, the wall and every budget use the participant bound,
    so they hold for anything the bound admits."""
    actors, rounds, budget, bound = profile['actors'], profile['rounds'], profile['memory_budget'], profile['agents_bound']
    agents = math.ceil(actors * GROWTH - 1e-9)
    out = {'expected_agents': agents}
    # Memory and window.
    out['window_need'] = window_need(budget)
    out['largest_budget'] = largest_budget()
    # Worst single calls and the time limits that must cover them.
    agent_worst, caller_worst = worst_call_seconds(AGENT_REPLY_CAP), worst_call_seconds(CALLER_REPLY_CAP)
    agent_slot, caller_slot = with_margin(agent_worst), with_margin(caller_worst)
    agents_in_flight = PLATFORMS * IN_FLIGHT_PER_PLATFORM
    out.update(agent_worst_s=round(agent_worst, 2), caller_worst_s=round(caller_worst, 2), agent_slot_s=agent_slot, caller_slot_s=caller_slot,
               agents_in_flight=agents_in_flight, caller_in_flight=CALLER_IN_FLIGHT, semaphore=IN_FLIGHT_PER_PLATFORM)
    arrival = ceil_to(max(agents_in_flight * agent_slot, CALLER_IN_FLIGHT * caller_slot), STEP_S)
    queue = ceil_to(max((agents_in_flight - 1) * agent_slot, (CALLER_IN_FLIGHT - 1) * caller_slot), STEP_S)
    client = arrival + CLIENT_LEAD_S
    out.update(gateway_arrival_s=arrival, gateway_queue_s=queue, agent_client_s=client, caller_client_s=client, embedding_client_s=client,
               request_bytes=REQUEST_BYTES, request_bytes_need=math.ceil(WINDOW * BYTES_PER_TOKEN_MAX), helper_request_cap=HELPER_REQUEST_CAP)
    out.update(wait_event_bound=wait_event_bound(), worker_event_bytes=worker_event_bytes())
    # Model calls and output.
    turns = rounds * agents * PLATFORMS
    sim_chats = math.ceil(turns * CALLS_PER_TURN)
    setup_chats = SETUP_FIXED_CHATS + agents
    expected_chats = setup_chats + sim_chats + REPORT_CHATS
    bound_turns = rounds * bound * PLATFORMS
    bound_sim_chats = math.ceil(bound_turns * CALLS_PER_TURN)
    bound_chats = SETUP_FIXED_CHATS + bound + bound_sim_chats + REPORT_CHATS
    chats = ceil_to(max(CHAT_MARGIN * expected_chats, bound_chats), 32)
    reserve = 2 * NATIVE_PROBE_OUTPUT + (chats - 2) * max(CALLER_REPLY_CAP, AGENT_REPLY_CAP)
    out.update(turns=turns, sim_chats=sim_chats, setup_chats=setup_chats, report_chats=REPORT_CHATS, expected_chats=expected_chats,
               bound_turns=bound_turns, bound_sim_chats=bound_sim_chats, bound_chats=bound_chats,
               chats=chats, reserve_need=reserve, output=ceil_to(reserve, OUTPUT_STEP))
    # Durations by phase, and the wall clock.
    setup_s = SETUP_GRAPH_S + (agents + SETUP_OTHER_CALLER_CALLS) * SETUP_CALLER_CALL_S
    ceiling_setup = SETUP_GRAPH_S + (bound + SETUP_OTHER_CALLER_CALLS) * SETUP_CALLER_CALL_S
    per_round = turns / rounds * CALLS_PER_TURN
    expected_rounds = [per_round * expected_round_seconds(budget, r) for r in range(1, rounds + 1)]
    ceiling_sim = bound_sim_chats * ceiling_call_seconds(budget)
    report_s = REPORT_MEASURED_S * REPORT_SCALE_ASSUMED
    expected_total = START_S + setup_s + sum(expected_rounds) + report_s + CLEANUP_S
    ceiling_total = START_S + ceiling_setup + ceiling_sim + report_s + CLEANUP_S
    wall = ceil_to(DURATION_MARGIN * ceiling_total, WALL_STEP_S)
    out.update(setup_s=round(setup_s), ceiling_setup_s=round(ceiling_setup), expected_round_s=[round(x) for x in expected_rounds], ceiling_sim_s=round(ceiling_sim),
               report_s=round(report_s), expected_total_s=round(expected_total), ceiling_total_s=round(ceiling_total), wall=wall,
               worker_deadline_s=wall - CLEANUP_S)
    records = int(wall / HOST_SAMPLE_S) + 1
    out.update(history_records=records, history_mib=ceil_to(records * HISTORY_RECORD_BYTES * 1.5 / 2**20, 32))
    return out


def violations(profile, d=None):
    """Every coupling inequality; an empty list means the profile is consistent. Each entry names the broken inequality."""
    d = d or derive(profile)
    checks = [
        ('memory budget fits the window at the assumed ratio: 1.25 B + 1750 + 1024 <= 32768', d['window_need'] <= WINDOW),
        ('largest persona fits the memory budget with room for a feed', PERSONA_LIBRARY_MAX < profile['memory_budget']),
        ('a call slot is the worst single call plus 10%', d['agent_slot_s'] >= MARGIN * d['agent_worst_s'] and d['caller_slot_s'] >= MARGIN * d['caller_worst_s']),
        ('gateway arrival-to-done covers every agent call in flight', d['gateway_arrival_s'] >= d['agents_in_flight'] * d['agent_slot_s']),
        ('gateway arrival-to-done covers every caller call in flight', d['gateway_arrival_s'] >= d['caller_in_flight'] * d['caller_slot_s']),
        ('gateway queue covers the calls ahead of one agent call', d['gateway_queue_s'] >= (d['agents_in_flight'] - 1) * d['agent_slot_s']),
        ('gateway queue is shorter than the arrival-to-done limit', d['gateway_queue_s'] < d['gateway_arrival_s']),
        ('agent client timeout outlasts the gateway by 30 s', d['agent_client_s'] >= d['gateway_arrival_s'] + CLIENT_LEAD_S),
        ('caller client timeouts outlast the gateway by 30 s', d['caller_client_s'] >= d['gateway_arrival_s'] + CLIENT_LEAD_S),
        ('embedding client timeout outlasts the gateway by 30 s', d['embedding_client_s'] >= d['gateway_arrival_s'] + CLIENT_LEAD_S),
        ('every limit is a whole multiple of 30 s', all(d[k] % STEP_S == 0 for k in ['gateway_arrival_s', 'gateway_queue_s', 'agent_client_s', 'agent_slot_s', 'caller_slot_s'])),
        ('request bytes cover a full-window prompt', d['request_bytes'] >= d['request_bytes_need']),
        ('request bytes sit at least 4096 below the SDK helper cap', d['request_bytes'] <= HELPER_REQUEST_CAP - 4096),
        ('worker event limit covers the largest wait event with its margin', d['worker_event_bytes'] >= MARGIN * (d['wait_event_bound'] + 1)),
        ('worker event limit covers every fixed-shape event', d['worker_event_bytes'] >= MARGIN * (FIXED_EVENT_BYTES + 1)),
        ('caller reply cap fits the per-call output ceiling', CALLER_REPLY_CAP <= 4096 and AGENT_REPLY_CAP <= CALLER_REPLY_CAP),
        ('model calls allowed cover the expected calls', d['chats'] >= d['expected_chats']),
        ('model calls allowed cover every call the participant bound admits', d['chats'] >= d['bound_chats']),
        ('output budget lets every admitted call reserve its cap', d['output'] >= d['reserve_need']),
        ('participant bound covers the named participants and the measured growth', profile['agents_bound'] >= max(profile['actors'], d['expected_agents'])),
        ('wall covers the ceiling at the participant bound with its margin', d['wall'] >= DURATION_MARGIN * d['ceiling_total_s']),
        ('host history covers the wall at one sample each 0.4 s', d['history_records'] >= int(d['wall'] / HOST_SAMPLE_S) + 1),
        ('host history bytes cover its records at 1.5 x the measured size', d['history_mib'] * 2**20 >= d['history_records'] * HISTORY_RECORD_BYTES * 1.5),
        ('worker deadline keeps the cleanup headroom', d['worker_deadline_s'] == d['wall'] - CLEANUP_S),
    ]
    return [name for name, ok in checks if not ok]
