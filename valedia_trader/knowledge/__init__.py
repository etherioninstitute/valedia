"""Valedia's knowledge store: one append-only, bitemporal SQLite file for every knowledge feed.

Feeds write facts with `add_fact`. The only way to read them is `facts_as_of`, which answers
"what did she know at time t". This package fetches nothing and makes no network calls."""
