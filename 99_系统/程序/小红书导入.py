#!/usr/bin/env python3
"""Adapt the standard Xiaohongshu Importer output to portable Vault sources."""
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlencode, urljoin, urlsplit, urlunsplit

import yaml

from 知识库 import clean_title, link, payload, read_md, timestamp
from 资料处理 import digest, identity, split_url
from 视频转写 import AUDIO, VIDEO, native_media
from 公共工具 import encode, file_hash, parser, run_cli, store_asset, update_fields

NOTE = re.compile(r"https://www\.xiaohongshu\.com/explore/([a-f0-9]{24})$")
XHS_URL = re.compile(r"https?://(?:www\.)?xiaohongshu\.com/[^\s)\]<>\"']+")
HTML_MEDIA = re.compile(r"<(?:video|audio|img)\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>(?:</(?:video|audio)>)?", re.I)
MD_MEDIA = re.compile(r"(!?\[[^\]\n]*\]\()([^()\n]+)(\))")
WIKI_MEDIA = re.compile(r"!\[\[([^\]\n]+)\]\]")
IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
IMPORT_INBOX = "00_收件箱/_小红书原始导入"
SHARE = re.compile(r"https?://[^\s,，。<>\[\]{}“”‘’\"']+")
SHORT_HOSTS = {"xhslink.com", "www.xhslink.com", "xhslink.cn", "www.xhslink.cn"}
NOTE_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com"}
NOTE_PATH = re.compile(r"/(?:explore/|discovery/item/|user/profile/[a-f0-9]{24}/)([a-f0-9]{24})/?", re.I)


def share_link(value):
    candidates = []
    for raw in SHARE.findall(value):
        raw = raw.rstrip(").）】")
        try:
            url = split_url(raw)
        except ValueError:
            continue
        if url.port is not None or len(raw) > 8192:
            continue
        if url.hostname in NOTE_HOSTS and NOTE_PATH.fullmatch(url.path):
            candidates.append(urlunsplit(("https", url.hostname, url.path, url.query, "")))
        elif url.hostname in SHORT_HOSTS and re.fullmatch(r"/(?:[ao]/)?[A-Za-z0-9_-]+/?", url.path):
            candidates.append(urlunsplit(("https", url.hostname, url.path, url.query, "")))
    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise ValueError("请粘贴一条小红书公开笔记链接，或包含它的分享文案")
    return unique[0]


def resolve_share(value):
    current = share_link(value)
    for _ in range(4):
        url = urlsplit(current)
        if url.hostname in NOTE_HOSTS:
            note_id = NOTE_PATH.fullmatch(url.path)[1].lower()
            return urlunsplit(("https", "www.xiaohongshu.com", f"/discovery/item/{note_id}", url.query, ""))
        try:
            result = subprocess.run(["/usr/bin/curl", "-q", "-sS", "--max-time", "8", "--proto", "=https",
                                     "-D", "-", "-o", "/dev/null", "--", current],
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("小红书短链接暂时无法打开，请粘贴完整笔记链接") from None
        locations = re.findall(r"^location:[ \t]*(.*?)\r?$", result.stdout, re.I | re.M)
        if result.returncode or not locations:
            break
        current = share_link(urljoin(current, locations[-1]))
    raise ValueError("小红书短链接没有返回可识别的笔记地址，请粘贴完整笔记链接")


def shortcut_prepare(vault, value):
    url = resolve_share(value)
    canonical, note_id, key = xhs_identity(url)
    found = lookup(vault, key)
    existing = vault.rel(found[0]) if found and found[1].get("status") == "ready" else None
    return {"url": url, "canonical": canonical, "note_id": note_id, "existing": existing}


def shortcut_finish(vault, note_id):
    if not re.fullmatch(r"[a-f0-9]{24}", note_id):
        raise ValueError("无法确认小红书笔记 ID")
    canonical = f"https://www.xiaohongshu.com/explore/{note_id}"
    for name in json.loads(payload()):
        if not name.startswith(IMPORT_INBOX + "/"):
            continue
        try:
            raw = vault.note_path(name, ("00_收件箱",))
            fields, _ = read_plugin(raw)
            _, found, _ = xhs_identity(fields.get("source") or fields.get("source_url") or "")
        except (ValueError, OSError, yaml.YAMLError):
            continue
        if found == note_id:
            return adopt(vault, name, note_id)
    return seed(vault, canonical, "标准版插件未生成这条笔记的可核验原件，请检查链接或重试")


def xhs_identity(value):
    canonical = identity(value.strip())
    match = NOTE.fullmatch(canonical)
    if not match:
        raise ValueError("需要可核对的标准小红书笔记 ID")
    return canonical, match[1], digest(("url:" + canonical).encode())


def lookup(vault, key):
    return vault.by_id(key, ("01_资料来源",)) or vault.by_id(key, ("00_收件箱",))


def managed_fields(canonical, note_id):
    return {"source_type": "external_reference", "platform": "xiaohongshu", "source_url": canonical,
            "note_id": note_id, "demo": False, "analysis_status": "pending",
            "visual_status": "not_reviewed", "transcription_status": "awaiting_media",
            "content_type": "unknown", "usage_scope": "研究和方法参考，作者个人风格独立记录",
            "methods": [], "related": [], "candidate_rules": [], "topics": ["外部创作参考"]}


def seed(vault, url, reason):
    canonical, note_id, key = xhs_identity(url)
    found = lookup(vault, key)
    if found:
        path, metadata = found
        if vault.rel(path).startswith("01_资料来源/"):
            updates = {k: v for k, v in managed_fields(canonical, note_id).items() if k not in metadata}
            if metadata.get("status") == "needs_manual" and metadata.get("import_note") != reason:
                updates["import_note"] = clean_title(reason)
            if updates:
                vault.write(path, update_fields(path.read_text(encoding="utf-8"), updates), replace=True)
            return vault.result(path, read_md(path)[0], duplicate=not updates)
        key = metadata.get("id", key)
    fields = managed_fields(canonical, note_id)
    fields.update(status="needs_manual", import_status="needs_manual", import_note=clean_title(reason))
    created = vault.record("01_资料来源", key, "source", f"小红书参考 {note_id}",
                           "公开笔记地址见属性。待补录可核对的原文或本地视频。", **fields)
    vault.log("系统｜外部参考登记", f"{link(vault.rel(created[0]))} · {reason}")
    vault.index()
    return vault.result(*created)


def read_plugin(path):
    try:
        return read_md(path)
    except (ValueError, yaml.YAMLError):
        # Upstream writes plain, unquoted titles, including titles that contain a colon.
        raw = path.read_text(encoding="utf-8-sig")
        if not raw.startswith("---\n") or "\n---\n" not in raw:
            raise ValueError("插件文件缺少可识别的属性区")
        header, body = raw[4:].split("\n---\n", 1)
        values = {}
        for row in header.splitlines():
            key, sep, value = row.partition(":")
            if sep and key in {"title", "source", "date", "Imported At", "category", "author", "published_at"}:
                values[key] = value.strip()
        return values, body.lstrip("\n")


def sanitize(value):
    def replace(match):
        try:
            return identity(match[0])
        except ValueError:
            return match[0].split("?", 1)[0]
    return XHS_URL.sub(replace, value)


def local_media(vault, origin, reference):
    text = unquote(reference.strip().strip("<>").split("|", 1)[0])
    if urlsplit(text).scheme or text.startswith(("/", "//")):
        return None
    if Path(text).suffix.lower() not in VIDEO | AUDIO | IMAGE:
        return None
    for candidate in (origin.parent / text, vault.root / text):
        try:
            actual = candidate.resolve(strict=True)
            if actual.is_relative_to(vault.root) and actual.is_file():
                return actual
        except FileNotFoundError:
            continue
    return None


def copy_body(vault, origin, body):
    media = {}
    remotes = {"video": False, "image": False}

    def capture(reference, category):
        local = local_media(vault, origin, reference)
        if local:
            if local not in media:
                media[local] = store_asset(vault, local)
            return media[local][0]
        if urlsplit(reference.strip()).scheme in {"http", "https"}:
            if category in remotes:
                remotes[category] = True
        return None

    def html(match):
        category = "image" if match[0].lower().startswith("<img") else "video"
        found = capture(match[1], category)
        return "!" + link(found) if found else match[0]

    def markdown(match):
        suffix = Path(urlsplit(match[2]).path).suffix.lower()
        category = "image" if match[1].startswith("![") or suffix in IMAGE else (
            "video" if suffix in VIDEO | AUDIO else None)
        found = capture(match[2], category) if category else None
        return ("!" if match[1].startswith("![") else "") + link(found) if found else match[0]

    def wiki(match):
        found = capture(match[1], None)
        return "!" + link(found) if found else match[0]

    body = WIKI_MEDIA.sub(wiki, body)
    body = MD_MEDIA.sub(markdown, body)
    body = HTML_MEDIA.sub(html, body)
    return sanitize(body).strip(), media, remotes


def adopt(vault, path, expected=None):
    origin = vault.note_path(path, ("00_收件箱", "01_资料来源"))
    metadata, original = read_plugin(origin)
    raw_url = metadata.get("source_url") or metadata.get("source") or metadata.get("url")
    if not isinstance(raw_url, str):
        raise ValueError("插件笔记缺少原始链接")
    canonical, note_id, key = xhs_identity(raw_url)
    if expected and note_id != expected:
        raise ValueError("插件产物的笔记 ID 与目标不一致")
    title = str(metadata.get("title") or "").strip()
    if not title or re.search(r"访问异常|小红书 - 你的生活|登录|^Untitled$", title, re.I):
        return seed(vault, canonical, "标准版插件未返回可核对的笔记内容")
    usable = re.sub(r"(?mi)^(?:# .*|Content not found)\s*$", "", original).strip()
    if not usable:
        return seed(vault, canonical, "标准版插件只返回标题，未取得正文或媒体")
    signature = digest(encode([canonical, sanitize(original), file_hash(origin)]))
    existing = lookup(vault, key)
    if existing and existing[1].get("import_fingerprint") == signature:
        return vault.result(*existing, duplicate=True)
    if existing and existing[1].get("import_fingerprint"):
        raise ValueError("已有该笔记的导入版本，请在 Obsidian 比较差异后补录")
    body, media, remote = copy_body(vault, origin, original)
    videos = [(asset, sha) for src, (asset, sha) in media.items() if src.suffix.lower() in VIDEO | AUDIO]
    images = [asset for src, (asset, _) in media.items() if src.suffix.lower() in IMAGE]
    indicates_video = videos or remote["video"] or "<video" in original.lower() or origin.name.startswith(("[V]", "[视频]"))
    content_type = "video" if indicates_video else "image_text" if images or remote["image"] else "text"
    missing_media = (indicates_video and not videos) or (remote["image"] and not images)
    fields = managed_fields(canonical, note_id)
    fields.update(import_fingerprint=signature, status="needs_manual" if missing_media else "ready",
                  import_status="needs_manual" if missing_media else "imported",
                  import_note="未取得全部本地媒体" if missing_media else "来自标准版插件的原文",
                  content_type=content_type, collected_at=timestamp(),
                  transcription_status="pending" if videos else "awaiting_media" if indicates_video else "not_applicable",
                  attachments=[asset for asset, _ in media.values()])
    for key_name in ("author", "published_at", "category", "Imported At"):
        if metadata.get(key_name):
            fields["plugin_imported_at" if key_name == "Imported At" else key_name] = sanitize(str(metadata[key_name]))
    if videos:
        fields.update(media_path=videos[0][0], media_sha256=videos[0][1])
    source_body = "## 00 导入原文\n\n" + body
    if indicates_video:
        embedded = "!" + link(videos[0][0]) if videos else "待补录可播放的本地视频。"
        if videos and embedded in source_body:
            source_body = source_body.replace(embedded, "", 1)
        source_body += "\n\n## 01 原始视频\n\n" + embedded
        source_body += "\n\n## 03 整理版口播\n\n## 04 内容拆解\n\n## 05 知识关联"
    if existing and vault.rel(existing[0]).startswith("01_资料来源/"):
        target, old = existing
        if old.get("import_status") != "needs_manual" or old.get("media_path") or target == origin:
            raise ValueError("已有原件或手工补录内容，保留原文件，请手工比较")
        original_receipt = target.read_text(encoding="utf-8")
        updates = {k: v for k, v in fields.items() if k not in {"related", "topics", "methods", "candidate_rules"}}
        updates["title"] = clean_title(title)
        updated = update_fields(original_receipt, updates)
        vault.write(target, updated.rstrip() + "\n\n" + source_body + "\n", replace=True)
        saved = target, read_md(target)[0]
    else:
        saved = vault.record("01_资料来源", key, "source", title, source_body, **fields)
    vault.log("系统｜标准版导入", f"{link(vault.rel(saved[0]))} · {content_type} · {len(media)} 个本地附件")
    vault.index()
    return vault.result(*saved)


def attach(vault, note, local, reason):
    target = vault.note_path(note, ("01_资料来源",))
    metadata, _ = read_md(target)
    source = Path(local).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in VIDEO | AUDIO:
        raise ValueError("需要一个可读取的本地音频或视频")
    info = json.loads(native_media("probe", source))
    if not (info["audio"] or info["video"]):
        raise ValueError("文件没有可读音轨或视频轨")
    path, sha = store_asset(vault, source)
    if metadata.get("media_sha256") == sha:
        return vault.result(target, metadata, duplicate=True)
    change = {"media_path": path, "media_sha256": sha, "content_type": "video" if info["video"] else "audio",
              "duration_seconds": info["duration"], "transcription_status": "pending" if info["audio"] else "no_audio",
              "status": "ready", "import_status": "manual_media", "import_note": clean_title(reason)}
    if metadata.get("analysis_id"):
        change.update(analysis_status="needs_review", visual_status="not_reviewed")
    if metadata.get("transcript_id"):
        change["previous_transcript_id"] = metadata["transcript_id"]
        change["transcript_id"] = None
    text = update_fields(target.read_text(encoding="utf-8"), change)
    heading = "## 01 原始视频" if "## 01 原始视频" not in text else "## 补录媒体版本"
    text += f"\n{heading}\n\n!{link(path)}\n"
    for number, section in (("03", "整理版口播"), ("04", "内容拆解"), ("05", "知识关联")):
        if f"## {number} {section}" not in text:
            text += f"\n## {number} {section}\n"
    vault.write(target, text, replace=True)
    vault.log("系统｜本地媒体补录", f"{link(note)} · {reason} · {sha}")
    vault.index()
    return vault.result(target, read_md(target)[0])


def status(vault):
    result = []
    for path in vault.files(("01_资料来源",)):
        fields, _ = read_md(path)
        if fields.get("source_type") == "external_reference":
            keys = ("note_id", "title", "author", "published_at", "source_url", "status", "content_type",
                    "import_status", "import_note", "media_path", "transcription_status", "analysis_status", "visual_status")
            result.append({"path": vault.rel(path), **{key: fields[key] for key in keys if key in fields}})
    return result


def metadata(vault, note_id):
    if not re.fullmatch(r"[a-f0-9]{24}", note_id):
        raise ValueError("笔记 ID 格式不正确")
    _, _, key = xhs_identity("https://www.xiaohongshu.com/explore/" + note_id)
    found = vault.by_id(key, ("01_资料来源",))
    if not found or found[1].get("note_id") != note_id:
        raise ValueError("请先导入并归档相应的笔记")
    target, _ = found
    observed = json.loads(payload())
    if observed.get("note_id") != note_id:
        raise ValueError("公开页面笔记 ID 与目标不一致")
    changes = {"metadata_source": "Obsidian 公开页面笔记详情"}
    if observed.get("author"):
        changes["author"] = clean_title(observed["author"])
    if observed.get("published_ms"):
        when = datetime.fromtimestamp(int(observed["published_ms"]) / 1000, timezone.utc).astimezone()
        changes["published_at"] = when.isoformat(timespec="seconds")
    if len(changes) == 1:
        raise ValueError("公开页面没有可核对的作者或发布日期")
    original = target.read_text(encoding="utf-8")
    updated = update_fields(original, changes)
    if original != updated:
        vault.write(target, updated, replace=True)
        vault.log("系统｜外部元数据核验", f"{link(vault.rel(target))} · {note_id}")
    return {"path": vault.rel(target), **changes, "duplicate": original == updated}


def dispatch(vault, args):
    if args.action == "shortcut-url":
        return {"uri": "obsidian://kb-import-xhs?" + urlencode({"vault": args.vault, "link": share_link(payload())})}
    if args.action == "shortcut-prepare":
        return shortcut_prepare(vault, payload())
    if args.action == "shortcut-finish":
        return shortcut_finish(vault, args.note_id)
    if args.action == "seed":
        return seed(vault, args.url, args.reason)
    if args.action == "adopt":
        return adopt(vault, args.path, args.expect_id)
    if args.action == "attach-media":
        return attach(vault, args.note, args.local, args.reason)
    if args.action == "metadata":
        return metadata(vault, args.note_id)
    return status(vault)


def main():
    cli = parser("v1.0 标准版小红书导入适配")
    actions = cli.add_subparsers(dest="action", required=True)
    cmd = actions.add_parser("shortcut-url")
    cmd.add_argument("--vault", required=True)
    actions.add_parser("shortcut-prepare")
    cmd = actions.add_parser("shortcut-finish")
    cmd.add_argument("note_id")
    cmd = actions.add_parser("seed")
    cmd.add_argument("url")
    cmd.add_argument("--reason", required=True)
    cmd = actions.add_parser("adopt")
    cmd.add_argument("path")
    cmd.add_argument("--expect-id")
    cmd = actions.add_parser("attach-media")
    cmd.add_argument("note")
    cmd.add_argument("local")
    cmd.add_argument("--reason", required=True)
    actions.add_parser("metadata").add_argument("note_id")
    actions.add_parser("status")
    return run_cli(cli, dispatch)


if __name__ == "__main__":
    raise SystemExit(main())
