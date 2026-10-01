# M1 Workhorse Cut Checklist — 2026-09-25 ~03:07 PT
Machine: Mac Studio M1 Ultra 128GB · READ-ONLY inventory · **no cuts applied yet**

Principle: keep the workhorse. Fix reservations and background tax. Do not gut Valedia.

---

## Impact order (do in this sequence)

1. **[HIGHEST RAM]** Reload Twin-Turbo ≤65536×1; lock model default; kill Auto Optimize
2. **[Overnight RAM]** `colima stop` (or shrink 2 CPU / 4 GiB)
3. **[Dual GPU tax]** Quit Ollama.app `:11434`; keep MiroFish Ollama `:11435` only if needed
4. **[Cron tax #6]** Comment 8 discovery/intel lines; keep trading spine + `mirofish_primary` as sole intel
5. **[Clutter]** Bootout Goal UI + Hermes WebUI + Claude bridge; stop Paperclip if idle
6. **[Leave]** OpenClaw, Hermes gateway, signal, tunnels, RuFlo MCP, LMS ensure, LiteLLM
7. **[Later]** `iogpu.wired_limit_mb` 118000 → 81920 (optional; not tonight)
8. **[Design]** Ponytail/RuFlo CronCreate + MiroFish swarm replaces intel spam (not Goal UI autopilot)

---

## Bucket 1 — Models / Bionic

### Goal
Twin-Turbo **262144×4 (~48 GB RSS)** → **≤65536×1 (~26 GB)**. Ornith stays 32768×1.

### CLI
```bash
export PATH="$HOME/.lmstudio/bin:$PATH"
lms unload qwen3.8-27b-twin-turbo-fable-cold-fusion-709-l-uncensored-nm-dau-neo-mtp
lms load qwen3.8-27b-twin-turbo-fable-cold-fusion-709-l-uncensored-nm-dau-neo-mtp \
  -c 65536 --parallel 1 -y
# do NOT pass --auto
```

### Bionic UI
1. Unload twin-turbo
2. Load gear: Context **65536**, Parallel **1**, Auto-fit/Optimize **OFF**
3. **Save as model default** for this concrete model
4. Settings: prefer configure-load-params-before-load ON

### Verify
```bash
lms ps   # CONTEXT ≤65536 PARALLEL 1
ps -axo rss,comm | awk '/llama-server/{printf "%.1f GB %s\n",$1/1024/1024,$2}'
ls ~/.lmstudio/apps/bionic/.internal/user-concrete-model-default-config/ | grep -i twin
```

### Rollback
Reload with `-c 262144 --parallel 4 -y` or re-enable Auto-fit.

---

## Bucket 2 — Docker / Colima

Running: Langfuse stack, MiroFish Neo4j, Tilion fortress. Colima ~4 CPU / 8 GiB.

### Stop for night
```bash
cd ~/langfuse && docker compose stop
cd ~/mirofish-offline && docker compose stop
docker stop tilion-fortress-38880-9222 2>/dev/null
colima stop
# if launchd restarts it:
# launchctl bootout gui/$(id -u) homebrew.mxcl.colima
```

### Or shrink
```bash
colima stop && colima start --cpu 2 --memory 4
```

### Morning
```bash
colima start   # or --cpu 4 --memory 8
cd ~/langfuse && docker compose up -d
cd ~/mirofish-offline && docker compose up -d
```

**Breaks when stopped:** Langfuse, MiroFish graph, Tilion CDP. **Does not break:** Bionic, Hermes, OpenClaw, Valedia crons, Paperclip host.

---

## Bucket 3 — Agents / tunnels / Ollama / Paperclip

### LEAVE loaded
`ai.openclaw.gateway`, `ai.hermes.gateway`, `com.burton.hermes-signal`, `com.burton.ruflo-mcp-http` (if coding), `com.burton.ruvocal`, matrix + VPS tunnels, `com.burton.lms-server`, `com.burton.litellm-gateway`

### UNLOAD candidates
```bash
UID=$(id -u)
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.burton.ruflo-goal-ui.plist
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.parantoux.hermes-webui.plist
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.goatworthy.valedia.claudebridge.plist
osascript -e 'quit app "Ollama"'   # :11434 dual stack
kill $(cat /tmp/paperclip-valedia.pid) 2>/dev/null
```

---

## Bucket 4 — Cron tax (#6)

Backup first:
```bash
crontab -l > ~/Desktop/crontab.backup.$(date +%Y%m%d).txt
crontab -e
```

### KEEP — trading spine
heartbeat, watchdog, arb, settlement, sports, allocator, quant, ledger reconcile, brain gates, morning brief, weather go, consistency, midday/close brain, rebalance, EOD portfolio, DCA, PnL, Kalshi audit, ML retrain, fund check, memory rsync, rclone NAS

### CUT CANDIDATES (comment these)
```cron
0 */4 * * * ... intel_refresh.py "INTEL REFRESH"
0 */6 * * * ... whale_copier.py "WHALE MONITOR"
0 2 * * * ... asi_scan.py "ASI SCAN"
0 2 * * * ... intel_sentiment.py "INTEL+SENTIMENT"
15 2 * * * ... insider_mirror.py "INSIDER"
30 2 * * * ... hedge_fund_intel.py "HEDGE INTEL"
30 */6 * * * ... dynamic_universe.py "DISCOVER"
30 */6 * * * ... scanner_run.py "MARKET SCANNER"
```

### UNSURE → prefer KEEP as sole intel path
`0 3 * * * mirofish_primary.sh` (or migrate to RuFlo CronCreate — see Ponytail)

### Rollback
`crontab ~/Desktop/crontab.backup.YYYYMMDD.txt`

---

## Bucket 5 — GPU wired (later only)

Current: `iogpu.wired_limit_mb = 118000` (~115 GiB).  
Later safe: **81920** (80 GiB) or **98304** (96 GiB). Do not apply until models are lean.

```bash
# later only:
sudo sysctl -w iogpu.wired_limit_mb=81920
```

---

## Ponytail / #6 — better version?

**Yes, partially** — for discovery/intel spam only.

- **Not** Goal UI Approve / continuous-coding autopilot (those are coding GOAP loops).
- **Yes** MiroFish primary + RuFlo `CronCreate` / swarm as the intel producer:
  - Trading spine stays cron
  - Cut the 8 spam lines
  - One nightly quiet-window swarm writes dated intel archive → atomic replace live intel
  - On-demand: RuVocal MCP swarm tools when you ask

If you refuse RuFlo for now: just delete the 8 lines and keep `mirofish_primary.sh` as the single intel cron.

---

## Rollback anchors
- Crontab Desktop backup
- `lms load` prior ctx/parallel
- `colima start --cpu 4 --memory 8`
- `launchctl bootstrap` for booted-out agents
- `sysctl` back to 118000
