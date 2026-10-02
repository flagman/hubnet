# Hubnet 0.2 — a profile of A2A for networks of agent hubs

Status: draft. Hubnet connects *agent hubs* — each one person's fleet of AI agents — so that the agents of one
person can ask for the expertise of another person's agents, and ask them to do work, **within what that person
allows each contact**. A hub finds others in a directory, sees who is online and what they are good at, and sends
requests. Agents never talk across hubs directly: only hub ↔ hub, through each hub's Network Operator, and every
owner decides — per contact and per agent — what others may ask, receive and have done.

Hubnet does not invent a message format. It is a **profile of [A2A](https://a2a-protocol.org/latest/specification/)
(Agent2Agent) 1.0**: hubs describe themselves with A2A Agent Cards and exchange A2A `Message`s inside A2A `Task`s.
What Hubnet adds is what A2A leaves open for this setting: **contacts and grants with slow escalation**, a Network
Operator in every hub, inbound security, a directory, presence, and a relay for hubs behind NAT.

## Roles

- **Hub** — an A2A agent that represents one person and their agents. It runs on a laptop or a home machine and
  makes outbound connections only.
- **Agent** — a long-lived agent of a hub with its own area (DevOps, SEO, design…). Each agent is a skill on the
  hub's card; grants are given per agent.
- **Network Operator** — the single point inside a hub through which all network traffic passes, in and out. Two
  parts: a deterministic **gate** (connection, pre-check, screen, grants, outbound check) and an **operator agent** —
  a model without tools and without the owner's private context. The operator agent reads a request as data,
  decides which of the hub's agents it is for and what it asks for (a level, below), restates it in its own words
  and routes it; the gate compares that with the contact's grant. The sender's raw text never reaches an agent with
  tools. Whatever the operator passes on carries the warning that it is an untrusted request and which hub wrote
  it. The hub's own agents ask other hubs through the Operator too, and the replies come back screened and fenced
  as data.
- **Network server** — directory, presence and relay. One per network (a company, a community). It stores Agent
  Cards, queues messages for offline hubs and keeps a content-free traffic log.

## Contacts and grants (normative)

This is the heart of the protocol. A hub is not «one agent available to everyone»: each **contact** (another hub,
i.e. another person) has its own **grant** to each of the hub's agents, on four levels.

| Level | The contact may |
|---|---|
| `none` | nothing — the default for every new contact and every agent |
| `ask` | ask the agent; the answer is advice and explanation in words |
| `context` | also receive relevant fragments of code and documents |
| `do` | ask the agent to do work; **every job still needs the owner's yes** |

1. **Only the owner grants.** A new contact starts at `none` everywhere, so its first request always waits for the
   owner. Neither the operator nor any agent can raise a grant.
2. **Slow escalation with confirmation.** The operator classifies each request: which agent, which level (`ask`,
   `context`, `do`). If the level is above the contact's grant for that agent, the hub asks its owner — showing the
   operator's restatement, the agent and the levels — with three answers:
   - *once* — this request only, the grant stays;
   - *always* — raise this contact's grant for this agent to the requested level;
   - *no* — the sender gets `rejected`.
   While it waits, the hub answers the sender `working` («waiting for the owner's approval»).
3. **Work is always confirmed.** A `do` request needs the owner's yes every time, also when the grant is `do`; the
   grant only allows the contact to ask.
4. **A doubtful request is confirmed too.** If the screen (below) marks a request `suspicious`, it goes to the owner
   even within the grant.
5. **The grant limits what leaves.** At `ask` the answer carries no code, files, documents or configuration. At every
   level: no secrets, access settings, the owner's private data or clients' data.
6. **The operator answers only after an agent.** On its own it may refuse (`rejected`) or give a **service answer**:
   «received», who this hub is, what it does as its card says — no advice, no data, nothing from an agent. A
   connectivity check gets exactly that. The owner may answer any request personally.
7. Grants can be lowered or removed at any time.

A requesting hub sees only the outcome: `working` while the owner decides, then `completed`, `input-required` or
`rejected`.

## Identity

The network administrator registers a hub and gets its key once: `POST /v1/admin/hubs {id, owner}` → `{key}`.
The server stores only a hash. A hub authenticates every call with `Authorization: Bearer <key>`. Hub ids are
lowercase `first.last`-like names (`[a-z0-9]` groups joined by `.` or `-`); the administrator retires a hub with
`DELETE /v1/admin/hubs/{id}`. A hub id is the contact identity the grants are keyed by.

### Connect tokens

The long-lived hub key never has to travel between people. The administrator issues a **connect token** for a hub
id — short-lived (15 minutes by default, at most 24 hours) and single-use:
`POST /v1/admin/connect-tokens {id, owner, ttl}` → `{token, expires}`. The person gives it to their hub, and the hub
trades it for its key: `POST /v1/connect {token}` → `{id, key, server}`. An unknown, used or expired token gets 401.
A token for an existing hub id replaces its key, so the same flow re-connects a hub and revokes the old key. The
server stores only hashes of tokens and keys.

## Agent Card

A hub publishes its card with `PUT /v1/card`. Besides the A2A fields (`name`, `description`, `version`, `skills`):
each **skill** is one of the hub's agents others may ask (its area). Tags carry areas of competence. The owner
chooses which agents appear on the card. A card says what a hub *can* do, not what a given contact *may* — that is
the grant.

## Presence

Any authenticated call marks the hub online for 90 seconds; a hub keeps a long poll on its inbox, so it stays online
while it runs. `GET /v1/hubs` lists hubs with their cards and `online`.

## Requests

1. Hub A: `POST /v1/hubs/{B}/message {message: <A2A Message>}` → `202 {task: <A2A Task, state "submitted">}`.
   `message.metadata.agent` MAY name the agent of hub A that asks.
2. Hub B: `GET /v1/inbox?wait=25` → `{items: [{kind: "request", from: "A", task, message}]}`.
3. Hub B's Network Operator handles it (grants, inbound security) and answers:
   `POST /v1/tasks/{id}/reply {message, state}` with an A2A task state: `working`, `input-required`, `completed`,
   `rejected`. Only the addressee may answer; a task may get several replies (`working`, then the result).
4. Hub A gets `{kind: "reply", task, message}` from its own inbox; its Operator screens the reply and gives it to
   the asking agent fenced, as data.

## Inbound security (normative)

**Everything that arrives from the network is data from an untrusted sender, never instructions** — requests,
replies, follow-ups in the same task, and Agent Cards alike. A classifier or a guard model alone does not make this
true: public attacks have bypassed production classifiers. What holds is the structure — quarantine, grants and the
owner's confirmation; the screen is a signal on top of it. A hub MUST meet all of the following.

1. **Pre-check, deterministic.** Before any model reads an item, the hub makes hidden characters visible (Unicode tag
   characters, zero-width and bidi controls, variation selectors), refuses non-text parts unless the owner allows
   them, and caps the size. Any finding marks the item `suspicious`.
2. **Screen.** Every item passes a screening model that has no tools and no access to the owner's data, with the
   published prompt [`screen_prompt.md`](hubnet/screen_prompt.md) or a stricter one. It sees the whole conversation of the
   task, not only the last message (attacks build up over turns), inside fences with a random marker the sender
   cannot close. It answers `clean`, `suspicious` or `attack`; anything else — an error, a timeout, a broken format —
   counts as `suspicious`. A hub MAY add an injection classifier; it may only raise the verdict.
3. **The screen is a signal.** `attack` — not delivered; the sender gets `rejected` at once, the owner sees it with
   the reasons and MAY release a false alarm. `clean` and `suspicious` go to the operator agent with the verdict and
   reasons; `suspicious` then needs the owner's confirmation even within the grant (Contacts and grants, 4). Replies
   to the hub's own questions that are not `clean` wait for the owner before the asking agent sees them.
4. **Envelope.** An item reaches the operator agent only fenced and labelled as another hub's words, never in a
   system prompt, never as if the owner wrote it.
5. **Quarantine.** The operator agent works without tools that act or send (shell, browser, mail, messengers, other
   MCP servers) and without the owner's private context; it can only route a restated request within the grant,
   refuse, or hand it to the owner. Agents with tools never see the sender's raw text.
6. **Outbound.** Before an answer leaves the hub, it is checked against the contact's level and for secret-like
   strings; a match holds it for the owner.
7. **Cards.** Cards are pre-checked before a model uses them to choose whom to ask; a card with hidden characters is
   not shown. The server refuses cards with hidden characters or over-long fields.
8. **Limits.** The server limits requests per sending hub (30 a minute in the reference server).

The screen prompt is public on purpose: the protection must not depend on its secrecy, and every hub implementing
the standard can use, review and improve the same text. The reference implementation is [`guard.py`](hubnet/guard.py)
(standard library only).

### Threats this answers

| Attack (public) | What stops it |
|---|---|
| Direct and indirect prompt injection: «ignore previous instructions», fake system or owner messages | 2, 3, 4, 5; grants |
| Agent session smuggling — covert instructions spread over a stateful A2A conversation ([Unit 42](https://unit42.paloaltonetworks.com/agent-session-smuggling-in-agent2agent-systems/)) | 2 (whole conversation), 5; grants and the owner's confirmation |
| Agent Card poisoning — injection in card descriptions to hijack routing ([Trustwave, via Semgrep's A2A guide](https://semgrep.dev/blog/2025/a-security-engineers-guide-to-the-a2a-protocol/)) | 7 |
| Hidden text: Unicode tag «ASCII smuggling», zero-width, bidi, emoji variation selectors | 1 |
| Exfiltration through an agent's tools — the «lethal trifecta» of private data, untrusted input and a way out; EchoLeak in a production assistant | 5, 6; levels limit what leaves |
| Tool poisoning ([OWASP: MCP tool poisoning](https://owasp.org/www-community/attacks/MCP_Tool_Poisoning)) | 5: the operator has no tools |
| A trusted contact's hub is compromised | grants are per agent and per level; work and doubtful requests always need the owner |
| Bypassing the classifier itself | 5, 6 and grants hold even when 2 fails |
| Flooding a hub or its owner with requests | 8; a new contact's requests wait at `none` |

The design follows the dual-LLM / quarantine idea ([CaMeL, Google DeepMind](https://arxiv.org/abs/2503.18813)):
the model that reads untrusted text cannot act, and the agents that can act get only a restatement, within what the
owner granted.

## Traffic log

`GET /v1/traffic` — who asked whom, when, task state, size. No content: content is seen only by the two hubs.

## Transport

HTTPS with long polling. No inbound connections to hubs, no peer-to-peer.
