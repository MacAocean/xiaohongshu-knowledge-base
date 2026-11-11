#!/usr/bin/env python3
"""Offline time-coded transcription and media inspection."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave

from 知识库 import link, read_md, timestamp
from 资料处理 import digest
from 公共工具 import SUPPORT, STATE, encode, file_hash, parser, run_cli, update_fields

VIDEO = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO = {".wav", ".m4a", ".mp3", ".aac", ".flac", ".aiff", ".opus", ".ogg"}
START = "<!-- kb-v21:transcript:"
END = "<!-- /kb-v21:transcript -->"
BLOCK = re.compile(r"(?m)^<!-- kb-v21:transcript:([0-9a-f]{64}) -->\n(.*?)^<!-- /kb-v21:transcript -->\n?", re.S)


def update_machine_block(original, transcript):
    content = f"## 02 原始口播逐字稿\n\n{transcript.strip()}\n\n"
    replacement = f"{START}{digest(content.encode())} -->\n{content}{END}\n"
    matches = list(BLOCK.finditer(original))
    if matches:
        if len(matches) != 1 or original.count(START) != 1 or original.count(END) != 1:
            raise ValueError("发现多个机器逐字稿区块，先核对笔记")
        match = matches[0]
        if digest(match[2].encode()) != match[1]:
            raise ValueError("机器逐字稿区块已被编辑，移到整理版后再重跑")
        return original[:match.start()] + replacement + original[match.end():]
    if START in original or END in original or re.search(r"(?m)^## 02 原始口播逐字稿\s*$", original):
        raise ValueError("存在人工逐字稿或不完整标记，保留原文，请先核对")
    following = re.search(r"(?m)^## 0[345] ", original)
    if following:
        return original[:following.start()] + replacement + "\n" + original[following.start():]
    return original.rstrip() + "\n\n" + replacement


def command(args, timeout):
    try:
        output = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError("媒体处理超时，请缩短媒体后重试") from None
    if output.returncode != 0:
        detail = next((line for line in reversed(output.stderr.splitlines()) if line.strip()), "进程退出")
        raise ValueError(f"本地媒体工具失败 {detail}")
    return output.stdout.strip()


def native_media(operation, path, *args):
    helper = SUPPORT / "bin/media"
    executable = [helper] if helper.is_file() and os.access(helper, os.X_OK) else [
        "/usr/bin/swift", Path(__file__).with_name("媒体.swift")]
    return command([*executable, operation, path, *args], timeout=180)


def locate(vault, note):
    target = vault.note_path(note, ("01_资料来源",))
    meta, _ = read_md(target)
    relative = meta.get("media_path")
    if not relative and meta.get("attachment"):
        relative = str(meta["attachment"]).removeprefix("[[").removesuffix("]]")
    if not relative:
        raise ValueError("没有本地媒体。请用标准版插件保存媒体，或使用 attach-media 补录。")
    media = vault.path(relative)
    if not media.is_file() or media.suffix.lower() not in VIDEO | AUDIO:
        raise ValueError("媒体不是可读取的库内视频或音频文件")
    return target, media


def extract(path, wav):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        command([ffmpeg, "-nostdin", "-v", "error", "-i", path, "-vn", "-ar", "16000",
                 "-ac", "1", "-c:a", "pcm_s16le", wav], timeout=180)
    else:
        native_media("extract", path, wav)
    with wave.open(str(wav)) as result:
        if (result.getframerate(), result.getnchannels(), result.getsampwidth()) != (16000, 1, 2):
            raise ValueError("语音识别需要 16 kHz 单声道 16-bit PCM")
        duration = result.getnframes() / 16000
    if duration <= 0:
        raise ValueError("音轨为空")
    return duration


def validate_segments(raw, duration):
    segments = []
    previous_start = -1
    for item in raw["transcription"]:
        start, end = item["offsets"]["from"], item["offsets"]["to"]
        text = item["text"].strip()
        if not text:
            continue
        if not (isinstance(start, int) and isinstance(end, int)
                and 0 <= start <= end <= (duration + 1) * 1000 and start >= previous_start):
            raise ValueError("识别结果的时间戳不合法")
        # Protect managed Markdown delimiters from recognized speech.
        if "<!--" in text:
            text = text.replace("<!--", "‹!--")
        segments.append({"start_ms": start, "end_ms": end, "text": " ".join(text.split())})
        previous_start = start
    if not segments:
        raise ValueError("未取得可保存的语音片段")
    return segments


def format_time(ms):
    hours, remainder = divmod(ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


def transcribe(vault, args):
    binary = Path(os.environ.get("KB_WHISPER_BIN", shutil.which("whisper-cli")
                                 or str(SUPPORT / "bin/whisper-cli")))
    model = Path(args.model or os.environ.get("KB_WHISPER_MODEL", str(SUPPORT / "models/ggml-small-q5_1.bin")))
    target, media = locate(vault, args.note)
    if not binary.is_file() or not model.is_file():
        raise ValueError("缺少 whisper-cli 或本地模型，请参阅 v1.0 安装说明")
    metadata, _ = read_md(target)
    args.language = args.language or metadata.get("transcription_language") or "auto"
    if not re.fullmatch(r"(?:auto|[a-z]{2,3})", args.language):
        raise ValueError("请使用 auto 或语种代码，例如 zh")
    before = file_hash(target)
    identity = {"media_sha256": file_hash(media), "model_sha256": file_hash(model),
                "model": model.name, "engine_sha256": file_hash(binary),
                "engine": "whisper.cpp", "language": args.language}
    key = digest(encode(identity))
    raw_path = vault.path(f"{STATE}/逐字稿/{key}.json")
    if raw_path.exists():
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        if data.get("identity") != identity:
            raise ValueError("机器逐字稿缓存与源文件不一致")
        data["segments"] = validate_segments(data["whisper"], data["duration"])
    else:
        with tempfile.TemporaryDirectory() as directory:
            wav, result = Path(directory) / "audio.wav", Path(directory) / "result"
            duration = extract(media, wav)
            command([binary, "-m", model, "-f", wav, "-l", args.language,
                     "-ng", "-t", "4", "-oj", "-of", result], timeout=1800)
            raw = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))
        data = {"identity": identity, "duration": duration, "segments": validate_segments(raw, duration),
                "whisper": raw, "created": timestamp()}
    lines = [f"本地机器识别 · {model.name} · {args.language}", ""]
    for item in data["segments"]:
        lines.append(f"- [{format_time(item['start_ms'])} → {format_time(item['end_ms'])}] {item['text']}")
    with vault.locked():
        if file_hash(target) != before or file_hash(media) != identity["media_sha256"]:
            raise ValueError("识别期间笔记或媒体发生变化。缓存可重用，请重新执行。")
        original = target.read_text(encoding="utf-8")
        if not raw_path.exists():
            vault.write(raw_path, encode(data))
        updated = update_machine_block(original, "\n".join(lines))
        fields = {"transcription_status": "completed", "transcript_id": key,
                                          "transcription_model": model.name, "media_path": vault.rel(media),
                                          "media_sha256": identity["media_sha256"],
                  "transcription_language": args.language, "duration_seconds": round(data["duration"], 3)}
        if metadata.get("transcript_id") and metadata["transcript_id"] != key:
            fields["previous_transcript_id"] = metadata["transcript_id"]
            if metadata.get("analysis_id"):
                fields["analysis_status"] = "needs_review"
        updated = update_fields(updated, fields)
        changed = updated != original
        if changed:
            vault.write(target, updated, replace=True)
            vault.log("系统｜转写", f"{link(args.note)} · {key} · {len(data['segments'])} 段")
            vault.index()
    return {"path": args.note, "status": "completed", "duration": data["duration"],
            "segments": len(data["segments"]), "transcript": vault.rel(raw_path),
            "duplicate": not changed}


def dispatch(vault, args):
    if args.action == "run":
        return transcribe(vault, args)
    _, media = locate(vault, args.note)
    if args.action == "probe":
        return json.loads(native_media("probe", media))
    if not math.isfinite(args.seconds) or args.seconds < 0:
        raise ValueError("画面时间需要是非负秒数")
    media_sha = file_hash(media)
    identifier = digest(encode([media_sha, args.seconds]))
    path = vault.path(f"{STATE}/抽帧/{identifier}.png")
    meta = path.with_suffix(".json")
    with vault.locked():
        if not path.exists() or not meta.exists():
            with tempfile.TemporaryDirectory() as directory:
                image = Path(directory) / "frame.png"
                actual = float(native_media("frame", media, args.seconds, image))
                vault.write(path, image.read_bytes(), replace=path.exists())
                vault.write(meta, encode({"actual_seconds": actual, "requested_seconds": args.seconds, "media_sha256": media_sha}), replace=meta.exists())
    return {"path": vault.rel(path), **json.loads(meta.read_text())}


def main():
    cli = parser("v1.0 本地视频转写")
    actions = cli.add_subparsers(dest="action", required=True)
    for name in ("run", "probe", "frame"):
        sub = actions.add_parser(name)
        sub.add_argument("note")
        if name == "run":
            sub.add_argument("--language")
            sub.add_argument("--model")
        if name == "frame":
            sub.add_argument("seconds", type=float)
    return run_cli(cli, dispatch, locked=False)


if __name__ == "__main__":
    raise SystemExit(main())
