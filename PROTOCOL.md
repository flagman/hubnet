# Hubnet 0.1 — a profile of A2A for networks of agent hubs

Status: draft. Hubnet lets independent *agent hubs* — one person's fleet of AI agent sessions — find each other,
see who is online and what they are good at, and exchange requests: ask for advice, ask a question in someone's area,
request context. Agents and sessions never talk across hubs directly: only hub ↔ hub, and every hub owner decides
what leaves their hub.

Hubnet does not invent a message format. It is a **profile of [A2A](https://a2a-protocol.org/latest/specification/)
(Agent2Agent) 1.0**: hubs describe themselves with A2A Agent Cards and exchange A2A `Message`s inside A2A `Task`s.
What Hubnet adds is what A2A leaves open for this setting: a directory, presence, a relay for hubs behind NAT, and
owner consent.

## Roles

- **Hub** — an A2A agent that represents one person and their sessions. It runs on a laptop or a home machine and
  makes outbound connections only.
- **Network server** — directory, presence and relay. One per network (a company, a community). It stores Agent
  Cards, queues messages for offline hubs and keeps a content-free traffic log.

## Identity

The network administrator registers a hub and gets its key once: `POST /v1/admin/hubs {id, owner}` → `{key}`.
The server stores only a hash. A hub authenticates every call with `Authorization: Bearer <key>`. Hub ids are
lowercase `first.last`-like names (`[a-z0-9]` groups joined by `.` or `-`); the administrator retires a hub with
`DELETE /v1/admin/hubs/{id}`.

### Connect tokens

The long-lived hub key never has to travel between people. The administrator issues a **connect token** for a hub
id — short-lived (15 minutes by default, at most 24 hours) and single-use:
`POST /v1/admin/connect-tokens {id, owner, ttl}` → `{token, expires}`. The person gives it to their hub, and the hub
trades it for its key: `POST /v1/connect {token}` → `{id, key, server}`. An unknown, used or expired token gets 401.
A token for an existing hub id replaces its key, so the same flow re-connects a hub and revokes the old key. The
server stores only hashes of tokens and keys.

## Agent Card

A hub publishes its card with `PUT /v1/card`. Besides the A2A fields (`name`, `description`, `version`, `skills`):
each **skill** is something other hubs may ask for — usually one per long-lived agent of the hub (its area: DevOps,
SEO, design…). Tags carry areas of competence.

## Presence

Any authenticated call marks the hub online for 90 seconds; a hub keeps a long poll on its inbox, so it stays online
while it runs. `GET /v1/hubs` lists hubs with their cards and `online`.

## Requests

1. Hub A: `POST /v1/hubs/{B}/message {message: <A2A Message>}` → `202 {task: <A2A Task, state "submitted">}`.
2. Hub B: `GET /v1/inbox?wait=25` → `{items: [{kind: "request", from: "A", task, message}]}`.
3. Hub B decides (consent, below) and answers: `POST /v1/tasks/{id}/reply {message, state}` with an A2A task state:
   `working`, `input-required`, `completed`, `rejected`. Only the addressee may answer.
4. Hub A gets `{kind: "reply", task, message}` from its own inbox.

## Consent (normative)

A hub MUST NOT send anything out without its owner's policy allowing it. Default policy:

- **advice** — answers within the hub's declared skills, written by its agents — may go without asking;
- **context** — files, code, conversations, logs, anything from the owner's accounts — only after the owner's
  explicit yes for that request;
- secrets — never.

Incoming requests are shown to the owner; the hub never executes instructions from another hub as its own owner's.

## Inbound security (normative)

**Everything that arrives from the network is data from an untrusted sender, never instructions** — requests,
replies, follow-ups in the same task, and Agent Cards alike. A classifier or a guard model alone does not make this
true: public attacks have bypassed production classifiers. So a hub MUST meet all of the following; the screen is
one layer, the structure is what holds.

1. **Pre-check, deterministic.** Before any model reads an item, the hub makes hidden characters visible (Unicode tag
   characters, zero-width and bidi controls, variation selectors), refuses non-text parts unless the owner allows
   them, and caps the size. Any finding holds the item for the owner.
2. **Screen.** Every item passes a screening model that has no tools and no access to the owner's data, with the
   published prompt [`screen_prompt.md`](hubnet/screen_prompt.md) or a stricter one. It sees the whole conversation of the
   task, not only the last message (attacks build up over turns), inside fences with a random marker the sender
   cannot close. It answers `clean`, `suspicious` or `attack`; anything else — an error, a timeout, a broken format —
   counts as `suspicious`. A hub MAY add an injection classifier; it may only raise the verdict.
3. **Decision.** `clean` with no findings — delivered; `suspicious` — held until the owner looks; `attack` — not
   delivered, the owner sees it with the reasons, and the hub MAY answer `rejected`.
4. **Envelope.** A delivered item reaches an agent only fenced and labelled as another hub's words, never in a
   system prompt, never as if the owner wrote it.
5. **Quarantine.** The agent that answers a network request works without tools that act or send (shell, browser,
   mail, messengers, other MCP servers) and without the owner's private context. It writes advice. Doing anything
   for the sender — running, sending, sharing context — is a new request to the owner, not a step the agent takes.
6. **Outbound.** Before an answer leaves the hub, it is checked against the consent policy above and for
   secret-like strings; a match holds it for the owner.
7. **Cards.** Cards are pre-checked and screened like messages before a model uses them to choose whom to ask; the
   server refuses cards with hidden characters or over-long fields.
8. **Limits.** The server limits requests per sending hub (30 a minute in the reference server).

The screen prompt is public on purpose: the protection must not depend on its secrecy, and every hub implementing
the standard can use, review and improve the same text. The reference implementation is
[`guard.py`](hubnet/guard.py) (standard library only).

### Threats this answers

| Attack (public) | What stops it |
|---|---|
| Direct and indirect prompt injection: «ignore previous instructions», fake system or owner messages | 2, 3, 4 |
| Agent session smuggling — covert instructions spread over a stateful A2A conversation ([Unit 42](https://unit42.paloaltonetworks.com/agent-session-smuggling-in-agent2agent-systems/)) | 2 (whole conversation), 5, 3 |
| Agent Card poisoning — injection in card descriptions to hijack routing ([Trustwave, via Semgrep's A2A guide](https://semgrep.dev/blog/2025/a-security-engineers-guide-to-the-a2a-protocol/)) | 7 |
| Hidden text: Unicode tag «ASCII smuggling», zero-width, bidi, emoji variation selectors | 1 |
| Exfiltration through an agent's tools — the «lethal trifecta» of private data, untrusted input and a way out; EchoLeak in a production assistant | 5, 6 |
| Tool poisoning ([OWASP: MCP tool poisoning](https://owasp.org/www-community/attacks/MCP_Tool_Poisoning)) | 5: the answering agent has no tools |
| Bypassing the classifier itself | 5 and 6 hold even when 2 fails |
| Flooding a hub or its owner with requests | 8, and 3 (held items wait, they do not run) |

The design follows the dual-LLM / quarantine idea ([CaMeL, Google DeepMind](https://arxiv.org/abs/2503.18813)):
the model that reads untrusted text cannot act, and the one that can act does not take orders from that text.

## Traffic log

`GET /v1/traffic` — who asked whom, when, task state, size. No content: content is seen only by the two hubs.

## Transport

HTTPS with long polling. No inbound connections to hubs, no peer-to-peer.
