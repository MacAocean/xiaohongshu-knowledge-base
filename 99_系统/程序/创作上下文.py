#!/usr/bin/env python3
"""Prepare and verify a reproducible set of references before creative writing."""
import json
from pathlib import Path
import re
from types import SimpleNamespace

import yaml

from 知识库 import APPROVED, clean_title, link, normalized, payload, read_md, timestamp, tokens
from 资料处理 import digest
from 公共工具 import HASH, STATE, encode, file_hash, parser, run_cli

CREATIVE = ("outline", "script", "storyboard", "article")
MEMORY = "99_系统/创作记忆"
PROFILES = ("99_系统/创作规则.md", "99_系统/风格库.md", "99_系统/事实与素材.md",
            f"{MEMORY}/创作者档案.md")
MEMORY_KINDS = ("style", "revision", "quality", "method", "external-method")
QUALITY = {"preferred": 8, "accepted": 4, "needs_revision": -4, "rejected": -12}


def entry(vault, relative, role, **extra):
    path = vault.path(relative)
    if not path.is_file() or path.suffix != ".md":
        raise ValueError(f"上下文来源不是可读 Markdown {relative}")
    meta, body = read_md(path)
    raw = path.read_bytes()
    sha = digest(raw)
    snapshot = vault.path(f"{STATE}/引用快照/{sha}.txt")
    if not snapshot.exists():
        vault.write(snapshot, raw)
        snapshot.chmod(0o444)
    elif file_hash(snapshot) != sha:
        raise ValueError("引用快照损坏，请从备份恢复")
    return {"path": relative, "sha256": sha, "role": role,
            "title": str(meta.get("title") or path.stem), "preview": normalized(body)[:240], **extra}


def approved_works(vault):
    journal = vault.path("log.md")
    records = APPROVED.findall(journal.read_text(encoding="utf-8")) if journal.exists() else []
    expected = dict(records)
    found = {}
    for path in vault.files(("04_创作成果",)):
        metadata, body = read_md(path)
        ident = metadata.get("id")
        if ident not in expected:
            continue
        if file_hash(path) != expected[ident] or metadata.get("status") != "approved" or metadata.get("demo"):
            raise ValueError(f"正式样本与登记不一致 {vault.rel(path)}")
        found[ident] = (path, metadata, body)
    if found.keys() != expected.keys():
        raise ValueError("成长日志中有正式作品文件缺失")
    return list(found.values())


def memory_records(vault):
    folder = vault.path(MEMORY)
    for path in sorted(folder.glob("*.md")):
        if path.is_symlink():
            continue
        fields, body = read_md(path)
        if fields.get("type") == "creative_memory" and fields.get("confirmed"):
            yield path, fields, body


def relevant(query, fields, body):
    return (3 * len(query & tokens(str(fields.get("title", ""))))
            + 2 * len(query & tokens(str(fields.get("topics", []))))
            + len(query & tokens(body[:24000])))


def ranked_history(vault, args, works, memories, query):
    quality = {}
    for _, fields, _ in memories:
        if fields["kind"] == "quality":
            key = fields["target"]
            if key not in quality or fields["created_at"] >= quality[key]["created_at"]:
                quality[key] = fields

    def rank(row):
        path, metadata, body = row
        feedback = quality.get(vault.rel(path), {})
        rating = QUALITY.get(feedback.get("quality"), 0)
        audience = len(tokens(args.audience) & tokens(str(metadata.get("audience", ""))))
        score = relevant(query, metadata, body) + 8 * (metadata.get("kind") == args.kind) + rating + 2 * audience
        return score, metadata.get("approved_at", "")

    def applicable(row):
        path, meta, body = row
        audience_match = bool(tokens(args.audience) & tokens(str(meta.get("audience", ""))))
        return (quality.get(vault.rel(path), {}).get("quality") != "rejected"
                and (relevant(query, meta, body) > 0 or (meta.get("kind") == args.kind and audience_match)))

    eligible = [row for row in works if applicable(row)]
    return [entry(vault, vault.rel(path), "history", version=meta.get("version"),
                  approved_at=meta.get("approved_at"), format=meta.get("kind"),
                  selection_score=rank((path, meta, body))[0],
                  recognition=quality.get(vault.rel(path), {}).get("quality", "用户确认定稿"))
            for path, meta, body in sorted(eligible, key=rank, reverse=True)[:4]]


def build(vault, args):
    project = vault.note_path(args.project, ("03_项目",))
    metadata, _ = read_md(project)
    if metadata.get("type") != "project":
        raise ValueError("需要可核对的项目笔记")
    if args.baseline and not metadata.get("demo"):
        raise ValueError("对照基线仅用于演示项目")
    query = clean_title(args.query)
    query_tokens = tokens(f"{query} {args.audience} {args.goal}")
    works = approved_works(vault) if not args.baseline else []
    memories = list(memory_records(vault)) if not args.baseline else []
    history = ranked_history(vault, args, works, memories, query_tokens)
    rules = [entry(vault, path, "rule") for path in PROFILES if vault.path(path).is_file()]
    mandatory = {"99_系统/创作规则.md", "99_系统/风格库.md"}
    if not mandatory.issubset(row["path"] for row in rules):
        raise ValueError("缺少创作规则或风格库")
    selected_paths = {row["path"] for row in history}
    memories = sorted(memories, key=lambda row: (
        (10 if row[1].get("target") in selected_paths else 0) + relevant(query_tokens, row[1], row[2]),
        row[1]["created_at"]), reverse=True)
    selected_memories = []
    for path, fields, body in memories:
        kind = fields["kind"]
        target = fields.get("target")
        if kind == "external-method":
            if relevant(query_tokens, fields, body) == 0 or not target:
                continue
            target_file = vault.note_path(target, ("01_资料来源",))
            source, _ = read_md(target_file)
            if source.get("analysis_status") != "verified" or fields.get("target_sha256") != file_hash(target_file):
                continue
        if kind in ("revision", "quality") and target not in selected_paths and not relevant(query_tokens, fields, body):
            continue
        if target and not vault.path(target).is_file():
            continue
        selected_memories.append(entry(vault, vault.rel(path), kind, reference=target))
    selected_memories = selected_memories[:8]
    from 混合检索 import retrieve
    options = SimpleNamespace(query=query, mode="hybrid", demo=bool(metadata.get("demo")), limit=12,
                              topic=None, type=None, source_type=None, format=None, related=None)
    try:
        local = retrieve(vault, options)
        retrieval_mode = "hybrid"
    except (OSError, ImportError, ValueError):
        local = vault.search(query, 12, bool(metadata.get("demo")))
        retrieval_mode = "keyword_fallback"
    candidates = list(dict.fromkeys([*args.ref, *(row["path"] for row in local)]))
    knowledge = []
    for name in candidates:
        target = vault.note_path(name)
        fields, _ = read_md(target)
        if fields.get("status") in {"needs_manual", "archived"}:
            if name in args.ref:
                raise ValueError(f"该记录尚不能作为创作证据 {name}")
            continue
        if fields.get("demo") and not metadata.get("demo"):
            if name in args.ref:
                raise ValueError(f"正式项目不能把演示资料当成真实依据 {name}")
            continue
        external = fields.get("source_type") == "external_reference" or fields.get("external_synthesis") is True
        if args.baseline and external:
            continue
        if fields.get("type") not in {"source", "note"} and name not in args.ref:
            continue
        if fields.get("source_type") == "external_reference" and fields.get("analysis_status") != "verified":
            if name in args.ref:
                raise ValueError(f"外部参考仍缺可核查分析 {name}")
            continue
        role = "external_reference" if external else "knowledge"
        knowledge.append(entry(vault, name, role))
    for row in selected_memories:
        target = row.get("reference")
        if row["role"] == "external-method" and target not in {item["path"] for item in knowledge}:
            knowledge.append(entry(vault, target, "external_reference"))
    revision = None
    if args.revision or args.replace_exact:
        if not args.revision or not args.replace_exact:
            raise ValueError("局部修改须提供旧版本路径及要替换的唯一原文")
        previous = vault.note_path(args.revision, ("04_创作成果",))
        old, body = read_md(previous)
        if old.get("project") != link(args.project) or old.get("kind") != args.kind:
            raise ValueError("局部修改的稿件必须属于同一项目与成果类型")
        if clean_output_body(body).count(args.replace_exact) != 1:
            raise ValueError("要替换的原文必须在指定版本中恰好出现一次")
        revision = entry(vault, args.revision, "revision_target", replace_exact=args.replace_exact)
    state = "baseline" if args.baseline else "available" if history else "insufficient"
    package = {"schema": 1, "query": query, "kind": args.kind, "audience": clean_title(args.audience),
               "goal": clean_title(args.goal), "project": entry(vault, args.project, "project"),
               "history_status": state, "history_choice": normalized(args.continue_without_history or ""),
               "history": history, "memory": selected_memories, "knowledge": knowledge[:max(8, len(args.ref))],
               "rules": rules, "revision": revision, "retrieval_mode": retrieval_mode, "created_at": timestamp()}
    key = digest(encode(package))
    target = vault.path(f"{STATE}/上下文/{key}.json")
    if not target.exists():
        vault.write(target, encode(package))
    return {"id": key, "path": vault.rel(target), **package}


def clean_output_body(body):
    before, separator, refs = body.rpartition("\n\n## 关联依据\n\n")
    if separator and refs.strip() and all(line.startswith("- [[") for line in refs.strip().splitlines()):
        return before.rstrip()
    return body.rstrip()


def verify(vault, key, *, project=None, kind=None):
    if not isinstance(key, str) or not HASH.fullmatch(key):
        raise ValueError("正式创作须先构建上下文包，并传入 --context-id")
    path = vault.path(f"{STATE}/上下文/{key}.json")
    raw = path.read_bytes()
    if digest(raw) != key:
        raise ValueError("创作上下文包已改变")
    package = json.loads(raw)
    if (project and package["project"]["path"] != project) or (kind and package["kind"] != kind):
        raise ValueError("上下文包与项目或成果类型不一致")
    if package["history_status"] == "insufficient" and not package["history_choice"]:
        raise ValueError("正式历史作品样本不足。由用户选择继续后，再生成上下文包。")
    if project:
        for row in all_entries(package):
            if file_hash(vault.path(row["path"])) != row["sha256"]:
                raise ValueError(f"上下文引用已修改，需重新构建 {row['path']}")
    return package


def all_entries(package):
    entries = [package["project"], *package["rules"], *package["history"], *package["memory"], *package["knowledge"]]
    if package["revision"]:
        entries.append(package["revision"])
    return entries


def output_data(vault, args, content):
    package = verify(vault, args.context_id, project=args.project, kind=args.kind)
    allowed = {row["path"]: row for row in all_entries(package)}
    requested = list(dict.fromkeys([*args.ref, *args.used]))
    if set(requested) - allowed.keys():
        raise ValueError("使用的资料不在已验证上下文中，请补入上下文包")
    if package["history"] and not any(allowed[path]["role"] == "history" for path in requested):
        raise ValueError("请用 --used 登记本次实际采用的正式作品")
    if package["revision"]:
        row = package["revision"]
        _, body = read_md(vault.path(row["path"]))
        content = clean_output_body(body).replace(row["replace_exact"], content, 1)
        if row["path"] not in requested:
            requested.append(row["path"])
    used = [{key: allowed[path][key] for key in ("path", "sha256", "role")} for path in requested]
    return content, {"context_id": args.context_id, "used_refs": [item["path"] for item in used],
                     "history_status": package["history_status"], "context_used": used}


def save_memory(vault, args):
    body = payload()
    target = vault.note_path(args.target) if args.target else None
    if args.kind in ("revision", "quality", "external-method") and not target:
        raise ValueError("这类记忆需要可回查的目标文件")
    if args.kind in {"revision", "quality"}:
        if not args.target.startswith("04_创作成果/") or read_md(target)[0].get("type") != "output":
            raise ValueError("稿件修改或质量反馈必须引用真实的稿件版本")
    if args.kind == "quality" and not args.quality:
        raise ValueError("作品反馈需要用户实际认可程度")
    if args.kind == "external-method":
        metadata, _ = read_md(target)
        if metadata.get("source_type") != "external_reference" or metadata.get("analysis_status") != "verified":
            raise ValueError("外部方法需要有依据且已分析的外部作品")
    previous = sorted(((path, meta) for path, meta, _ in memory_records(vault)
                       if meta["kind"] == args.kind and meta["title"] == clean_title(args.title)),
                      key=lambda pair: pair[1].get("version", 0))
    from difflib import unified_diff
    difference = ""
    if previous:
        _, old_body = read_md(previous[-1][0])
        original, _, _ = old_body.partition("\n\n## 相对上一条的变化")
        difference = "\n".join(unified_diff(original.splitlines(), body.splitlines(),
                                            fromfile="上一条", tofile="本次", lineterm=""))
    source_revision = None
    if args.previous:
        if args.kind != "revision":
            raise ValueError("旧版本只用于 revision 记忆")
        before = vault.note_path(args.previous, ("04_创作成果",))
        old_fields, old_body = read_md(before)
        new_fields, new_body = read_md(target)
        if (before == target or old_fields.get("project") != new_fields.get("project")
                or old_fields.get("kind") != new_fields.get("kind")):
            raise ValueError("两个版本必须属于同一项目和阶段")
        source_revision = {"path": args.previous, "sha256": file_hash(before)}
        draft_diff = "\n".join(unified_diff(clean_output_body(old_body).splitlines(),
                                              clean_output_body(new_body).splitlines(),
                                              fromfile="改前", tofile="改后", lineterm=""))
        body += "\n\n## 稿件变动\n\n" + link(args.previous) + " → " + link(args.target)
        if draft_diff:
            body += "\n\n```diff\n" + draft_diff + "\n```"
    version = previous[-1][1]["version"] + 1 if previous else 1
    ident = digest(encode([args.kind, clean_title(args.title), args.target, body, args.confirmed, args.quality]))
    existing = vault.path(MEMORY)
    for path in existing.glob(f"*-{ident[:16]}.md"):
        metadata, _ = read_md(path)
        if metadata.get("id") == ident:
            return vault.result(path, metadata, duplicate=True)
    saved = vault.record(MEMORY, ident, "creative_memory", args.title,
                         body + ("\n\n## 相对上一条的变化\n\n```diff\n" + difference + "\n```" if difference else ""),
                         status="confirmed", kind=args.kind, confirmed=clean_title(args.confirmed),
                         target=args.target, target_sha256=file_hash(target) if target else None,
                         quality=args.quality, version=version, created_at=timestamp(), source_revision=source_revision,
                         previous=vault.rel(previous[-1][0]) if previous else None)
    vault.log("创作｜记忆确认", f"{link(vault.rel(saved[0]))} · {args.kind} · v{version}")
    return vault.result(*saved)


def add_history(vault, args):
    project = vault.note_path(args.project, ("03_项目",))
    meta, _ = read_md(project)
    if meta.get("type") != "project" or meta.get("demo"):
        raise ValueError("个人历史作品只登记在真实项目下")
    source = args.file.expanduser()
    if source.suffix.lower() != ".md" or not source.is_file():
        raise ValueError("请提供原始 Markdown 文件")
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    if not text.strip():
        raise ValueError("历史作品为空")
    confirmation = clean_title(args.confirmed)
    identifier = digest(encode(["history", meta["id"], args.kind, digest(raw)]))
    existing = vault.by_id(identifier, ("04_创作成果",))
    if existing:
        return vault.result(*existing, duplicate=True)
    stored = vault.path(f"{MEMORY}/_原始历史作品/{digest(raw)}.md")
    if not stored.exists():
        vault.write(stored, raw)
        stored.chmod(0o444)
    elif file_hash(stored) != digest(raw):
        raise ValueError("已保存历史原稿和预期哈希不同")
    snapshot, fields = vault.record(f"04_创作成果/{project.stem}", identifier, "output",
                                    args.title, text, status="approved", demo=False,
                                    kind=args.kind, audience=clean_title(args.audience),
                                    version=1, approved_at=timestamp(), history_import=True,
                                    original_sha256=digest(raw), original_path=vault.rel(stored),
                                    project=link(args.project), related=[link(args.project), link(vault.rel(stored))],
                                    topics=meta.get("topics", []))
    snapshot.chmod(0o444)
    vault.log("创作｜登记个人历史作品", f"{link(vault.rel(snapshot))} · {confirmation}",
              f"<!-- approved:{identifier}:{file_hash(snapshot)} -->")
    vault.index()
    return vault.result(snapshot, fields)

def dispatch(vault, args):
    if args.action == "build":
        return build(vault, args)
    if args.action == "memory":
        return save_memory(vault, args)
    if args.action == "history":
        return add_history(vault, args)
    return verify(vault, args.id)


def main():
    cli = parser("v1.0 创作上下文与确认记忆")
    actions = cli.add_subparsers(dest="action", required=True)
    cmd = actions.add_parser("build")
    cmd.add_argument("query")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--kind", choices=CREATIVE, required=True)
    cmd.add_argument("--audience", required=True)
    cmd.add_argument("--goal", required=True)
    cmd.add_argument("--ref", action="append", default=[])
    cmd.add_argument("--continue-without-history")
    cmd.add_argument("--baseline", action="store_true")
    cmd.add_argument("--revision")
    cmd.add_argument("--replace-exact")
    actions.add_parser("show").add_argument("id")
    cmd = actions.add_parser("memory")
    cmd.add_argument("kind", choices=MEMORY_KINDS)
    cmd.add_argument("--title", required=True)
    cmd.add_argument("--confirmed", required=True)
    cmd.add_argument("--target")
    cmd.add_argument("--previous", help="revision 的旧稿路径，用于实际稿件差异")
    cmd.add_argument("--quality", choices=QUALITY)
    cmd = actions.add_parser("history")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--kind", choices=CREATIVE, required=True)
    cmd.add_argument("--file", type=Path, required=True)
    cmd.add_argument("--title", required=True)
    cmd.add_argument("--audience", required=True)
    cmd.add_argument("--confirmed", required=True)
    return run_cli(cli, dispatch)


if __name__ == "__main__":
    raise SystemExit(main())
