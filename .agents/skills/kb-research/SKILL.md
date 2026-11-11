---
name: kb-research
description: Retrieve personal knowledge or research a question or video topic with local evidence, source comparison, and optional public research. Use for 找资料、查笔记、研究、选题.
---

Start with `python3 99_系统/程序/知识库.py search "关键词 同义词"`, then read candidate Markdown and follow source and relationship links. Search independently with another phrasing when results are thin. Use native web tools only when the task needs current or missing public facts, open the actual sources and state what each proves. Use no private excerpts as external queries. Distinguish personal observation, verified fact and open question. Report actual files that were read.

For a topic shortlist give the audience's problem, a scene that can be filmed, evidence and the smallest test that could resolve uncertainty. For research outside creation, use findings, source confidence, conflicting evidence and gaps. Save requested durable research as a `note --kind research` with checked excerpts or a `research` output if the user already selected a project. Work from local evidence when offline and record the limit.

Case to verify: recover the original Spotlight capture using related search terms, open it, and return a topic with one on-screen success condition.

prefer `知识库.py search QUERY --mode hybrid`, with `--topic`, `--type`, `--source-type external_reference`, `--format video` or `--related PATH` as needed. Read the original passage and video timestamp. Fall back to keyword if embeddings are unavailable. Similarity alone does not establish a factual relationship.
