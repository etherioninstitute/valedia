#!/usr/bin/env python3
"""Claude Bridge Server — Valedia ASI
Runs on NIMROD. Accepts HTTP requests from VPS via Tailscale.
Routes questions through Claude Code CLI using Burton's Max subscription.
Zero API cost.

Listens on port 7878 (Tailscale only — not exposed to internet).

POST /ask
  {"question": "...", "model": "sonnet|opus", "with_data": true}

LOCAL ONLY — NOT FOR SIGNAL. "response" is free model text and cannot be verified;
it must never be relayed to Signal. For Signal use "signal_safe_text": the gated
facts-only message (claude_bridge.gate_answer: code-built broker/ledger facts + the
fixed "AI answer not sent to Signal" line), or claude_router.sh / --router.

(c) GOATWORTHY Post LLC
"""
import http.server, json, subprocess, os, sys, threading
from datetime import datetime, timezone

PORT = 7878
CLAUDE_BIN = os.path.expanduser("~/.npm-global/bin/claude")
sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts/integrations"))


def get_portfolio_context():
    """Pull live trading data."""
    from claude_bridge import get_portfolio_context as _get
    return _get()

class ClaudeBridgeHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/ask':
            self.send_response(404)
            self.end_headers()
            return
        
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        
        question = body.get('question', 'What is my portfolio status?')
        model = body.get('model', 'sonnet')
        with_data = body.get('with_data', True)
        
        model_id = "sonnet"
        if model == "opus":
            model_id = "opus"
        
        if with_data:
            portfolio = get_portfolio_context()
            full_prompt = f"LIVE PORTFOLIO DATA:\n{portfolio}\n\nQUESTION: {question}"
        else:
            full_prompt = question
        
        # Add system context
        full_prompt = (
            "You are Valedia, an autonomous trading intelligence. "
            "Answer concisely. Be direct. This answer is shown LOCALLY only — "
            "it is not sent to Signal (Signal gets verified broker/ledger facts only).\n\n"
            + full_prompt
        )

        try:
            env = {**os.environ, 'PATH': os.path.expanduser('~/.npm-global/bin') + ':' + os.environ.get('PATH', '')}
            result = subprocess.run(
                [CLAUDE_BIN, '--print', '--model', model_id, '--max-turns', '1', full_prompt],
                capture_output=True, text=True, timeout=120, env=env,
                stdin=subprocess.DEVNULL
            )
            response_text = result.stdout.strip()
            if not response_text:
                response_text = f"Claude returned empty. stderr: {result.stderr[:200]}"
        except subprocess.TimeoutExpired:
            response_text = "Claude Code timed out (120s)"
        except Exception as e:
            response_text = f"Error: {str(e)}"
        
        try:  # the only Signal-safe text: code-built facts + fixed line, never model wording
            import claude_bridge as cb
            gated = cb.gate_answer(question, response_text, cb.verified_facts(), send=False)
            signal_safe = gated["text"] if gated["status"] in ("verified", "withheld") else None
        except Exception:
            signal_safe = None
        resp = json.dumps({"response": response_text, "local_only": True, "not_for_signal": True,
                           "signal_safe_text": signal_safe, "model": model,
                           "ts": datetime.now(timezone.utc).isoformat()})
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(resp.encode())
    
    def log_message(self, format, *args):
        pass  # Suppress access logs

def run():
    server = http.server.HTTPServer(('100.125.9.30', PORT), ClaudeBridgeHandler)
    print(f"Claude Bridge Server listening on port {PORT}")
    print(f"Endpoint: POST http://100.125.9.30:{PORT}/ask")
    print(f"Auth: Claude Max ({CLAUDE_BIN})")
    server.serve_forever()

if __name__ == "__main__":
    run()
