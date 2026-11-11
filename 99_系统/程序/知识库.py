#!/usr/bin/env python3
"""Local file operations for the Obsidian and Codex workflows."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from urllib.parse import quote

import yaml

from 资料处理 import MAX_BYTES, digest, file_text, from_url, identity

FOLDERS = ('00_收件箱','01_资料来源','02_知识笔记','03_项目','04_创作成果')
OUTPUTS = {'research':'研究', 'outline':'大纲', 'script':'口播', 'storyboard':'分镜',
           'review':'审核', 'retrospective':'复盘', 'article':'文章'}
WIKI = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]')
APPROVED = re.compile(r'^<!-- approved:([a-f0-9]{64}):([a-f0-9]{64}) -->$', re.M)


def timestamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def today():
    return timestamp()[:10]


def normalized(value):
    return ' '.join(unicodedata.normalize('NFC', value).split())


def clean_title(title):
    title = normalized(title)
    if not title:
        raise ValueError('标题不能为空')
    return title[:160]


def filename(title):
    return re.sub(r'[^\w\-\u3000-\u303f]+', '-', title, flags=re.UNICODE).strip('-_')[:56] or '无标题'


def payload(value=None):
    text = sys.stdin.read() if value is None else value
    if not text.strip():
        raise ValueError('输入内容为空')
    if len(text) > 500000:
        raise ValueError('文字超过 500,000 字符，请先分批')
    return text.strip()


def read_md(path):
    text = path.read_text(encoding='utf-8-sig')
    if text.startswith('---\n'):
        fields = text.split('\n---\n', 1)
        if len(fields) != 2:
            raise ValueError(f'YAML 属性缺少结束行：{path.name}')
        metadata = yaml.safe_load(fields[0][4:]) or {}
        if not isinstance(metadata, dict):
            raise ValueError(f'YAML 属性应是键值表：{path.name}')
        return metadata, fields[1].lstrip('\n')
    return {}, text


def frontmatter(fields, body):
    # JSON scalars and arrays are valid YAML and keep dates as editable strings.
    header = '\n'.join(f'{k}: {json.dumps(v, ensure_ascii=False)}' for k,v in fields.items())
    return f'---\n{header}\n---\n\n{body.rstrip()}\n'


def link(path):
    target = Path(path)
    stem = target.with_suffix('').as_posix() if target.suffix == '.md' else target.as_posix()
    if re.search(r'[\[\]#^|\\]', stem):
        # Managed files never require this; path-based maps use Markdown for special names.
        raise ValueError('请用普通 Markdown 链接引用含 Obsidian 保留符号的手动文件')
    return f'[[{stem}]]'


class Vault:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        for folder in FOLDERS + ('05_视图',):
            path = self.root / folder
            if not path.is_dir() or path.is_symlink():
                raise ValueError(f'不是可访问的知识库，缺少安全的 {folder}')

    def path(self, name):
        target = Path(name)
        if target.is_absolute() or not target.parts or any(p.startswith('.') for p in target.parts):
            raise ValueError('需要 Vault 内的可见相对路径')
        path = self.root / target
        if path.resolve() != path:
            raise ValueError('拒绝符号链接或指向库外的路径')
        return path

    def note_path(self, name, allowed=FOLDERS):
        p = self.path(name)
        if Path(name).parts[0] not in allowed or p.suffix != '.md' or not p.is_file():
            raise ValueError(f'找不到允许引用的 Markdown：{name}')
        return p

    def rel(self, path):
        return path.relative_to(self.root).as_posix()

    @contextmanager
    def locked(self):
        lock = self.root / '.kb-lock'
        with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'r+b') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def write(self, target, text, *, replace=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stage:
            temporary = Path(stage.name)
            try:
                stage.write(text.encode('utf-8') if isinstance(text,str) else text)
                stage.flush()
                os.fsync(stage.fileno())
                if replace:
                    os.replace(temporary, target)
                else:
                    os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

    def log(self, event, detail, marker=''):
        event = clean_title(event).replace('<!--','')
        detail = normalized(detail).replace('<!--','').replace('-->','')
        if not detail:
            raise ValueError('日志需要可核实的内容')
        now = timestamp()
        record = f'\n## [{now}] {event}\n\n{detail}\n'
        self.append_journal('log.md', '成长日志', record + (marker + '\n' if marker else ''))
        category = event.partition('｜')[0]
        if category == '知识' or event in {'知识新增', '知识整理', '关系确认', '知识演化'}:
            self.append_journal('02_知识笔记/_演化/知识成长记录.md', '知识成长记录', record)
        elif category == '创作' or event in {'阶段交付', '定稿', '立项'}:
            self.append_journal('99_系统/创作记忆/创作记录.md', '创作记录', record)
        else:
            self.append_journal('99_系统/_运行数据/日志/系统日志.md', '系统日志', record)

    def append_journal(self, relative, title, record):
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
        with os.fdopen(os.open(path, flags, 0o600), 'a', encoding='utf-8') as handle:
            if path.stat().st_size == 0:
                handle.write(f'# {title}\n\n按时间追加。纠错请新增条目并引用旧记录。\n')
            handle.write(record)
            handle.flush()
            os.fsync(handle.fileno())

    def files(self, folders=FOLDERS):
        for folder in folders:
            for path in sorted((self.root / folder).rglob('*.md')):
                if path.is_file() and path.resolve() == path and all(not x.startswith(('.', '_')) for x in path.relative_to(self.root).parts):
                    yield path

    def by_id(self, ident, folders):
        paths = sorted(self.files(folders), key=lambda path: not path.stem.endswith('-' + ident[:16]))
        for path in paths:
            try:
                meta, _ = read_md(path)
            except (OSError, ValueError, yaml.YAMLError):
                continue
            if meta.get('id') == ident:
                return path, meta
        return None

    def record(self, folder, ident, record_type, title, body, **fields):
        title = clean_title(title)
        target = self.path(f'{folder}/{filename(title)}-{ident[:16]}.md')
        meta = dict(id=ident,type=record_type,title=title,created=today(),updated=today(),topics=[])
        meta.update(fields)
        self.write(target, frontmatter(meta, body))
        return target, meta

    def result(self, path, meta, duplicate=False):
        return {'path':self.rel(path),'id':meta.get('id'),'status':meta.get('status'),'duplicate':duplicate}

    def attachment(self, data, suffix):
        target = self.path(f'01_资料来源/_附件/{digest(data)}{suffix}')
        if not target.exists():
            self.write(target,data)
        elif digest(target.read_bytes()) != digest(data):
            raise ValueError('现有附件哈希与预期不一致')
        return self.rel(target)

    def collect(self, args):
        if args.url or args.url_stdin:
            url = (args.url or sys.stdin.read()).strip()
            key = identity(url)
            ident = digest(('url:' + key).encode())
            prior = self.by_id(ident, ('01_资料来源','00_收件箱'))
            if prior:
                return self.result(*prior, duplicate=True)
            title, metadata, content, blob, problem = from_url(url, args.offline)
            if metadata.get('source_url') != key:
                ident = digest(('url:' + metadata['source_url']).encode())
                prior = self.by_id(ident, ('01_资料来源','00_收件箱'))
                if prior:
                    return self.result(*prior, duplicate=True)
        elif args.file:
            src = Path(args.file).expanduser().resolve(strict=True)
            if not src.is_file() or src.stat().st_size > MAX_BYTES:
                raise ValueError('需要可读的普通文件，大小不超过 40 MiB')
            data = src.read_bytes()
            ident = digest(b'file:' + data)
            prior = self.by_id(ident, ('01_资料来源','00_收件箱'))
            if prior:
                return self.result(*prior, duplicate=True)
            suffix = src.suffix.lower()
            suffix = suffix if re.fullmatch(r'\.[a-z0-9]{1,10}',suffix) else '.bin'
            content, modality = file_text(data,suffix)
            title, metadata, blob, problem = src.stem, {'modality':modality,'original_name':src.name}, (suffix,data), ''
        else:
            content = payload()
            ident = digest(('text:' + normalized(content)).encode())
            prior = self.by_id(ident, ('01_资料来源','00_收件箱'))
            if prior:
                return self.result(*prior, duplicate=True)
            title, metadata, blob, problem = content.splitlines()[0][:54], {'modality':'text'}, None, ''
        status = 'pending' if content.strip() or blob else 'needs_manual'
        metadata.update(demo=args.demo, status=status, collected_at=timestamp())
        if args.topics:
            metadata['topics'] = topics(args.topics)
        if blob:
            metadata['attachment'] = link(self.attachment(blob[1],blob[0]))
        if problem:
            metadata['import_note'] = problem
        if not content.strip():
            content = ('原件已保存，未提取正文。' if blob else
                       '尚未取得可核验正文。可以在公开页面复制所需段落，然后补录。')
        if blob:
            content += '\n\n原始附件 ' + metadata['attachment']
        saved = self.record('00_收件箱', ident, 'inbox', args.title or title, content, **metadata)
        self.log('收件', link(self.rel(saved[0])) + f'，状态 {status}。')
        self.index()
        return self.result(*saved)

    def archive(self, args):
        path = self.note_path(args.path, ('00_收件箱',))
        meta, body = read_md(path)
        manual = not meta.get('id') or meta.get('manual_capture') is True
        manual_id = digest(('manual:' + normalized(body)).encode()) if manual else None
        if meta.get('status') == 'archived' and (not manual or meta.get('id') == manual_id):
            archived = self.note_path(meta['archived_to'], ('01_资料来源',))
            return self.result(archived,read_md(archived)[0],duplicate=True)
        if meta.get('status') == 'needs_manual' and not args.paste:
            raise ValueError('该来源未取到正文，先复制公开正文再用 archive --paste 补录')
        if args.paste:
            if meta.get('status') != 'needs_manual':
                raise ValueError('只对待手动补录的记录使用 --paste')
            body = payload()
            meta.pop('import_note',None)
            meta['extraction'] = '用户手动粘贴，保留提交的文字'
        if not body.strip() or re.fullmatch(r'\s*##\s*原文或灵感\s*', body):
            raise ValueError('收件箱笔记还没有正文')
        ident = manual_id if manual else meta['id']
        if not re.fullmatch(r'[0-9a-f]{64}',str(ident)):
            raise ValueError('收件箱 id 不是合法稳定标识')
        prior = self.by_id(ident, ('01_资料来源',))
        if prior:
            destination, source = prior
        else:
            fields = {k:v for k,v in meta.items() if k not in {'id','type','title','status','updated','related','archived_to','manual_capture'}}
            if fields.get('created') is not None:
                fields['created'] = str(fields['created'])
            fields.setdefault('collected_at', timestamp())
            fields.update(status='ready',related=[link(args.path)])
            destination, source = self.record('01_资料来源',ident,'source',meta.get('title') or path.stem,body,**fields)
        receipt = dict(meta, id=ident, type='inbox', title=meta.get('title') or path.stem,
                       status='archived', created=str(meta.get('created') or today()), updated=today(),
                       archived_to=self.rel(destination), related=[link(self.rel(destination))])
        if manual:
            receipt['manual_capture'] = True
        receipt_text = body if manual else f'原文与附件已归档到 {link(self.rel(destination))}。'
        self.write(path, frontmatter(receipt, receipt_text), replace=True)
        self.log('归档', f'{link(args.path)} → {link(self.rel(destination))}。')
        self.index()
        return self.result(destination,source,duplicate=bool(prior))

    def references(self, paths):
        return [link(self.rel(self.note_path(name))) for name in dict.fromkeys(paths)]

    def note(self, args):
        body = payload()
        references = []
        seen = set()
        for name, excerpt in args.evidence:
            path = self.note_path(name, ('01_资料来源','02_知识笔记'))
            meta, original = read_md(path)
            snippet = normalized(excerpt)
            if not 8 <= len(snippet) <= 260 or snippet not in normalized(original):
                raise ValueError(f'证据摘录与实际正文不匹配：{name}')
            if name not in seen:
                seen.add(name)
                references.append((name,snippet))
        ident = digest(json.dumps([normalized(body),references,args.kind,args.demo], ensure_ascii=False).encode())
        prior = self.by_id(ident, ('02_知识笔记',))
        if prior:
            return self.result(*prior,duplicate=True)
        if references:
            body += '\n\n## 可核对的出处\n\n' + '\n'.join(f'- {link(name)} · {excerpt}' for name,excerpt in references)
        path, meta = self.record('02_知识笔记',ident,'note',args.title,body,status='unverified' if args.kind == 'hypothesis' else 'active',
                                 kind=args.kind,topics=topics(args.topics),related=[link(p) for p,_ in references],demo=args.demo)
        self.log('知识整理', f'{link(self.rel(path))}，核对 {len(references)} 条原文关联。')
        self.index()
        return self.result(path,meta)

    def project(self,args):
        title = clean_title(args.title)
        ident = digest(('project:' + title.casefold() + str(args.demo)).encode())
        prior = self.by_id(ident, ('03_项目',))
        if prior:
            return self.result(*prior,duplicate=True)
        refs = self.references(args.ref)
        body = args.summary or '下一步在 Codex 指定本项目要推进的阶段。'
        if refs:
            body += '\n\n## 资料与知识\n\n' + '\n'.join(f'- {value}' for value in refs)
        path,meta = self.record('03_项目',ident,'project',title,body,status='active',
                                topics=topics(args.topics),related=refs,demo=args.demo)
        self.log('立项',link(self.rel(path)))
        self.index()
        return self.result(path,meta)

    def output(self,args):
        project = self.note_path(args.project, ('03_项目',))
        meta,_ = read_md(project)
        if meta.get('type') != 'project' or not meta.get('id'):
            raise ValueError('目标需要是有稳定 ID 的项目')
        content = payload()
        refs = self.references(args.ref)
        from 创作上下文 import CREATIVE, output_data
        context_fields = {}
        if args.kind in CREATIVE:
            content, context_fields = output_data(self, args, content)
        elif args.kind == 'review':
            from 公共工具 import file_hash
            reviewed = []
            for name in args.ref:
                candidate = self.note_path(name)
                fields, _ = read_md(candidate)
                if fields.get('project') == link(args.project) and fields.get('kind') in CREATIVE:
                    reviewed.append({'path': name, 'sha256': file_hash(candidate)})
            context_fields['reviewed_versions'] = reviewed
        related = [link(args.project), *refs]
        location = '04_创作成果/' + project.stem
        existing = []
        for path in self.files(('04_创作成果',)):
            if path.parent == self.path(location):
                item,_ = read_md(path)
                if item.get('kind') == args.kind and item.get('status') != 'approved':
                    existing.append((path,item))
        previous = max(existing,key=lambda row:row[1].get('version',0),default=None)
        fingerprint = digest(json.dumps([content,refs,context_fields],ensure_ascii=False).encode())
        if previous and previous[1].get('fingerprint') == fingerprint:
            return self.result(*previous,duplicate=True)
        version = (previous[1]['version'] if previous else 0) + 1
        ident = digest(f'{meta["id"]}:{args.kind}:{version}'.encode())
        title = f'{OUTPUTS[args.kind]} v{version:02d}'
        if previous:
            prior_link = link(self.rel(previous[0]))
            if prior_link not in related:
                related.append(prior_link)
        if refs:
            content += '\n\n## 关联依据\n\n' + '\n'.join(f'- {value}' for value in refs)
        path, saved = self.record(location,ident,'output',title,content,kind=args.kind,status='draft',
                                  version=version,fingerprint=fingerprint,topics=meta.get('topics',[]),
                                  related=related,project=link(args.project),demo=meta.get('demo',False),**context_fields)
        self.log('阶段交付',f'{link(self.rel(path))}，项目 {link(args.project)}。')
        self.index()
        return self.result(path,saved)

    def approve(self,args):
        target = self.note_path(args.path, ('04_创作成果',))
        fields,body = read_md(target)
        if fields.get('type') != 'output' or fields.get('status') != 'draft' or fields.get('demo'):
            raise ValueError('只有明确确认的正式项目草稿能登记为风格样本')
        confirmation = clean_title(args.confirmed)
        if fields.get('context_id'):
            from 稿件核查 import check as assess
            from types import SimpleNamespace
            quality = assess(self, SimpleNamespace(output=args.path))
            if not quality['reviews']:
                raise ValueError('v1.0 正式稿先保存一条与当前稿件哈希一致的独立审核')
            if any('缺少可验证快照' in warning for warning in quality['warnings']):
                raise ValueError('存在无法核验的创作上下文，请重新预查和审稿')
        source_hash = digest(target.read_bytes())
        ident = digest(('approved:' + fields['id'] + ':' + source_hash).encode())
        existing = self.by_id(ident, ('04_创作成果',))
        if existing:
            return self.result(*existing,duplicate=True)
        extras = {k:v for k,v in fields.items() if k not in {'id','type','title','status','updated','created','related'}}
        path,meta = self.record('04_创作成果/'+target.parent.name,ident,'output','定稿 '+fields['title'],body,
                                **extras,status='approved',approved_at=timestamp(), approved_from=args.path,
                                approved_source_sha256=source_hash, related=[link(args.path),*fields.get('related',[])])
        path.chmod(0o444)
        self.log('定稿',f'{link(self.rel(path))}。依据：{confirmation}。',f'<!-- approved:{ident}:{digest(path.read_bytes())} -->')
        self.index()
        return self.result(path,meta)

    def index(self):
        lines = ['# 资料地图','','从这里进入所有收件、来源、知识、项目与成果。证据关系见笔记正文及反向链接。','']
        for folder in FOLDERS:
            lines.extend([f'## {folder}',''])
            for path in self.files((folder,)):
                try:
                    meta,_ = read_md(path)
                    title = str(meta.get('title') or path.stem)
                    topics_value = meta.get('topics') or []
                    label = ' · '.join([str(meta.get('status','')),', '.join(str(x) for x in topics_value)])
                except (OSError,ValueError,yaml.YAMLError):
                    title,label = path.stem,'需修复编码或属性'
                label = normalized(label).strip(' ·')
                url = '../' + quote(self.rel(path),safe='/')
                display = re.sub(r'[\[\]\\]', '', normalized(title))
                lines.append(f'- [{display}]({url})' + (f' · {label}' if label else ''))
            lines.append('')
        self.write(self.path('05_视图/资料地图.md'),'\n'.join(lines),replace=True)
        return {'path':'05_视图/资料地图.md'}

    def search(self,query,limit,demo=False):
        terms = tokens(query)
        if not terms or not 1 <= limit <= 50:
            raise ValueError('需要有效的检索词和 1 到 50 的结果上限')
        scored = []
        for path in self.files():
            try:
                meta,body = read_md(path)
            except (OSError,ValueError,yaml.YAMLError):
                continue
            if meta.get('status') in {'archived','needs_manual'} or (meta.get('demo') and not demo):
                continue
            title = str(meta.get('title',path.stem))
            names = tokens(title + ' ' + path.stem)
            labels = tokens(str(meta.get('topics','')))
            references = tokens(str(meta.get('related','')))
            content = tokens(body)
            score = 6*len(terms & names) + 2*len(terms & labels)
            score += len(terms & references) + len(terms & content)
            if score and meta.get('type') in {'source','note'}:
                score += 2
            if score:
                snippet = max(body.splitlines() or [''],key=lambda line:len(terms & tokens(line))).strip()[:170]
                scored.append((score,self.rel(path),title,snippet))
        scored.sort(key=lambda row:(-row[0],row[1]))
        return [{'path':name,'title':title,'excerpt':snippet} for _,name,title,snippet in scored[:limit]]

    def context(self,args):
        result = {'matches':self.search(args.query,args.limit,args.demo),'log':'log.md'}
        if args.creative:
            journal = self.path('log.md')
            approvals = APPROVED.findall(journal.read_text()) if journal.exists() else []
            expected = dict(approvals)
            selected = []
            for path in self.files(('04_创作成果',)):
                meta,_ = read_md(path)
                ident = meta.get('id')
                if ident in expected:
                    if expected[ident] != digest(path.read_bytes()):
                        raise ValueError(f'定稿校验失败：{self.rel(path)}')
                    selected.append((ident,{'path':self.rel(path),'title':meta['title']}))
            known = dict(selected)
            if set(known) != set(expected):
                raise ValueError('成长日志内登记的定稿文件缺失')
            result.update(rules='99_系统/创作规则.md', style='99_系统/风格库.md',
                          approved_samples=[known[key] for key,_ in reversed(approvals)][:4])
        return result

    def check(self):
        warnings = []
        counted = 0
        identities = {}
        for path in self.files():
            counted += 1
            try:
                meta, body = read_md(path)
            except (OSError, ValueError, yaml.YAMLError) as error:
                warnings.append(f'{self.rel(path)} · 编码或属性无效：{error.__class__.__name__}')
                continue
            if not all(meta.get(field) for field in ('id','type','title','created','status')):
                warnings.append(f'{self.rel(path)} · 元数据不全，正文仍可检索')
            if meta.get('id') and meta.get('type') != 'inbox':
                ident = (meta['type'],meta['id'])
                if ident in identities:
                    warnings.append(f'{self.rel(path)} · 重复 ID')
                identities[ident] = path
            references = '\n'.join(str(meta.get(key,'')) for key in ('related','project','attachment'))
            for value in dict.fromkeys(WIKI.findall(body+'\n'+references)):
                # Existing imported literal excerpts are data; validate only generated links.
                if meta.get('type') in {'source','inbox'} and value not in references:
                    continue
                try:
                    if '/' in value and not any(self.path(value+suffix).is_file() for suffix in ('','.md')):
                        warnings.append(f'{self.rel(path)} · 缺失链接 {value}')
                except (OSError,ValueError):
                    warnings.append(f'{self.rel(path)} · 非法链接 {value}')
        return {'checked':counted,'warnings':warnings}


def topics(text):
    return list(dict.fromkeys(normalized(x) for x in re.split(r'[,，]',text or '') if normalized(x)))


def tokens(text):
    words = set(re.findall(r'[a-z0-9][a-z0-9._-]*',text.casefold()))
    for chunk in re.findall(r'[\u3400-\u9fff]+',text):
        words.update(chunk[i:i+2] for i in range(max(1,len(chunk)-1)))
    return words


def arguments():
    parser = argparse.ArgumentParser(description='Obsidian v1.0 本地知识工具')
    parser.add_argument('--root',default=Path(__file__).resolve().parents[2],help=argparse.SUPPRESS)
    actions = parser.add_subparsers(dest='action',required=True)
    collect = actions.add_parser('collect',help='收集到统一收件箱')
    capture = collect.add_mutually_exclusive_group(required=True)
    capture.add_argument('--text',action='store_true',help='从 stdin 读文字')
    capture.add_argument('--url')
    capture.add_argument('--url-stdin',action='store_true',help='从 stdin 读链接')
    capture.add_argument('--file')
    collect.add_argument('--offline',action='store_true')
    collect.add_argument('--title')
    collect.add_argument('--topics')
    collect.add_argument('--demo',action='store_true')
    archive = actions.add_parser('archive',help='归档收件箱原件')
    archive.add_argument('path')
    archive.add_argument('--paste',action='store_true',help='从 stdin 补录实际复制的正文')
    note = actions.add_parser('note',help='新增知识，正文来自 stdin')
    note.add_argument('--title',required=True)
    note.add_argument('--topics')
    note.add_argument('--kind',default='knowledge',choices=['knowledge','idea','experience','research','thought','hypothesis','insight'])
    note.add_argument('--evidence',nargs=2,action='append',default=[],metavar=('PATH','QUOTE'))
    note.add_argument('--demo',action='store_true')
    project = actions.add_parser('project',help='显式创建项目')
    project.add_argument('--title',required=True)
    project.add_argument('--summary')
    project.add_argument('--topics')
    project.add_argument('--ref',action='append',default=[])
    project.add_argument('--demo',action='store_true')
    output = actions.add_parser('output',help='保存独立阶段或新版本，正文来自 stdin')
    output.add_argument('project')
    output.add_argument('kind',choices=OUTPUTS)
    output.add_argument('--ref',action='append',default=[])
    output.add_argument('--used',action='append',default=[])
    output.add_argument('--context-id')
    approve = actions.add_parser('approve',help='登记用户明确确认过的正式版本')
    approve.add_argument('path')
    approve.add_argument('--confirmed',required=True)
    for command in ('search','context'):
        find = actions.add_parser(command,help='检索或生成任务上下文入口')
        find.add_argument('query')
        find.add_argument('--limit',type=int,default=8)
        find.add_argument('--demo',action='store_true',help='包含隔离标记的演示数据')
        if command == 'context':
            find.add_argument('--creative',action='store_true')
    lookup = actions.choices['search']
    lookup.add_argument('--mode', choices=['keyword','hybrid','semantic'], default='keyword')
    lookup.add_argument('--topic')
    lookup.add_argument('--type')
    lookup.add_argument('--source-type')
    lookup.add_argument('--format')
    lookup.add_argument('--related')
    journal = actions.add_parser('log',help='追加成长日志')
    journal.add_argument('event')
    journal.add_argument('detail')
    actions.add_parser('index',help='刷新资料地图')
    actions.add_parser('check',help='检查元数据与引用')
    return parser.parse_args()


def main():
    args = arguments()
    try:
        vault = Vault(args.root)
        if args.action == 'search':
            if args.mode != 'keyword' or any((args.topic,args.type,args.source_type,args.format,args.related)):
                from 混合检索 import retrieve
                result = retrieve(vault, args)
            else:
                result = vault.search(args.query,args.limit,args.demo)
        elif args.action == 'context':
            result = vault.context(args)
        elif args.action == 'check':
            result = vault.check()
        else:
            with vault.locked():
                if args.action == 'log':
                    vault.log(args.event,args.detail)
                    result = {'path':'log.md'}
                elif args.action == 'index':
                    result = vault.index()
                else:
                    result = getattr(vault,args.action)(args)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except UnicodeDecodeError:
        print('知识库：文本编码无效，请另存为 UTF-8 后重试。',file=sys.stderr)
    except (OSError,ValueError,KeyError,yaml.YAMLError) as error:
        print('知识库：' + str(error).splitlines()[0],file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
