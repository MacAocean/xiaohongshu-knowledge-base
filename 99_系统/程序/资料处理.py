"""Bounded public web and local file ingestion. Never use saved browser sessions."""
import hashlib
import ipaddress
import re
import shutil
import socket
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

MAX_BYTES = 40 * 1024 * 1024
XHS = {'www.xiaohongshu.com', 'xiaohongshu.com', 'xhslink.com', 'www.xhslink.com'}
TRACKING = {'fbclid', 'gclid', 'xsec_token', 'xsec_source', 'token', 'key', 'api_key',
            'signature', 'sig', 'access_token', 'auth', 'password', 'cookie', 'sessionid',
            'redirect', 'redirectpath', 'returnto', 'return_url'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def split_url(url):
    parsed = urlsplit(url.strip())
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username
            or parsed.password or re.search(r'[\s\\\x00-\x1f\x7f]', url.strip())):
        raise ValueError('只接受公开 HTTP/HTTPS 链接，URL 不能含账号或空白')
    if parsed.port not in {None, 80, 443}:
        raise ValueError('只接受标准 HTTP/HTTPS 端口')
    return parsed


def identity(url):
    parsed = split_url(url)
    host = parsed.hostname.lower()
    if host in XHS:
        note_id = re.search(r'/([a-f0-9]{24})/?$', parsed.path, re.I)
        if note_id:
            return 'https://www.xiaohongshu.com/explore/' + note_id[1].lower()
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() not in TRACKING and not k.lower().startswith('utm_')]
    netloc = parsed.netloc.lower()
    if parsed.port == (443 if parsed.scheme == 'https' else 80):
        netloc = parsed.hostname.lower()
    return urlunsplit((parsed.scheme, netloc, parsed.path or '/', urlencode(sorted(pairs)), ''))


class Page(HTMLParser):
    blocks = {'p', 'h1', 'h2', 'h3', 'h4', 'li', 'pre', 'blockquote', 'br'}
    ignored = {'script', 'style', 'noscript', 'nav', 'footer', 'header', 'aside', 'svg', 'form'}
    void = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.meta = {}
        self.title = []
        self.areas = {'article': [], 'main': [], 'body': []}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('property') or attrs.get('name')
            if key and attrs.get('content'):
                self.meta[key.lower()] = attrs['content'].strip()
        if tag not in self.void:
            self.stack.append(tag)
        if tag in self.blocks:
            self.separate()

    def handle_endtag(self, tag):
        if tag in self.blocks:
            self.separate()
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]

    def separate(self):
        if not self.ignored.intersection(self.stack):
            for name in self.areas:
                if name in self.stack:
                    self.areas[name].append('\n')

    def handle_data(self, text):
        if 'title' in self.stack:
            self.title.append(text)
        if not self.ignored.intersection(self.stack):
            for name in self.areas:
                if name in self.stack:
                    self.areas[name].append(text)

    def content(self, description_only=False):
        if description_only:
            return self.meta.get('og:description','')
        for area in ('article', 'main', 'body'):
            lines = [re.sub(r'\s+', ' ', line).strip() for line in ''.join(self.areas[area]).splitlines()]
            text = '\n\n'.join(line for line in lines if line)
            if len(text) >= 160:
                return text
        return ''


def public_host(parsed):
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = {row[4][0] for row in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
    if not addresses or not all(ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('链接必须指向公开网站')


def download(url):
    """Use the macOS trust store, inspect each redirect, and cap data and time."""
    current = url
    with tempfile.TemporaryDirectory() as directory:
        data, headers = Path(directory)/'data', Path(directory)/'headers'
        for _ in range(5):
            parsed = split_url(current)
            public_host(parsed)
            result = subprocess.run([
                '/usr/bin/curl', '-q', '-sS', '--max-time', '14', '--max-filesize', str(MAX_BYTES),
                '--proto', '=http,https', '-A', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36',
                '-D', str(headers), '-o', str(data), '-w', '%{http_code}\n%{content_type}', '--', current
            ], capture_output=True, text=True, timeout=18)
            if result.returncode != 0:
                raise ValueError('公开请求失败，检查网络、证书或文件大小')
            status, _, mime = result.stdout.partition('\n')
            if status in {'301','302','303','307','308'}:
                locations = re.findall(r'^location:[ \t]*(.*?)\r?$', headers.read_text(errors='replace'), re.I|re.M)
                if not locations:
                    break
                current = urljoin(current, locations[-1])
                continue
            if status != '200':
                raise ValueError(f'公开页面返回 HTTP {status}')
            if data.stat().st_size > MAX_BYTES:
                raise ValueError('来源超过 40 MiB')
            return data.read_bytes(), mime, current
    raise ValueError('公开链接重定向过多或缺少目标')


def file_text(data, suffix):
    if suffix in {'.txt','.md','.csv','.tsv','.json'}:
        return data.decode('utf-8-sig').strip(), 'text'
    if suffix == '.pdf':
        tool = shutil.which('pdftotext')
        if tool:
            result = subprocess.run([tool, '-layout', '-', '-'], input=data, capture_output=True, timeout=25)
            if result.returncode == 0:
                text = result.stdout.decode('utf-8', errors='replace').strip()
                if text:
                    return text[:250000], 'pdf'
        swift = shutil.which('swift')
        if swift:
            with tempfile.TemporaryDirectory() as directory:
                pdf = Path(directory)/'source.pdf'
                pdf.write_bytes(data)
                try:
                    result = subprocess.run([swift, str(Path(__file__).with_name('PDF文字提取.swift')), str(pdf)],
                                            capture_output=True, timeout=30)
                    if result.returncode == 0:
                        return result.stdout.decode('utf-8', errors='replace').strip()[:250000], 'pdf'
                except (OSError, subprocess.SubprocessError):
                    pass
        return '', 'pdf'
    return '', 'file'


def from_url(url, offline=False):
    canonical = identity(url)
    is_xhs = split_url(url).hostname in XHS
    title = ('小红书 ' + canonical.rsplit('/',1)[-1]) if is_xhs else split_url(url).hostname
    properties = {'source_url':canonical, 'modality':'social' if is_xhs else 'web'}
    if offline:
        return title, properties, '', None, '已选择离线保存'
    try:
        data, mime, resolved = download(url)
        destination = split_url(resolved)
        if is_xhs or destination.hostname in XHS:
            is_xhs = True
            properties['modality'] = 'social'
            if '/login' in destination.path.lower():
                return title, properties, '', None, '公开请求跳转到登录页，未取得笔记正文'
            target = identity(resolved)
            if not canonical.startswith('https://www.xiaohongshu.com/explore/') and target.startswith('https://www.xiaohongshu.com/explore/'):
                properties['source_url'] = target
        if data.startswith(b'%PDF-') or mime.split(';')[0] == 'application/pdf':
            text, modality = file_text(data, '.pdf')
            properties['modality'] = modality
            return title, properties, text, ('.pdf', data), ''
        if 'html' not in mime and mime and not mime.startswith('text/'):
            return title, properties, '', None, '公开链接返回未支持的内容类型'
        charset = re.search(r'charset\s*=\s*([a-z0-9_-]+)', mime, re.I)
        raw = data.decode(charset[1] if charset else 'utf-8-sig')
        if mime.startswith('text/plain'):
            return title, properties, raw[:250000], None, ''
        page = Page()
        page.feed(raw)
        text = page.content(description_only=is_xhs)
        page_title = page.meta.get('og:title') or ''.join(page.title).strip()
        if page_title and not (is_xhs and re.search(r'生活兴趣社区|登录|访问异常', page_title)):
            title = page_title
        author = page.meta.get('author') or page.meta.get('article:author')
        if author:
            properties['author'] = author[:160]
        if is_xhs:
            if text:
                properties['extraction'] = '公开页面摘要，未核验为完整正文'
            return title, properties, text[:8000], None, '小红书未公开返回可核验正文' if not text else ''
        if re.search(r'access denied|verify (that )?you are human|just a moment|访问异常', title, re.I):
            text = ''
        return title, properties, text[:250000], None, '页面未提供可核验正文' if not text else ''
    except (OSError, ValueError, LookupError, subprocess.SubprocessError) as exc:
        error = str(exc) if type(exc) is ValueError else type(exc).__name__
        return title, properties, '', None, '公开读取失败。' + error
