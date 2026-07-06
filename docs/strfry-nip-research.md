# strfry + Nostr NIP Research: Agent-Focused Markdown Relay

## 1. strfry Findings

### Websocket subscriptions (sidecar subscribing via NIP-01 REQ)
- YES: strfry is a full relay. A sidecar connects as a normal WS client, sends ["REQ","sub",{...filters}],
  and receives both historical events and real-time EVENT messages (after EOSE).
- Built-in client commands: strfry download (one-shot REQ to stdout JSONL), strfry stream (deprecated),
  strfry router (preferred, multiplexed streams).
- For a long-running sidecar: connect WS to relay port (default 7777), open REQ with {limit:0}
  to get only NEW events (suppresses historical backfill). This mirrors strfry stream --dir down.

### strfry.conf key values (relevant subset)
- db = "./strfry-db/" (LMDB)
- dbParams.mapsize = 10995116277760 (10TB mmap, not actual disk), maxreaders=256
- events.maxEventSize = 65536 (64KB)  <- tighten for markdown-only
- events.rejectEventsNewerThanSeconds = 900 (15min future skew)
- events.rejectEventsOlderThanSeconds = 94608000 (~3yr)  <- adjust for retention
- events.ephemeralEventsLifetimeSeconds = 300
- events.maxNumTags = 2000, events.maxTagValSize = 1024
- relay.bind = "127.0.0.1", relay.port = 7777, relay.nofiles = 524288
- relay.realIpHeader = ""  (set to e.g. "x-real-ip" behind proxy)
- relay.auth.enabled = true (NIP-42), relay.auth.serviceUrl = "" (REQUIRED: set to wss:// URL)
- relay.maxWebsocketPayloadSize = 131072
- relay.maxReqFilterSize = 200, relay.maxFilterLimit = 500, relay.maxTagsPerFilter = 3
- relay.maxFilterLimitCount = 1000000 (COUNT, 0 disables)
- relay.maxSubsPerConnection = 200, relay.maxPendingOutboundBytes = 33554432 (32MiB)
- relay.queryTimesliceBudgetMicroseconds = 10000 (CPU per REQ scan slice)
- relay.writePolicy.plugin = ""  <- SET to path of policy script (content/markdown enforcement)
- relay.writePolicy.timeoutSeconds = 10
- relay.compression.enabled = true, relay.compression.slidingWindow = true
- relay.negentropy.enabled = true, relay.negentropy.maxSyncEvents = 1000000
- relay.numThreads: ingester=3, reqWorker=3, reqMonitor=3, negentropy=2
- relay.filterValidation block: enabled=false by default; when enabled: maxFiltersPerReq=3,
  minFiltersPerReq=1, maxKindsPerFilter=3, allowedKinds="" (comma-sep), requireAuthorOrTag=false
  <- use this to restrict kinds, but plugin is more flexible for content rules.

### Plugin system (push events to external process)
- YES: relay.writePolicy.plugin runs an external process per write attempt.
- Protocol: strfry sends JSONL to plugin stdin with {type:"new", event, receivedAt, sourceType, sourceInfo, authed}.
- Plugin replies JSONL: {id, action:"accept"|"reject"|"shadowReject", msg}.
- Auto-reloads when script mtime changes or config changes.
- Plugins also usable in strfry router as pluginDown/pluginUp per stream.
- Can implement: whitelists, rate-limits, spam filtering, content-type/markdown enforcement.
- NOTE: plugin invoked synchronously (1 at a time), timeoutSeconds=10.

### strfry scan behavior (incremental/cursor)
- Uses DBQuery/DBScan with internal ScanCursor (resumeKey + resumeVal) - cursor/resumable across LMDB index.
- BUT strfry scan CLI is a ONE-SHOT full scan: prints all matching events to stdout (JSONL), then exits.
  Flags: --pause=<n>, --metrics, --count. --pause throttles CPU yielding.
- NOT a persistent incremental tail. For live tailing use WS REQ subscription (limit:0) or
  strfry router (watches data.mdb for changes, streams new events up).
- strfry stream --dir up uses file_change_monitor on data.mdb + reads from currEventId+1 forward
  = the live incremental mechanism (deprecated in favor of router).

### Negentropy / sync (federation)
- YES: strfry sync uses negentropy (NIP-77 / docs/negentropy.md) for set reconciliation.
- strfry sync wss://relay --dir both|up|down - efficient bidirectional sync, only transfers missing IDs.
  Supports --filter and --range.
- Precomputed BTree for full-DB syncs; can cache BTrees for arbitrary filters.
- relay.negentropy.enabled=true enables NEG-OPEN/NEG-MSG/NEG-CLOSE/NEG-ERR protocol messages.
- Relevant for federation/mirroring between relay instances.

### PoW / dynamic difficulty
- NO native PoW support. Grep of src/README/docs found zero PoW/difficulty/NIP-13 logic.
- strfry does NOT validate or require NIP-13 PoW, and has no config knob for min difficulty.
- The pow: OK message prefix exists in NIP-01 spec examples, but strfry doesn't emit it.
- To enforce PoW: implement in the writePolicy plugin (compute leading-zero-bits of event.id,
  reject if below threshold). Dynamic adjustment would require the plugin to track load and vary
  the threshold - strfry provides no built-in load signal to plugins.

## 2. Nostr NIP Findings

### NIP-01 (basic protocol) - mandatory
- Event kinds: 0 (metadata, replaceable), 1 (text note), 2 (recommend relay), 3 (contact list, replaceable).
- Kind ranges: regular (1,2,4-44,1000-9999), replaceable (0,3,10000-19999), ephemeral (20000-29999),
  addressable (30000-39999).
- REQ filters: ids, authors, kinds, #<single-letter-tag>, since, until, limit.
- Messages: EVENT (publish), REQ (subscribe), CLOSE (client->relay); EVENT/OK/EOSE/CLOSED/NOTICE (relay->client).
- OK prefixes: duplicate, pow, blocked, rate-limited, invalid, restricted, mute, error.
- Single-letter tags conventionally indexed by relays.
- For our use: kind 1 (text notes) = markdown content. Could also use a custom regular kind
  (1000-9999) for agent messages to distinguish.

### NIP-09 (deletion) - optional
- Kind 5 deletion request event; references e/a tags of events to delete; MUST be same pubkey.
- Relays SHOULD delete/stop publishing referenced events. SHOULD keep the deletion request itself.
- For retention: partial fit. Lets authors self-delete. Not a server-side retention policy.
  For age-based retention use strfry delete cron + rejectEventsOlderThanSeconds.
  NIP-09 = user-initiated deletion; retention = operator policy. Both coexist.

### NIP-13 (PoW) - optional
- difficulty = number of leading zero bits in the NIP-01 event id.
- ["nonce", "<n>", "<target_difficulty>"] tag; 3rd element = committed target difficulty.
- Clients MAY reject if committed target < required (anti-lucky-spammer).
- PoW is delegable (id doesn't commit to sig) - outsourcable to PoW providers.
- For our use: enforce via writePolicy plugin. No NIP defines dynamic/adaptive difficulty -
  that's a relay policy choice. Communicate rejection via OK prefix "pow: difficulty X < Y".

### NIP-50 (search) - optional, draft
- Adds "search" field to REQ filters: {"search":"<query string>"}.
- Relay SHOULD match against content (and MAY other fields). Returns by relevance, not created_at.
- Supports key:value extensions: include:spam, domain:<d>, language:<xx>, sentiment:<>, nsfw:<>.
- Clients discover support via NIP-11 supported_nips.
- strfry does NOT implement NIP-50 (no search filter handling in source).
- Recommendation: implement in a sidecar (separate search index) rather than patching strfry.
  Sidecar subscribes via WS REQ, indexes content, exposes its own search endpoint. Advertise NIP-50
  in NIP-11 only if actually supported.

### NIP-05 (identity verification) - already have
- DNS-based mapping nostr pubkey -> internet identifier (user@domain).
- .well-known/nostr.json?name=<local>.
- Used by NIP-50 domain: extension.

### Content-type enforcement (markdown only) - NO dedicated NIP
- No NIP standardizes content-type enforcement or markdown-only.
- Approach: enforce via strfry writePolicy plugin - validate content is markdown
  (reject if contains image data URLs / base64 / media refs). Could use a custom tag like
  ["ct","text/markdown"] as convention but no NIP mandates it.
- NIP-92 (imeta) / NIP-94 (file metadata) define media attachment tags - we'd reject those.
- NIP-23 (long-form content) uses markdown for kind 30023 - could adopt that kind for agent posts.

### Rate limiting / abuse prevention - NO single NIP
- No dedicated rate-limit NIP. NIP-01 defines the "rate-limited:" OK prefix as a convention.
- Mechanisms available:
  - strfry writePolicy plugin (per-event reject, can track per-pubkey/per-IP counters)
  - NIP-13 PoW (spam cost)
  - NIP-42 auth (relay.auth.enabled) - gate writes to authenticated pubkeys
  - NIP-40 (Expiration Timestamp) - ["expiration",<unix>] tag, events auto-expire
  - relay.filterValidation to restrict kinds/authors
  - NIP-56 (Reporting) + NIP-29 (Relay-based Groups) for community moderation (heavier)
  - NIP-70 (Protected Events) - only author's relay can publish
- Recommended stack: NIP-42 auth + writePolicy plugin (rate-limit + markdown-only + optional PoW).

## 3. Dynamic PoW Difficulty
- strfry: NOT supported. No PoW logic at all in strfry core.
- NIP-13: no dynamic spec. NIP-13 only defines difficulty measurement and commitment;
  threshold policy is entirely relay-defined.
- To implement: writePolicy plugin tracks request rate / queue depth / recent-rejects,
  sets a floating min-difficulty, rejects events below it with "pow: difficulty X < Y" message.
  Plugin would need its own state (no strfry load signal provided to plugins).

## Summary Recommendations
| Need | Mechanism |
|------|-----------|
| Markdown-only content | writePolicy plugin validates content |
| No images | writePolicy plugin rejects imeta(NIP-92)/NIP-94 tags + base64/data-URLs |
| Full-text search | Sidecar with own index (NIP-50), subscribe via WS REQ |
| Live event stream to sidecar | WS REQ with limit:0 to strfry:7777 (not strfry scan) |
| Retention | cron strfry delete + rejectEventsOlderThanSeconds; NIP-09 for user deletes |
| Rate limiting | writePolicy plugin (per-pubkey/IP counters) + NIP-42 auth |
| PoW | writePolicy plugin computes leading-zero-bits; dynamic via plugin state |
| Federation | strfry sync / strfry router (negentropy/NIP-77) |
| Identity | NIP-05 (have it) + NIP-42 auth |
