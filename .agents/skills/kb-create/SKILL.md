---
name: kb-create
description: Plan, draft or revise one v1.0 creative stage using verified local sources and approved personal works.
---

Before drafting run `python3 99_系统/程序/创作上下文.py build QUERY --project PATH --kind script --audience TEXT --goal TEXT --ref PATH` with the requested creative kind. Read actual project, selected historical finals, approved feedback, confirmed rules, and relevant reference analyses. State what is usable and what needs validation. If history is insufficient, ask the user whether to proceed, then use `--continue-without-history REASON`. External work supplies technique; never borrow the author's identity, anecdotes or style.

Save with `知识库.py output PROJECT KIND --context-id ID --ref PATH --used PATH` and draft on stdin. Record actual uses. Storyboards give A-roll, B-roll, inputs, success and fallback shots. For a scoped revision build with `--revision OLD --replace-exact TEXT` and save only the replacement prose. Preserve the original. Flag unresolved facts and rehearsal.
