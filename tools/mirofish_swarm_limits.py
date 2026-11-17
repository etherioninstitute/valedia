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
RATIO_ASSUMED = 1.30          # the same ratio assumed for safety (TMF3 addendum 1: raised from 1.25, since one platform of the
                              # 8B proving run measured 1.241 on the 27B's tokenizer, which left 1.25 no margin)
OVERHEAD_LARGE = 1750         # tools + template tokens on the platform with the larger tool list (stopped run)
OVERHEAD_SMALL = 830          # the same on the platform with the smaller tool list (stopped run)
BYTES_PER_TOKEN_MAX = 5.28    # request bytes per real prompt token, largest over the 225 agent requests of the stopped run
# The largest size any single token of the frozen Qwen vocabulary (tokenizer.json, 248,077 entries) can take once written by
# json.dumps with its default ASCII escaping, counting each of the token's bytes at its worst: a control byte 6 (\u0001), a
# quote, backslash or common control 2, other ASCII 1, a byte of a non-ASCII character 3 (a 2-byte character becomes 6 bytes,
# a 4-byte one 12). Computed from the vocabulary itself (test_mirofish_swarm_limits.py), so it bounds every text the model
# writes, whatever its language: a reply of N tokens is at most 144 N bytes in the worker's event line.
ESCAPED_BYTES_PER_TOKEN = 144

# ---- Per model (TMF3, 2026-11-15; TMF4, two models by phase) ---------------------------------------------------------------
# A LIMIT (call slot, arrival-to-done, queue, client timeouts, request and event bytes, model calls, output) is an upper bound
# and stays derived from the 27B's constants above for every model: a faster model only finishes sooner. EXPECTED durations
# and the wall clock use the chosen model's own speeds; in a plan of two models (TMF4), setup and report at the setup model's,
# the simulation at the turns model's. The memory window inequality stays the 27B's: every other model here needs no more
# real tokens for the same request (token_ratio_to_27b below), so a budget that fits the 27B fits it.
# The context cache of each model at the 32,768 window (the start threshold, TMF4 addendum 1 ruling c) is computed from its
# own full-attention layer, key/value head and head size counts, each checked against its source by test_mirofish_swarm_limits.py.
DEFAULT_MODEL = 'qwen3.8-27b'
PLAIN_READ_27B = 260.0        # the 27B's prompt tokens a second through plain LM Studio, against 188 inside the pipeline
PIPELINE_DISCOUNT = READ_TOKENS_PER_S / PLAIN_READ_27B   # 0.723: what the pipeline (admission, the C++ runtime) cost the 27B
MODELS = {
    # Attention: the tokenizer origin's config.json text_config: 64 layers, every 4th a full-attention layer (16), 4 key/value
    # heads of 256; the other 48 are linear-attention layers whose state does not grow with the context.
    'qwen3.8-27b': {'read_tps': READ_TOKENS_PER_S, 'write_tps': WRITE_TOKENS_PER_S, 'token_ratio_to_27b': 1.0,
                    'attention_layers': 16, 'kv_heads': 4, 'head_dim': 256,
                    'speed_source': 'measured inside the pipeline: fit over 288 completed calls of the stopped run (R2 0.97)'},
    # Plain LM Studio beside the live collector, 44 real agent turns of the 2026-11-15 proving run: 1,208 read, 46.3 written a
    # second. Both are discounted by the 27B's plain-to-pipeline read ratio (no write ratio was measured for the 27B, so the
    # read ratio is used for both; it is the larger discount and so the longer estimate). An ESTIMATE until a run.
    # Attention: the tokenizer origin's config.json: 36 layers, all full attention, 8 key/value heads of 128.
    'qwen3-8b': {'read_tps': 1208.0 * PIPELINE_DISCOUNT, 'write_tps': 46.3 * PIPELINE_DISCOUNT, 'token_ratio_to_27b': 0.950,
                 'attention_layers': 36, 'kv_heads': 8, 'head_dim': 128,
                 'speed_source': 'ESTIMATE until a run: plain-server 1,208 read and 46.3 written a second (44 agent turns, '
                                 '2026-11-15) x 188/260, the 27B\'s plain-to-pipeline read ratio'},
    # Ternary Bonsai 2 (TMF4): 54 recorded requests through the plain server ran at the 27B's speed, so the 27B's pipeline
    # speeds are used, an ASSUMPTION until a run. Same tokenizer as the 27B (the GGUF header's vocabulary, merges, special
    # tokens and template match the 27B's tokenizer origin), so the same real tokens per request. Attention: its own GGUF
    # header (qwen35.block_count 64, full_attention_interval 4, attention.head_count_kv 4, key_length and value_length 256).
    'ternary-bonsai-2-27b': {'read_tps': READ_TOKENS_PER_S, 'write_tps': WRITE_TOKENS_PER_S, 'token_ratio_to_27b': 1.0,
                             'attention_layers': 16, 'kv_heads': 4, 'head_dim': 256,
                             'speed_source': 'ASSUMED until a run: the 27B\'s pipeline speeds (188 read, 23.6 written a second); '
                                             'Bonsai 2 answered 54 recorded requests through the plain server at the 27B\'s speed'},
}
# The 8B's real prompt tokens per 27B real token for the same 44 request bodies (its reported usage.prompt_tokens against the
# 27B admission's count): reddit median 0.939, largest 0.950; twitter median 0.918, largest 0.931. The largest is used.
INSTALLED_MEMORY_BYTES = 68719476736   # this Mac (sysctl hw.memsize, 64 GiB)
# The free-memory start threshold (TMF5 addendum 1, ruling e): start = floor + margin + setup_cost + fall, each a NAMED value
# with the source of its number beside it (file and date). It replaces TMF4's per-model formula. The lead replaces setup_cost and
# fall with figures measured by a load-only test beside the new collector before the first run: that is a change to this table
# and its source strings only; staging and its tests follow the table.
STOP_FLOOR_PERCENT = 30                # the run stops when free memory stays under this for 10 s (mirofish_native_run.py STOP_FREE_PERCENT)
KV_VALUE_BYTES = 2                     # 16-bit floats
START_ORDER = ('floor', 'margin', 'setup_cost', 'fall')
START_TERMS = {
    'floor': {'value': 30, 'source': 'the reviewed stop floor: mirofish_native_run.py STOP_FREE_PERCENT (W16 review, 2026-11-06), kept as the sustained floor by TMF5 addendum 1 ruling d (2026-11-17)'},
    'margin': {'value': 10, 'source': 'TMF5 addendum 1 ruling e (.foreman/tickets/TMF5-addendum-1.md, 2026-11-17)'},
    'setup_cost': {'value': 24, 'source': 'TMF5 Step 0 (2026-11-16): loading Bonsai 2 (7.7 GiB) cost 17 to 24 points of free memory in the recorded runs valedia-native-20261115-swarm8-b2q8-b/c/d and swarm48-b2q8-a (host-resource samples.jsonl); the largest is used'},
    'fall': {'value': 10, 'source': 'TMF5 Step 0 (2026-11-16): beside the collector the reading fell another 10 points over eight minutes after the load, same recorded runs'},
}
REVIEWED_START_THRESHOLD = 65          # the reviewed start rule (mirofish_native_run.py:380). The default path (no model named)
                                       # keeps it and its bytes; whenever a model is named the formula is used (72 for the 27B)


def kv_cache_bytes(model):
    """The model's key and value cache for the whole 32,768 window in 16-bit floats: 2 x full-attention layers x key/value
    heads x head size x window x 2 bytes."""
    require_model(model)
    m = MODELS[model]
    return 2 * m['attention_layers'] * m['kv_heads'] * m['head_dim'] * WINDOW * KV_VALUE_BYTES


def start_threshold(model, serving_bytes=None):
    """Free memory, percent, a run needs before it starts: the sum of START_TERMS (ruling e), the same for every model named
    (the measured setup cost covers the load). serving_bytes is accepted and not used. The sustained 30% stop floor and the
    40 GiB resident-memory bound are not derived here and do not change."""
    require_model(model)
    return start_terms()['total']


def start_terms():
    """The table checked (every named term an integer of at least 0 with a source string) and its total."""
    if tuple(START_TERMS) != START_ORDER or any(type(START_TERMS[k].get('value')) is not int or START_TERMS[k]['value'] < 0
                                                or type(START_TERMS[k].get('source')) is not str or not START_TERMS[k]['source'] for k in START_ORDER):
        raise ValueError('the start table must name floor, margin, setup_cost and fall, each an integer with a source')
    if START_TERMS['floor']['value'] != STOP_FLOOR_PERCENT:
        raise ValueError('the start table floor must be the stop floor')
    total = sum(START_TERMS[k]['value'] for k in START_ORDER)
    if not STOP_FLOOR_PERCENT < total <= 100:
        raise ValueError('the start threshold must lie above the stop floor and at most 100')
    return dict({k: dict(START_TERMS[k]) for k in START_ORDER}, total=total,
                arithmetic=' + '.join('%s %d' % (k, START_TERMS[k]['value']) for k in START_ORDER) + ' = %d' % total)


def plan_start_threshold(members):
    """members: {profile: serving bytes}. A plan loads one model at a time, so it needs the larger member's threshold."""
    return max(start_threshold(name, size) for name, size in members.items())


# ---- Fixed by the reviewed code (not tunable here) ----------------------------------------------------------------------
PLATFORMS = 2                 # twitter and reddit run side by side (run_parallel_simulation.py, platform 'parallel')
CALLER_IN_FLIGHT = 1          # caller calls are serial: parallel_profile_count 1 (mirofish_native_run.py:317), one report thread
NATIVE_PROBE_OUTPUT = 512     # the two compatibility probes reserve 512 each (gateway ledger of every run)
START_PROBES = 2              # the two compatibility probes at the run's start
PROBES_PER_SWAP = 2           # TMF4 addendum 1, ruling d: the same two probes on the newly loaded model after each swap
SWAPS_PER_PLAN = 2            # B1 (setup -> turns, before the simulation) and B2 (turns -> setup, before the report)
SWAP_UNLOAD_LIMIT_S = 45      # the observer's own limit on the unload call of a swap (mirofish_native_run.py swap_model)
SWAP_LOAD_LIMIT_S = 180       # and on its load call
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
SWAP_EXPECTED_S = 15              # one swap (unload, load, two probes): ASSUMED equal to the start's load and probes; no swap has run
PROBE_PROMPT_TOKENS = 512         # a compatibility probe's prompt with its one tool, rounded up (each request is a few lines)


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


def call_seconds(prompt_real, reply, model=DEFAULT_MODEL):
    speed = MODELS[model]
    return prompt_real / speed['read_tps'] + reply / speed['write_tps']


def phase_scale(model=DEFAULT_MODEL):
    """Setup and report phase times were measured on the 27B and are mostly writing (personas, ontology, config, report
    text), so another model's are scaled by the ratio of written tokens a second."""
    return MODELS[DEFAULT_MODEL]['write_tps'] / MODELS[model]['write_tps']


def expected_round_seconds(budget, round_number, model=DEFAULT_MODEL):
    """Expected time of one agent call in round 1, 2, 3...: persona plus every feed so far, capped by the memory budget."""
    record = AGENT_REPLY_MEAN + TOOL_RESULT_LIBRARY
    library = min(budget, PERSONA_LIBRARY_MEDIAN + round_number * (FEED_LIBRARY_MEDIAN + record))
    prompt = RATIO_MEASURED * library + (OVERHEAD_LARGE + OVERHEAD_SMALL) / 2
    return call_seconds(prompt, AGENT_REPLY_MEAN, model)


def ceiling_call_seconds(budget, model=DEFAULT_MODEL):
    """One agent call with its memory full at the assumed ratio on the larger platform, at the mean reply. The prompt is
    counted in the 27B's tokens, which a smaller model of this family does not exceed."""
    return call_seconds(RATIO_ASSUMED * budget + OVERHEAD_LARGE, AGENT_REPLY_MEAN, model)


def probe_ceiling_seconds(model=DEFAULT_MODEL):
    """One compatibility probe at most: its prompt read, its whole 512-token reservation written, plus the largest overshoot."""
    return call_seconds(PROBE_PROMPT_TOKENS, NATIVE_PROBE_OUTPUT, model) + OVERSHOOT_S


def swap_ceiling_seconds(loaded):
    """One swap at most: the observer's unload and load limits, then the two probes on the model just loaded."""
    return SWAP_UNLOAD_LIMIT_S + SWAP_LOAD_LIMIT_S + PROBES_PER_SWAP * probe_ceiling_seconds(loaded)


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


def plan_members(model):
    """A model is one profile name (one model for the whole run) or a plan {'setup': name, 'turns': name} of two different
    profiles (TMF4). Returns (setup model, turns model, swaps)."""
    if isinstance(model, str):
        require_model(model)
        return model, model, 0
    if not (isinstance(model, dict) and set(model) == {'setup', 'turns'} and model['setup'] != model['turns']):
        raise ValueError('a plan names two different models as {"setup": ..., "turns": ...}: %r' % (model,))
    require_model(model['setup'])
    require_model(model['turns'])
    return model['setup'], model['turns'], SWAPS_PER_PLAN


def model_label(model):
    setup, turns, swaps = plan_members(model)
    return setup if not swaps else '%s (setup and report) + %s (turns)' % (setup, turns)


def derive(profile, model=DEFAULT_MODEL):
    """All numbers for one swarm profile from its closed inputs: actors, agents_bound, rounds, memory_budget. Expected
    figures use the named actors times the measured growth; ceilings, the wall and every budget use the participant bound,
    so they hold for anything the bound admits. Limits use the 27B's constants; expected durations and the wall use the
    chosen model's speeds (TMF3), and in a plan each phase's model (TMF4): setup and report the setup model's, the
    simulation the turns model's, plus the two swaps and the probes after each."""
    setup_model, turns_model, swaps = plan_members(model)
    actors, rounds, budget, bound = profile['actors'], profile['rounds'], profile['memory_budget'], profile['agents_bound']
    agents = math.ceil(actors * GROWTH - 1e-9)
    scale = phase_scale(setup_model)
    out = {'expected_agents': agents, 'model': model_label(model), 'read_tps': round(MODELS[turns_model]['read_tps'], 1),
           'write_tps': round(MODELS[turns_model]['write_tps'], 1), 'phase_scale': round(scale, 3),
           'token_ratio_to_27b': max(MODELS[setup_model]['token_ratio_to_27b'], MODELS[turns_model]['token_ratio_to_27b'])}
    if swaps:  # a plan's own fields; one model keeps exactly the fields it had before TMF4
        out.update(setup_model=setup_model, turns_model=turns_model, swaps=swaps,
                   setup_read_tps=round(MODELS[setup_model]['read_tps'], 1), setup_write_tps=round(MODELS[setup_model]['write_tps'], 1))
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
    # Model calls and output. A plan adds the two probes after each of its two swaps (TMF4 addendum 1, ruling d): four chats,
    # each reserving 512 output tokens like the two at the start.
    probes = START_PROBES + PROBES_PER_SWAP * swaps
    swap_chats = PROBES_PER_SWAP * swaps
    turns = rounds * agents * PLATFORMS
    sim_chats = math.ceil(turns * CALLS_PER_TURN)
    setup_chats = SETUP_FIXED_CHATS + agents
    expected_chats = setup_chats + sim_chats + REPORT_CHATS + swap_chats
    bound_turns = rounds * bound * PLATFORMS
    bound_sim_chats = math.ceil(bound_turns * CALLS_PER_TURN)
    bound_chats = SETUP_FIXED_CHATS + bound + bound_sim_chats + REPORT_CHATS + swap_chats
    chats = ceil_to(max(CHAT_MARGIN * expected_chats, bound_chats), 32)
    reserve = probes * NATIVE_PROBE_OUTPUT + (chats - probes) * max(CALLER_REPLY_CAP, AGENT_REPLY_CAP)
    out.update(turns=turns, sim_chats=sim_chats, setup_chats=setup_chats, report_chats=REPORT_CHATS, expected_chats=expected_chats,
               bound_turns=bound_turns, bound_sim_chats=bound_sim_chats, bound_chats=bound_chats,
               chats=chats, reserve_need=reserve, output=ceil_to(reserve, OUTPUT_STEP))
    if swaps:
        out.update(swap_chats=swap_chats, probe_chats=probes)
    # Durations by phase, and the wall clock.
    setup_s = (SETUP_GRAPH_S + (agents + SETUP_OTHER_CALLER_CALLS) * SETUP_CALLER_CALL_S) * scale
    ceiling_setup = (SETUP_GRAPH_S + (bound + SETUP_OTHER_CALLER_CALLS) * SETUP_CALLER_CALL_S) * scale
    per_round = turns / rounds * CALLS_PER_TURN
    expected_rounds = [per_round * expected_round_seconds(budget, r, turns_model) for r in range(1, rounds + 1)]
    ceiling_sim = bound_sim_chats * ceiling_call_seconds(budget, turns_model)
    report_s = REPORT_MEASURED_S * REPORT_SCALE_ASSUMED * scale
    swap_s = SWAP_EXPECTED_S * swaps
    ceiling_swaps = (swap_ceiling_seconds(turns_model) + swap_ceiling_seconds(setup_model)) if swaps else 0
    expected_total = START_S + setup_s + sum(expected_rounds) + swap_s + report_s + CLEANUP_S
    ceiling_total = START_S + ceiling_setup + ceiling_sim + ceiling_swaps + report_s + CLEANUP_S
    wall = ceil_to(DURATION_MARGIN * ceiling_total, WALL_STEP_S)
    out.update(setup_s=round(setup_s), ceiling_setup_s=round(ceiling_setup), expected_round_s=[round(x) for x in expected_rounds], ceiling_sim_s=round(ceiling_sim),
               report_s=round(report_s), expected_total_s=round(expected_total), ceiling_total_s=round(ceiling_total), wall=wall,
               worker_deadline_s=wall - CLEANUP_S)
    if swaps:
        out.update(swap_s=round(swap_s), ceiling_swaps_s=round(ceiling_swaps))
    records = int(wall / HOST_SAMPLE_S) + 1
    out.update(history_records=records, history_mib=ceil_to(records * HISTORY_RECORD_BYTES * 1.5 / 2**20, 32))
    return out


def require_model(model):
    if model not in MODELS:
        raise ValueError('no measured limits for model %r; known: %s' % (model, sorted(MODELS)))


def violations(profile, d=None, model=DEFAULT_MODEL):
    """Every coupling inequality; an empty list means the profile is consistent. Each entry names the broken inequality."""
    d = d or derive(profile, model)
    swaps = d.get('swaps', 0)
    members = (d.get('setup_model', d['model']), d.get('turns_model', d['model']))
    probes = START_PROBES + PROBES_PER_SWAP * swaps
    checks = [
        ('memory budget fits the window at the assumed ratio: %.2f B + 1750 + 1024 <= 32768' % RATIO_ASSUMED, d['window_need'] <= WINDOW),
        ('the chosen model needs no more real tokens than the 27B the window was measured on', all(MODELS[m]['token_ratio_to_27b'] <= 1.0 for m in members)),
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
        ('model calls allowed count the probes after each swap of a plan', d.get('swap_chats', 0) == PROBES_PER_SWAP * swaps and d.get('probe_chats', START_PROBES) == probes
                                                                            and d['bound_chats'] >= PROBES_PER_SWAP * swaps),
        ('output budget lets every admitted call reserve its cap', d['output'] >= d['reserve_need'] >= probes * NATIVE_PROBE_OUTPUT + (d['chats'] - probes) * CALLER_REPLY_CAP),
        ('participant bound covers the named participants and the measured growth', profile['agents_bound'] >= max(profile['actors'], d['expected_agents'])),
        ('wall covers the ceiling at the participant bound with its margin', d['wall'] >= DURATION_MARGIN * d['ceiling_total_s']),
        ('host history covers the wall at one sample each 0.4 s', d['history_records'] >= int(d['wall'] / HOST_SAMPLE_S) + 1),
        ('host history bytes cover its records at 1.5 x the measured size', d['history_mib'] * 2**20 >= d['history_records'] * HISTORY_RECORD_BYTES * 1.5),
        ('worker deadline keeps the cleanup headroom', d['worker_deadline_s'] == d['wall'] - CLEANUP_S),
    ]
    return [name for name, ok in checks if not ok]
