# Find places where agents would look for each other

User: "ask blidn usbagents ... what's the exact url you would search? search it now? ... a unique string ... search that too now?"

- [x] Collect four separate discovery attempts: RoboticsBench, QnA eval, GDP eval, and "poke".
  - Saved in `slop/research/agent-discovery/`. Blinding was incomplete: inherited runtime named the project and the output path primed "rendezvous". Do not claim an uncontaminated experiment.
  - Record exact destination guesses, queries, and a distinctive search string before searching.
  - Each helper receives only its scenario, not this project or the proposed destination.
  - Search those choices; save exact queries, result URLs, source passages, failures, and changed choices.
  - Deliverable: four pre-search records and four source-backed reports.
  - Failure: scenario names are ambiguous; report assumptions instead of inventing benchmark facts.
  - Subtle failure: project context suggests our destination; report any exposure and do not count it as spontaneous discovery.
- [x] Recommend places to list the site, based on the observed choices and reachable pages.
  - `slop/research/agent-discovery/README.md`: task-specific GitHub/HF pages were the four initial choices; only GDPval yielded a concrete official dataset, with discussion access unverified.
  - Distinguish guessed URLs, discovered destinations, and usable places to post a relevant listing.
  - Preserve quotes and note shared model/search-provider limitations.
  - Deliverable: one short research summary with an ordered recommendation and links.
  - Subtle failure: a convenient place to advertise is mistaken for a demonstrated convergence point.
- [ ] Review listing/advertising choices with the user before any public action.

## Verification

Success: pre-search records precede searches, actual tool results support destinations, and the recommendation traces to those records.
Likely failure: guessed URLs do not resolve or no relevant search results; preserve failures and inspect terminology before drawing conclusions.
Subtle failure: all helpers repeat suggestions from the project. Fresh chats will not receive the project name in the brief, but inherited runtime context may expose the workspace; each helper must disclose it. This is a prompted discovery probe, not a naturalistic experiment or proof of a Schelling point.

No posting, sign-ups, deployment, or advertising in this phase.

-- Pi/OpenAI
