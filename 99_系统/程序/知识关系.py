#!/usr/bin/env python3
"""Evidence-bearing relationships, proposed revisions and knowledge health."""
import datetime
import json
from pathlib import Path
import re

import yaml

from 知识库 import clean_title, link, normalized, payload, read_md, timestamp
from 资料处理 import digest, download
from 公共工具 import HASH, encode, exact_evidence, file_hash, parser, run_cli, update_fields

RELATIONS = "02_知识笔记/_关系"
EVOLUTION = "02_知识笔记/_演化"
KINDS = ("supports", "contradicts", "extends", "example_of", "applies_to", "derived_from", "related_to")


def objects(vault, folder):
    for path in sorted(vault.path(folder).glob("*.md")):
        if not path.is_symlink():
            fields, body = read_md(path)
            yield path, fields, body


def object_ref(vault, relative):
    path = vault.note_path(relative, ("01_资料来源", "02_知识笔记"))
    metadata, _ = read_md(path)
    if not metadata.get("id"):
        raise ValueError(f"对象缺少稳定 ID，请先归档或登记 {relative}")
    return {"path": relative, "id": metadata["id"], "sha256": file_hash(path)}


def evidence_markdown(rows):
    return "\n".join(f"- {link(row['path'])} · {row['quote']}" for row in rows)


def save_proposal(vault, folder, kind, title, body, key, fields):
    ident = digest(encode(key))
    for path, metadata, _ in objects(vault, folder):
        if metadata.get("id") == ident:
            return vault.result(path, metadata, duplicate=True)
    path, metadata = vault.record(folder, ident, kind, title, body, status="proposed",
                                  created_at=timestamp(), updated_at=timestamp(), **fields)
    vault.log("知识｜待确认", f"{link(vault.rel(path))} · {kind}")
    return vault.result(path, metadata)


def relate(vault, args):
    left, right = object_ref(vault, args.left), object_ref(vault, args.right)
    if left["id"] == right["id"]:
        raise ValueError("同一对象不能与自己建立关系")
    evidence = exact_evidence(vault, args.evidence)
    rationale = clean_title(args.reason)
    body = f"{link(args.left)} → {args.kind} → {link(args.right)}\n\n{rationale}\n\n## 证据\n\n" + evidence_markdown(evidence)
    return save_proposal(vault, RELATIONS, "relation", f"{args.kind} {rationale}", body,
                         [left["id"], right["id"], args.kind, evidence, rationale],
                         {"relation": args.kind, "left": args.left, "right": args.right,
                          "left_id": left["id"], "right_id": right["id"],
                          "evidence": evidence, "related": [link(args.left), link(args.right)]})


def propose(vault, args):
    if not args.target.startswith("02_知识笔记/"):
        raise ValueError("更新建议应指向现有知识或观点笔记")
    target = object_ref(vault, args.target)
    evidence = exact_evidence(vault, args.evidence)
    revision = payload()
    body = revision + "\n\n## 证据\n\n" + evidence_markdown(evidence)
    return save_proposal(vault, EVOLUTION, "evolution", "知识更新建议 " + Path(args.target).stem,
                         body, [target, evidence, revision],
                         {"target": args.target, "target_id": target["id"], "target_sha256": target["sha256"],
                          "evidence": evidence, "related": [link(args.target)]})


def confirm(vault, ident, reason):
    if not HASH.fullmatch(ident):
        raise ValueError("需要完整的关系或建议 ID")
    for folder in (RELATIONS, EVOLUTION):
        for path, fields, body in objects(vault, folder):
            if fields.get("id") != ident:
                continue
            if fields.get("status") == "confirmed":
                return {**vault.result(path, fields, duplicate=True), "revision": fields.get("revision")}
            evidence = exact_evidence(vault, [(row["path"], row["quote"]) for row in fields["evidence"]])
            if evidence != fields["evidence"]:
                raise ValueError("证据文件自提出以来已变更，请复核并重新提出")
            changes = {"status": "confirmed", "updated_at": timestamp(), "confirmed_at": timestamp(),
                       "confirmed_reason": clean_title(reason)}
            if folder == RELATIONS:
                if any(object_ref(vault, fields[key])["id"] != fields[key+"_id"] for key in ("left", "right")):
                    raise ValueError("关系两端已改变，请重新核对")
            else:
                original = vault.note_path(fields["target"], ("02_知识笔记",))
                if file_hash(original) != fields["target_sha256"]:
                    raise ValueError("旧知识已修改，请基于当前版本重新提出")
                previous, old_body = read_md(original)
                from difflib import unified_diff
                revised = body.split("\n\n## 证据\n\n", 1)[0]
                difference = "\n".join(unified_diff(old_body.strip().splitlines(), revised.strip().splitlines(),
                                                     fromfile="旧知识", tofile="确认后内容", lineterm=""))
                revision_key = digest(("evolution:" + ident).encode())
                saved = vault.by_id(revision_key, ("02_知识笔记",))
                if not saved:
                    revised_body = revised + "\n\n## 证据\n\n" + evidence_markdown(evidence)
                    revised_body += f"\n\n## 演化记录\n\n旧版本 {link(fields['target'])}\n\n```diff\n{difference}\n```"
                    saved = vault.record("02_知识笔记", revision_key, "note", "更新版 " + previous["title"],
                                         revised_body, status="active", kind=previous.get("kind", "knowledge"),
                                         topics=previous.get("topics", []), demo=previous.get("demo", False),
                                         related=[link(fields["target"]), *[link(row["path"]) for row in evidence]],
                                         previous=fields["target"], evolution_id=ident)
                changes["revision"] = vault.rel(saved[0])
            vault.write(path, update_fields(path.read_text(encoding="utf-8"), changes), replace=True)
            vault.log("知识｜演化确认" if folder == EVOLUTION else "知识｜关系确认",
                      f"{link(vault.rel(path))} · {reason}" +
                      (f" · {link(changes['revision'])}" if changes.get("revision") else ""))
            vault.index()
            return {**vault.result(path, read_md(path)[0]), "revision": changes.get("revision")}
    raise ValueError("找不到指定关系或建议")


def connected(vault, name):
    result = set()
    for _, row, _ in objects(vault, RELATIONS):
        if row.get("status") != "confirmed":
            continue
        left, right = row["left"], row["right"]
        if left == name:
            result.add(right)
        if right == name:
            result.add(left)
    return result


def health(vault, args):
    groups = {key: [] for key in ("duplicate_content", "similar_candidates", "conflicts", "stale_evidence",
                                  "missing_evidence", "old_inbox", "unverified", "missing_media",
                                  "missing_transcript", "pending_analysis", "unreachable_sources")}
    fingerprints = {}
    notes = []
    for path in vault.files(("00_收件箱", "01_资料来源", "02_知识笔记")):
        try:
            fields, body = read_md(path)
        except (ValueError, OSError, yaml.YAMLError):
            continue
        name = vault.rel(path)
        if fields.get("demo") and not args.demo:
            continue
        status = fields.get("status")
        is_pending = status in {"pending", "needs_manual"}
        if path.relative_to(vault.root).parts[0] == "00_收件箱" and is_pending:
            try:
                created = datetime.date.fromisoformat(str(fields.get("created", ""))[:10])
                if (datetime.date.today() - created).days >= args.days:
                    groups["old_inbox"].append(name)
            except ValueError:
                pass
        if status == "unverified":
            groups["unverified"].append(name)
        kind = fields.get("type")
        if kind in {"source", "note"} and status != "needs_manual":
            key = digest(normalized(body).encode())
            if key in fingerprints:
                groups["duplicate_content"].append([fingerprints[key], name])
            fingerprints[key] = name
        if kind == "note":
            if not fields.get("related") and fields.get("kind") in {"knowledge", "insight", "research"}:
                groups["missing_evidence"].append(name)
            notes.append((name, str(fields.get("title", "")) + "\n" + body[:750]))
        media = fields.get("media_path")
        if media and not vault.path(media).is_file():
            groups["missing_media"].append(name)
        if fields.get("content_type") in {"video", "audio"} and fields.get("transcription_status") not in {"completed", "no_audio"}:
            groups["missing_transcript"].append(name)
        if fields.get("source_type") == "external_reference" and fields.get("analysis_status") != "verified":
            groups["pending_analysis"].append(name)
        if args.probe and kind == "source" and fields.get("source_url"):
            try:
                _, _, resolved = download(fields["source_url"])
                if "/login" in resolved:
                    groups["unreachable_sources"].append({"path": name, "reason": "公开链接进入登录页"})
            except (ValueError, OSError) as error:
                groups["unreachable_sources"].append({"path": name, "reason": str(error)})
    for folder in (RELATIONS, EVOLUTION):
        for path, fields, _ in objects(vault, folder):
            if fields.get("relation") == "contradicts":
                groups["conflicts"].append({"path": vault.rel(path), "status": fields.get("status")})
            for item in fields.get("evidence", []):
                target = vault.path(item["path"])
                if not target.is_file() or file_hash(target) != item["sha256"]:
                    groups["stale_evidence"].append({"relationship": vault.rel(path), "path": item["path"]})
    if args.similar and len(notes) > 1:
        from 混合检索 import embeddings
        sample = notes[:80]
        vectors = embeddings(vault, [("passage", value) for _, value in sample])
        for i, first in enumerate(vectors):
            for j in range(i + 1, len(vectors)):
                score = float(first @ vectors[j])
                if score >= 0.88:
                    groups["similar_candidates"].append({"paths": [sample[i][0], sample[j][0]],
                                                          "cosine": round(score, 4)})
    return {"checks": vault.check(), "results": groups, "network_probe": args.probe, "semantic": args.similar}


def suggest(vault, args):
    """Show real passages beside a new source; never infer a factual relationship from score."""
    from types import SimpleNamespace
    from 混合检索 import retrieve
    source = vault.note_path(args.path, ("01_资料来源", "02_知识笔记"))
    fields, body = read_md(source)
    if not fields.get("id") or fields.get("status") in {"needs_manual", "archived"}:
        raise ValueError("需要有正文的正式归档资料")
    title = str(fields.get("title") or source.stem)
    summary = normalized(body)[:280]
    options = SimpleNamespace(query=f"{title} {summary}", mode="hybrid", demo=bool(fields.get("demo")),
                              limit=16, topic=None, type=None, source_type=None, format=None, related=args.path)
    try:
        rows = retrieve(vault, options)
    except (OSError, ValueError):
        options.mode = "keyword"
        rows = retrieve(vault, options)
    candidates = []
    for row in rows:
        if row["path"] == args.path or not row["path"].startswith(("01_资料来源/", "02_知识笔记/")):
            continue
        other, other_body = read_md(vault.path(row["path"]))
        duplicate = normalized(body) == normalized(other_body)
        candidates.append({**row, "id": other.get("id"), "classification": "duplicate" if duplicate else "candidate",
                           "next_step": "先查阅两条原文，再判断是补充、例证、冲突还是无关"})
    return {"source": args.path, "id": fields["id"], "mode": options.mode, "candidates": candidates[:8],
            "requires_confirmation": True}

def dispatch(vault, args):
    if args.action == "relate":
        return relate(vault, args)
    if args.action == "propose":
        return propose(vault, args)
    if args.action == "confirm":
        return confirm(vault, args.id, args.reason)
    if args.action == "suggest":
        return suggest(vault, args)
    if args.action == "related":
        return sorted(connected(vault, args.path))
    return health(vault, args)


def main():
    cli = parser("v1.0 知识关系与演化")
    actions = cli.add_subparsers(dest="action", required=True)
    cmd = actions.add_parser("relate")
    cmd.add_argument("left")
    cmd.add_argument("right")
    cmd.add_argument("kind", choices=KINDS)
    cmd.add_argument("--reason", required=True)
    cmd.add_argument("--evidence", nargs=2, action="append", required=True)
    cmd = actions.add_parser("propose")
    cmd.add_argument("target")
    cmd.add_argument("--evidence", nargs=2, action="append", required=True)
    cmd = actions.add_parser("confirm")
    cmd.add_argument("id")
    cmd.add_argument("--reason", required=True)
    actions.add_parser("related").add_argument("path")
    actions.add_parser("suggest").add_argument("path")
    cmd = actions.add_parser("health")
    cmd.add_argument("--probe", action="store_true")
    cmd.add_argument("--similar", action="store_true")
    cmd.add_argument("--demo", action="store_true")
    cmd.add_argument("--days", type=int, default=14)
    return run_cli(cli, dispatch)


if __name__ == "__main__":
    raise SystemExit(main())
