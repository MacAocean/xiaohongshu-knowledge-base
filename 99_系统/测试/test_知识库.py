import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys
from urllib.parse import unquote

CLI = Path(__file__).resolve().parents[1] / '程序/知识库.py'
sys.path.insert(0, str(CLI.parent))
import 资料处理 as sources

class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ('00_收件箱','01_资料来源','02_知识笔记','03_项目','04_创作成果','05_视图'):
            (self.root / folder).mkdir()
        rules = self.root / '99_系统'
        rules.mkdir()
        for name in ('创作规则.md', '风格库.md'):
            (rules / name).write_text('测试用创作规则。')

    def invoke(self, *args, stdin='', ok=True):
        run = subprocess.run(['python3', str(CLI), '--root', str(self.root), *args],
                             input=stdin, capture_output=True, text=True)
        self.assertEqual(run.returncode == 0, ok, run.stdout + run.stderr)
        if ok:
            return json.loads(run.stdout)

    def creative_output(self, project, kind, source, text):
        result = subprocess.run(['python3', str(CLI.with_name('创作上下文.py')),
                                 '--root', str(self.root), 'build', '聚焦搜索', '--project', project,
                                 '--kind', kind, '--audience', 'Mac 用户', '--goal', '查找文件',
                                 '--ref', source, '--continue-without-history', '测试用户同意'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.invoke('output', project, kind, '--ref', source, '--context-id',
                           json.loads(result.stdout)['id'], stdin=text)

    def capture(self, value='聚焦搜索可以打开已存在的文件。', title='Mac 工作记录'):
        return self.invoke('collect','--text', '--title',title, stdin=value)

    def test_archive_dedup_retrieval_and_content_relation(self):
        capture = self.capture()
        self.assertEqual(self.capture()['path'], capture['path'])
        archived = self.invoke('archive',capture['path'])
        self.assertEqual(self.invoke('archive',capture['path'])['path'], archived['path'])
        source = self.root / archived['path']
        self.assertIn('聚焦搜索可以打开已存在的文件', source.read_text())
        log = (self.root/'log.md').read_bytes()
        other = self.invoke('archive',self.capture('先使用同一个文件做验证，再增加样本。','Mac 验收记录')['path'])
        note = self.invoke('note','--title','找回文件的验收','--topics','Mac,检索',
                           '--evidence',archived['path'],'聚焦搜索可以打开已存在的文件',
                           '--evidence',other['path'],'先使用同一个文件做验证',
                           stdin='先准备一个已存在的文件，再在聚焦搜索中打开同一份文件并核对。')
        self.assertIn(note['path'], unquote((self.root/'05_视图/资料地图.md').read_text()))
        self.assertTrue(any(row['path']==archived['path'] for row in self.invoke('search','Mac 聚焦搜索')))
        self.assertIn(other['path'][:-3],(self.root/note['path']).read_text())
        self.assertTrue((self.root/'log.md').read_bytes().startswith(log))
        self.assertEqual(len(list((self.root/'01_资料来源').glob('*.md'))),2)

    def test_title_collision_empty_encoding_and_outside_path(self):
        one = self.capture()
        two = self.capture('另一条明确不同的内容。')
        self.assertNotEqual(one['path'],two['path'])
        self.invoke('collect','--text',stdin=' ',ok=False)
        broken=self.root/'bad.txt'
        broken.write_bytes(b'\xff\xff')
        self.invoke('collect','--file',str(broken),ok=False)
        self.invoke('archive','../missing.md',ok=False)
        self.invoke('note','--title','不可保存','--evidence','01_资料来源/不存在.md','这是一段实际并不存在的句子',stdin='稿子',ok=False)
        self.invoke('search','Mac','--limit','0',ok=False)

    def test_offline_manual_completion_reuses_original_id(self):
        capture=self.invoke('collect','--url','https://example.org/example','--offline')
        self.assertEqual(capture['status'],'needs_manual')
        self.invoke('archive',capture['path'],ok=False)
        archived=self.invoke('archive',capture['path'],'--paste',stdin='我实际复制保存的一段公开正文。')
        self.assertEqual(self.invoke('collect','--url','https://example.org/example?utm_source=test','--offline')['path'],archived['path'])
        self.assertIn('我实际复制保存的一段公开正文',(self.root/archived['path']).read_text())

    def test_social_login_redirect_keeps_note_identity(self):
        public = 'https://www.xiaohongshu.com/explore/' + 'a'*24
        requested = public + '?xsec_token=fixture-secret'
        redirect = 'https://www.xiaohongshu.com/login?redirectPath=' + requested
        with patch.object(sources, 'download', return_value=(b'<html></html>', 'text/html', redirect)):
            _, meta, body, _, error = sources.from_url(requested)
        self.assertEqual(meta['source_url'],public)
        self.assertEqual(body,'')
        self.assertIn('登录',error)

    def test_topic_tag_does_not_hide_primary_source(self):
        origin=self.invoke('archive',self.capture('公开英文正文。','Apple 聚焦搜索指南')['path'])
        project=self.invoke('project','--title','效率演示','--topics','聚焦搜索','--ref',origin['path'])
        for kind in ('outline','script','storyboard'):
            self.creative_output(project['path'],kind,origin['path'],'在聚焦搜索打开这个文件。')
        self.invoke('output',project['path'],'review','--ref',origin['path'],stdin='验证稿件。')
        best=self.invoke('search','聚焦搜索','--limit','2')
        self.assertEqual(best[0]['path'],origin['path'])

    def test_manual_inbox_archive_keeps_author_text(self):
        manual = self.root/'00_收件箱/日记.md'
        manual.write_text('## 原文或灵感\n\n这一段由我手动输入。\n')
        first = self.invoke('archive','00_收件箱/日记.md')
        self.assertEqual(self.invoke('archive','00_收件箱/日记.md')['path'],first['path'])
        self.assertIn('status: "archived"',manual.read_text())
        self.assertIn('这一段由我手动输入。',manual.read_text())
        self.assertIn('这一段由我手动输入。',(self.root/first['path']).read_text())
        self.assertEqual(self.invoke('check')['warnings'],[])

    def test_manual_note_search_and_bad_encoding_diagnostic(self):
        manual=self.root/'02_知识笔记/记录.md'
        manual.write_text('这条手写记录里的唯一关键词是半导体。')
        broken=self.root/'02_知识笔记/损坏.md'
        broken.write_bytes(b'\xff')
        self.assertEqual(self.invoke('search','半导体')[0]['path'],'02_知识笔记/记录.md')
        warnings=self.invoke('check')['warnings']
        self.assertTrue(any('元数据不全' in warning for warning in warnings))
        self.assertTrue(any('编码或属性无效' in warning for warning in warnings))

    def test_revision_keeps_original_and_checks_references(self):
        source=self.invoke('archive',self.capture()['path'])
        project=self.invoke('project','--title','演示','--ref',source['path'])
        original=self.creative_output(project['path'],'script',source['path'],'第一版实际口播。')
        revised=self.creative_output(project['path'],'script',source['path'],'修改版实际口播。')
        self.assertNotEqual(original['path'],revised['path'])
        self.assertIn('第一版实际口播',(self.root/original['path']).read_text())
        self.invoke('output',project['path'],'script','--ref','01_资料来源/缺失.md',stdin='稿子',ok=False)
        before=self.invoke('context','Mac','--creative')
        self.assertEqual(before['approved_samples'],[])
        self.invoke('approve',revised['path'],'--confirmed','用户明确同意此版本',ok=False)
        self.invoke('output',project['path'],'review','--ref',revised['path'],stdin='审稿已核对版本与事实。')
        final=self.invoke('approve',revised['path'],'--confirmed','用户明确同意此版本')
        self.assertIn(final['path'],[row['path'] for row in self.invoke('context','Mac','--creative')['approved_samples']])
        self.assertEqual(self.invoke('approve',revised['path'],'--confirmed','用户明确同意此版本')['path'],final['path'])
        self.assertIn('修改版实际口播',(self.root/revised['path']).read_text())

if __name__ == '__main__':
    unittest.main()
