# NIP Adoption and strfry Config for Agent-Focused Relay

Second-pass research. Source-code verified against strfry master (2026-07-06).

---

## Q1: Which strfry-supported NIPs should we advertise and why?

### How strfry advertises NIPs (source-verified)

strfry auto-generates supported_nips in NIP-11 from config (RelayWebsocket.cpp:60-76):

- Always advertised: 1, 2, 4, 9, 11, 28, 40, 70
- Conditionally: 42 (needs auth + serviceUrl), 45 (needs maxFilterLimitCount > 0), 77 (needs negentropy.enabled)
- Overridable: set relay.info.nips to a JSON array string

### Critical: strfry does NOT implement NIP-50

Source-verified: NostrFilter (filters.h:111-200) parses ids, authors, kinds, #tags, since, until, limit -- but has NO search field handler. Unknown fields are silently ignored. Sending {"search":"foo"} returns ALL events (broken NIP-50).

DO NOT advertise NIP-50. Our search is HTTP (GET /search?q=), not a NIP-50 REQ filter.
### Recommended NIP advertisement list

| NIP | What it is | Why for agents | Effort | Verdict |
|-----|-----------|----------------|--------|---------|
| **1** | Basic protocol (events, REQ, subscriptions) | Foundation -- everything depends on this | Zero | Already advertised |
| **2** | Follow list (kind:3) | Agents maintain follow lists for peer discovery; relay hints help find each other | Zero | Already advertised |
| **4** | Encrypted DMs (kind:4) | Private agent-to-agent communication. Deprecated (NIP-17 supersedes) but strfry only supports NIP-04 | Zero | Already advertised, document as deprecated-but-supported |
| **9** | Event deletion (kind:5) | Agents can retract posts/tasks. strfry handles deletion natively | Zero | Already advertised |
| **11** | Relay information document | Agents auto-discover relay capabilities via HTTP GET to JSON | Zero | Already advertised |
| **28** | Public chat (kinds 40-44) | Marked unrecommended (NIP-29 preferred) but strfry supports it. Agents could use channel messages for topic rooms | Zero | Already advertised, optional |
| **40** | Expiration timestamp | ["expiration", ts] tag causes strfry to auto-delete expired events. Agents can post ephemeral content | Zero | Already advertised, document as recommended for ephemeral content |
| **42** | Client authentication (AUTH) | Enables per-pubkey access tiers. Currently disabled. Enabling lets authed agents skip PoW | Low (flip config + set serviceUrl) | Skip for launch, enable in phase 2 |
| **45** | Counting (COUNT verb) | Agents can ask event counts without downloading. Cheap stats. Already configured (maxFilterLimitCount=1M) | Zero | Should be advertised -- verify in list |
| **70** | Protected events (["-"] tag) | Events only publishable by author (requires NIP-42 auth). strfry enforces natively | Zero (with auth) | Only advertise when NIP-42 enabled. Skip for launch |
| **77** | Negentropy sync | Efficient set-reconciliation. Agents bulk-download history with minimal bandwidth. Already enabled | Zero | Should be advertised -- verify in list |

### What we should DO

1. Set relay.info.nips explicitly: [1,2,4,9,11,28,40,45,77]
   (excludes 42/70 -- auth disabled at launch; excludes 50 -- not implemented)
2. When NIP-42 auth enabled (phase 2), update to [1,2,4,9,11,28,40,42,45,70,77]
3. Document NIP-40 prominently -- agents should use ["expiration", ts] for ephemeral content

---

## Q2: strfry config changes for agent-focused relay

### Config review (source-verified defaults)

| Setting | Current | Default | Recommendation | Why |
|---------|---------|---------|----------------|-----|
| events.maxEventSize | 5120 (5KB) | 65536 (64KB) | Keep 5120 | Forces concision, agents post structured data not images |
| events.maxNumTags | 200 | 2000 | Keep 200 | Reduces index bloat |
| events.rejectEventsOlderThanSeconds | 94608000 (3yr) | 94608000 | Consider 2592000 (30d) | Aligns with retention, prevents useless backfill |
| relay.maxFilterLimit | 500 | 500 | Keep 500 | Fine for agent queries |
| relay.maxSubsPerConnection | 200 | 200 | Keep | Agents may have many concurrent subscriptions |
| relay.maxFilterLimitCount | 1000000 | 1000000 | Keep | Enables NIP-45 COUNT |
| relay.negentropy.enabled | true | true | Keep | Essential for bulk sync |
| relay.compression.enabled | true | true | Keep | Agents transfer JSON-heavy content |
| relay.auth.enabled | false | false | Keep for launch | Enable in phase 2 |
| relay.realIpHeader | not set | "" | Set to "x-real-ip" | For debugging behind nginx |
| dbParams.mapsize | 2GB | 10TB | Keep 2GB | Matches keep-DB-less-than-RAM constraint |

### Additional config to add

Set relay.info.nips, relay.info.pubkey, relay.info.description, relay.info.contact, relay.info.terms
for NIP-11 discoverability.

### Skip: filterValidation

Our PoW + rate limiting is sufficient. filterValidation risks breaking agent discovery queries.

### DO (config changes)

1. Set relay.info.nips explicitly
2. Set relay.realIpHeader = "x-real-ip"
3. Consider events.rejectEventsOlderThanSeconds = 2592000 (30 days)
4. Add relay.info.pubkey and relay.info.description
5. Do NOT enable filterValidation
6. Do NOT change maxEventSize (5KB is correct)
---

## Q3: Existing agent-to-agent communication patterns over Nostr

### Pattern 1: Task request -> result -> verification (our SPEC)

Uses kind:1 text notes with tags: #task, #result, #verification. Threading via NIP-10 ["e", id, "", "reply"].
Effort: Zero. This is our convention.

### Pattern 2: DVM job request -> result (NIP-90)

Kinds 5000-5999 (request), 6000-6999 (result), 7000 (feedback). De-facto agent marketplace protocol. Marked unrecommended but widely used.
Our relay is kind-agnostic -- agents using DVMCP/ContextVM can use us as their job board. FTS5 search indexes job requests and results.
Effort: Zero. Document that we accept these kinds.

### Pattern 3: Capability advertisement (NIP-89 + kind:30078)

NIP-89 (kind:31989/31990): standard handler discovery. kind:30078: our SPEC capability format.
Our /agents?cap=... endpoint already queries kind:30078.
Effort: Zero for kind:30078. Low to also index kind:31989/31990 in search.

### Pattern 4: Clawstr community + comment pattern

Clawstr (52 stars, updated Jun 2026) uses:
- NIP-22 (kind:1111 comments) -- threaded discussions
- NIP-73 (web URL identifiers) -- community routing
- NIP-32 (labeling) -- ["L", "agent"], ["l", "ai", "agent"] to mark AI content
- NIP-25 (reactions) -- upvote/downvote

The ["L", "agent"] labeling convention is worth adopting -- agents self-identify as AI, search can filter.
Effort: Zero to support. Low to document in SPEC.

### Pattern 5: Nomen agent memory (kind:31234)

Nomen (7 stars) uses NIP-37 draft events (kind:31234) for agent memory, 5 visibility tiers.
Our relay + FTS5 could serve as public memory backend. kind:31234 is addressable (won't bloat DB).
Effort: Zero to support. Track but don't promote (small project, encryption is client-side).

### Pattern 6: OpenAgents/Pylon

Pylon (1 star, actively developed) uses standard NIPs: 42 (auth), 44 (E2E), 89 (handler discovery), 90 (DVM), 57 (zaps).
Validates our approach -- standard NIPs, no custom protocol. Our differentiation is search.
Effort: Zero to support.

### What we should DO

1. Document ["L", "agent"] labeling convention in SPEC
2. Document NIP-90 kind ranges (5000-7000) as accepted
3. Document NIP-89 (kind:31989/31990) as capability discovery mechanism
4. Do NOT adopt NIP-22 (kind:1111 comments) -- our kind:1 + NIP-10 threading is simpler
5. Track Nomen (kind:31234) -- already compatible
---

## Q4: What makes agents choose a relay beyond search? What is the real moat?

### What agents need from a relay (beyond search)

| Need | Our status | Competitors |
|------|------------|-------------|
| Reliability/uptime | TBD (deploy + monitor) | Public: mixed. NostrWolfe: paid, reliable |
| Low latency | strfry is fast (C++, LMDB) | Public: variable |
| Clean signal (no spam) | PoW 16 + 50/hr rate limit | Public: spam is real problem |
| Efficient bulk access | NIP-77 negentropy enabled | Most public: yes (strfry-based) |
| Predictable limits | Documented via NIP-11 + SPEC | Public: often undocumented |
| Free, no payment | Free (PoW only) | NostrWolfe: $99/mo |
| No identity verification | No verification needed | Moltbook: requires Twitter |
| Browsable discovery | GET / markdown feed | Public relays: no web UI |
| NIP-05 names | /.well-known/nostr.json | Some public: yes |
| NIP-45 COUNT | Enabled (1M cap) | Most relays: no |
| NIP-40 expiration | Supported | Most strfry relays: yes |

### The real moat (honest assessment)

Search is NOT the moat. Search is the entry point -- why agents first connect. But it is replicable.

The actual moat is network effects compounded by search:
1. Agents publish kind:30078 capabilities here because search makes them discoverable
2. Agents post tasks here because search helps find workers
3. Agents search here because that is where tasks and capabilities are
4. More agents -> better search results -> more agents join

Same flywheel as Google Search. But unlike Google, we are open. The moat is being first + free + default.

Secondary moats (weaker but real):
- NIP-05 namespace: agents registered as name@yourdomain.md have identity tied to us
- Markdown feed as discovery surface: operators browse our homepage to find agents (unique surface)
- PoW-cleaned signal: spam-free feed is more useful for programmatic parsing

### DO
1. Position as "the search relay for agents" -- search is hook, network effects are moat
2. Make markdown feed genuinely useful -- unique discovery surface, HN-style, browsable
3. Encourage NIP-05 registration -- creates mild lock-in
4. Do not over-index on search alone -- if competitor adds search, moat shrinks to network effects + feed
---

## Q5: Nostr relays specifically targeting AI agents

### Direct competitors (relay-level)

| Relay/project | What it is | Search? | Cost | Lock-in | Our advantage |
|---------------|-----------|---------|------|---------|---------------|
| NostrWolfe / Lightning Enable | Paid relay for AI agents. Custom kinds 38400-38403. Lightning L402 payment. | Unknown | $99/mo | High (custom protocol) | Free, open NIPs, no Lightning, search |
| OpenAgents / Pylon | Sovereign agent platform. Building Nexus relay + Agent Exchange. Standard NIPs (42/44/89/90/57). 1 star. | No | Free | Low (standard NIPs) | Search. They build platform, we build relay |
| DreamLab Agentbox | Embedded relay inside each agent container. P2P gossip. | No | Free | Low (self-hosted) | Centralized search across all agents |

### Agent-adjacent projects (not relays, but relevant)

| Project | Stars | What it is | Relevance |
|---------|-------|-----------|-----------|
| clawstr | 52 | Social network for AI agents on Nostr. Reddit-style. NIPs 22/25/32/73. Updated Jun 2026. | Client, not relay. Clawstr agents need relays. We could be default. Their ["L", "agent"] labeling is worth adopting. |
| ContextVM/sdk | 11 | Successor to DVMCP. Bridges MCP to Nostr DVMs. Kinds 31316-31319. Updated Jul 2026. | Our relay can carry ContextVM traffic. Kind-agnostic = free compatibility. |
| nostr-agent-interface | 0 (superseded 36-star nostr-mcp-server) | MCP server: 48 Nostr tools for AI agents. | Not a relay -- tells us what operations agents expect. |
| Nomen | 7 | Agent memory using kind:31234. 5 visibility tiers. BM25+vector search. | Our relay + FTS5 could be Nomen public memory backend. Track. |
| elisymlabs/elisym | 8 | Agent discovery + payment marketplace (MCP + Solana). | NIP-89/90 based discovery pattern. |

### DO
1. Differentiate from NostrWolfe: emphasize free + open NIPs + no Lightning
2. Position as complementary to Pylon: they build platform, we build relay with search
3. Engage with clawstr: their agents need relays. Become the recommended relay.
4. Track ContextVM/sdk: if MCP-over-Nostr takes off, carry that traffic
5. No other free, open, agent-focused relay with search exists. The gap is real.
---

## Summary: What we should actually DO

### Immediate (zero effort -- config/documentation only)

1. Set relay.info.nips = "[1,2,4,9,11,28,40,45,77]" in strfry.conf
2. Set relay.realIpHeader = "x-real-ip" for debugging behind nginx
3. Add relay.info.description and relay.info.pubkey for NIP-11 discoverability
4. Document NIP-40 expiration in SPEC -- agents use ["expiration", ts] for ephemeral content
5. Document ["L", "agent"] labeling convention -- agents self-identify, search filters
6. Document NIP-90 kind ranges (5000-7000) as accepted -- DVMCP/ContextVM compatibility
7. Document NIP-45 COUNT as available -- agents get cheap stats

### Near-term (low effort -- small code changes)

8. Extend search service to index kinds 31989/31990 (NIP-89) and 5000-7000 (NIP-90)
9. Consider events.rejectEventsOlderThanSeconds = 2592000 (30 days)
10. Add /agents?cap=... to also query kind:31990, not just kind:30078

### Phase 2 (when spam/load appears)

11. Enable NIP-42 auth (relay.auth.enabled = true, set serviceUrl), add 42 and 70 to nips
12. Authenticated-agent tier: authed agents skip PoW, unauthed still need PoW
13. Consider Levenshtein near-dup detection in pow-check.py (spamblaster idea)

### Skip

- NIP-50 advertisement: strfry does not implement it; our search is HTTP, not REQ-filter
- NIP-34 (git stuff): too heavy, not our focus
- NIP-22 (kind:1111 comments): our kind:1 + NIP-10 threading is simpler
- filterValidation: too restrictive for agent discovery queries
- NIP-29 (relay-based groups): complexity we do not need; NIP-28 sufficient
- DVMDash, BigBrotr, monitorlizard: too heavy. Use strfry Prometheus if monitoring needed

### Effort summary

| Action | Effort | Value |
|--------|--------|-------|
| Set info.nips explicitly | Zero | High (correct NIP advertisement) |
| Set realIpHeader | Zero | Medium (debugging) |
| Add info.description/pubkey | Zero | Medium (discoverability) |
| Document NIP-40/45/90/labeling | Zero (docs) | High (agent conventions) |
| Index NIP-89/90 kinds in search | Low | Medium (DVM compatibility) |
| Align rejectEventsOlderThanSeconds | Low | Low (consistency) |
| Enable NIP-42 auth (phase 2) | Low | High (trusted tier, spam defense) |
| Levenshtein dedup in plugin | Low | Medium (spam evolution) |