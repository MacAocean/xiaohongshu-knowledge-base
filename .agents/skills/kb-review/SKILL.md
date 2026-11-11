---
name: kb-review
description: Review an existing draft for facts, clarity and filmability, preserve feedback, register an explicitly approved final, or record publication metrics. Use for 审核、定稿、发布复盘.
---

Open the named version, project and actual sources. Trace each important assertion and demo step. For each issue cite the passage or scene, effect, and a direct fix. Evaluate audience fit, unsupported claims, oral pacing, repetitive phrasing and whether the shot can show the result. Save an independent `review` output, then revise only when asked. Never overwrite the reviewed draft.

After the user's explicit approval of a named formal draft, use `approve OUTPUT_PATH --confirmed "依据"`. This creates a read-only snapshot checked against the log. Demo files cannot become personal style samples. For publication, read `99_系统/模板/发布复盘.md`, record only manually supplied metrics and the observation window, distinguish hypotheses from evidence, and save a `retrospective` output. Log decisions and link them to actual versions.

Case to verify: a review identifies an unmeasured speed claim, a revision keeps the old version, and the recorded review points to the draft it checked.

run `稿件核查.py OUTPUT` before and after saving the independent review via `知识库.py output PROJECT review --ref OUTPUT`. Discuss actual evidence, depth, density, language, narrative, creativity, pacing and filmability. Once the user confirms actual feedback, save it via `创作上下文.py memory revision|quality|style|method --title TITLE --confirmed REASON`, specifying the exact target where required. Avoid unsupported ratings.
