import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

import yaml

BIN = Path(__file__).resolve().parents[1] / "程序"


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in ("00_收件箱", "01_资料来源", "02_知识笔记", "03_项目", "04_创作成果", "05_视图", "99_系统"):
            (self.root / name).mkdir()
        for name in ("创作规则.md", "风格库.md", "事实与素材.md"):
            (self.root / "99_系统" / name).write_text("测试规则\n")

    def cli(self, program, *args, content="", ok=True):
        result = subprocess.run([sys.executable, str(BIN / program), "--root", str(self.root), *args],
                                input=content, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stderr

    def capture(self, title, text):
        pending = self.cli("知识库.py", "collect", "--text", "--title", title, content=text)
        return self.cli("知识库.py", "archive", pending["path"])["path"]

    def test_import_identity_preserves_plugin_original_and_media(self):
        ident = "a" * 24
        canonical = f"https://www.xiaohongshu.com/explore/{ident}"
        self.cli("小红书导入.py", "seed", canonical, "--reason", "暂未取得正文")
        raw = self.root / "00_收件箱/_小红书原始导入/原件.md"
        raw.parent.mkdir()
        media = raw.parent / "片段.mp4"
        media.write_bytes(b"fixture media bytes")
        original = f'---\ntitle: 测试标题\nsource: {canonical}?xsec_token=fixture\nauthor: 测试作者\n---\n\n<video src="./片段.mp4" controls></video>\n\n原作者文字与用户补记。\n'
        raw.write_text(original)
        adopted = self.cli("小红书导入.py", "adopt", raw.relative_to(self.root).as_posix())
        path = self.root / adopted["path"]
        self.assertIn("原作者文字与用户补记", path.read_text())
        self.assertNotIn("fixture", path.read_text())
        fields = yaml.safe_load(path.read_text().split("---", 2)[1])
        self.assertEqual((self.root / fields["media_path"]).read_bytes(), media.read_bytes())
        self.assertEqual(fields["source_type"], "external_reference")
        self.assertEqual(raw.read_text(), original)
        repeated = self.cli("小红书导入.py", "adopt", raw.relative_to(self.root).as_posix())
        self.assertEqual(repeated["path"], adopted["path"])
        self.assertEqual(len(list((self.root / "01_资料来源").glob("*.md"))), 1)

    def test_shortcut_routes_only_the_share_link_and_reuses_an_archived_source(self):
        note_id = "b" * 24
        shared = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=test%26token&xsec_source=app_share"
        result = self.cli("小红书导入.py", "shortcut-url", "--vault", "个人知识库-T",
                          content=f"分享文案不进入本地 URL {shared} 复制打开小红书")
        uri = urlsplit(result["uri"])
        self.assertEqual((uri.scheme, uri.netloc), ("obsidian", "kb-import-xhs"))
        self.assertEqual(parse_qs(uri.query), {"vault": ["个人知识库-T"], "link": [shared]})
        self.cli("小红书导入.py", "shortcut-url", "--vault", "个人知识库-T",
                 content="https://xiaohongshu.com.example/explore/" + note_id, ok=False)
        raw = self.root / "00_收件箱/_小红书原始导入/图文.md"
        raw.parent.mkdir()
        (raw.parent / "图.jpg").write_bytes(b"fixture image")
        raw.write_text(f"---\ntitle: 测试图文\nsource: {shared}\n---\n\n实际的测试正文。\n\n![Image](图.jpg)\n")
        adopted = self.cli("小红书导入.py", "shortcut-finish", note_id,
                           content=json.dumps([raw.relative_to(self.root).as_posix()]))
        fields = yaml.safe_load((self.root / adopted["path"]).read_text().split("---", 2)[1])
        self.assertEqual(fields["content_type"], "image_text")
        self.assertTrue((self.root / fields["attachments"][0]).is_file())
        prepared = self.cli("小红书导入.py", "shortcut-prepare", content=shared)
        self.assertEqual(prepared["existing"], adopted["path"])
        self.assertEqual(prepared["note_id"], note_id)

    def test_upstream_video_cover_fallback_waits_for_video(self):
        note_id = "c" * 24
        raw = self.root / "00_收件箱/_小红书原始导入/[V]只有封面.md"
        raw.parent.mkdir()
        (raw.parent / "封面.jpg").write_bytes(b"fixture cover")
        raw.write_text(f"---\ntitle: 只有封面\nsource: https://www.xiaohongshu.com/explore/{note_id}\n---\n\n![Cover Image](封面.jpg)\n\nContent not found\n")
        adopted = self.cli("小红书导入.py", "adopt", raw.relative_to(self.root).as_posix())
        fields = yaml.safe_load((self.root / adopted["path"]).read_text().split("---", 2)[1])
        self.assertEqual((fields["content_type"], fields["status"], fields["transcription_status"]),
                         ("video", "needs_manual", "awaiting_media"))

    def test_machine_transcript_does_not_overwrite_manual_sections(self):
        sys.path.insert(0, str(BIN))
        from 视频转写 import update_machine_block
        original = "## 01 原始视频\n\n![[视频.mp4]]\n\n## 03 整理版口播\n\n我手工修过的句子。\n"
        first = update_machine_block(original, "第一份机器结果")
        again = update_machine_block(first, "第二份机器结果")
        self.assertEqual(again.count("## 02 原始口播逐字稿"), 1)
        self.assertIn("第二份机器结果", again)
        self.assertIn("![[视频.mp4]]", again)
        self.assertIn("我手工修过的句子。", again)
        with self.assertRaises(ValueError):
            update_machine_block(first.replace("第一份机器结果", "我手改了机器区块"), "重跑")

    def test_creative_output_requires_fresh_package_and_explicit_empty_history_choice(self):
        source = self.capture("Mac 检索", "同一份文件可以通过聚焦搜索查到。")
        project = self.cli("知识库.py", "project", "--title", "Mac 演示", "--ref", source)["path"]
        self.cli("知识库.py", "output", project, "script", "--ref", source, content="未经检查的口播。", ok=False)
        args = ("build", "聚焦搜索", "--project", project, "--kind", "script", "--audience", "Mac 新手",
                "--goal", "打开同一份文件", "--ref", source)
        package = self.cli("创作上下文.py", *args)
        self.assertEqual(package["history_status"], "insufficient")
        self.cli("知识库.py", "output", project, "script", "--context-id", package["id"], "--ref", source,
                 content="同一份文件可以通过聚焦搜索查到。", ok=False)
        allowed = self.cli("创作上下文.py", *args, "--continue-without-history", "用户选择继续测试")
        saved = self.cli("知识库.py", "output", project, "script", "--context-id", allowed["id"], "--ref", source,
                         content="按下快捷键，打开刚才准备的文件。")
        self.assertIn(allowed["id"], (self.root / saved["path"]).read_text())
        with (self.root / source).open("a") as handle:
            handle.write("\n测试修改。")
        self.cli("知识库.py", "output", project, "script", "--context-id", allowed["id"], "--ref", source,
                 content="修改后的内容。", ok=False)

    def test_formal_relation_and_evolution_keep_original(self):
        first = self.capture("第一份材料", "搜索命中不等于内容已经核实。")
        second = self.capture("补充材料", "打开文件并核对正文是额外验证。")
        relation = self.cli("知识关系.py", "relate", first, second, "extends",
                            "--evidence", second, "打开文件并核对正文是额外验证", "--reason", "补足验收步骤")
        self.assertEqual(relation["status"], "proposed")
        done = self.cli("知识关系.py", "confirm", relation["id"], "--reason", "确认该证据支持补充")
        self.assertEqual(done["status"], "confirmed")
        self.cli("知识关系.py", "relate", first, second, "contradicts", "--evidence", second,
                 "这句话并没有出现在原文中", "--reason", "测试虚构摘录", ok=False)
        thought = self.cli("知识库.py", "note", "--title", "待核实的想法", "--kind", "hypothesis",
                           content="只确认文件名是否已经足够？")["path"]
        original = (self.root / thought).read_bytes()
        proposal = self.cli("知识关系.py", "propose", thought, "--evidence", second,
                            "打开文件并核对正文是额外验证", content="增加核对正文这一步。")
        revision = self.cli("知识关系.py", "confirm", proposal["id"], "--reason", "确认保留原观点并补充")
        self.assertEqual((self.root / thought).read_bytes(), original)
        self.assertIn("增加核对正文", (self.root / revision["revision"]).read_text())

    def test_history_ranks_relevance_and_user_recognition_and_keeps_original(self):
        source = self.capture("聚焦搜索资料", "聚焦搜索命中后要打开文件核对正文。")
        project = self.cli("知识库.py", "project", "--title", "真实项目测试夹具", "--ref", source)["path"]
        def add(title, kind, audience, content):
            target = self.root / (title + ".md")
            target.write_text(content)
            value = self.cli("创作上下文.py", "history", "--file", str(target), "--kind", kind,
                             "--project", project, "--title", title, "--audience", audience,
                             "--confirmed", "测试用户明确确认")
            return value["path"], target
        first, original = add("聚焦搜索回执", "script", "Mac 新手", "聚焦搜索确认两份回执。")
        off_topic, _ = add("园艺手册", "article", "植物用户", "春季玫瑰需要修剪。")
        rejected, _ = add("聚焦搜索旧写法", "script", "Mac 新手", "聚焦搜索能直接选择回执。")
        self.cli("创作上下文.py", "memory", "quality", "--title", "用户反馈", "--target",
                 rejected, "--quality", "rejected", "--confirmed", "测试用户不认可", content="不够准确。")
        pack = self.cli("创作上下文.py", "build", "Mac 聚焦搜索 回执", "--kind", "script",
                        "--project", project, "--audience", "Mac 新手", "--goal", "选出可靠文件")
        self.assertEqual([first], [row["path"] for row in pack["history"]])
        self.assertNotIn(off_topic, [row["path"] for row in pack["history"]])
        metadata = yaml.safe_load((self.root / first).read_text().split("---", 2)[1])
        self.assertEqual((self.root / metadata["original_path"]).read_bytes(), original.read_bytes())
        error = self.cli("知识库.py", "output", project, "script", "--context-id", pack["id"], "--ref",
                         source, content="这次实际写了正文。", ok=False)
        self.assertIn("实际采用", error)

    def test_edit_after_review_requires_rechecking_exact_version(self):
        source = self.capture("搜索证据", "查找后需要核对真正打开的文件。")
        project = self.cli("知识库.py", "project", "--title", "草稿哈希测试", "--ref", source)["path"]
        pack = self.cli("创作上下文.py", "build", "搜索", "--kind", "script", "--project", project,
                        "--audience", "Mac 用户", "--goal", "找文件", "--ref", source,
                        "--continue-without-history", "测试用户选择")
        saved = self.cli("知识库.py", "output", project, "script", "--ref", source, "--context-id",
                         pack["id"], content="这是已审过的正文。")["path"]
        review = self.cli("知识库.py", "output", project, "review", "--ref", saved,
                          content="打开文件核对真实内容，正文可用。")["path"]
        self.assertEqual(self.cli("稿件核查.py", saved)["reviews"], [review])
        with (self.root / saved).open("a") as handle:
            handle.write("\n本句为新补充。\n")
        report = self.cli("稿件核查.py", saved)
        self.assertEqual(report["reviews"], [])
        self.assertEqual(report["outdated_or_legacy_reviews"], [review])
        self.cli("知识库.py", "approve", saved, "--confirmed", "测试用户确认", ok=False)


if __name__ == "__main__":
    unittest.main()
