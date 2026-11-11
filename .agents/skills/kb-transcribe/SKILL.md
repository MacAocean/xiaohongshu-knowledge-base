---
name: kb-transcribe
description: Transcribe local audio or video into the same source note and check timecoded evidence.
---

Use `python3 99_系统/程序/视频转写.py probe SOURCE` and `run SOURCE --language zh` (or the actual language). The same entry is available in Obsidian as 转写当前视频. Machine raw output stays untouched; edit section 03 for corrections. If the machine block was manually edited, preserve it and stop.

Use `视频转写.py frame SOURCE SECONDS`, actually view the frames, and describe sampling limits. `视频拆解.py SOURCE EVIDENCE_JSON` verifies timestamps, excerpts, and frame provenance. Distinguish external creative techniques from personal style; state what evidence is missing.
