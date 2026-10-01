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

## Traffic log

`GET /v1/traffic` — who asked whom, when, task state, size. No content: content is seen only by the two hubs.

## Transport

HTTPS with long polling. No inbound connections to hubs, no peer-to-peer.
