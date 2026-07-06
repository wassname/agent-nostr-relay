Reviewed the full codebase. Findings below, ordered by severity. This is a static review — I ran no code.

## High

### 1. Docker deployment is broken: services bind to `127.0.0.1`

`strfry.conf`:
```
relay { bind = "127.0.0.1" }
```
`search/search.py`:
```python
app.run(host="127.0.0.1", port=SEARCH_PORT, debug=False)
```

In `docker-compose.yml`, `nginx`, `search`, and `strfry` are separate containers. Binding to `127.0.0.1` means each process only accepts connections from inside its own container. As a result:

- `nginx` → `search:8888` fails (loopback-only Flask)
- `nginx` → `strfry:7777` fails
- `search` → `ws://strfry:7777` (`RELAY_URL`) fails — the subscriber can never connect, so nothing gets indexed
- Published ports (`7777:7777`, `8888:8888`) also won't work, because Docker's port forwarding targets the container's interface IP, not its loopback.

The entire compose stack is non-functional as written. Both services need to bind `0.0.0.0` inside containers (strfry `bind = "0.0.0.0"`, Flask `host="0.0.0.0"`), with external exposure controlled by not publishing those ports and only exposing nginx. This likely worked in the local `/opt/` test because everything ran on one host sharing loopback — the container topology changes the requirement.

### 2. Stored XSS via profile `name`

In `feed()`, `post_view()`, and `search()`, the display name is interpolated into HTML without escaping:

```python
name = names.get(pubkey, pubkey[:8])
parts.append(f"<div class='post'><div class='meta'><a href='/p/{pid}'>{name}</a> ...")
```

`name` comes from the kind:0 profile `name` field, which is fully attacker-controlled. Post content is run through `render_markdown` → `sanitize_html`, but the name never is. An agent publishing a profile with `name` set to `<script>...</script>` (or an `onerror` payload) gets it rendered verbatim on the feed, post, and search pages. Note the search route already escapes `q` with `html_escape` but not `name`, so the fix is clearly available — wrap names in `html_escape`.

## Medium

### 3. `enforce_retention()` writes on the shared connection outside the lock

```python
with _db_lock:
    conn = get_db()
deleted = 0
old_ids = conn.execute("SELECT id FROM events ...").fetchall()   # outside lock
for (eid,) in old_ids:
    conn.execute(...)                                            # outside lock
conn.commit()                                                    # outside lock
```

The `with _db_lock:` block only guards `conn = get_db()`; every subsequent read/write/commit on the shared `_db_conn` runs without the lock. The retention thread and the websocket subscriber thread both use `_db_conn`, so concurrent access to the same connection object can raise `Recursive use of cursors not allowed` / `ProgrammingError`, or interleave transactions. This reintroduces the exact class of bug the connection refactor (changelog 2026-07-06) was meant to fix. The whole delete loop plus commit should be inside `with _db_lock:`. Only triggers when the DB exceeds 5GB, so low frequency but real.

### 4. Reconnect catch-up doesn't recover events missed during downtime

```python
if since == 0:
    req = ... {"kinds": [...], "limit": 1000}
else:
    req = ... {"kinds": [...], "since": since, "limit": 0}
```

With `limit: 0`, strfry returns zero stored events and only streams new ones. If the subscriber is down for a period, events stored while it was disconnected (those with `created_at > last_seen`) will not be delivered on reconnect, because the `since` filter with `limit: 0` returns no historical events. This contradicts the SPEC claim of catching up on reconnect ("Falls back to `strfry scan` on reconnect") — that fallback is not implemented anywhere. Use a nonzero limit (or omit limit) so `since` actually returns the backlog.

### 5. Rate limit counts rejected events

In `pow-check.py` `main()`, the order is:
1. `check_rate_limit(pubkey)` — which **inserts a row** into `rate_limit`
2. no-images check (may reject)
3. PoW check (may reject)

Because the rate-limit row is written before the content and PoW checks, events that are subsequently rejected still consume the pubkey's 50/hr budget. Two consequences:
- A legitimate agent whose events fail PoW (misconfigured miner) burns its hourly quota anyway.
- An attacker can force `rate_limit` row inserts with cheap, invalid-PoW events (no PoW cost), causing write amplification before the PoW gate ever runs. Rows are purged hourly, so it's bounded, but the PoW gate should come first so spam is cheap to reject and doesn't touch the DB. Move rate-limit recording to only happen on accept.

### 6. `data:` filter false-positives on ordinary text

```python
(re.compile(r'data:', re.I), "data: URIs not allowed"),
```

This matches the substring `data:` anywhere, including common words agents will use in JSON/prose such as `metadata:`, `userdata:`, `formdata:`. Since the stated goal is to allow JSON and markdown freely and only block actual data URIs, this rejects legitimate content. Anchor it (e.g. `\bdata:\s*[\w.+-]+/` or `\bdata:image`) so it targets real `data:` URIs rather than any occurrence of the four letters `data:`.

## Low

### 7. NIP-05 registration has no proof of pubkey ownership

`register_nip05()` accepts `{name, pubkey, pow_proof}` and only verifies that `sha256(name+pubkey+pow_proof)` has 16 leading zero bits. Nothing proves the requester controls `pubkey` — there's no signature over the request. Anyone willing to spend the PoW can register a name pointing at an arbitrary pubkey (name squatting / impersonation via NIP-05). Given the "no identity verification" red line this may be acceptable, but requiring a Nostr signature over the registration payload would close the impersonation gap without adding human verification.

### 8. `save_last_seen` trusts event `created_at` and commits per event

`save_last_seen(event["created_at"])` is called for every indexed event. strfry allows timestamps up to 900s in the future (`rejectEventsNewerThanSeconds = 900`), so a single future-dated event advances `last_seen` ahead of real time; on the next reconnect the `since` filter can skip events created in that window. Also, `index_event` commits and then `save_last_seen` commits again — two commits per event on the locked shared connection, which will limit ingest throughput under load. Consider clamping `last_seen` to `min(created_at, now)` and batching the cursor write.

### 9. `/p/<event_id>` reply matching is a substring scan

```python
"... WHERE tags LIKE ? AND id != ? ...", (f"%{event_id}%", event_id)
```

This matches any event whose serialized tag string contains the id anywhere, not specifically an `e` reply tag. Collisions are unlikely with 64-char hex ids, but it will also surface non-reply references (mentions, `q` tags, etc.) as replies. Minor behavior note; parsing the `tags` JSON for actual `["e", ...]` entries would be more correct.

### 10. Terraform: unused/missing pieces and region mismatch

- `aws_instance.relay` references `file("${path.module}/user-data.sh")`, but no `user-data.sh` is included in the provided files. If it's absent, `terraform apply` fails at plan time.
- `aws_region` defaults to `ap-southeast-2`, while SPEC and the justfile assume `us-east-1` / `~/.aws/agent-relay.pem`. Worth aligning to avoid deploying in the wrong region.
- SSH ingress is `0.0.0.0/0`; the inline comment acknowledges this. Restricting to a known IP is the usual recommendation.

---

Not flagged as bugs: the PoW check operating only on `event_id` is fine because strfry validates event id/signature before invoking the writePolicy plugin. The single-connection, single-threaded stdin loop in `pow-check.py` is safe without a lock. The FTS external-content management (manual rowid delete/insert) looks correct in `index_event` and retention.