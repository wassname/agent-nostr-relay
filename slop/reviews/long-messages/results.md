# Long messages and read-more links

Deployed to production through the existing `therustyclaw-existing-host` credential. Live verification is recorded below. -- PI/OpenAI

## What changed

- Search, feed and agent timelines show explicit “Read full message” links when a preview is shorter than the original. Previews render Markdown independently, preserving formatting while an unfinished code fence cannot hide the link.
- Unsigned message lists also link to complete individual messages at `/req/<token>/<id>`. Their JSON views return the exact stored text.
- `POST /req/<token>` accepts UTF-8 `text/plain`, up to 65,536 Unicode code points. It returns JSON with the saved text and a message URL. GET writes retain the 2,000-character limit because proxy URL limits apply separately.
- Oversized messages return HTTP 413 before storage or background tasks. Invalid UTF-8, empty POST bodies and unsupported content types are rejected. No write path slices message text.
- Bitcoin timestamp submission hashes the full original message. The signed relay retains its 5 KB event limit. Messages that would exceed it produce an explicit full-message link and SHA-256 digest instead of a shortened copy or a failed oversized mirror.
- Updated `skill.md` and the simulation page's submission instructions; rebuilt its HTML.

## Verification

Run from `/workspace/therustyclaw`:

```bash
uv run search/test_messages.py
uv run search/test_linkify.py
uv run search/test_pages.py
just amisim-build
git diff --check
```

Observed:

- `message-tests.log`: “PASS: exact 65,536-character ASCII/Unicode writes, hashes, rejection without side effects, HEAD safety, formatted previews, read-more links and long-message feed pointers.” Tests compare stored data and SHA-256, not just success status; 65,537-character writes must leave the record count and background task count unchanged.
- `linkify-tests.log`: “linkify ok”.
- `browser-tests.log`: “Markdown routes, source preservation, sanitization, JS and no-JS browser checks passed.” Chromium clicks read-more links in search, feed, agent timeline and unsigned notes, and checks the previously hidden message ending on the detail page.
- Static page build and `git diff --check` succeeded.

The local tests use real routes, SQLite and Chromium; external Bitcoin submissions and Nostr publishing are intercepted to avoid public test messages. The same regression script also runs in the deployed container against temporary databases. No valid public test messages are posted. Account billing and completed Bitcoin proofs for new long messages were not tested.

## Production deployment

- Deployed application commit: `657b4cb` (includes the formatting correction).
- Deployment used the existing AWS profile `therustyclaw-existing-host`, targeting only instance `i-0d0b588f2940cb931`. Its audit records expiry at `2027-01-31T00:00:00Z`; no new credential was created.
- `deploy-preflight.log`: the remote worktree was clean before deployment. `deploy.log` and `deploy-formatting-fix.log` record successful SSM deployments and public health checks.
- `container-checks.log`: the deployed commit matches, and the exact same regression script passes inside the production image using temporary databases, including accepted ASCII/Unicode limits, formatted previews and rejection without side effects.
- Live verification uses `scripts/scratch/verify_live_long_messages.py`. It compares every pre-deploy message with the current history, checks individual text/hash responses, deliberately submits only invalid oversized requests, tests browser links/formatting, and reads one signed event over the public websocket. It never makes a valid public write.
- `live-checks.log`: “PASS: all 29 pre-deploy messages preserved exactly; current count=29”; all 29 individual URLs also returned matching text and SHA-256.
- `live-checks.log`: “PASS: a long preview retains its bold author and emphasis in the source and live browser”. Chromium followed the read-more links in feed, search and unsigned messages, found the final plain-text message ending, and reported no JavaScript errors or mobile/desktop overflow.
- The public health endpoint returned `status=ok`, 13 agents and 98 events. The public Nostr websocket returned a signed event and `EOSE`. Oversized GET/ASCII POST/Unicode POST requests all returned explicit HTTP 413 errors without adding records.
- Existing unrelated local Terraform/docs changes were not published or deployed.

## Monthly storage implications

These are maximum-size assumptions, not a traffic forecast. Short messages keep their current size. A 65,536-character ASCII message is 64 KiB; UTF-8 can require up to 256 KiB. Code points are counted, not visually combined characters.

At 10,000 maximum-size posts per month:

- ASCII: 625 MiB of new raw text per month.
- Worst-case UTF-8: 2,500 MiB per month.
- At the AWS pricing page's example rate of US$0.08/GiB-month: about US$0.049 or US$0.195 to retain each monthly batch. Keeping 12 such batches means about US$0.59 or US$2.34/month of raw-text capacity at year end.

EBS charges for provisioned disk capacity, not occupied bytes. Using spare space on an existing disk does not increase that storage bill. The working-tree Terraform configuration specifies a 50 GiB gp3 root disk. Deployment preflight reported a 49G filesystem, with 5.9G used and 43G available (`deploy-preflight.log`). SQLite overhead, proof records, relay pointers/indexes, optional snapshots, bandwidth and compute are additional. Full long messages remain in the unsigned SQLite log; they are not copied into Nostr or its S3 event archive.

AWS, primary pricing page, retrieved 2026-11-02:

> Volume storage for General Purpose SSD (gp3) volumes is charged by the amount you provision in GB per month until you release the storage.

https://aws.amazon.com/ebs/pricing/

The same page uses “$0.08 per GB-month” as a regional example and defines GB as 1024³ bytes. No account-specific price or bill was inspected.

Bandwidth is the less bounded cost. Message-list previews avoid sending every full message in the HTML/Markdown list, and POST returns only the newly submitted record. The legacy all-history `?json` view still returns full messages without pagination; repeatedly polling it can cost far more than storage. The unsigned message database also has no retention cap. A larger per-message limit does not provide a total storage or spending cap.

AWS, primary pricing page, retrieved 2026-11-02:

> AWS customers receive 100 GB of free data transfer out to the internet free each month, aggregated across all AWS Services and Regions (except China and GovCloud).

https://aws.amazon.com/ec2/pricing/on-demand/

This allowance is account-wide, not reserved for Rusty Claw. No assumption is made that it is unused.
