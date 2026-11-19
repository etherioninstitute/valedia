"""Draws the owner's maps as self-contained SVG files with no library and no network: a spec (bands of nodes and the edges
between them) in, an SVG out. The specs are data in this file (TRADING) or are built from the live Hermes configuration
(see tools/hermes_topology.py); edit the data, run again, and the picture follows.
Usage: python tools/mission_maps.py OUT_DIR   (writes trading-system.svg, and hermes-setup.svg when its topology file exists)."""
import html
import json
import sys
from pathlib import Path

KIND = {  # kind: (fill, stroke, label)
    "owner": ("#fde7c8", "#b8742a", "Owner / his gate"),
    "control": ("#dbe7ff", "#4a6fc0", "Control and agents"),
    "job": ("#e3f2e1", "#4f8a4a", "Scheduled or running job"),
    "model": ("#efe1ff", "#7a52b5", "Model"),
    "data": ("#e0f3f5", "#3c8d96", "Read-only data source"),
    "guard": ("#ffe1e1", "#c2493f", "Guard before money"),
    "store": ("#ececec", "#777777", "Record or store"),
    "venue": ("#fff3b8", "#a08a1d", "Venue / broker"),
    "idea": ("#f6f6f6", "#9a9a9a", "Not built or not running"),
}

# Bands are rows from top to bottom; a node is (id, label with | for a line break, kind).
TRADING = {
    "title": "Valedia trading system",
    "bands": [
        ("Owner and gates", [
            ("owner", "Owner|(Burton)", "owner"), ("keys", "Live key pair|his file, mode 600", "owner"),
            ("files", "Three config files|confirm, go-live, cap", "owner"), ("stake", "Stake entries|his own command", "owner"),
            ("go", "His word:|first order, go", "owner")]),
        ("Agents and control", [
            ("lead", "Lead session|builders, blind checks", "control"), ("paperclip", "Paperclip pipeline|Planner, Implementer, Reviewer", "control"),
            ("hermes", "Hermes profiles|chat, CTO, scout, decider, auditor", "control"), ("panel", "Research panel|and weekly cycle (Sun 6 PM)", "control")]),
        ("Models", [
            ("paid", "Paid frontier|ChatGPT/Codex, Claude, Grok", "model"), ("hs27b", "hs27b 27B|etherion via LM Link", "model"),
            ("free", "FreeLLMAPI chain|Groq, Cloudflare, OVH", "model"), ("e1", "elephant-1 9B|this Mac, collector only", "model")]),
        ("Research and decisions", [
            ("book", "Order-book loop|every minute, scored to 3 Dec", "job"), ("forecast", "Daily forecast|weekdays 3:05 PM", "job"),
            ("picks", "Pick-list reader|hourly at :07", "job"), ("leaders", "Leaders pass|daily 05:20", "job"),
            ("copier", "Forward copier|built, not running", "idea"), ("chatter", "Reddit chatter|hand audit first", "idea")]),
        ("Read-only data", [
            ("kalshi", "Kalshi public API", "data"), ("poly", "Polymarket public data", "data"), ("fin", "Finnhub, Alpaca|read-only, local only", "data"),
            ("odds", "The Odds API|through its credit ledger", "data")]),
        ("Guards before money", [
            ("risk", "risk.json limits|his numbers", "guard"), ("gate", "Go-live gate|paper record first", "guard"),
            ("first", "First-contact rule|one fractional order per run", "guard"), ("shared", "Shared-account book|her fills only", "guard"),
            ("kill", "Kill switch|per venue", "guard")]),
        ("Execution", [
            ("fundstep", "Order step|fund book, practice 06:55 daily", "job"), ("eventstep", "Event order step|T45-2 queued", "idea"),
            ("adapter", "Webull adapter|orders and account reads only", "control")]),
        ("Venues", [
            ("paper", "Webull paper account", "venue"), ("live", "Webull live account|shared with the owner", "venue")]),
        ("Records", [
            ("journal", "Journal and ledgers|append-only", "store"), ("state", "State files|backed up 03:40", "store"),
            ("obl", "orderbook_loop.sqlite|collector's store", "store"), ("cl", "copy_leaders store|internal disk", "store"),
            ("ml", "MASTER-LIST and ROADMAP|the plan", "store")]),
    ],
    "edges": [
        ("lead", "paperclip", "assigns"), ("lead", "ml", "keeps"), ("hermes", "panel", ""), ("panel", "hs27b", ""), ("panel", "paid", "tie-break"),
        ("paperclip", "hs27b", "writer"), ("paperclip", "paid", "checker"), ("hermes", "free", "coder"),
        ("kalshi", "book", ""), ("book", "e1", ""), ("book", "obl", "writes"), ("kalshi", "forecast", ""), ("fin", "forecast", ""),
        ("odds", "forecast", ""), ("forecast", "e1", ""), ("poly", "leaders", ""), ("leaders", "cl", "writes"), ("poly", "copier", ""),
        ("kalshi", "copier", ""), ("picks", "hs27b", ""), ("chatter", "kalshi", "compared to"),
        ("keys", "adapter", "placed by him"), ("files", "gate", ""), ("files", "risk", ""), ("stake", "shared", ""),
        ("risk", "fundstep", ""), ("gate", "fundstep", ""), ("first", "fundstep", ""), ("shared", "fundstep", ""), ("kill", "fundstep", ""),
        ("risk", "eventstep", ""), ("gate", "eventstep", ""), ("fundstep", "adapter", ""), ("eventstep", "adapter", ""),
        ("adapter", "paper", "practice"), ("adapter", "live", "only after his go"), ("go", "live", ""),
        ("fundstep", "journal", ""), ("fundstep", "state", ""), ("adapter", "journal", ""),
    ],
}

W, BOX_W, BOX_H, BAND_H, GAP, LEFT = 1500, 168, 44, 84, 46, 170


def esc(s):
    return html.escape(s, quote=True)


def layout(spec):
    pos, y = {}, 70
    bands = []
    for title, nodes in spec["bands"]:
        n = len(nodes)
        span = W - LEFT - 40
        slot = span / max(n, 1)
        for i, (nid, label, kind) in enumerate(nodes):
            cx = LEFT + slot * (i + 0.5)
            pos[nid] = (cx, y + BAND_H / 2, label, kind, min(BOX_W, slot - 14))
        bands.append((title, y))
        y += BAND_H + GAP
    return pos, bands, y


def svg(spec, as_of):
    pos, bands, height = layout(spec)
    height += 60
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" font-family="system-ui, sans-serif">'
           % (W, height, W, height)]
    out.append('<rect width="100%%" height="100%%" fill="#fbfbfa"/><text x="20" y="34" font-size="22" font-weight="600" fill="#222">%s</text>'
               '<text x="20" y="54" font-size="12" fill="#666">drawn from tools/mission_maps.py; %s</text>' % (esc(spec["title"]), esc(as_of)))
    for title, y in bands:
        out.append('<rect x="8" y="%d" width="%d" height="%d" rx="8" fill="#f1f1ee" stroke="#e2e2dc"/>' % (y - 6, W - 16, BAND_H + 12))
        out.append('<text x="18" y="%d" font-size="13" font-weight="600" fill="#555">%s</text>' % (y + BAND_H / 2 + 4, esc(title)))
    out.append('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
               '<path d="M0,0 L10,5 L0,10 z" fill="#7b7b75"/></marker></defs>')
    for src, dst, label in spec["edges"]:
        if src not in pos or dst not in pos:
            continue
        x1, y1 = pos[src][0], pos[src][1]
        x2, y2 = pos[dst][0], pos[dst][1]
        if abs(y2 - y1) < 1:                                        # same band: an arc above
            d = "M%.0f,%.0f C%.0f,%.0f %.0f,%.0f %.0f,%.0f" % (x1 + pos[src][4] / 2, y1 - 6, x1 + pos[src][4] / 2, y1 - 40, x2 - pos[dst][4] / 2, y2 - 40, x2 - pos[dst][4] / 2, y2 - 6)
            lx, ly = (x1 + x2) / 2, y1 - 36
        else:
            sy = y1 + (BOX_H / 2 if y2 > y1 else -BOX_H / 2)
            ey = y2 - (BOX_H / 2 if y2 > y1 else -BOX_H / 2)
            my = (sy + ey) / 2
            d = "M%.0f,%.0f C%.0f,%.0f %.0f,%.0f %.0f,%.0f" % (x1, sy, x1, my, x2, my, x2, ey)
            lx, ly = (x1 + x2) / 2, my - 3
        out.append('<path d="%s" fill="none" stroke="#7b7b75" stroke-opacity="0.45" stroke-width="1.3" marker-end="url(#a)"/>' % d)
        if label:
            out.append('<text x="%.0f" y="%.0f" font-size="10" fill="#555" text-anchor="middle" paint-order="stroke" stroke="#fbfbfa" stroke-width="3">%s</text>' % (lx, ly, esc(label)))
    for nid, (cx, cy, label, kind, bw) in pos.items():
        fill, stroke, _ = KIND.get(kind, KIND["store"])
        dash = ' stroke-dasharray="5 3"' if kind == "idea" else ""
        out.append('<rect x="%.0f" y="%.0f" width="%d" height="%d" rx="7" fill="%s" stroke="%s" stroke-width="1.4"%s/>' % (cx - bw / 2, cy - BOX_H / 2, bw, BOX_H, fill, stroke, dash))
        lines = [t if len(t) <= int(bw / 5.6) else t[: max(int(bw / 5.6) - 1, 3)] + "\u2026" for t in label.split("|")]
        for i, t in enumerate(lines):
            ty = cy + (4 if len(lines) == 1 else (-3 if i == 0 else 11))
            out.append('<text x="%.0f" y="%.0f" font-size="%d" text-anchor="middle" fill="#222"%s>%s</text>' % (cx, ty, (12 if bw > 140 else 10) if i == 0 else (10 if bw > 140 else 9), ' font-weight="600"' if i == 0 else "", esc(t)))
    lx = LEFT
    out.append('<g transform="translate(%d,%d)">' % (lx, height - 40))
    for i, (k, (fill, stroke, name)) in enumerate(KIND.items()):
        out.append('<rect x="%d" y="0" width="14" height="14" rx="3" fill="%s" stroke="%s"/><text x="%d" y="11" font-size="11" fill="#444">%s</text>' % (i * 150, fill, stroke, i * 150 + 20, esc(name)))
    out.append("</g></svg>")
    return "\n".join(out)


def main() -> int:
    out = Path(sys.argv[1])
    asof = sys.argv[2] if len(sys.argv) > 2 else ""
    out.mkdir(parents=True, exist_ok=True)
    (out / "trading-system.svg").write_text(svg(TRADING, asof))
    print("trading-system.svg")
    topo = out / "hermes-topology.json"
    if topo.exists():
        (out / "hermes-setup.svg").write_text(svg(json.loads(topo.read_text()), asof))
        print("hermes-setup.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
