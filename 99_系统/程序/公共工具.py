"""Shared file operations for the v1.0 commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

import yaml

from 知识库 import Vault, normalized, read_md

ROOT = Path(__file__).resolve().parents[2]
STATE = "99_系统/_运行数据"
SUPPORT = Path(os.environ.get("KB_V21_SUPPORT", str(
    Path.home() / "Library/Application Support/personal-knowledge-base-t")))
HASH = re.compile(r"[0-9a-f]{64}")


def file_hash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def update_fields(text, changes):
    """Only change owned, top-level scalar YAML fields and preserve the rest of the file."""
    if not text.startswith("---\n"):
        return "---\n" + "".join(f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
                                   for key, value in changes.items()) + "---\n\n" + text
    header, separator, body = text[4:].partition("\n---\n")
    if not separator:
        raise ValueError("Markdown 属性区未闭合")
    parsed = yaml.compose(header)
    if parsed and not isinstance(parsed, yaml.MappingNode):
        raise ValueError("Markdown 属性区不是键值表")
    lines = header.splitlines()
    for key, value in changes.items():
        field = f"{key}: {json.dumps(value, ensure_ascii=False)}"
        nodes = [(k, v) for k, v in (parsed.value if parsed else []) if k.value == key]
        if len(nodes) > 1:
            raise ValueError(f"属性重复，需先人工检查 {key}")
        if nodes:
            k, v = nodes[0]
            if k.start_mark.column != 0 or v.end_mark.line != k.start_mark.line:
                raise ValueError(f"程序管理的属性存在人工多行格式 {key}")
            lines[k.start_mark.line] = field
        else:
            lines.append(field)
    return "---\n" + "\n".join(lines) + separator + body


def store_asset(vault, path):
    suffix = path.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    sha = file_hash(path)
    destination = vault.path(f"01_资料来源/_附件/{sha}{suffix}")
    if destination.exists():
        if file_hash(destination) != sha:
            raise ValueError("现有附件内容与哈希不一致")
        return vault.rel(destination), sha
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent) as staging:
        with path.open("rb") as source:
            shutil.copyfileobj(source, staging)
        staging.flush()
        os.fsync(staging.fileno())
        if file_hash(Path(staging.name)) != sha:
            raise ValueError("原件在复制期间被修改，请重试")
        os.link(staging.name, destination)
    return vault.rel(destination), sha


def exact_evidence(vault, pairs):
    evidence = []
    for name, quote in pairs:
        path = vault.note_path(name, ("01_资料来源", "02_知识笔记"))
        meta, body = read_md(path)
        excerpt = normalized(quote)
        if not 8 <= len(excerpt) <= 260 or excerpt not in normalized(body):
            raise ValueError(f"证据摘录与实际正文不匹配 {name}")
        evidence.append({"path": name, "id": meta.get("id"), "quote": excerpt, "sha256": file_hash(path)})
    if not evidence:
        raise ValueError("至少提供一条可回查的原文摘录")
    return evidence


def parser(description):
    args = argparse.ArgumentParser(description=description)
    args.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    return args


def run_cli(args, dispatch, *, locked=True):
    options = args.parse_args()
    try:
        vault = Vault(options.root)
        if locked:
            with vault.locked():
                result = dispatch(vault, options)
        else:
            result = dispatch(vault, options)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, yaml.YAMLError) as error:
        print(f"知识库 v1.0：{str(error).splitlines()[0]}", file=sys.stderr)
        return 1
