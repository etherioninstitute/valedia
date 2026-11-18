-- The leaders' record's store schema as it was at commit c106abe (T52 first build), kept as a fixture so that
-- a test can make a store of that older shape without reading any commit. Do not edit: it is history.
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leaders (wallet_key TEXT PRIMARY KEY, wallet TEXT NOT NULL,
    sports_rank INTEGER, sports_pnl REAL, sports_volume REAL, sports_read_ts REAL,
    crypto_rank INTEGER, crypto_pnl REAL, crypto_volume REAL, crypto_read_ts REAL, fixed_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS passes (pass_id INTEGER PRIMARY KEY AUTOINCREMENT, started_ts REAL NOT NULL,
    finished_ts REAL, status TEXT NOT NULL, good INTEGER, failed INTEGER, removed INTEGER);
CREATE INDEX IF NOT EXISTS passes_ts ON passes (started_ts);
CREATE TABLE IF NOT EXISTS wallet_reads (read_id INTEGER PRIMARY KEY AUTOINCREMENT, pass_id INTEGER,
    wallet_key TEXT NOT NULL, good INTEGER NOT NULL, reason TEXT, detail TEXT, rows INTEGER, pages INTEGER,
    rejects TEXT, unread TEXT, baseline INTEGER, prev_read_id INTEGER, prev_read_ts REAL, gap REAL,
    n_new INTEGER, n_increase INTEGER, n_decrease INTEGER, n_exit INTEGER, read_ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS wallet_reads_wallet ON wallet_reads (wallet_key, good, read_id);
CREATE INDEX IF NOT EXISTS wallet_reads_ts ON wallet_reads (read_ts);
CREATE TABLE IF NOT EXISTS holdings_current (wallet_key TEXT NOT NULL, token_id TEXT NOT NULL,
    read_id INTEGER NOT NULL, condition_id TEXT, opposite_token_id TEXT, outcome_index INTEGER, size REAL NOT NULL, avg_price REAL,
    current_value REAL, first_entry_at INTEGER, last_event_at INTEGER, title TEXT, outcome TEXT, end_date TEXT,
    current_price REAL, event_slug TEXT, PRIMARY KEY (wallet_key, token_id));
CREATE TABLE IF NOT EXISTS snapshots (read_id INTEGER NOT NULL, wallet_key TEXT NOT NULL, token_id TEXT NOT NULL,
    kind TEXT NOT NULL, utc_day TEXT NOT NULL, read_ts REAL NOT NULL, condition_id TEXT, opposite_token_id TEXT, outcome_index INTEGER, size REAL NOT NULL, avg_price REAL,
    current_value REAL, first_entry_at INTEGER, last_event_at INTEGER, title TEXT, outcome TEXT, end_date TEXT,
    current_price REAL, event_slug TEXT);
CREATE INDEX IF NOT EXISTS snapshots_day ON snapshots (wallet_key, utc_day);
CREATE INDEX IF NOT EXISTS snapshots_ts ON snapshots (read_ts);
CREATE TABLE IF NOT EXISTS changes (change_id INTEGER PRIMARY KEY AUTOINCREMENT, read_id INTEGER NOT NULL,
    wallet_key TEXT NOT NULL, token_id TEXT NOT NULL, kind TEXT NOT NULL, flip INTEGER NOT NULL,
    size_before REAL NOT NULL, size_after REAL NOT NULL, first_entry_at INTEGER, read_ts REAL NOT NULL,
    prev_read_ts REAL, gap REAL);
CREATE INDEX IF NOT EXISTS changes_ts ON changes (read_ts);
CREATE INDEX IF NOT EXISTS changes_read ON changes (read_id);
CREATE TABLE IF NOT EXISTS rejects (read_id INTEGER, wallet_key TEXT, reason TEXT, raw TEXT, read_ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS rejects_ts ON rejects (read_ts);
