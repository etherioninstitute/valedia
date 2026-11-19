"""Reads the NAMES of the owner's Hermes set-up (profiles, models, providers, plugins, MCP servers, delegation routes,
Kanban dispatch list) from the config files on disk and writes the spec of the Hermes map for tools/mission_maps.py.
Read-only; no key, token, header or other value of the config is read into the output: only names, model ids and
host:port of local endpoints. Usage: python tools/hermes_topology.py OUT_JSON   (then tools/mission_maps.py)."""
import json
import os
import re
import sys
from pathlib import Path

import yaml

HOME = Path(os.path.expanduser("~/.hermes"))


def load(path):
    try:
        return yaml.safe_load(Path(path).read_text()) or {}
    except Exception:
        return {}


def host(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else ""


def short(names, n=5):
    names = [str(x) for x in names]
    return ", ".join(names[:n]) + (" +%d" % (len(names) - n) if len(names) > n else "")


def main() -> int:
    root = load(HOME / "config.yaml")
    profiles = {"default": root}
    for d in sorted((HOME / "profiles").glob("*")):
        if (d / "config.yaml").exists():
            profiles[d.name] = load(d / "config.yaml")
    bands, edges, seen = [], [], {}

    def prov_node(prov, model, base):
        h = host(base)
        key = "prov:%s@%s" % (prov, h)
        if key not in seen:
            label = "%s|%s" % (prov or "provider?", (h + " " if h else "") + (model or ""))
            seen[key] = (key, label.strip(), "model")
        return key

    plat = list((root.get("platforms") or {}).keys())
    bands.append(("Surfaces", [("s:" + p, p.capitalize() + "|gateway", "control") for p in plat] +
                  [("s:desktop", "Hermes Desktop|Live page, chat", "control"), ("s:cli", "CLI|hermes", "control")]))
    pnodes, pedges, models = [], [], {}
    for name, cfg in profiles.items():
        m = cfg.get("model") or {}
        if isinstance(m, str):
            m = {"default": m}
        node = prov_node(m.get("provider"), m.get("default"), m.get("base_url"))
        models[node] = seen[node]
        kind = "control"
        pnodes.append(("p:" + name, "%s|%s" % (name, (m.get("default") or "?")), kind))
        pedges.append(("p:" + name, node, ""))
    bands.append(("Profiles (each its own config)", pnodes))
    deleg = (root.get("delegation") or {})
    opts = ((deleg.get("backends") or {}).get("model_map") or {}).get("options") or []
    routes = []
    for o in opts:
        nm = o.get("name", "?")
        node = prov_node(o.get("provider"), o.get("model"), o.get("base_url"))
        models[node] = seen[node]
        routes.append(("r:" + nm, "route %s|%s" % (nm, o.get("model", "")), "job"))
        edges.append(("r:" + nm, node, ""))
    kan = root.get("kanban") or {}
    routes.append(("kanban", "Kanban|dispatch: %s" % short(str(kan.get("dispatch_profiles", "any")).split(","), 4), "job"))
    routes.append(("paperclip", "Paperclip|task server, 3 seats", "control"))
    routes.append(("launcher", "Seat launcher|bin/seat-hermes", "guard"))
    routes.append(("tools", "Seat tool server|bin/seat-tools, sandboxed tests", "guard"))
    bands.append(("Delegation and orchestration (backend: %s)" % deleg.get("backend", "?"), routes))
    pl = root.get("plugins") or {}
    enabled, disabled = pl.get("enabled") or [], pl.get("disabled") or []
    mcp = list((root.get("mcp_servers") or {}).keys())
    bands.append(("Plugins and tools", [("plugins", "Plugins on|%s" % short(enabled, 4), "store"),
                                        ("off", "Plugins off|%s" % short(disabled, 3), "idea"),
                                        ("mcp", "MCP servers (%d)|%s" % (len(mcp), short(mcp, 4)), "store")]))
    bands.append(("Providers and models", list(models.values())))
    bands.append(("Where the models run", [
        ("lms", "LM Studio (Bionic)|127.0.0.1:1235", "job"), ("e1", "elephant-1|this Mac, collector's", "model"),
        ("hs27b", "hs27b|etherion via LM Link", "model"), ("fla", "FreeLLMAPI|127.0.0.1:31415", "job"),
        ("free", "Groq, Cloudflare, OVH|zero retention", "data"), ("cloud", "ChatGPT/Codex, Claude, Grok|paid plans", "venue")]))
    # edges
    for p in plat:
        edges.append(("s:" + p, "p:default", ""))
    edges += [("s:desktop", "p:default", ""), ("s:cli", "p:default", "")]
    edges += pedges
    edges += [("paperclip", "launcher", ""), ("launcher", "tools", ""), ("launcher", "p:seat-planner", ""),
              ("launcher", "p:seat-implementer", ""), ("launcher", "p:seat-reviewer", ""), ("kanban", "p:valedia-coder", "")]
    for node, (_, label, _) in models.items():
        if "1235" in node:
            edges += [(node, "lms", ""), ("lms", "e1", ""), ("lms", "hs27b", "")]
        elif "31415" in node:
            edges += [(node, "fla", ""), ("fla", "free", "")]
        elif "openai-codex" in node or "anthropic" in node or "xai" in node:
            edges.append((node, "cloud", ""))
    spec = {"title": "Hermes and orchestration set-up (read from the config files)", "bands": bands, "edges": edges}
    Path(sys.argv[1]).write_text(json.dumps(spec, indent=1))
    print("profiles", len(profiles), "| routes", len(opts), "| plugins on", len(enabled), "| off", disabled, "| mcp", len(mcp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
