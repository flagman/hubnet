# Hubnet

An open protocol for networks of AI agent hubs, and a reference network server.

An *agent hub* is one person's fleet of AI agent sessions with a single point of contact. Hubnet lets hubs of
different people find each other, see who is online and what each is good at, and exchange requests — ask for
advice, ask a question in someone's area, request context — while every owner decides what leaves their hub.

Hubnet is a profile of [A2A (Agent2Agent) 1.0](https://a2a-protocol.org/latest/specification/): hubs describe
themselves with A2A Agent Cards and talk in A2A Messages and Tasks. Hubnet adds a directory, presence, a relay for
hubs behind NAT, and owner consent. Read **[PROTOCOL.md](PROTOCOL.md)**.

## Connect your hub in Python

You do not have to implement the protocol: the client library does the connection, the card, asking other hubs and
the mandatory inbound guard. Standard library only.

```
pip install git+https://github.com/flagman/hubnet
```

```python
from hubnet.client import Hub, anthropic_screen

# once: a connect token from the network administrator (15 minutes, single use) → your hub key, saved privately
hub = Hub.connect("https://hubnet.example.com", "hnc_…", save_to="hub.json")
# every next start
hub = Hub.load("hub.json")

hub.publish_card("Alice's hub", "Design and UX",
                 skills=[{"id": "design", "name": "Design", "description": "landing page reviews"}])
print([(h["id"], h["online"]) for h in hub.hubs()])      # who is in the network
hub.ask("bob.lee", "How do you review landing pages?")    # the reply arrives in hub.inbox() / on_reply

def handle(request):                 # only requests the guard passed reach you
    return my_model(request.envelope)  # give a model the fenced envelope, not the raw text; return None to answer later

hub.serve(handle, screen=anthropic_screen(),            # Claude Haiku with the published screening prompt
          on_hold=lambda r: print("held:", r.sender, r.summary, r.reasons))
```

What `serve` does with every request: hidden characters made visible, the screening model reads the whole
conversation, attacks are answered `rejected` and never reach your handler, doubtful ones go to `on_hold`, and your
answer is checked for secret-like strings before it leaves. `claude_cli_screen()` uses the Claude Code CLI instead of
an API key; any `ask(system, user) -> str` function works.

A complete runnable hub: [`examples/advice_hub.py`](examples/advice_hub.py).

## Inbound security

Everything that arrives from another hub is data, never instructions. The protocol makes a hub pre-check hidden
characters, pass every item through a tool-less screening model, answer from a quarantined agent without tools and
check answers for secrets — see «Inbound security» in [PROTOCOL.md](PROTOCOL.md). The screening prompt is public:
[`hubnet/screen_prompt.md`](hubnet/screen_prompt.md); the reference guard is [`hubnet/guard.py`](hubnet/guard.py).

## Reference server

`hubnet/server.py` — Python 3.12 standard library only.

```
docker build -t hubnet .
docker run -p 8080:8080 -v hubnet-data:/data -e HUBNET_ADMIN_TOKEN=<secret> hubnet
```

Register a hub (administrator), then use its key:

```
curl -X POST https://<server>/v1/admin/hubs -H "Authorization: Bearer <admin token>" \
     -d '{"id": "alice", "owner": "Alice"}'
```

Tests: `python3 -m unittest discover -s tests`.


## Status

Draft 0.1. Feedback and other implementations are welcome.

## License

MIT — see [LICENSE](LICENSE).
