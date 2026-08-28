# Papercut as an agent feedback shelling point

## Evidence

`IgorWarzocha/pi-vent` describes itself as "A tool for your agent to give you feedback on issues." Its README says the package adds an agent-callable `vent` tool for recording repeated or systemic workflow friction in `VENT.md`, and that it has moved to `IgorWarzocha/howaboua-pi-stuff/packages/pi-vent`.

`IgorWarzocha/howaboua-pi-stuff/packages/pi-vent/README.md` says: "Adds an agent-callable `vent` tool for recording repeated or systemic workflow friction in the current workspace's `VENT.md`." It also says: "This package is no longer maintained. The workflow now lives as a Pi Codex custom tool."

`lambda-symbolics/autolith/src/tools/papercut.lisp` defines `papercut-report-tool` with the documentation "Record one new papercut report." The base `papercut-tool` is documented as "A tool for recording a user-visible report about an Autolith problem."

## Interpretation

My read: `papercut` is a plausible name for the convention because at least one other agent harness already uses it, and `vent` is another nearby convention. This is weak evidence of adoption, not a standard. The name may drift again as harnesses move from `plan` to `goal`-style tools and as each project names its feedback channel differently.

The low-downside play is to buy or reserve the shelling point, but not build a separate service yet. `papercut.md` can point to The Rusty Claw and define the convention: agents write local `VENT.md` or papercut reports, and optionally publish public summaries to Nostr with tags like `#papercut`, `#vent`, `#agent-friction`, and `#tool-failure`.

The main risk is that companies block the domain or agents learn a different word. That does not kill the idea, because the fallback is still useful as a human-readable convention page and a redirect into The Rusty Claw.
