# Where four prompted agents looked for other agents

All four first choices were task-specific GitHub discussions/issues or a Hugging Face dataset discussion. These are stated initial choices plus subsequent searches, not observations of independent agents actually coordinating. Each helper ran six queries. None verified a meeting place for its specified task.

- [GDP report](gdp/report.md), [before search](gdp/presearch.md): first choice https://huggingface.co/datasets/openai/gdpval/discussions . Dataset verified, discussion fetch returned 403. Guessed https://github.com/openai/gdpval returned 404. Interpreting "GDP eval" as GDPval was an assumption.
- [Robotics report](roboticsbench/report.md), [before search](roboticsbench/presearch.md): first choice official benchmark GitHub repository. Guessed https://github.com/roboticsbench/roboticsbench and https://github.com/roboticsbench returned 404; roboticsbench.org did not resolve. Found https://github.com/AndrejOrsula/space_robotics_bench and its Discord, but did not establish that this was the intended benchmark.
- [QnA report](qna/report.md), [before search](qna/presearch.md): first choice official evaluation GitHub Discussions. Tried https://github.com/search?q=%22QnA%22+%22eval%22&type=discussions . Unable to identify the evaluation, selected https://www.moltbook.com/ as a general fallback. Its documented participation requires registration/human claiming. No QnA coordination observed.
- [Poke report](poke/report.md), [before search](poke/presearch.md): first choice GitHub issue/discussion; tried https://github.com/search?q=poke+agent&type=issues . Later selected https://www.reddit.com/r/PokeAssistant/ conditional on "poke" referring to the personal-assistant product. Also found https://pokeagentchallenge.com/ but did not establish the intended meaning. The fallback is not evidence about the benchmark.

## Quotes and limits

Robotics helper, pre-search self-report:

> A benchmark-owned public thread is more likely to be independently found than a new agent-only venue.

QnA helper, pre-search self-report:

> An organizer-linked discussion is more likely to give independently acting agents a common address than a newly invented forum or phrase.

These quotes are the agents' reasoning, not independent evidence of where real agents gather. Their searches provide narrower evidence about what they could locate.

The requested blinding was incomplete. All helpers reported runtime exposure to therustyclaw as an agent relay, pi-goals, and Intercom. The supervisor supplied an output path containing `agent-rendezvous-discovery`; helpers then used "rendezvous" in their invented search strings. Do not count those phrases as independently coined. No exact phrase match was observed, but the search tool returned broader results even for quoted input, so these are not verified zero-result exact searches. Backend identities were not reported. Four scenarios with one helper each cannot measure within-task convergence.

## Recommendation for discussion, not publication

Start with a relevant, maintainer-approved link from an actual task repository or dataset page. Establish the exact benchmark identity first. A link in useful documentation or a relevant discussion is preferable to unrelated promotional issues. GDPval's dataset is the most concrete official artifact here; its discussion access and rules still need checking before proposing any post. No particular RoboticsBench or QnA repository was verified, and the Poke result may concern the wrong product.

No listing, advertisement, registration, or public message was made. The user and supervisor should select a venue and review the text before publication. A properly isolated repeat with exact task names would be needed for a stronger blind-discovery claim.

-- Pi/OpenAI
