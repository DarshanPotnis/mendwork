# 11. Approvals, egress, and interrupted runs

- **Status:** Accepted
- **Date:** 2026-09-14

## Finding F1: Playwright request routing cannot see a redirect hop

`ARCHITECTURE.md` §8 and the Phase 7 plan said egress would be enforced "before navigation and via
Playwright request routing". Before designing around that, it was measured (Playwright 1.62,
Chromium 151, a local server that answers `/to-meta` with a 302 to `http://169.254.169.254/`):

- A `page.route("**/*")` handler is called for the first request of a redirect chain and never for
  the hops that follow. With `route.continue_()`, the handler saw `/to-meta` once; Chromium then
  followed the 302 to the metadata address without the handler running again.
- The documented workaround, fetching the request in the handler with `max_redirects=0` and
  fulfilling each 3xx by hand, does not change that: the fulfilled redirect is again followed by the
  browser, outside the route.
- A per-context SOCKS5 proxy (`socks5://127.0.0.1:<port>`, bypass `<-loopback>`) saw every
  connection, every hop included, with the host name the page asked for. Refusing one gives the
  page `net::ERR_SOCKS_CONNECTION_FAILED`, the same code a refused upstream gives, so the proxy
  must record which of the two happened.
- The DevTools protocol's `Fetch` domain with a `Document` pattern paused every hop (each carrying
  `redirectedRequestId`), a failed request was never sent, and it coexisted with Playwright routes
  and the portal's blob download.
- Cost per chaos portal page load: direct 20–26 ms, with a route handler 36–48 ms, through the
  proxy 29–35 ms.

A policy enforced by routing would have let any allowed site redirect the browser to an internal
address or a cloud metadata endpoint. That is the attack an egress policy exists to stop, so the
documented design was replaced.

## Context

Phase 7 makes three safety promises real:

- **Egress.** A run can only reach the sites a workspace named, and never internal networks,
  whatever a page, a redirect, or a DNS answer tries.
- **Approvals.** An irreversible step never acts on a heal by itself. A person decides; the
  decision is audited; a resumed run acts only on what the person saw.
- **Interrupts.** A run stopped by Ctrl+C, a second Ctrl+C, or a crash leaves a record that says
  truthfully whether an irreversible action may have been sent.

Resolved secrets also had to be removed from log lines, which only redacted fields by name.

## Decision

### Egress: three layers

1. **The engine** (`NavigationGuard`) checks every navigate URL before the browser is asked, and
   every URL known up front (literals and bound inputs) before a run id exists: scheme, URL shape,
   allowlist, and the addresses the host resolves to. A failed lookup is a navigation error, retried
   like any transient failure; a refusal is `EgressBlocked`, never retried, never healed.
2. **The document filter** (DevTools `Fetch`, `Document` requests, the run's own page) checks every
   document request, every redirect hop included: scheme, URL shape, literal addresses, and, for
   the page itself but not its frames, the allowlist. A refusal fails the request before it is sent.
3. **The SOCKS5 gateway**, one per browser session on `127.0.0.1:0`, carries every connection of
   the run's context. It resolves a name once, refuses the connection when any address is internal
   or a metadata endpoint, and connects only to the addresses it checked, so a name cannot resolve
   to a public address for the check and a private one for the connection. It records refusals and
   upstream failures, so `ERR_SOCKS_CONNECTION_FAILED` is reported as the refusal or the failure it
   was, and retries stay correct. The context blocks service workers, and Chromium is launched with
   `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`.

What each layer refused reaches the step through `BrowserPort.take_egress_blocks`, and the step
fails with the first refusal's rule, host, and layer.

**Rules.** Deny by default: an empty allowlist allows no navigation. Allowlist entries are exact
names or `*.example.com` (subdomains only). Refused addresses: loopback, RFC 1918 and IPv6 ULA,
100.64.0.0/10, link-local, multicast, unspecified, every other non-global range, and named metadata
endpoints (169.254.169.254, 169.254.170.2, fd00:ec2::254, 100.100.100.200, 192.0.0.192, and the
publicly routable 168.63.129.16). IPv4 inside IPv6 (mapped, compatible, 6to4, Teredo, NAT64) is
checked as the IPv4 it carries, and NAT64 is refused outright. Hosts are parsed as a browser parses
them, numeric IPv4 forms such as `http://2130706433/` included.

**Local testing exception.** `MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS` names exact literal loopback
`ip:port` origins (the chaos portal, fixture sites). A name resolving to that address is still
refused, and Settings refuses any exception when `MENDWORK_ENVIRONMENT=production`.

### Approvals

- **The proposal.** A heal accepted for an irreversible step stops the run `awaiting_approval` with a
  `HealProposal`: an id (`<step id>-<n>`), the step, the rung, the candidate and its confirmed
  identity, score, margin, threshold, required margin, a model's reason, and the element's
  *identity signature*: tag, role, normalized name, type, id, name attribute, test id, href,
  structural path, and nearby text, scrubbed of secrets. Its position on the page is recorded as
  evidence and shown to the person, and is **never** matched: a cookie banner, a viewport, or fonts
  move an element without changing what it is. A test puts a cookie banner above the button between
  pause and approval and the approval still acts.
- **Commands.** `mendwork show <run>` shows the run as it stands with each proposal's evidence and
  screenshot; `mendwork approve <run> <proposal>` and `mendwork reject <run> <proposal> [--reason]`
  decide one. Nothing is recorded for an unknown or decided proposal, a run another process holds, or
  (for an approval) a run that cannot resume, a missing secret, or a URL today's policy refuses.
- **The audit log** (`AuditLog`, files for now): `<artifacts>/audit/audit.jsonl`, created 0600, one
  entry per decision, numbered and chained by SHA-256 over canonical JSON, appended under an
  exclusive `flock` with one write and an fsync. Every read and append checks the whole chain; a
  broken chain (edited, removed, reordered, or cut-short entry) refuses every further decision.
- **Order of writes.** The run is claimed (an exclusive `flock` on its directory); the audit entry is
  written first, then `run.json`. Both writes finish even if an interrupt arrives. A record that says
  approved always has the entry that proves it; a process that stops between them leaves an entry
  that the next command, or `show`, applies (`as_left`).
- **Rejection** ends the run `failed` with `ProposalRejected`; the reason is one line of at most 500
  characters, scrubbed of the run's secrets.
- **Resume** (D3–D5). An approval resumes the run at once, as a new segment of the same record, in a
  new browser held to today's policy. Steps 1..k−1 are replayed quietly (no events, earlier results
  stand; a step healed earlier is found again by its identity). Step k then runs from Rung 0:
  - the recorded element found again: the step runs as recorded, the proposal was not needed (D4);
  - otherwise the ladder runs **without a model**, and a heal may act only on the approved element:
    the same identity signature and the same confirmed role and name. A match passes the
    irreversible gate and every other check and checkpoint; an unverified irreversible action ends
    `needs_review`;
  - anything else (an earlier step that cannot be replayed, nothing accepted, another element, a
    changed identity) fails the step with `ApprovalStale`, acts on nothing, and fails the run with
    exit 1. No new proposal is made (D5); a fresh run makes one.
  - The outcome is recorded on the proposal: `acted_verified`, `acted_unverified`, `not_needed`,
    `stale` (with the reason), `interrupted`, or `not_resumed`.
- **What cannot resume**: a version 1 record (D7), a `workflow.json` whose digest changed, an
  earlier step that did not succeed, an irreversible step before the approved one (replaying it would
  repeat its action), or an input that held a secret's value (the record keeps only the redacted
  form).
- **One record.** Events continue the run's sequence (`run_resumed`); evidence is named for its
  segment (`steps/009_download_csv.segment2.png`), so the pause's evidence stays; model calls count
  against the run's budget; durations add up, time waiting for approval excluded.

### Interrupted runs

- **The journal.** `run.json` is rewritten atomically at start, after every step, just before an
  irreversible action is dispatched, and at the end. Artifact writes to one path are applied in the
  order they were issued, so a slow earlier write never overwrites a later one.
- **First Ctrl+C or SIGTERM.** The run's task is cancelled. The step in progress is recorded without
  touching the browser again (`action_outcome_unknown` when the action was on its way), the run ends
  `cancelled` or, when any irreversible dispatch was journaled, `needs_review`, `run_finished` is
  emitted, and the command exits 130 or 4 (D6). A decision being recorded finishes first.
- **Second Ctrl+C.** The record is finished from the journal on disk, synchronously, and the process
  ends at once; Playwright's driver and Chromium end with it.
- **A process that ended without either** leaves a `running` record nobody claims; `show`, `approve`,
  and `reject` read it as ended (`process_ended`).

### Redaction completeness

Every command wraps its `SecretResolver` in `RegisteringSecretResolver`, which registers each value
with the process's `SecretScrubber` the moment it is resolved. The log pipeline scrubs each rendered
line, tracebacks included, of every registered value in every encoding the scrubber covers, after
the field-name redaction. The scrubber replaces its sets instead of changing them, so a log handler
on another thread always reads a whole set. The end-to-end leak search covers a pause, `show`,
`approve`, and a `reject` with the secret in its reason.

## Alternatives considered

- **Playwright routing, as documented.** Rejected by F1: blind to redirect hops.
- **Routing plus `max_redirects=0` and manual fulfilment.** Measured blind as well.
- **The document filter alone.** Sees every document and hop, but not subresources, WebSockets, or
  what a page fetches, and it does not control DNS: a name could pass a check and connect elsewhere.
- **The gateway alone.** Sees every connection, but not the URL: it cannot enforce schemes or refuse a
  malformed URL the browser would read differently, and its refusals reach the page as a generic
  connection failure.
- **An HTTP proxy.** Would see full URLs for HTTP only; HTTPS arrives as `CONNECT host:port`, the same
  information SOCKS5 gives, with more protocol to implement.
- **Resume in the paused browser.** Keeps page state, but holds a browser for as long as a person
  takes to decide, and does not survive the command ending. Replaying in a new browser (D3) keeps the
  run stateless while it waits, at the cost of refusing resumes past an earlier irreversible step.
- **Re-proposing when the page changed.** Rejected (D5): an approval would silently become consent to
  an element nobody saw.
- **Matching the approved element including its position.** Rejected by review: layout shifts are
  common and change nothing about what the element is; the position is shown as evidence instead.

## Consequences

- A run navigates nowhere until its sites are allowlisted, and local test runs name their loopback
  origin. Every browser test and benchmark does.
- Egress adds a local SOCKS5 relay per session (measured +9 ms per portal page load) and a DevTools
  session per page.
- Approving takes as long as replaying the steps before the approved one.
- `run.json` is version 2; version 1 records still read.
- Exit codes: 130 for a cancelled run; 4 still covers everything that needs a person.

### Limitations

- A run cannot resume past an earlier irreversible step; it must be run again.
- Pending approvals do not expire; a page can drift arbitrarily far before someone approves, and
  resume then fails stale rather than acting.
- The recorder is not held to the egress policy: a person drives that browser.
- Pages the run did not open (popups) are not attached to the document filter; their connections
  still pass the gateway, and the step that opened them fails.
- `*.co.uk` is accepted as an allowlist entry; there is no public-suffix check.
- The WebRTC launch flag is set but no test proves WebRTC cannot leave the proxy.
- A second Ctrl+C that lands between the journal write before an irreversible dispatch and the
  dispatch itself records `needs_review` for an action that was never sent: the safe direction.
- A secret first typed after an earlier step's verified heal was recorded may scrub that heal's
  identity differently on resume, which makes the resume stale rather than wrong.
