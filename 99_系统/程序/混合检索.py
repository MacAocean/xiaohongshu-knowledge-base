"""Local E5 embeddings and Markdown chunk retrieval."""
from pathlib import Path
import re
import sqlite3

import yaml

from 知识库 import normalized, read_md, tokens
from 资料处理 import digest
from 公共工具 import SUPPORT, STATE, encode, file_hash

MODEL_DIR = SUPPORT / "models/e5-small"


def embeddings(vault, rows):
    try:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as error:
        raise ValueError("缺少 numpy、onnxruntime 或 tokenizers") from error
    model = MODEL_DIR / "model_quantized.onnx"
    tokenizer_file = MODEL_DIR / "tokenizer.json"
    if not model.is_file() or not tokenizer_file.is_file():
        raise ValueError("缺少本地 E5 模型文件")
    revision_path = MODEL_DIR / "revision.txt"
    if revision_path.is_file():
        revision = revision_path.read_text().strip()
    else:
        revision = file_hash(model) + file_hash(tokenizer_file)
    fingerprints = [digest(encode([revision, role, text])) for role, text in rows]
    cache = vault.path(f"{STATE}/语义向量缓存.sqlite3")
    cache.parent.mkdir(parents=True, exist_ok=True)
    wanted = set(fingerprints)
    with sqlite3.connect(cache, timeout=12) as db:
        db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB NOT NULL)")
        vectors = {key: np.frombuffer(data, dtype="<f4")
                   for key, data in db.execute("SELECT key, vector FROM vectors")
                   if key in wanted and len(data) == 384 * 4}
        missing = {key: row for key, row in zip(fingerprints, rows) if key not in vectors}
        if missing:
            tokenizer = Tokenizer.from_file(str(tokenizer_file))
            tokenizer.enable_truncation(max_length=384)
            tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 3
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
            items = list(missing.items())
            for start in range(0, len(items), 12):
                batch = items[start:start+12]
                encoded = tokenizer.encode_batch([f"{role}: {text}" for _, (role, text) in batch])
                ids = np.array([item.ids for item in encoded], dtype="int64")
                mask = np.array([item.attention_mask for item in encoded], dtype="int64")
                hidden = session.run(["last_hidden_state"], {"input_ids": ids, "attention_mask": mask,
                                      "token_type_ids": np.zeros_like(ids)})[0]
                pooled = (hidden * mask[..., None]).sum(1) / np.maximum(mask.sum(1)[:, None], 1)
                output = (pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)).astype("<f4")
                for (key, _), vector in zip(batch, output):
                    vectors[key] = vector
                db.executemany("INSERT OR IGNORE INTO vectors VALUES (?, ?)",
                               [(key, vector.tobytes()) for (key, _), vector in zip(batch, output)])
    return [vectors[key] for key in fingerprints]


def chunks(body):
    pending = []
    first = 1
    for number, line in enumerate(body.splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("<!--"):
            continue
        if pending and (text.startswith("#") or sum(map(len, pending)) + len(text) > 430):
            yield first, "\n".join(pending)
            pending = []
        if not pending:
            first = number
        if len(text) > 430:
            for offset in range(0, len(text), 350):
                yield number, text[offset:offset+430]
        else:
            pending.append(text)
    if pending:
        yield first, "\n".join(pending)


def retrieve(vault, args):
    query = tokens(args.query)
    if not query or not 1 <= args.limit <= 50:
        raise ValueError("需要检索词和 1 到 50 的结果上限")
    peers = set()
    if args.related:
        from 知识关系 import connected
        target = vault.note_path(args.related)
        fields, _ = read_md(target)
        peers = connected(vault, args.related)
        for item in fields.get("related", []):
            name = str(item).removeprefix("[[").removesuffix("]]").split("|", 1)[0]
            peers.add(name if name.endswith(".md") else name + ".md")
    passages = []
    for path in vault.files():
        try:
            fields, body = read_md(path)
        except (ValueError, OSError, yaml.YAMLError):
            continue
        name = vault.rel(path)
        if fields.get("status") in {"archived", "needs_manual"} or (fields.get("demo") and not args.demo):
            continue
        if args.type and fields.get("type") != args.type:
            continue
        if args.topic and args.topic.casefold() not in {str(x).casefold() for x in fields.get("topics", []) or []}:
            continue
        if args.source_type and fields.get("source_type") != args.source_type:
            continue
        if args.format and args.format not in {fields.get("kind"), fields.get("content_type"), fields.get("content_format")}:
            continue
        title = str(fields.get("title", path.stem))
        heading_hits = 6 * len(query & tokens(title + " " + path.stem))
        tag_hits = 2 * len(query & tokens(str(fields.get("topics", []))))
        relation_hits = len(query & tokens(str(fields.get("related", []))))
        markdown = path.read_text(encoding="utf-8-sig")
        offset = (len(markdown) - len(body))
        header_lines = markdown[:offset].count("\n")
        for line, text in list(chunks(body)) or [(1, title)]:
            line += header_lines
            lexical = heading_hits + tag_hits + relation_hits + len(query & tokens(text))
            if lexical and fields.get("type") in {"source", "note"}:
                lexical += 2
            related = name in peers
            passages.append((name, title, line, text, lexical, related))
    similarities = None
    if args.mode in {"hybrid", "semantic"} and passages:
        values = embeddings(vault, [("query", args.query), *[("passage", f"{title}\n{text}") for _, title, _, text, _, _ in passages]])
        similarities = [float(vector @ values[0]) for vector in values[1:]]
    best = {}
    for index, (name, title, line, text, lexical, related) in enumerate(passages):
        semantic = similarities[index] if similarities is not None else None
        if args.mode == "keyword" and not lexical and not related:
            continue
        strength = max(0, ((semantic or 0) - 0.65) / 0.35)
        if args.mode != "keyword" and lexical == 0 and semantic is not None and semantic < 0.68 and not related:
            continue
        key_score = lexical / (lexical + 10)
        score = {"keyword": key_score, "semantic": strength, "hybrid": 0.50 * key_score + 0.50 * strength}[args.mode]
        score += 0.05 if related else 0
        timecode = re.search(r"\[(\d\d:\d\d:\d\d\.\d+) →", text)
        result = {"path": name, "title": title, "line": line,
                  "timestamp": timecode[1] if timecode else None, "excerpt": normalized(text)[:240],
                  "keyword_score": lexical, "semantic_score": round(semantic, 4) if semantic is not None else None,
                  "related": related}
        if name not in best or score > best[name][0]:
            best[name] = score, result
    return [row for _, row in sorted(best.values(), key=lambda item: (-item[0], item[1]["path"]))[:args.limit]]
