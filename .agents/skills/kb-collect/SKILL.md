---
name: kb-collect
description: Save a public link, copied text, quick idea, local PDF or file into the Obsidian inbox and optionally archive readable originals. Use for collecting, importing, pasting or dictating material, including Xiaohongshu.
---

Use the Vault-root command `python3 99_系统/程序/知识库.py`. For a URL pass `collect --url-stdin` and provide the exact URL on stdin. For prose use `collect --text`, with optional `--title` and the original text on stdin. For a file use `collect --file PATH`. Inspect the returned status and path. These commands keep content-addressed attachments and deduplicate normalized URLs, file bytes or text.

For requested archiving, call `archive INBOX_PATH` only when status is `pending` and the text or attachment is verified. For `needs_manual`, explain the actual failure and retain the receipt. When the user supplies text they copied from the public page, use `archive INBOX_PATH --paste` with that text. Do not classify a generic site shell as content or treat an attachment without a transcript as spoken evidence. Use no browser cookies or access workarounds. Return the real file path and distinguish new, duplicate, partial and failed imports.

Case to verify: collecting the same public URL twice with a tracking parameter must return a single receipt or archived source. A blocked link must remain visible as `needs_manual`.

retain the standard Xiaohongshu plugin and its download setting. Normalize its output via `小红书导入.py adopt INBOX_PATH` or the matching Obsidian command. Verify ID, real body and local attachments. Keep a manual receipt when unavailable. Run `知识关系.py suggest SOURCE` after archiving to inspect possible connections.
