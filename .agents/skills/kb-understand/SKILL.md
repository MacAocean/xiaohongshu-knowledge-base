---
name: kb-understand
description: Process the Obsidian inbox, extract reusable knowledge from real sources, and connect it to existing notes. Use to organize captures, distill a paper or tutorial, or connect evidence.
---

Identify requested captures by date or path. Review statuses and actual text, archive usable originals through the Vault CLI and leave blocked items visible. Search existing notes first using `python3 99_系统/程序/知识库.py search QUERY`. Read the matching originals and linked notes. Choose a form fitted to the material. A paper needs question, method, results and limits. A tutorial needs operation, reason and failure boundary. An idea retains the exact author's expression and marks additions as suggestions.

Save one distinct, useful idea with `note --title TITLE --topics TOPICS --evidence SOURCE_PATH EXACT_QUOTE` and text on stdin. Repeat `--evidence` for additional real notes. State which edge is a prerequisite, example, correction, application or contrast and why it helps retrieval. Use `--kind idea` for independent ideas. Preserve authorship and count same-source restatements once. If an attachment has no text, retain it and state what is missing. For manual notes run `index` and append consequential decisions through `log`.

Case to verify: a generated note links to two readable originals with exact supporting excerpts. A missing path or invented excerpt stops saving.

run `知识关系.py suggest SOURCE` and read original passages. Decide duplicate, example, supplement, conflict, new knowledge or no useful connection. Use `知识关系.py relate LEFT RIGHT TYPE --evidence PATH QUOTE --reason TEXT` for candidates, `知识关系.py propose NOTE --evidence PATH QUOTE` for possible evolution. Only `confirm ID --reason TEXT` after the user agrees.
