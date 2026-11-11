#!/usr/bin/env python3
"""Check the reproducibility of a draft and report its independent review state."""
from 知识库 import APPROVED, link, read_md
from 创作上下文 import CREATIVE, all_entries, verify
from 公共工具 import STATE, file_hash, parser, run_cli


def check(vault, args):
    target = vault.note_path(args.output, ("04_创作成果",))
    fields, body = read_md(target)
    warnings = []
    package = None
    context_id = fields.get("context_id")
    if fields.get("kind") in CREATIVE and not context_id:
        warnings.append("这是早期稿件，未记录创作上下文包")
    if context_id:
        package = verify(vault, context_id)
        allowed = {row["path"]: row for row in all_entries(package)}
        if fields.get("project") != link(package["project"]["path"]) or fields["kind"] != package["kind"]:
            raise ValueError("稿件与上下文包的项目或类型不匹配")
        for row in fields.get("context_used", []):
            reference = allowed.get(row["path"])
            if not reference or row["sha256"] != reference["sha256"]:
                raise ValueError("实际引用记录与上下文包不匹配")
        for row in all_entries(package):
            path = vault.path(row["path"])
            if not path.is_file() or file_hash(path) != row["sha256"]:
                snapshot = vault.path(f"{STATE}/引用快照/{row['sha256']}.txt")
                if snapshot.is_file() and file_hash(snapshot) == row["sha256"]:
                    warnings.append(f"上下文引用此后发生变化，原版本快照已校验 {row['path']}")
                else:
                    warnings.append(f"上下文引用此后发生变化，缺少可验证快照 {row['path']}")
        if package["history_status"] == "insufficient":
            warnings.append("本次经选择在个人正式样本不足时继续")
    reviews = []
    own_hash = file_hash(target)
    journal = vault.path("log.md")
    approvals = dict(APPROVED.findall(journal.read_text())) if journal.exists() else {}
    approved = fields.get("status") == "approved" and approvals.get(fields.get("id")) == own_hash
    if fields.get("status") == "approved" and not approved:
        raise ValueError("定稿文件哈希与日志登记不一致")
    draft_path = fields.get("approved_from") if approved else args.output
    draft_path = draft_path or args.output
    draft_hash = (fields.get("approved_source_sha256") if approved else None) or own_hash
    stale_reviews = []
    for path in vault.files(("04_创作成果",)):
        item, _ = read_md(path)
        if (item.get("kind") == "review" and item.get("project") == fields.get("project")
                and link(draft_path) in item.get("related", [])):
            matching = any(row.get("path") == draft_path and row.get("sha256") == draft_hash
                           for row in item.get("reviewed_versions", []))
            (reviews if matching else stale_reviews).append(vault.rel(path))
    if "待核实" in body or "待实拍" in body:
        warnings.append("稿件包含待核实或待实拍项")
    return {"path": args.output, "version": fields.get("version"), "context_id": context_id,
            "program": "verified" if package else "legacy" if fields.get("kind") in CREATIVE else "not_required",
            "actual_references": fields.get("context_used", []), "reviews": reviews,
            "outdated_or_legacy_reviews": stale_reviews,
            "user_confirmation": approved, "warnings": warnings}


def main():
    cli = parser("v1.0 稿件质量程序检查")
    cli.add_argument("output")
    return run_cli(cli, check, locked=False)


if __name__ == "__main__":
    raise SystemExit(main())
