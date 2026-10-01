"""Hubnet network server: the administrator issues hub keys, a hub publishes its card (A2A Agent Card), keeps
presence by long polling, sends an A2A message to another hub, which takes it from its inbox and answers; the server
keeps a traffic log without content."""
import http.client
import json
import os
import tempfile
import threading
import time
import unittest

from hubnet import server as hs


class Server(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hubnet-")
        self.net = hs.Hubnet(self.dir, admin_token="adm")
        self.http = self.net.serve(0, "127.0.0.1")
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.keys = {h: self.req("POST", "/v1/admin/hubs", {"id": h, "owner": h.title()}, "adm")[1]["key"]
                     for h in ("alice", "bob")}

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()

    def req(self, method, path, body=None, token=None, timeout=10):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, (json.loads(data) if data else None)

    def card(self, hub):
        return {"name": f"Hub {hub}", "description": "DevOps and SEO", "version": "1.0", "skills": [
            {"id": "advice", "name": "Advice", "description": "DevOps: k8s, CI", "tags": ["devops"]}]}

    def test_keys_card_presence(self):
        self.assertEqual(self.req("POST", "/v1/admin/hubs", {"id": "x"}, "wrong")[0], 401)
        self.assertEqual(self.req("PUT", "/v1/card", self.card("alice"), "nope")[0], 401)
        self.assertEqual(self.req("PUT", "/v1/card", self.card("alice"), self.keys["alice"])[0], 200)
        code, hubs = self.req("GET", "/v1/hubs", token=self.keys["bob"])
        self.assertEqual(code, 200)
        alc = {h["id"]: h for h in hubs["hubs"]}["alice"]
        self.assertEqual((alc["card"]["skills"][0]["id"], alc["online"]), ("advice", True))   # published a card — online
        self.net.clock = lambda: time.time() + hs.ONLINE_FOR + 1
        self.assertFalse({h["id"]: h for h in self.req("GET", "/v1/hubs", token=self.keys["bob"])[1]["hubs"]}
                         ["alice"]["online"])
        # the server card — entry point for any A2A client
        self.assertEqual(self.req("GET", "/.well-known/agent-card.json")[0], 200)

    def test_message_to_another_hub_and_reply(self):
        msg = {"role": "user", "messageId": "m1", "parts": [{"kind": "text", "text": "how do you deploy to k8s?"}]}
        code, sent = self.req("POST", "/v1/hubs/bob/message", {"message": msg}, self.keys["alice"])
        self.assertEqual(code, 202)
        task = sent["task"]
        self.assertEqual(task["status"]["state"], "submitted")
        code, box = self.req("GET", "/v1/inbox?wait=1", token=self.keys["bob"])
        (item,) = box["items"]
        self.assertEqual((item["from"], item["task"]["id"], item["message"]["parts"][0]["text"]),
                         ("alice", task["id"], "how do you deploy to k8s?"))
        self.assertEqual(self.req("GET", "/v1/inbox?wait=0", token=self.keys["bob"])[1]["items"], [])  # already taken
        reply = {"role": "agent", "messageId": "r1", "parts": [{"kind": "text", "text": "with ArgoCD"}]}
        self.assertEqual(self.req("POST", f"/v1/tasks/{task['id']}/reply", {"message": reply, "state": "completed"},
                                  self.keys["bob"])[0], 202)
        (back,) = self.req("GET", "/v1/inbox?wait=1", token=self.keys["alice"])[1]["items"]
        self.assertEqual((back["kind"], back["task"]["status"]["state"], back["message"]["parts"][0]["text"]),
                         ("reply", "completed", "with ArgoCD"))
        # nobody else answers the task; the log has no content
        self.assertEqual(self.req("POST", f"/v1/tasks/{task['id']}/reply", {"message": reply}, self.keys["alice"])[0], 403)
        code, log = self.req("GET", "/v1/traffic", token=self.keys["alice"])
        self.assertEqual([(e["from"], e["to"], e["state"]) for e in log["events"]],
                         [("alice", "bob", "submitted"), ("bob", "alice", "completed")])
        self.assertNotIn("k8s", json.dumps(log))

    def test_long_poll_wakes_on_new_message(self):
        got = {}
        t = threading.Thread(target=lambda: got.update(r=self.req("GET", "/v1/inbox?wait=5", token=self.keys["bob"])))
        t.start()
        time.sleep(0.3)
        self.req("POST", "/v1/hubs/bob/message", {"message": {"role": "user", "messageId": "m", "parts": []}},
                 self.keys["alice"])
        t.join(3)
        self.assertEqual(len(got["r"][1]["items"]), 1)

    def test_hub_ids_like_people_and_removal(self):
        # ids like «first.last»; the administrator can retire a hub
        code, made = self.req("POST", "/v1/admin/hubs", {"id": "alice.smith", "owner": "Alice"}, "adm")
        self.assertEqual(code, 201)
        for bad in ("Alice", "a b", "../x", ".a", "a.", ""):
            self.assertEqual(self.req("POST", "/v1/admin/hubs", {"id": bad}, "adm")[0], 400, bad)
        self.assertEqual(self.req("DELETE", "/v1/admin/hubs/alice.smith", token="wrong")[0], 401)
        self.assertEqual(self.req("DELETE", "/v1/admin/hubs/alice.smith", token="adm")[0], 200)
        self.assertEqual(self.req("GET", "/v1/hubs", token=made["key"])[0], 401)
        self.assertEqual(self.req("DELETE", "/v1/admin/hubs/nobody", token="adm")[0], 404)

    def test_connect_token_is_short_lived_and_single_use(self):
        # the administrator hands a person a connect token instead of the hub key; the hub trades it for its key
        code, t = self.req("POST", "/v1/admin/connect-tokens", {"id": "carol.white", "owner": "Carol", "ttl": 600}, "adm")
        self.assertEqual((code, t["id"]), (201, "carol.white"))
        self.assertTrue(t["token"].startswith("hnc_") and t["expires"] > time.time())
        self.assertEqual(self.req("POST", "/v1/admin/connect-tokens", {"id": "x"}, "wrong")[0], 401)
        code, got = self.req("POST", "/v1/connect", {"token": t["token"]})
        self.assertEqual((code, got["id"]), (200, "carol.white"))
        self.assertEqual(self.req("GET", "/v1/hubs", token=got["key"])[0], 200)
        self.assertEqual(self.req("POST", "/v1/connect", {"token": t["token"]})[0], 401)      # one use
        late = self.req("POST", "/v1/admin/connect-tokens", {"id": "dan.green", "ttl": 60}, "adm")[1]
        self.net.clock = lambda: time.time() + 61
        self.assertEqual(self.req("POST", "/v1/connect", {"token": late["token"]})[0], 401)   # expired
        self.net.clock = time.time
        # an existing hub reconnects with a new token: its key is replaced, the old one stops working
        again = self.req("POST", "/v1/admin/connect-tokens", {"id": "carol.white"}, "adm")[1]
        new = self.req("POST", "/v1/connect", {"token": again["token"]})[1]["key"]
        self.assertEqual(self.req("GET", "/v1/hubs", token=got["key"])[0], 401)
        self.assertEqual(self.req("GET", "/v1/hubs", token=new)[0], 200)
        self.assertGreater(len(self.req("POST", "/v1/admin/connect-tokens", {"id": "e", "ttl": 99999}, "adm")[1]
                               ["token"]), 10)
        self.assertLessEqual(self.req("POST", "/v1/admin/connect-tokens", {"id": "f", "ttl": 99999}, "adm")[1]["expires"],
                             time.time() + hs.CONNECT_MAX_TTL + 1)

    def test_unknown_hub_and_state_survives_restart(self):
        self.assertEqual(self.req("POST", "/v1/hubs/nobody/message", {"message": {}}, self.keys["alice"])[0], 404)
        again = hs.Hubnet(self.dir, admin_token="adm")
        self.assertIsNotNone(again.hub_by_key(self.keys["bob"]))
        with open(os.path.join(self.dir, "hubs.json")) as f:
            self.assertNotIn(self.keys["bob"], f.read())   # keys are stored hashed


if __name__ == "__main__":
    unittest.main()
