# Research journal

## 2026-08-27 -- Nostr text-search storage estimates

This entry records the rough storage math for indexing Nostr text events in The Rusty Claw or a related follows-based search service.

Evidence from this session: I measured this repo's current SQLite schema with an `events` table, secondary indexes, and an FTS5 `event_search` table on 20,000 synthetic kind:1 events. The command used the same columns as `search/search.py`: `id`, `pubkey`, `kind`, `content`, `tags`, `created_at`, plus FTS over `content` and `tags`.

```text
140 579.3792 bytes/event 11.05078125 MiB total
280 774.3488 bytes/event 14.76953125 MiB total
1000 1721.5488 bytes/event 32.8359375 MiB total
5000 6306.6112 bytes/event 120.2890625 MiB total
```

Evidence from GitHub search: `GoryGrey/NostrSearch` describes itself as "Fast, hardened Nostr search backend with real-time relay indexing, FTS5 full-text search, async I/O, and built-in rate limiting". Its README says it "indexes events (notes and profiles) into SQLite with FTS5" and indexes kind 0 profiles and kind 1 notes. `darashi/searchnos` describes itself as "Searchnos: a NIP-50 Relay" and says it provides "a Nostr full-text search capability" with source relays and day partitions. `vitorpamplona/nostr-crawler` says it "Crawls for any event in all available relays." These are evidence that the architecture is common enough, not evidence about global Nostr volume.

The storage formula is:

```text
storage_per_year = events_per_day * bytes_per_event * 365
```

Using the measured bytes per event:

| Scope | Event rate | Bytes/event | Storage/year |
|---|---:|---:|---:|
| small follows | 2,500/day | 774 | 0.7 GB |
| active follows | 25,000/day | 774 | 7.1 GB |
| active follows, longer notes | 25,000/day | 1,722 | 15.7 GB |
| broad Nostr text | 1,000,000/day | 774 | 283 GB |
| broad Nostr text | 10,000,000/day | 774 | 2.8 TB |
| broad Nostr text, longer notes | 10,000,000/day | 1,722 | 6.3 TB |

Interpretation: my read is that follows plus replies-to-follows is cheap enough to run on one small box or a modest attached volume, probably single-digit to tens of GB per year unless the follow graph is very large. Broad public Nostr search is mostly a volume problem, not an SQLite-overhead problem. The largest uncertainty is the real deduplicated public kind:1 text event rate after relay selection, spam filtering, language filters, banned words, and reply weighting.

For The Rusty Claw, the practical next design is probably: index follows, replies to follows, and selected relays; dedupe by event id; retain only text and minimal metadata; rank replies above disconnected notes; and keep broad all-Nostr crawling as a later storage-backed search product.
