#!/usr/bin/env python3
"""Write evidence-checked video research to its existing source note."""
import json
import math
from pathlib import Path
import re

from 知识库 import link, read_md
from 资料处理 import digest
from 视频转写 import format_time, locate, validate_segments
from 公共工具 import HASH, STATE, encode, file_hash, parser, run_cli, update_fields

DIMENSIONS = {"content": "内容与受众", "language": "语言与句式", "narrative": "叙事与过渡",
              "visual": "画面与口播的分工", "pacing": "节奏与信息密度", "limitations": "可用范围与不足"}


def analysis(vault, note, data):
    target, media = locate(vault, note)
    meta, _ = read_md(target)
    if meta.get("source_type") != "external_reference":
        raise ValueError("只接受独立登记的外部视频参考")
    transcript_id = meta.get("transcript_id", "")
    if not HASH.fullmatch(transcript_id):
        raise ValueError("先完成本地转写")
    raw = json.loads(vault.path(f"{STATE}/逐字稿/{transcript_id}.json").read_text())
    sha = file_hash(media)
    if raw["identity"]["media_sha256"] != sha:
        raise ValueError("原视频版本与逐字稿不一致")
    segments = validate_segments(raw["whisper"], raw["duration"])
    anchors, frames, methods = data["anchors"], data["frames"], data["methods"]
    if len(anchors) < 3 or len(methods) < 3:
        raise ValueError("至少需要三个口播证据和三个可复用方法")
    for item in anchors:
        if not 5 <= len(item["quote"]) <= 50 or not any(
                s["start_ms"] == item["start_ms"] and item["quote"] in s["text"] for s in segments):
            raise ValueError("口播摘录与指定时间不匹配")
    checked_frames = []
    for frame in frames:
        path = vault.path(frame["path"])
        if not re.fullmatch(rf"{STATE}/抽帧/[0-9a-f]{{64}}\.png", frame["path"]) or not path.is_file():
            raise ValueError("画面必须来自本地原视频抽帧")
        properties = json.loads(path.with_suffix(".json").read_text())
        time = properties["actual_seconds"]
        if (properties["media_sha256"] != sha or not math.isfinite(time)
                or not 0 <= time <= raw["duration"] + 1):
            raise ValueError("画面不属于当前媒体版本")
        checked_frames.append({**frame, **properties, "sha256": file_hash(path)})
    if len({f["actual_seconds"] for f in checked_frames}) != len(checked_frames):
        raise ValueError("画面证据含重复时间")
    if not checked_frames and data["dimensions"]["visual"] != "未分析画面":
        raise ValueError("缺画面证据时必须标为未分析画面")
    for item in methods:
        if not item["anchor_indices"] or any(type(i) is not int or not 0 <= i < len(anchors)
                                           for i in item["anchor_indices"]):
            raise ValueError("方法缺少对应证据")
        if not item["title"] or not item["application"] or not item["limits"]:
            raise ValueError("方法应写清用法和边界")
    record = {**data, "frames": checked_frames, "media_sha256": sha, "transcript_id": transcript_id}
    identifier = digest(encode(record))
    original = target.read_text()
    if meta.get("analysis_id") == identifier and meta.get("analysis_status") == "verified":
        return {"path": note, "id": identifier, "duplicate": True}
    if not re.search(r"(?m)^## 04 内容拆解$", original) or not re.search(r"(?m)^## 05 知识关联$", original):
        raise ValueError("找不到规范分析区，保留笔记不修改")
    result = [f"### 研究记录 · {identifier[:12]}", "",
              f"受众：{data['audience']}　形式：{data['format']}",
              "以下是外部创作方法，尚未采纳为个人规则。逐字稿来自机器，原片可回查。",
              "画面基于带时间戳的抽样，未测量连续剪辑频率。" if checked_frames else "未分析画面。", ""]
    for name, label in DIMENSIONS.items():
        result.extend([f"**{label}**　{data['dimensions'][name]}", ""])
    result.extend(["**可回查的口播证据**", ""])
    for i, item in enumerate(anchors, 1):
        result.append(f"- {i}. {format_time(item['start_ms'])} · 「{item['quote']}」 · {item['purpose']}")
    if checked_frames:
        result.extend(["", "**抽帧观察**", ""])
        for frame in checked_frames:
            result.append(f"- {format_time(round(frame['actual_seconds'] * 1000))} · {link(frame['path'])} · {frame['observation']}")
    result.extend(["", "**可复用方法候选**", ""])
    for item in methods:
        ids = "、".join(str(i + 1) for i in item["anchor_indices"])
        result.append(f"- **{item['title']}**（口播证据 {ids}）：{item['application']} 边界：{item['limits']}")
    at = re.search(r"(?m)^## 05 知识关联$", original).start()
    updated = original
    if f"### 研究记录 · {identifier[:12]}" not in original:
        updated = original[:at].rstrip() + "\n\n" + "\n".join(result) + "\n\n" + original[at:]
    updated = update_fields(updated, {"analysis_id": identifier, "analysis_status": "verified",
                                      "visual_status": "sampled" if checked_frames else "not_reviewed",
                                      "content_format": data["format"], "audience": data["audience"],
                                      "methods": [item["title"] for item in methods],
                                      "candidate_rules": [item["title"] for item in methods]})
    path = vault.path(f"{STATE}/分析/{identifier}.json")
    if not path.exists():
        vault.write(path, encode(record))
    vault.write(target, updated, replace=True)
    vault.log("知识｜外部视频拆解", f"{link(note)} · {len(anchors)} 口播证据 · {len(checked_frames)} 抽帧")
    vault.index()
    return {"path": note, "id": identifier, "frames": len(checked_frames), "methods": len(methods)}


def dispatch(vault, args):
    return analysis(vault, args.note, json.loads(args.file.read_text()))


if __name__ == "__main__":
    cli = parser("v1.0 外部视频证据分析")
    cli.add_argument("note")
    cli.add_argument("file", type=Path)
    raise SystemExit(run_cli(cli, dispatch))
