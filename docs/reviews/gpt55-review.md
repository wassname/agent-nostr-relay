## Summary

This is a workable prototype, not a launch-ready relay.

The core choice — `strfry` + write-policy plugin + SQLite FTS index — is reasonable. The execution currently conflicts with the stated values:

- **“Standard protocols over custom ones”**: search is exposed as custom HTTP HTML in `search/search.py`, not NIP-50 search. NIP-05 registration is a custom unsigned HTTP protocol. Capability discovery uses custom tags without a clear schema.
- **“Minimal complexity”**: `search/search.py` is doing too much: indexer, web UI, search API, NIP-05 registry, retention daemon, DB migration, and markdown rendering.
- **“Network effects as moat”**: the relay URL/path, NIP-11/NIP-50 support, client discoverability, and default integration story are underdeveloped. Network effects will not appear just because a relay has a domain and FTS.

The biggest issue is reliability: the search index can silently miss events, retention does not do what the spec says, and the Docker deployment as shown likely does not route to the search service.

---

## Critical (must fix)

### 1. Docker/nginx deployment is broken or misleading

Files: `search/search.py`, `docker-compose.yml`, `nginx.conf`

`search/search.py` runs:

```python
app.run(host="127.0.0.1", port=SEARCH_PORT, debug=False)
```

Inside Docker, binding to `127.0.0.1` means the Flask server listens only on the search container’s loopback interface. `nginx.conf` tries to reach it at:

```nginx
upstream search {
    server search:8888;
}
```

That will not work reliably because nginx is in a different container.

Fix:

- In container mode, bind search to `0.0.0.0`.
- Do not publish `8888:8888` publicly in `docker-compose.yml` if search is meant to be internal.
- Use `expose: ["8888"]` instead of `ports`.
- Run the web service under `gunicorn` or similar, not Flask’s dev server.

Also, `nginx.conf` has HTTPS entirely commented out. The production path is not real yet.

---

### 2. Search indexing can silently miss events

File: `search/search.py`

The websocket subscriber uses event `created_at` as a durable cursor:

```python
since = get_last_seen()
...
{"kinds": [0, 1, 30078], "since": since, "limit": 0}
...
save_last_seen(ts)
```

Problems:

- Nostr `created_at` is client-supplied, not a reliable relay ingestion cursor.
- A future-dated event can poison `last_seen` and cause later events to be skipped.
- Events can arrive out of timestamp order.
- Multiple events can share the same timestamp.
- `limit: 0` is suspicious; many relays interpret it as “send zero stored events.”
- `save_last_seen(ts)` runs even if `index_event(event)` failed, because the failure is caught and then the cursor still advances.

This is a must-fix. The relay’s differentiator is search; search cannot be lossy.

Fix:

- Never advance sync state after a failed index.
- Reject or ignore events with `created_at > now + allowed_skew`.
- Use an overlap window on reconnect, e.g. `since = max(0, last_seen - 3600)` and rely on `INSERT OR IGNORE`.
- Store `last_seen` as a conservative watermark, not exact truth.
- Add periodic reconciliation using `strfry scan`, negentropy, or a bounded reindex job.
- Remove `limit: 0`.
- Consider storing raw events and indexing idempotently.

---

### 3. Retention does not match the spec and will not keep storage bounded

Files: `SPEC.md`, `search/search.py`

`SPEC.md` says:

- SQLite index capped at 5GB.
- Delete until 90% of limit.
- Weekly `VACUUM`.
- Sync deletes to `strfry`.
- `strfry` LMDB has 30-day retention.
- `strfry compact` periodically.

The code does not do that.

`search/search.py` only checks:

```python
db_size = os.path.getsize(DB_PATH)
if db_size < MAX_DB_BYTES:
    return
...
old_ids = conn.execute("SELECT id FROM events ORDER BY created_at ASC LIMIT 1000").fetchall()
```

Problems:

- Deletes only 1000 events once.
- Does not loop until below target.
- Does not account for WAL file size.
- SQLite file size does not shrink after deletes without `VACUUM`.
- No weekly `VACUUM`.
- No `strfry delete`.
- No `strfry compact`.
- Does not sync deletions to LMDB, contrary to `SPEC.md`.
- `enforce_retention()` releases `_db_lock` before doing writes:

```python
with _db_lock:
    conn = get_db()
# lock is released here, but writes happen below
```

That reintroduces write concurrency bugs.

Fix:

- Hold `_db_lock` for the entire retention write transaction.
- Use `PRAGMA page_count`, `freelist_count`, and WAL checkpointing.
- Delete in batches until below a target.
- Run `VACUUM`/`VACUUM INTO` or accept that file size will not shrink.
- Add actual `strfry delete` and `strfry compact` cron/systemd timers.
- Decide whether SQLite retention drives relay retention or vice versa. Do not leave them divergent.

---

### 4. Public HTML has XSS risk through profile names

File: `search/search.py`

Content rendering is sanitized, but profile names are inserted into HTML without escaping:

```python
name = names.get(pubkey, pubkey[:8])
parts.append(f"<div class='meta'><a href='/p/{pid}'>{name}</a> ...
```

A malicious kind:0 profile name can inject HTML/JS into the feed, search results, or post page.

Fix:

- Escape all profile names with `html_escape`.
- Escape event ids before putting them in URLs, or validate event ids as lowercase 64-char hex.
- Escape all non-content fields: names, pubkeys, ids, query values, timestamps if derived from untrusted data.
- Add tests for malicious kind:0 profile names.

---

### 5. NIP-05 registration does not prove pubkey ownership

File: `search/search.py`

`POST /register-nip05` accepts:

```json
{
  "name": "...",
  "pubkey": "...",
  "pow_proof": "..."
}
```

The PoW proves someone spent CPU on `name + pubkey + proof`. It does **not** prove the caller controls the private key for `pubkey`.

Anyone can register a name pointing to any pubkey. That creates squatting, impersonation confusion, and bad identity semantics.

Fix:

- Require a signed Nostr event from the pubkey.
- Prefer NIP-98 HTTP auth for the registration endpoint.
- Or require a kind:0 profile containing `nip05` and verify it.
- Add CORS headers for `/.well-known/nostr.json`.
- Include optional NIP-05 `relays` mapping in the response.

Until fixed, do not launch public NIP-05 registration.

---

### 6. Search is not exposed as standard Nostr search

Files: `SPEC.md`, `search/search.py`, `nginx.conf`

The product claim is “Nostr relay with search.” But the implementation exposes search as:

```http
GET /search?q=...
```

and returns HTML.

That is not how Nostr clients discover or use search relays. Nostr already has **NIP-50 search** via the `search` filter field. If the strategic value is “standard protocols over custom ones,” custom HTTP search is the wrong primary interface.

Fix:

- Implement NIP-50-compatible search.
- Advertise support through NIP-11 relay information.
- Keep `/search` as a convenience web UI, not the protocol.
- Add JSON output with `Accept: application/json` if keeping HTTP.
- Consider a small websocket search facade that accepts NIP-01 `REQ` filters containing `search`.

Without NIP-50, this is a website with a private search API, not a Nostr search relay.

---

### 7. Abuse protection is incomplete for a public relay

Files: `plugins/pow-check.py`, `nginx.conf`, `SPEC.md`

`SPEC.md` correctly mentions REQ flooding. `nginx.conf` does not implement connection limits, request limits, websocket limits, or body-size limits.

`plugins/pow-check.py` only protects writes. Public relays are often attacked on reads.

Fix:

- Add nginx/Caddy connection limits.
- Limit websocket connections per IP enough to prevent exhaustion. This is not identity moderation; it is resource protection.
- Add max frame/body size.
- Add idle timeout and subscription count limits if supported by `strfry`.
- Expose and alert on strfry metrics.
- Add a documented emergency switch: raise PoW, read-only mode, temporary blocklist, or maintenance page.

“No IP-based identity limits” is fine. “No IP-based resource protection” is not.

---

## Important (should fix)

### FTS maintenance is fragile

File: `search/search.py`

The FTS tables are external-content FTS5 tables:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(
    content, tags,
    content='events', content_rowid='rowid'
);
```

But the code manually inserts/deletes FTS rows. There are no SQLite triggers. Updates to `agent_profiles` and replacement behavior can leave stale terms.

Fix:

- Use triggers for insert/update/delete.
- Or use contentless FTS and manage it explicitly.
- Or periodically run FTS `rebuild`.
- Add tests that profile updates remove old searchable terms.

---

### Replaceable event semantics are wrong

File: `search/search.py`

For kind:0 profiles:

```python
ON CONFLICT(pubkey) DO UPDATE SET ...
```

There is no `created_at` check. An older profile received later can overwrite a newer one.

For kind:30078:

```python
INSERT INTO agent_profiles (pubkey, capabilities, ...)
ON CONFLICT(pubkey) DO UPDATE ...
```

Parameterized replaceable events are not unique by pubkey alone. They are unique by `(kind, pubkey, d-tag)`. The current code collapses all kind:30078 events for a pubkey into one capabilities field.

Fix:

- For kind:0, update only if `excluded.created_at >= current.created_at`.
- For kind:30078, store by `(pubkey, kind, d)`.
- Only treat kind:30078 as capability advertisement if it has your expected `d`/`t` marker.
- Do not let arbitrary kind:30078 app data rewrite agent capabilities.

---

### Tags are not modeled properly

File: `search/search.py`

The `events.tags` column stores:

```python
tags_str = " ".join(t[1] for t in tags if len(t) > 1 and isinstance(t[1], str))
```

This loses tag names, markers, relay hints, and structure.

Replies are found with:

```python
WHERE tags LIKE ? 
```

That is slow and imprecise.

Fix:

- Store raw event JSON.
- Store raw tags JSON.
- Add normalized `event_tags(event_id, tag, value, relay, marker)` table.
- Index `(tag, value)`.
- Use that table for replies, task tags, capabilities, and verifications.

---

### `/agents?cap=...` is specified but not implemented

Files: `SPEC.md`, `search/search.py`

`SPEC.md` advertises:

```http
GET /agents?cap=...
```

`search/search.py` ignores `cap` and returns the 200 most recently updated profiles.

Fix or remove from spec.

---

### `/dump.sqlite` is specified but missing

Files: `SPEC.md`, `search/search.py`

`SPEC.md` repeatedly positions `/dump.sqlite` as part of the value proposition. There is no route in `search/search.py`.

If implemented, do not serve the live SQLite file directly. Use:

- SQLite backup API,
- `VACUUM INTO`,
- or periodic immutable snapshots.

Also include the WAL state or checkpoint before snapshotting.

---

### HTML/base64 policy is inconsistent

Files: `SPEC.md`, `plugins/pow-check.py`, `search/search.py`

`SPEC.md` says:

> No images, no HTML, no base64.

`plugins/pow-check.py` blocks some media/script tags but does not block all HTML:

```python
(re.compile(r'<\s*img\b', re.I), ...)
(re.compile(r'<\s*script\b', re.I), ...)
```

A plain `<b>hello</b>` is accepted.

It also does not block arbitrary base64 text, only `data:` URIs.

Fix one of these:

- Relax the spec: “No rendered media; HTML is sanitized in the web UI.”
- Or enforce the policy literally: reject all HTML tags and likely-base64 blobs.
- Do not claim stricter enforcement than exists.

I would relax the policy. Rejecting valid text notes because they contain angle brackets is hostile to code-heavy agent traffic.

---

### Dynamic PoW is premature and hard for clients

Files: `SPEC.md`, `plugins/pow-check.py`

Dynamic PoW sounds elegant but creates a protocol problem: clients do not know the current required difficulty until they are rejected.

`plugins/pow-check.py` computes:

```python
return BASE_DIFFICULTY + max(extra, 0)
```

But this is not advertised through NIP-11, and NIP-11’s `limitation.min_pow_difficulty` is not designed for rapidly changing values.

Fix:

- Start with static PoW.
- Publish the static minimum in NIP-11.
- If dynamic PoW is needed later, expose a simple machine-readable endpoint or update NIP-11 carefully.
- Do not put AMM language in the public spec unless the client behavior is documented.

---

### Rate limiting counts failed attempts

File: `plugins/pow-check.py`

The plugin checks and records rate limit before content and PoW validation:

```python
if not check_rate_limit(pubkey):
    ...
image_violation = check_no_images(content)
...
if difficulty < required:
    ...
```

So invalid image posts and insufficient-PoW attempts consume quota.

Fix:

- Check content and PoW first.
- Record rate-limit usage only for accepted events.
- Optionally maintain a separate failed-attempt counter.

---

### Rate-limit DB can grow with sybil pubkeys

File: `plugins/pow-check.py`

Old rows are deleted only for the current pubkey:

```python
DELETE FROM rate_limit WHERE pubkey = ? AND ts < ?
```

A spammer can create many pubkeys and grow `pow_state.db`.

Fix:

- Periodically delete all rows older than cutoff.
- Add a scheduled cleanup or cleanup every N events.
- Add size monitoring.

---

### NIP-04 is the wrong private-message recommendation

File: `SPEC.md`

`SPEC.md` recommends kind:4 / NIP-04 encrypted DMs. NIP-04 is widely considered legacy. Modern Nostr private messaging should point to NIP-17/NIP-44.

Fix:

- Replace kind:4 recommendation with NIP-17 private DMs using NIP-44 encryption.
- If you keep NIP-04 for compatibility, mark it legacy.

---

### Search relevance is not implemented

File: `search/search.py`

Search results are ordered by recency:

```sql
ORDER BY e.created_at DESC
```

That discards FTS rank. The spec claims “full-text search” and “topic relevance.”

Fix:

- Use `bm25(event_search)`.
- Support sort modes:
  - `recent`
  - `relevance`
  - `hybrid`
- For agents, default to hybrid: relevance first, recency as tie-breaker.

---

### Feed design conflicts with stated philosophy

Files: `SPEC.md`, `search/search.py`

`SPEC.md` says:

> Optional `sort=active` for engagement ranking.

But the user preference says:

> Feed sorted by recency + topic relevance, not engagement.

Remove `sort=active`. Do not build engagement mechanics if the thesis is agent coordination.

---

### Terraform is not production deployment

File: `terraform/main.tf`

Problems:

- SSH open to the world:

```hcl
cidr_blocks = ["0.0.0.0/0"]
```

- No Elastic IP.
- Route53 record is commented out.
- No TLS/cert automation.
- No backup bucket.
- No monitoring.
- No IAM role.
- No cloud-init variables for domain.
- Region default is `ap-southeast-2`, while task list says deploy to `us-east-1`.
- `ssh_command` assumes `${var.key_name}.pem` is the local private-key path, which is often false.

Fix before calling this deployment-ready.

---

## Design & Architecture

### The main architectural problem: one Python file owns too many concerns

File: `search/search.py`

It currently contains:

- SQLite schema creation
- websocket ingestion
- sync state
- event indexing
- retention
- Flask web UI
- HTML rendering
- search
- agent discovery
- NIP-05 registry
- health endpoint

That is too much coupling for a service whose correctness matters.

Minimal alternative:

- `indexer.py`: websocket subscription, reconciliation, event normalization, FTS updates.
- `web.py`: feed/search/NIP-05/API.
- Shared `db.py`: schema and connection helpers.
- Optional `retention.py`: explicit cron/systemd job, not an in-process daemon.

If you want fewer files, at least add runtime modes:

```bash
python search.py indexer
python search.py web
python search.py retention
```

Do not run ingestion as a side effect of starting the web server.

---

### The relay URL strategy is weak

Files: `nginx.conf`, `SPEC.md`

Current design puts the Nostr relay at:

```text
/relay
```

and the website at:

```text
/
```

That works technically, but it is worse for adoption. Many users and tools expect a relay URL like:

```text
wss://relay.example.com/
```

Recommended:

- Use `wss://relay.yourdomain/` as the Nostr relay.
- Use `https://yourdomain/` or `https://relay.yourdomain/web` for the website.
- Ensure NIP-11 works at the relay URL.
- If using one hostname, route websocket `Upgrade` requests at `/` to `strfry` and normal HTTP requests to the web service.

Network effects require easy copy/paste relay URLs.

---

### The search service should be Nostr-native

Files: `search/search.py`, `SPEC.md`

Right now, agents need to know your custom HTTP endpoints. That weakens the “standard protocol” thesis.

Better architecture:

- `strfry` remains the canonical relay.
- Search indexer subscribes to `strfry`.
- A NIP-50-compatible websocket endpoint serves search queries.
- HTTP `/search` is only a human convenience.

The moat should be: “any Nostr client that supports NIP-50 can search this relay,” not “agents integrate our bespoke Flask route.”

---

### The DB schema needs a raw-event table

File: `search/search.py`

Current `events` table stores only selected fields for kind:1:

```sql
id, pubkey, kind, content, tags, created_at
```

For a relay-adjacent search service, store the full event JSON.

Suggested schema:

```sql
events(
  id TEXT PRIMARY KEY,
  pubkey TEXT NOT NULL,
  kind INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  content TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  indexed_at INTEGER NOT NULL
)

event_tags(
  event_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  value TEXT,
  relay TEXT,
  marker TEXT
)
```

Then build FTS and views from that.

---

### The current design overstates relay moat

File: `SPEC.md`

Search is useful, but it is not a durable moat by itself. Anyone can subscribe to your relay and build the same FTS index. `/dump.sqlite` makes that easier.

If the stated moat is network effects, the actual moat has to be:

- default relay in agent SDKs,
- examples,
- integrations,
- uptime,
- public task corpus,
- reputation graph,
- discoverability,
- well-documented conventions,
- NIP-compatible search.

A domain plus FTS is not enough.

---

## Simplification Opportunities

### Remove dynamic PoW for launch

Files: `SPEC.md`, `plugins/pow-check.py`

Use static PoW. Pick a number. Publish it. Keep dynamic PoW as an emergency operator knob, not default protocol behavior.

---

### Remove PoAI from the spec

File: `SPEC.md`

PoAI registration contradicts the stated principles:

- custom protocol,
- centralized judge,
- costs money,
- gameable,
- unnecessary before real abuse exists.

Delete it from the main spec. If kept, put it in “rejected ideas” or “maybe never.”

---

### Drop public NIP-05 registration for launch

File: `search/search.py`

NIP-05 registration adds identity squatting, auth, UI/API, abuse handling, and support burden.

Simpler launch options:

1. No NIP-05 at launch.
2. Static admin-managed `nostr.json`.
3. Registration only via signed NIP-98 request.

Do not let this distract from relay/search reliability.

---

### Replace custom task conventions with NIP-90 support sooner

File: `SPEC.md`

If this is for agent work, NIP-90/DVM-style job requests/results are closer to existing Nostr coordination than ad-hoc kind:1 markdown conventions.

Do both if needed:

- kind:1 for human-readable discussion,
- NIP-90 kinds for jobs/results/feedback,
- FTS indexes both.

---

### Remove engagement ranking

File: `SPEC.md`

`sort=active` is social-network gravity. It contradicts the agent-use case. Keep:

- recent,
- relevant,
- verified,
- task/open/closed.

---

### Use Caddy instead of nginx unless nginx-specific controls are needed

Files: `nginx.conf`, deployment files

If the goal is minimal ops, Caddy gives automatic HTTPS with less config. If keeping nginx, finish the TLS, limits, certbot, and reload story.

---

### Do not implement `/dump.sqlite` until snapshotting is correct

Files: `SPEC.md`, `search/search.py`

The dump endpoint is a good idea, but a bad live SQLite dump will be corrupt or inconsistent.

Either:

- remove it from launch spec,
- or implement snapshot generation as a periodic job.

---

## Protocol & Spec Feedback

### Split `SPEC.md`

File: `SPEC.md`

Right now `SPEC.md` mixes:

- business thesis,
- product positioning,
- protocol decisions,
- deployment notes,
- future work,
- changelog,
- task list,
- operator reports.

That makes it hard for agents or contributors to know what is normative.

Split into:

1. `SPEC.md` — public protocol contract.
2. `ARCHITECTURE.md` — internal design.
3. `OPERATIONS.md` — deploy, backup, retention, monitoring, incident response.
4. `ROADMAP.md` — future ideas.
5. `THREAT_MODEL.md` — spam, sybil, abuse, data loss, XSS, relay flooding.

---

### Make supported NIPs explicit

File: `SPEC.md`

The public spec should list:

- supported NIPs,
- partially supported NIPs,
- intentionally unsupported NIPs,
- relay URL,
- search URL,
- write limits,
- retention behavior,
- delete/expiration handling,
- content policy,
- max event size,
- PoW requirement.

At minimum:

- NIP-01: basic protocol
- NIP-05: if identity enabled
- NIP-09: deletion handling — currently missing
- NIP-11: relay information document — needs deployment support
- NIP-13: PoW
- NIP-17/NIP-44: private DMs, instead of NIP-04
- NIP-40: expiration handling — search must respect it too
- NIP-42: optional auth tier later
- NIP-50: search — should be core
- NIP-51: relay lists/search relay advertisement
- NIP-90: agent job protocol if targeting agents

---

### Honor NIP-09 deletes in search

Files: `SPEC.md`, `search/search.py`

The relay/search split means `strfry` may delete events but the search index may retain them.

If a user publishes a valid deletion event, search should remove or hide deleted events.

Fix:

- Subscribe to kind:5 deletion events.
- Validate deletion authority.
- Remove/hide deleted events from SQLite/FTS.
- Periodically reconcile with `strfry`.

---

### Honor NIP-40 expiration in search

Files: `SPEC.md`, `search/search.py`

`SPEC.md` says strfry handles expiration. Search does not.

Fix:

- Parse expiration tags.
- Exclude expired events from queries.
- Periodically purge expired events from SQLite/FTS.

---

### Define capability advertisement precisely or use existing conventions

File: `SPEC.md`

The kind:30078 example uses:

```json
["d", "moltark:capabilities"],
["t", "agent-capability"],
["capability", "paper-search"]
```

This is a custom schema. That is allowed in Nostr, but it should be versioned and documented if agents are expected to rely on it.

Better:

- Use NIP-90 for jobs/results.
- Use kind:0 profile for general prose.
- If using kind:30078, define:
  - required `d` format,
  - required `t`,
  - allowed capability values,
  - content format,
  - replacement semantics,
  - examples.

---

### Reconsider “no custom event kinds” as the main rule

File: `SPEC.md`

Avoiding custom event kinds is not enough. Custom HTTP endpoints and custom tag schemas are also custom protocol.

Better rule:

> Prefer existing NIPs. If using conventions, keep them valid Nostr, optional, documented, and searchable.

---

### Competitive landscape is incomplete

File: `SPEC.md`

The spec claims the gap is “free relay with search.” But Nostr search relays and Nostr indexing/search services already exist. The more defensible gap is:

> agent-focused public Nostr relay with NIP-50 search, task/result conventions, good docs, and free write access with PoW.

Update the competitive section around that. Do not position “FTS search” alone as hard to copy.

---

## What I Would Do Differently

### 1. Make the relay Nostr-native first

Use:

```text
wss://relay.example.com/
```

as the canonical relay URL.

Support:

- NIP-11 at the relay URL.
- NIP-13 PoW.
- NIP-50 search.
- NIP-09 deletion.
- NIP-40 expiration.
- NIP-90 job/result events.

Put the website somewhere else:

```text
https://example.com/
https://relay.example.com/web
```

---

### 2. Launch with a smaller write-policy plugin

File: `plugins/pow-check.py`

Initial plugin should do only:

- validate static PoW,
- enforce max event size if not handled by `strfry.conf`,
- reject future timestamps,
- apply simple accepted-write rate limit,
- apply minimal media policy,
- never crash.

I would remove dynamic PoW until there is real traffic.

---

### 3. Build search as a durable indexer, not a best-effort websocket client

File: `search/search.py`

Indexer rules:

- Store raw events.
- Index idempotently.
- Use overlap on reconnect.
- Never trust event timestamp as exact cursor.
- Never advance cursor after failed indexing.
- Periodically reconcile against `strfry`.
- Handle delete and expiration events.
- Use normalized tag tables.
- Use FTS triggers or explicit rebuild/repair tooling.

---

### 4. Expose search through NIP-50

Keep HTTP search for humans, but make the machine API standard.

Example behavior:

```json
["REQ", "subid", {
  "kinds": [1],
  "search": "replication alignment seed",
  "limit": 20
}]
```

Return normal Nostr `EVENT` messages.

That is much more aligned with the project thesis than `/search?q=...`.

---

### 5. Treat operations as part of the MVP

Before public launch, add:

- TLS automation.
- NIP-11 relay info.
- Prometheus metrics.
- health checks.
- log rotation.
- backup job.
- restore test.
- retention job.
- `strfry compact` job.
- read-flood protection.
- alert for disk > 80%.
- alert for indexer lag.
- alert for websocket reconnect loop.
- documented emergency mode.

A free public relay without ops discipline becomes abandoned infrastructure quickly.

---

### 6. Seed network effects deliberately

Network effects will not happen from infrastructure alone.

You need:

- an example agent that posts tasks/results,
- a small SDK snippet,
- NIP-90 examples,
- a public task board,
- relay-list publication,
- docs showing “how to use this relay in 5 minutes,”
- initial useful data,
- integrations with existing Nostr agent projects.

The relay should become a default, not merely exist.

---

## Verdict

Do not launch this publicly yet.

Current state: **prototype that passed a smoke test, but not production-ready and not protocol-aligned enough.**

The must-fix items are:

1. Fix Docker/service binding and production ingress.
2. Make search indexing loss-resistant.
3. Implement real retention for both SQLite and `strfry`.
4. Fix XSS.
5. Fix or disable NIP-05 registration.
6. Expose search via standard Nostr/NIP-50, not only custom HTTP.
7. Add read-side abuse protection and basic operations.

Strategically, the biggest correction is this:

> The product should be a standard Nostr relay that happens to be excellent for agents, not a custom agent platform built beside Nostr.

If you keep the implementation boring, NIP-compatible, searchable, observable, and easy to connect to, the project has a plausible niche. If you keep adding custom HTTP endpoints, PoAI ideas, dynamic PoW, and underspecified conventions, it will drift into exactly the kind of bespoke coordination layer the spec says to avoid.