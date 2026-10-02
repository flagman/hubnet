"""Hubnet client for Python — connect a hub to a Hubnet network without implementing the protocol yourself.

    from hubnet.client import Hub, anthropic_screen

    hub = Hub.connect("https://hubnet.example.com", "hnc_…", save_to="hub.json")   # once, with a connect token
    hub = Hub.load("hub.json")                                                      # every next start
    hub.publish_card("Alice's hub", "Design and UX", skills=[{"id": "design", "name": "Design", "description": "…"}])
    hub.ask("bob.lee", "How do you review landing pages?")                          # → task; the reply comes later
    hub.serve(handle, screen=anthropic_screen())                                    # answer requests, forever

`serve` runs every incoming request through the mandatory inbound guard of the protocol (PROTOCOL.md, «Inbound
security»; hubnet/guard.py): hidden characters made visible, a screening model with the published prompt, the
decision. Your `handle(request)` gets only clean requests and returns the answer text (or None to answer later with
`hub.answer`). Attacks are rejected without reaching you; doubtful requests go to `on_hold`. Answers are checked for
secret-like strings before they leave. Keep your handler a model or code without tools: it answers, it does not act.

Python standard library only.
"""
import json
import os
import time
import urllib.error
import urllib.request

from hubnet import guard

DECLINED = "This hub does not take this request."
PROTOCOL = "0.2"                 # версия профиля Hubnet (PROTOCOL.md, «Versions and compatibility»)
CLIENT = "hubnet-python/0.2"


class Request:
    """An incoming request after the guard. `text` has hidden characters made visible; `envelope` is the text fenced
    and labelled as another hub's words — give a model the envelope, not the raw text."""

    def __init__(self, item, pre, verdict, decision):
        self.item, self.task_id = item, (item.get("task") or {}).get("id")
        self.sender, self.context = item.get("from"), (item.get("task") or {}).get("contextId")
        self.text, self.findings = pre["text"], pre["findings"]
        self.verdict, self.reasons, self.summary = verdict["verdict"], verdict["reasons"], verdict["summary"]
        self.decision = decision
        self.envelope = guard.envelope(self.sender, self.text, self.summary)
        self.held_answer = None

    def __repr__(self):
        return f"<Request {self.task_id} from {self.sender}: {self.decision}>"


class Hub:
    def __init__(self, server, key, hub_id=None, user_agent=CLIENT):
        self.server, self.key, self.id, self.ua = server.rstrip("/"), key, hub_id, user_agent
        self.history = {}   # contextId -> earlier texts: the screen sees the whole conversation

    # --- identity ----------------------------------------------------------------------------------------------
    @classmethod
    def connect(cls, server, token, save_to=None):
        """Trade a connect token (from the network administrator) for this hub's key; save it privately."""
        out = _call(server.rstrip("/"), "POST", "/v1/connect", {"token": token})
        hub = cls(server, out["key"], out["id"])
        if save_to:
            fd = os.open(save_to, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"server": hub.server, "hub_id": hub.id, "hub_key": hub.key}, f, indent=1)
        return hub

    @classmethod
    def load(cls, path):
        with open(path) as f:
            c = json.load(f)
        return cls(c["server"], c["hub_key"], c.get("hub_id"))

    def call(self, method, path, body=None, timeout=20):
        return _call(self.server, method, path, body, self.key, timeout, self.ua)

    # --- directory and requests --------------------------------------------------------------------------------
    def publish_card(self, name, description="", skills=(), version="0.1", **extra):
        """Your A2A Agent Card: what other hubs see and may ask you about. Skills — areas, one per agent or topic."""
        return self.call("PUT", "/v1/card", {"name": name, "description": description, "version": version,
                                             "skills": list(skills), **extra})

    def hubs(self):
        return self.call("GET", "/v1/hubs")["hubs"]

    def ask(self, hub_id, text, context_id=None):
        msg = {"role": "user", "messageId": f"m{time.time_ns()}", "parts": [{"kind": "text", "text": text}]}
        if context_id:
            msg["contextId"] = context_id
        return self.call("POST", f"/v1/hubs/{hub_id}/message", {"message": msg})["task"]

    def inbox(self, wait=25):
        """Raw items: requests to you and replies to your asks. Replies are untrusted too — screen them before a
        model with tools reads them (guard.precheck + guard.screen)."""
        return self.call("GET", f"/v1/inbox?wait={wait}", timeout=wait + 15).get("items") or []

    def reply(self, task_id, text, state="completed"):
        """Send as is — no checks. Prefer `answer`."""
        msg = {"role": "agent", "messageId": f"{task_id}-r{time.time_ns()}", "parts": [{"kind": "text", "text": text}]}
        return self.call("POST", f"/v1/tasks/{task_id}/reply", {"message": msg, "state": state})

    def answer(self, request, text, state="completed", force=False):
        """Answer a request after the outbound check: secret-like strings hold the answer (returns False)."""
        if not force and guard.outbound(text):
            request.decision, request.held_answer = "answer_held", text
            return False
        self.reply(request.task_id, text, state)
        return True

    # --- serving -----------------------------------------------------------------------------------------------
    def screen(self, item, screen, classify=None):
        if screen is None:
            raise ValueError("a screening model is mandatory (PROTOCOL.md, Inbound security): pass screen=")
        pre = guard.precheck(item.get("message") or {})
        task = item.get("task") or {}
        past = self.history.setdefault(task.get("contextId") or task.get("id"), [])
        verdict = guard.screen(pre["text"], past[-10:], screen, classify=classify)
        past.append(pre["text"])
        return Request(item, pre, verdict, guard.decide(pre, verdict))

    def serve_once(self, handle, screen, on_hold=None, on_reply=None, classify=None, wait=25):
        """One long poll: every request through the guard, clean ones to `handle`."""
        if screen is None:
            raise ValueError("a screening model is mandatory (PROTOCOL.md, Inbound security): pass screen=")
        on_hold = on_hold or (lambda r: print(f"hubnet: held {r!r}: {r.summary} {r.reasons + r.findings}"))
        for item in self.inbox(wait):
            if item.get("kind") != "request":
                if on_reply:
                    on_reply(item)
                continue
            req = self.screen(item, screen, classify)
            if req.decision == "block":
                self.reply(req.task_id, DECLINED, "rejected")
                on_hold(req)
            elif req.decision == "hold":
                on_hold(req)
            else:
                text = handle(req)
                if text is not None and not self.answer(req, text):
                    on_hold(req)

    def serve(self, handle, screen, on_hold=None, on_reply=None, classify=None):
        """Answer requests forever (long polling keeps the hub online). Publish your card first."""
        while True:
            try:
                self.serve_once(handle, screen, on_hold, on_reply, classify)
            except (OSError, urllib.error.URLError) as e:
                print(f"hubnet: connection: {e!r}; retrying")
                time.sleep(10)


# --- screening models -------------------------------------------------------------------------------------------

def anthropic_screen(api_key=None, model="claude-haiku-4-5"):
    """The screen on the Anthropic Messages API (ANTHROPIC_API_KEY): no tools, the published prompt."""
    key = api_key or os.environ["ANTHROPIC_API_KEY"]

    def ask(system, user):
        body = {"model": model, "max_tokens": 400, "system": system, "messages": [{"role": "user", "content": user}]}
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return "".join(b.get("text", "") for b in json.load(r)["content"])
    return ask


def claude_cli_screen(model="haiku"):
    """The screen through the Claude Code CLI (`claude -p`), with every tool, setting and MCP server off."""
    import shutil
    import subprocess
    import tempfile
    claude = shutil.which("claude") or "claude"

    def ask(system, user):
        r = subprocess.run([claude, "-p", "--model", model, "--tools", "", "--setting-sources", "",
                            "--strict-mcp-config", "--no-session-persistence", "--system-prompt", system],
                           input=user, capture_output=True, text=True, timeout=120, cwd=tempfile.gettempdir())
        return r.stdout
    return ask


def _call(server, method, path, body=None, key=None, timeout=20, ua=CLIENT):
    headers = {"Content-Type": "application/json", "User-Agent": ua, "Hubnet-Client": ua, "Hubnet-Protocol": PROTOCOL}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(server + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)
