"""Isolated OASIS agent memory, bounded by construction; preserve the provider output cap.

Reviewed replacement (TMF2 addendum 1, 2026-11-15) for the producer's owned_context_budget.py in swarm profiles only. The
owner asked on 2026-11-15 for the 2,048-token agent cap to go; the design and the numbers are the lead's. The canonical file
gave every agent a ScoreBasedContextCreator at 2,048 tokens. With a persona of 1,100 to 1,700 tokens in that budget the
agent library cut each feed into one-token chunks ("Slicing into smaller chunks") and then dropped all but the last few
dozen ("Context truncation performed"), so agents were no longer reading their input; when truncation dropped the user
message between two assistant messages, the template admission refused the call and the attempt died.

This file keeps the canonical guards and replaces only the context creator:
- P1 nothing is ever sliced: the library slices a message only when it is larger than token_limit minus the current
  context, and this creator reports a token_limit that no message can reach. The real budget is enforced below.
- P2 the context is the system message and a run of WHOLE units, newest always included. A unit is one user message and
  everything after it up to the next user message. Older units are dropped whole, oldest first, until the rest fits.
- P3 never two assistant messages in a row, and never a tool result without its call; records that would force either
  raise OwnedMemoryShapeError instead.
- P4 if the system message and the newest unit alone exceed the budget, the newest user message is trimmed from its end,
  with one plain last line saying how much was kept, and one WARNING line. Nothing else is ever trimmed.
- P5 the library-token total given to the model never exceeds the budget. The library counts an assistant message's tool
  calls as nothing (a few tokens whatever the arguments' length), so this file adds the tool calls' own text, encoded by the
  same counter (its UTF-8 byte length, an upper bound for a byte-level tokenizer, if the counter cannot encode it): the
  budget is a bound on real content, tool-call arguments included.
- P6 the same records always give the same context.
Every refusal (OwnedMemoryShapeError or OwnedMemoryBudgetError) also writes one WARNING line with a fixed phrase and the role of
the newest record, because the agent library ends such a turn without a line of its own.

Only the copied owned runner calls this, before starting either platform environment. Version and shape guards
intentionally fail closed if CAMEL/OASIS changes.
"""
from importlib.metadata import version
import json
import logging
import os
import sys

from camel.memories import ChatHistoryMemory, ScoreBasedContextCreator
from camel.memories.base import BaseContextCreator
from camel.types import OpenAIBackendRole


INPUT_CONTEXT_TOKENS = 2048
OUTPUT_COMPLETION_TOKENS = 512
# The gateway refuses a chat call with more messages than this (validate_chat); whole units are dropped to stay under it.
CONTEXT_MESSAGES = 128
# The library compares a new message with token_limit minus the current context and slices it if it is larger. This value
# can never be reached, so the library always stores a message as one record; the budget is enforced by create_context.
NO_SLICING_LIMIT = sys.maxsize

DROPPED_PHRASE = 'Owned memory dropped whole units'
TRIMMED_PHRASE = 'Owned memory trimmed the newest user message'
REFUSED_PHRASE = 'Owned memory refused the context'

# The simulation's camel lines reach simulation.log through the root handler; camel's own level is WARNING, so the INFO
# line for dropped units needs this logger's level set explicitly.
logger = logging.getLogger('camel.owned_agent_memory')
logger.setLevel(logging.INFO)


class OwnedMemoryShapeError(RuntimeError):
    """The stored records cannot be given to the model without breaking the chat shape. args[1] is a token count, as the
    agent library expects of a context error, so a turn that hits this ends instead of being sent."""


class OwnedMemoryBudgetError(RuntimeError):
    """The system message and the newest unit cannot fit the budget even with the newest user message trimmed to nothing."""


def _role(message):
    return message.get('role')


class WholeUnitContextCreator(BaseContextCreator):
    def __init__(self, token_counter, budget):
        if type(budget) is not int or budget < 1:
            raise RuntimeError('Owned memory budget must be a positive integer')
        self._token_counter = token_counter
        self.budget = budget
        self._logged = {}

    @property
    def token_counter(self):
        return self._token_counter

    @property
    def token_limit(self):
        return NO_SLICING_LIMIT

    def _count(self, message):
        tokens = self._token_counter.count_tokens_from_messages([message])
        calls = message.get('tool_calls')
        if calls:
            # The library's count ignores this list; count the calls' own text (ids, names and arguments) here.
            text = json.dumps(calls, ensure_ascii=False, sort_keys=True)
            try:
                tokens += len(self._token_counter.encode(text))
            except Exception:
                tokens += len(text.encode('utf-8'))
        return tokens

    def _log_once(self, kind, key, level, text, *args):
        # get_context runs several times a turn while the turn's records are written; log once per newest unit, with the
        # numbers of the first time it happened, so a finished run's log counts turns that dropped or trimmed.
        if self._logged.get(kind) != key:
            self._logged[kind] = key
            logger.log(level, text, *args)

    def create_context(self, records):
        try:
            return self._create_context(records)
        except (OwnedMemoryShapeError, OwnedMemoryBudgetError) as exc:
            logger.warning(REFUSED_PHRASE + ': %s; newest record role=%s', exc.args[0], self._newest_role(records))
            raise

    @staticmethod
    def _newest_role(records):
        try:
            newest = max(enumerate(records), key=lambda item: (item[1].timestamp, item[0]))[1]
            return newest.memory_record.to_openai_message().get('role')
        except Exception:
            return 'unknown'

    def _create_context(self, records):
        if not records:
            return [], 0
        system = None
        if records[0].memory_record.role_at_backend == OpenAIBackendRole.SYSTEM:
            system = records[0]
        # Chronological order (timestamp, then storage order), as the library assembles it; later system records are
        # skipped exactly as the library's own creator skips them.
        body = sorted(((record.timestamp, index, record) for index, record in enumerate(records)
                       if index > 0 or system is None), key=lambda item: (item[0], item[1]))
        body = [record for _, _, record in body if record.memory_record.role_at_backend != OpenAIBackendRole.SYSTEM]
        system_message = system.memory_record.to_openai_message() if system is not None else None
        system_tokens = self._count(system_message) if system is not None else 0
        if system_tokens > self.budget:
            raise OwnedMemoryBudgetError('Owned memory: the system message alone exceeds the budget', system_tokens)

        units = []
        for record in body:
            if record.memory_record.role_at_backend == OpenAIBackendRole.USER or not units:
                units.append([])
            message = record.memory_record.to_openai_message()
            units[-1].append((record, message, self._count(message)))

        kept, used, messages = [], system_tokens, 1 if system is not None else 0
        for unit in reversed(units):
            tokens = sum(count for _, _, count in unit)
            if kept and (used + tokens > self.budget or messages + len(unit) > CONTEXT_MESSAGES):
                break
            kept.insert(0, unit)
            used += tokens
            messages += len(unit)
        dropped = units[:len(units) - len(kept)]
        newest = kept[-1] if kept else []
        if messages > CONTEXT_MESSAGES:
            raise OwnedMemoryShapeError('Owned memory: the newest unit alone has more messages than the gateway admits', used)

        output = [system_message] if system is not None else []
        for unit in kept[:-1]:
            output.extend(message for _, message, _ in unit)
        if newest and used > self.budget:
            output.extend(self._trim_newest(newest, used, system_tokens))
        else:
            output.extend(message for _, message, _ in newest)
        total = sum(self._count(message) for message in output)
        if total > self.budget:
            raise OwnedMemoryBudgetError('Owned memory: context exceeds the budget after assembly', total)
        if dropped:
            self._log_once('dropped', newest[0][0].memory_record.uuid if newest else None, logging.INFO,
                           DROPPED_PHRASE + ': dropped=%d units, %d tokens; kept=%d units, %d tokens; budget=%d',
                           len(dropped), sum(count for unit in dropped for _, _, count in unit), len(kept), total, self.budget)
        self._check_shape(output, total)
        return output, total

    def _trim_newest(self, unit, used, system_tokens):
        record, message, count = unit[0]
        if record.memory_record.role_at_backend != OpenAIBackendRole.USER or not isinstance(record.memory_record.message.content, str):
            raise OwnedMemoryBudgetError('Owned memory: the newest unit exceeds the budget and has no user message to trim', used)
        rest = [m for _, m, _ in unit[1:]]
        room = self.budget - system_tokens - sum(c for _, _, c in unit[1:])
        original = record.memory_record.message.content
        ids = self._token_counter.encode(original)

        def build(keep):
            text = self._token_counter.decode(ids[:keep]) + (
                '\n[Owned memory budget: this message was cut here; %d of %d tokens kept.]' % (keep, len(ids)))
            trimmed = record.memory_record.message.create_new_instance(text)
            return trimmed.to_openai_message(OpenAIBackendRole.USER)

        low, high = 0, len(ids)
        if self._count(build(0)) > room:
            raise OwnedMemoryBudgetError('Owned memory: the newest unit cannot fit even with its user message trimmed to nothing', used)
        while low < high:
            middle = (low + high + 1) // 2
            if self._count(build(middle)) <= room:
                low = middle
            else:
                high = middle - 1
        trimmed = build(low)
        self._log_once('trimmed', record.memory_record.uuid, logging.WARNING,
                       TRIMMED_PHRASE + ': kept=%d tokens of %d; budget=%d', low, len(ids), self.budget)
        return [trimmed] + rest

    @staticmethod
    def _check_shape(output, total):
        calls = set()
        for previous, message in zip([None] + output[:-1], output):
            if previous is not None and _role(previous) == 'assistant' and _role(message) == 'assistant':
                raise OwnedMemoryShapeError('Owned memory: two assistant messages in a row', total)
            for call in message.get('tool_calls') or []:
                calls.add(call.get('id'))
            if _role(message) == 'tool' and message.get('tool_call_id') not in calls:
                raise OwnedMemoryShapeError('Owned memory: a tool result without its call', total)


def admit_owned_agent_context(graph, model, platform):
    if os.environ.get('MIROFISH_TEST_ISOLATED') != '1':
        raise RuntimeError('Owned context repair requires isolated runner')
    if version('camel-ai') != '0.2.78' or version('camel-oasis') != '0.2.5':
        raise RuntimeError('Unreviewed CAMEL/OASIS version')
    if platform not in ('twitter', 'reddit'):
        raise RuntimeError('Unreviewed platform')
    if (model.model_config_dict.get('max_tokens') != OUTPUT_COMPLETION_TOKENS
            or model.model_config_dict.get('parallel_tool_calls') is not False
            or model.token_limit != OUTPUT_COMPLETION_TOKENS):
        raise RuntimeError('Provider completion cap or model shape drift')

    agents = list(graph.get_agents())
    if not 1 <= len(agents) <= 4:
        raise RuntimeError('Owned agent count outside bound')
    for _, agent in agents:
        if (agent.model_backend.current_model is not model
                or agent.model_backend.model_config_dict.get('max_tokens') != OUTPUT_COMPLETION_TOKENS):
            raise RuntimeError('Generated agent model binding drift')
        memory = agent.memory
        if type(memory) is not ChatHistoryMemory:
            raise RuntimeError('Unreviewed agent memory')
        creator = memory.get_context_creator()
        records = memory.retrieve()
        if (type(creator) is not ScoreBasedContextCreator
                or creator.token_limit != OUTPUT_COMPLETION_TOKENS
                or len(records) != 1
                or records[0].memory_record.role_at_backend != OpenAIBackendRole.SYSTEM):
            raise RuntimeError('Unreviewed agent context initialization')

    # ChatAgent's public memory setter re-adds the same system message. At this
    # pre-environment point there is no user/action history to discard.
    for _, agent in agents:
        agent.memory = ChatHistoryMemory(
            WholeUnitContextCreator(model.token_counter, INPUT_CONTEXT_TOKENS),
            agent_id=agent.agent_id,
        )
        creator = agent.memory.get_context_creator()
        context, tokens = agent.memory.get_context()
        if (type(creator) is not WholeUnitContextCreator
                or creator.budget != INPUT_CONTEXT_TOKENS or creator.token_limit != NO_SLICING_LIMIT
                or len(context) != 1 or not 0 < tokens < INPUT_CONTEXT_TOKENS
                or model.model_config_dict.get('max_tokens') != OUTPUT_COMPLETION_TOKENS):
            raise RuntimeError('Owned context repair verification failed')
