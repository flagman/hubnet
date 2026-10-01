"""Hubnet network server: hub directory, presence, and relay of A2A messages between hubs (profile: PROTOCOL.md).

Hubs sit behind NAT, so every connection is outbound: a hub publishes its card (A2A Agent Card), drains its inbox by
long polling and sends messages to other hubs through the server. A task (A2A Task) lives on the server: who asked,
whom, its state; only the addressee may answer. The traffic log for a network view carries no content.

  python3 -m hubnet.server --dir /data --port 8080          (HUBNET_ADMIN_TOKEN — administrator token)
  POST /v1/admin/hubs {id, owner}          (administrator) register a hub → its key, shown once
  DELETE /v1/admin/hubs/<id>               (administrator) retire a hub
  POST /v1/admin/connect-tokens {id, owner, ttl}  (administrator) short-lived single-use connect token for a hub
  POST /v1/connect {token}                 trade a connect token for the hub key (no other credential needed)
  PUT  /v1/card <AgentCard>                hub: my card (also marks me online)
  GET  /v1/hubs                            hubs of the network: cards, online
  POST /v1/hubs/<id>/message {message}     ask a hub → a task (submitted)
  GET  /v1/inbox?wait=25                   my inbox: new requests and replies (long poll; also marks me online)
  POST /v1/tasks/<id>/reply {message, state}   addressee's answer: working | input-required | completed | rejected
  GET  /v1/traffic                         log: who asked whom, when, state — no content
  GET  /.well-known/agent-card.json        the server's own card — entry point for A2A clients
Python standard library only.
"""
import argparse
import re
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ONLINE_FOR = 90        # seconds a hub counts as online after its last call
MAX_WAIT = 30
MAX_BODY = 1 << 20
TRAFFIC_KEEP = 2000
CONNECT_TTL = 15 * 60       # connect token lifetime by default
CONNECT_MAX_TTL = 24 * 3600
HUB_ID = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
STATES = ("submitted", "working", "input-required", "completed", "rejected", "failed", "canceled")


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


class Hubnet:
    def __init__(self, data_dir, admin_token=None, clock=time.time):
        self.dir, self.admin, self.clock = data_dir, admin_token, clock
        os.makedirs(data_dir, exist_ok=True)
        self.lock = threading.Condition()
        self.hubs = self._load("hubs.json", {})      # id -> {owner, key_hash, card, seen}
        self.tasks = self._load("tasks.json", {})    # id -> {from, to, created}
        self.invites = self._load("invites.json", {})  # hash of a connect token -> {id, owner, expires}
        self.boxes = {}                              # hub id -> inbox items
        self.traffic = []

    # --- storage ---------------------------------------------------------------------------------------------
    def _load(self, name, empty):
        try:
            with open(os.path.join(self.dir, name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return empty

    def _save(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path + ".tmp", "w") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)

    # --- hubs ------------------------------------------------------------------------------------------------
    def add_hub(self, hub_id, owner):
        key = "hn_" + secrets.token_urlsafe(32)
        with self.lock:
            self.hubs[hub_id] = {"owner": owner, "key_hash": _hash(key), "card": None, "seen": 0}
            self._save("hubs.json", self.hubs)
        return key

    def connect_token(self, hub_id, owner, ttl):
        """Short-lived, single-use token a person uses to connect their hub; the server keeps only its hash."""
        token = "hnc_" + secrets.token_urlsafe(24)
        expires = self.clock() + min(max(int(ttl or CONNECT_TTL), 30), CONNECT_MAX_TTL)
        with self.lock:
            self.invites[_hash(token)] = {"id": hub_id, "owner": owner, "expires": expires}
            self._save("invites.json", self.invites)
        return token, expires

    def connect(self, token):
        """Trade a connect token for the hub key (new hub, or a new key for an existing one)."""
        with self.lock:
            inv = self.invites.pop(_hash(token or ""), None)
            self.invites = {h: x for h, x in self.invites.items() if x["expires"] > self.clock()}
            self._save("invites.json", self.invites)
        if not inv or inv["expires"] <= self.clock():
            return None, None
        owner = inv["owner"] or (self.hubs.get(inv["id"]) or {}).get("owner") or inv["id"]
        key = "hn_" + secrets.token_urlsafe(32)
        with self.lock:
            old = self.hubs.get(inv["id"]) or {}
            self.hubs[inv["id"]] = {**old, "owner": owner, "key_hash": _hash(key), "card": old.get("card"),
                                    "seen": old.get("seen", 0)}
            self._save("hubs.json", self.hubs)
        return inv["id"], key

    def hub_by_key(self, key):
        h = _hash(key or "")
        return next((i for i, x in self.hubs.items() if hmac.compare_digest(x["key_hash"], h)), None)

    def touch(self, hub_id):
        self.hubs[hub_id]["seen"] = self.clock()

    def listing(self):
        now = self.clock()
        return [{"id": i, "owner": x["owner"], "card": x["card"], "online": now - x["seen"] < ONLINE_FOR,
                 "seen": x["seen"]} for i, x in sorted(self.hubs.items())]

    # --- tasks ----------------------------------------------------------------------------------------------
    def send(self, sender, to, message):
        task = {"id": uuid.uuid4().hex, "contextId": message.get("contextId") or uuid.uuid4().hex,
                "status": {"state": "submitted", "timestamp": self.clock()}, "kind": "task"}
        with self.lock:
            self.tasks[task["id"]] = {"from": sender, "to": to, "created": self.clock()}
            self._save("tasks.json", self.tasks)
            self._put(to, {"kind": "request", "from": sender, "task": task, "message": message})
            self._log(sender, to, task, message)
        return task

    def reply(self, hub_id, task_id, message, state):
        t = self.tasks.get(task_id)
        if not t:
            return 404, "no such task"
        if t["to"] != hub_id:
            return 403, "only the addressee may answer"
        task = {"id": task_id, "status": {"state": state, "timestamp": self.clock()}, "kind": "task"}
        with self.lock:
            self._put(t["from"], {"kind": "reply", "from": hub_id, "task": task, "message": message})
            self._log(hub_id, t["from"], task, message)
        return 202, {"task": task}

    def _put(self, hub_id, item):
        self.boxes.setdefault(hub_id, []).append(item)
        self.lock.notify_all()

    def _log(self, sender, to, task, message):
        self.traffic.append({"ts": self.clock(), "from": sender, "to": to, "task": task["id"],
                             "state": task["status"]["state"], "size": len(json.dumps(message, ensure_ascii=False))})
        del self.traffic[:-TRAFFIC_KEEP]

    def take(self, hub_id, wait):
        end = time.time() + min(max(wait, 0), MAX_WAIT)
        with self.lock:
            while not self.boxes.get(hub_id) and time.time() < end:
                self.lock.wait(max(0.0, end - time.time()))
            return self.boxes.pop(hub_id, [])

    # --- HTTP ------------------------------------------------------------------------------------------------
    def handler(self):
        net = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send_json(self, code, data):
                body = json.dumps(data if isinstance(data, (dict, list)) else {"error": data}, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    raise ValueError("body too large")
                return json.loads(self.rfile.read(n) or b"{}")

            def token(self):
                a = self.headers.get("Authorization") or ""
                return a[7:] if a.startswith("Bearer ") else ""

            def hub(self):
                h = net.hub_by_key(self.token())
                if h:
                    with net.lock:
                        net.touch(h)
                return h

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/.well-known/agent-card.json":
                    return self.send_json(200, {"name": "Hubnet", "version": "1.0", "protocolVersion": "1.0",
                                                "description": "Network of agent hubs: directory, presence, A2A relay",
                                                "url": f"https://{self.headers.get('Host', '')}/v1",
                                                "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
                                                "skills": []})
                h = self.hub()
                if not h:
                    return self.send_json(401, "hub key required")
                if u.path == "/v1/hubs":
                    return self.send_json(200, {"hubs": net.listing()})
                if u.path == "/v1/inbox":
                    wait = float((parse_qs(u.query).get("wait") or ["0"])[0])
                    return self.send_json(200, {"items": net.take(h, wait)})
                if u.path == "/v1/traffic":
                    return self.send_json(200, {"events": net.traffic[-500:]})
                return self.send_json(404, "no such path")

            def do_POST(self):
                u = urlparse(self.path)
                try:
                    data = self.body()
                except ValueError as e:
                    return self.send_json(400, str(e))
                if u.path == "/v1/connect":  # no hub key yet: the connect token is the credential
                    hid, key = net.connect(str(data.get("token") or ""))
                    if not hid:
                        return self.send_json(401, "connect token unknown, used or expired")
                    return self.send_json(200, {"id": hid, "key": key, "server": f"https://{self.headers.get('Host', '')}"})
                if u.path == "/v1/admin/connect-tokens":
                    if not net.admin or not hmac.compare_digest(self.token(), net.admin):
                        return self.send_json(401, "administrator token required")
                    hid = str(data.get("id") or "")
                    if not HUB_ID.fullmatch(hid):
                        return self.send_json(400, "id: lowercase latin letters, digits, dot, dash (first.last)")
                    tok, exp = net.connect_token(hid, data.get("owner"), data.get("ttl"))
                    return self.send_json(201, {"id": hid, "token": tok, "expires": exp})
                if u.path == "/v1/admin/hubs":
                    if not net.admin or not hmac.compare_digest(self.token(), net.admin):
                        return self.send_json(401, "administrator token required")
                    hid = str(data.get("id") or "")
                    if not HUB_ID.fullmatch(hid):
                        return self.send_json(400, "id: lowercase latin letters, digits, dot, dash (first.last)")
                    return self.send_json(201, {"id": hid, "key": net.add_hub(hid, data.get("owner") or hid)})
                h = self.hub()
                if not h:
                    return self.send_json(401, "hub key required")
                parts = u.path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["v1", "hubs"] and parts[3] == "message":
                    if parts[2] not in net.hubs:
                        return self.send_json(404, "no such hub")
                    return self.send_json(202, {"task": net.send(h, parts[2], data.get("message") or {})})
                if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "reply":
                    state = data.get("state") or "completed"
                    if state not in STATES:
                        return self.send_json(400, f"state: {', '.join(STATES)}")
                    code, out = net.reply(h, parts[2], data.get("message") or {}, state)
                    return self.send_json(code, out)
                return self.send_json(404, "no such path")

            def do_DELETE(self):
                parts = urlparse(self.path).path.strip("/").split("/")
                if len(parts) != 4 or parts[:3] != ["v1", "admin", "hubs"]:
                    return self.send_json(404, "no such path")
                if not net.admin or not hmac.compare_digest(self.token(), net.admin):
                    return self.send_json(401, "administrator token required")
                with net.lock:
                    if net.hubs.pop(parts[3], None) is None:
                        return self.send_json(404, "no such hub")
                    net._save("hubs.json", net.hubs)
                return self.send_json(200, {"removed": parts[3]})

            def do_PUT(self):
                if urlparse(self.path).path != "/v1/card":
                    return self.send_json(404, "no such path")
                h = self.hub()
                if not h:
                    return self.send_json(401, "hub key required")
                try:
                    card = self.body()
                except ValueError as e:
                    return self.send_json(400, str(e))
                with net.lock:
                    net.hubs[h]["card"] = card
                    net._save("hubs.json", net.hubs)
                return self.send_json(200, {"ok": True})

        return Handler

    def serve(self, port, host="0.0.0.0"):
        srv = ThreadingHTTPServer((host, port), self.handler())
        srv.daemon_threads = True
        return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.environ.get("HUBNET_DIR", "/data"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    a = ap.parse_args()
    net = Hubnet(a.dir, admin_token=os.environ.get("HUBNET_ADMIN_TOKEN"))
    print(f"hubnet: port {a.port}, data {a.dir}", flush=True)
    net.serve(a.port).serve_forever()


if __name__ == "__main__":
    main()
