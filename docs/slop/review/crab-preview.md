# Crab header preview

Author: CODEX

- [x] Use a fixed crab body and a nine-column face. Cowsay draws the speech bubble.
- [x] Check the real feed route and all face changes in a local browser.
- [x] Save desktop and mobile screenshots, then obtain a fresh review.

The preview uses an empty temporary database. It does not connect to the live relay.

Evidence: [desktop](crab-preview/desktop.png), [mobile](crab-preview/mobile.png),
and [browser checks](crab-preview/checks.json).

All eight faces keep the same position and width through a complete rotation.
All seven speech bubbles fit at 375 CSS pixels. No JavaScript errors occurred.
The existing linkify check also passes.

The first screenshot review found missing Kannada glyphs in `ಠ_ಠ`. The final
face list uses `-_-`. The title now uses text without the redundant emoji.
A viewport tag makes the mobile browser use the device width.

Fresh CODEX subagent review passed after inspecting all eight regenerated faces
and the mobile screenshot. All glyphs are visible; the crab and bubble are clear.

Reproduce with `uv run scripts/scratch/preview_crab.py`.
Install Chromium first with `uv run --with playwright playwright install chromium`.
