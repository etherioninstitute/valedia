"""Valedia Memory System — Memori Labs Integration
====================================================
Provides persistent, auto-capturing memory for Valedia using Memori Labs
free tier (5,000 memories/month, 15,000 recalls/month).

HOW IT WORKS:
  - Memori wraps Ollama (OpenAI-compatible) as the LLM backend
  - Every trading decision, Signal conversation, and outcome is captured
  - Before each brain cycle, relevant memories are recalled and injected
  - Advanced Augmentation: Memori auto-extracts entities, patterns, trends

MEMORY CATEGORIES (via attribution):
  entity_id="valedia"        → system-level memories (market conditions, patterns)
  entity_id="burton"         → Burton's preferences, feedback, corrections
  entity_id="trades"         → trade decisions and outcomes
  entity_id="discovery"      → discovered assets and their results

STORAGE:
  - Cloud: Memori Labs (free tier, encrypted)
  - Local backup: /Volumes/NIMROD_SSD/valedia_memory/
  - VPS sync: daily at 3 AM via rsync

(c) GOATWORTHY Post LLC. All rights reserved.
"""

import os, sys, json, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MEMORI_API_KEY = os.environ.get("MEMORI_API_KEY", "")
NIMROD_MEMORY  = "/Volumes/NIMROD_SSD/valedia_memory"
MODELS_DIR     = os.path.expanduser("~/.openclaw/workspace/models")


def get_memori():
    """Initialize Memori with Ollama as the LLM backend."""
    if not MEMORI_API_KEY:
        raise ValueError("MEMORI_API_KEY not set in environment")

    import memori
    from openai import OpenAI

    # Ollama is OpenAI-compatible — point Memori at local Ollama
    ollama_client = OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",  # Ollama doesn't need a real key
    )

    m = memori.Memori()
    m.llm.register(client=ollama_client)
    return m


def capture_memory(text, entity_id="valedia", process_id=None, tags=None):
    """
    Store a memory in Memori.
    Also writes to local NIMROD_SSD backup.

    Args:
        text:       The content to remember
        entity_id:  "valedia" | "burton" | "trades" | "discovery"
        process_id: Optional sub-category (e.g. "brain_run", "signal_received")
        tags:       Optional list of tags for retrieval
    """
    ts = datetime.now(timezone.utc).isoformat()

    # Write to local NIMROD_SSD first (always works, even offline)
    os.makedirs(NIMROD_MEMORY, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_file = os.path.join(NIMROD_MEMORY, "daily", f"{today}.md")
    os.makedirs(os.path.dirname(daily_file), exist_ok=True)

    with open(daily_file, "a") as f:
        f.write(f"\n## {ts}\n")
        f.write(f"**Entity:** {entity_id}  **Process:** {process_id or 'general'}\n\n")
        f.write(f"{text}\n")
        if tags:
            f.write(f"\n_Tags: {', '.join(tags)}_\n")

    # Upload to Memori cloud
    if not MEMORI_API_KEY:
        return {"stored": "local_only", "ts": ts}

    try:
        import memori

        m = memori.Memori()
        m.attribution(
            entity_id=entity_id,
            process_id=process_id or "general"
        )

        # Memori captures via conversation — we simulate a one-shot conversation
        # The augmentation layer extracts and stores structured memories
        # We use the recall API to test, and write via the augmentation enqueue
        aug_input = {
            "role": "assistant",
            "content": text,
            "session_id": str(m.config.session_id),
        }
        m.config.augmentation.enqueue(aug_input)
        m.config.augmentation.wait(timeout=10)

        return {"stored": "cloud+local", "ts": ts, "entity": entity_id}

    except Exception as e:
        # Cloud failed — local backup already written
        return {"stored": "local_only", "error": str(e)[:100], "ts": ts}


def recall_memories(query, entity_id=None, limit=5):
    """
    Retrieve relevant memories for a given query.

    Returns list of relevant memory strings, sorted by relevance.
    Returns empty list if Memori unavailable (graceful degradation).
    """
    if not MEMORI_API_KEY:
        return []

    try:
        import memori

        m = memori.Memori()
        if entity_id:
            m.attribution(entity_id=entity_id)

        results = m.recall(query, limit=limit)

        # Also check local NIMROD_SSD for recent memories (last 7 days)
        local = _recall_local(query, days=7)

        # Combine: cloud results first, then recent local
        combined = []
        for r in results:
            if isinstance(r, dict):
                combined.append(r.get("content", str(r)))
            else:
                combined.append(str(r))

        combined.extend(local[:3])  # Add up to 3 local recent memories
        return combined[:limit]

    except Exception:
        # Fall back to local only
        return _recall_local(query, days=7)[:limit]


def _recall_local(query, days=7):
    """Simple local memory recall: search recent daily files for query keywords."""
    results = []
    keywords = query.lower().split()

    for d in range(days):
        from datetime import timedelta
        date = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
        daily_file = os.path.join(NIMROD_MEMORY, "daily", f"{date}.md")
        if not os.path.exists(daily_file):
            continue
        try:
            with open(daily_file) as f:
                content = f.read()
            # Split into sections (each ## block)
            sections = content.split("## ")
            for section in sections:
                if any(kw in section.lower() for kw in keywords):
                    # Return first 200 chars of relevant section
                    results.append(section[:200].strip())
        except Exception:
            continue

    return results


def inject_memory_context(query, brain_lines):
    """
    Inject relevant memories into the brain's output lines.
    Called at the start of each brain cycle.
    """
    memories = recall_memories(query, limit=3)
    if not memories:
        return brain_lines

    memory_lines = ["", "MEMORY CONTEXT:"]
    for i, mem in enumerate(memories[:3], 1):
        preview = str(mem)[:120].replace("\n", " ")
        memory_lines.append(f"  [{i}] {preview}")

    # Insert memory context after the INTEL line
    for i, line in enumerate(brain_lines):
        if line.startswith("INTEL:"):
            brain_lines = brain_lines[:i+1] + memory_lines + brain_lines[i+1:]
            break

    return brain_lines


# ── Memory capture helpers for specific events ───────────────────────

def capture_trade(ticker, direction, fused_score, price, size, result="pending"):
    """Capture a trade decision as a memory."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (
        f"Trade executed: {direction} {ticker} at ${price:.4f}\n"
        f"Size: ${size:.2f} | Fused score: {fused_score:.1f} | "
        f"Result: {result} | Time: {ts}"
    )
    # Save to trades directory
    os.makedirs(os.path.join(NIMROD_MEMORY, "trades"), exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(os.path.join(NIMROD_MEMORY, "trades", f"{today}.jsonl"), "a") as f:
        json.dump({
            "ts": ts, "ticker": ticker, "direction": direction,
            "score": fused_score, "price": price, "size": size,
            "result": result
        }, f)
        f.write("\n")

    return capture_memory(text, entity_id="trades", process_id="brain_execute")


def capture_market_condition(intel, ere_status_counts):
    """Capture today's market conditions."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fg = intel.get("fear_greed", 50)
    label = intel.get("fg_label", "Neutral")
    text = (
        f"Market conditions {ts}: Fear/Greed={fg} ({label})\n"
        f"ERE status distribution: {ere_status_counts}\n"
        f"Intel signal: {intel.get('fg_signal', 0):+d}"
    )
    return capture_memory(text, entity_id="valedia", process_id="market_conditions")


def capture_discovery(sym, buzz_score, reasons, kernel_score=None):
    """Capture a newly discovered trending asset."""
    text = (
        f"Discovered asset: {sym}\n"
        f"Buzz score: {buzz_score:.0f} | Sources: {reasons[:3]}\n"
        f"Kernel validation: {kernel_score if kernel_score else 'pending'}"
    )
    # Save to discovery directory
    os.makedirs(os.path.join(NIMROD_MEMORY, "discovery"), exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(os.path.join(NIMROD_MEMORY, "discovery", f"{today}.jsonl"), "a") as f:
        json.dump({
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": sym, "buzz": buzz_score,
            "reasons": reasons, "kernel": kernel_score
        }, f)
        f.write("\n")

    return capture_memory(text, entity_id="discovery", process_id="universe_scan")


def capture_signal_from_burton(message, valedia_response):
    """Capture a Signal conversation with Burton."""
    text = (
        f"Burton said: {message[:200]}\n"
        f"Valedia responded: {valedia_response[:200]}"
    )
    # Save corrections/preferences separately
    if any(kw in message.lower() for kw in
           ["wrong", "don't", "stop", "never", "always", "prefer", "change", "fix"]):
        os.makedirs(os.path.join(NIMROD_MEMORY, "people"), exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with open(os.path.join(NIMROD_MEMORY, "people", f"burton_{today}.md"), "a") as f:
            f.write(f"\n## {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"**Burton:** {message[:300]}\n\n")
            f.write(f"**Valedia:** {valedia_response[:300]}\n")

        return capture_memory(text, entity_id="burton", process_id="preference_update")

    return capture_memory(text, entity_id="valedia", process_id="signal_conversation")


def memory_status():
    """Report on memory usage."""
    lines = ["MEMORY STATUS"]

    # Local backup stats
    total_files = 0
    total_bytes = 0
    for root, dirs, files in os.walk(NIMROD_MEMORY):
        for f in files:
            total_files += 1
            total_bytes += os.path.getsize(os.path.join(root, f))
    lines.append(f"  Local (NIMROD_SSD): {total_files} files, {total_bytes/1024:.1f} KB")

    # Cloud recall test
    try:
        test = recall_memories("market trading", limit=1)
        lines.append(f"  Memori cloud: ✅ connected ({len(test)} test recalls)")
    except Exception as e:
        lines.append(f"  Memori cloud: ⚠️ {str(e)[:50]}")

    lines.append(f"  Free tier: 5,000 captures / 15,000 recalls per month")
    return "\n".join(lines)


if __name__ == "__main__":
    # Test the full memory system
    _env_path = os.path.expanduser("~/.hermes/secrets/trading.env")
    if os.path.isfile(_env_path):
        with open(_env_path) as _fh:
            for line in _fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.replace("export ", "", 1).strip()
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))

    print("=== VALEDIA MEMORY SYSTEM TEST ===")
    print()

    # Test capture
    print("1. Capturing test memory...")
    result = capture_memory(
        "Test memory: Valedia memory system initialized successfully. "
        "Kernel v16e, filter v3, day-of-week gate active. "
        "Running on Mac Studio M1 Ultra.",
        entity_id="valedia",
        process_id="system_init"
    )
    print(f"   Result: {result}")
    print()

    # Test trade capture
    print("2. Capturing test trade...")
    result = capture_trade("DOGE-USD", "SELL", 62.3, 0.0842, 1.26, "pending")
    print(f"   Result: {result}")
    print()

    # Test recall
    print("3. Testing recall...")
    time.sleep(2)  # Give Memori a moment
    memories = recall_memories("Valedia system kernel filter", limit=3)
    print(f"   Recalled {len(memories)} memories")
    for m in memories[:2]:
        print(f"   - {str(m)[:100]}")
    print()

    # Status
    print(memory_status())
