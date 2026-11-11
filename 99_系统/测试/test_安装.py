"""Exercise the public installer in a fresh vault and per-user directory."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(sys.platform == 'darwin', 'Mac 安装器')
class InstallerTest(unittest.TestCase):
    def test_install_creates_portable_configuration_and_preserves_existing_notes(self):
        with tempfile.TemporaryDirectory(prefix='kb-install-') as tmp:
            root = Path(tmp).resolve()
            vault = root / '测试 仓库'
            shutil.copytree(ROOT, vault, ignore=shutil.ignore_patterns('.git', '__pycache__', '.kb-lock'))
            support = root / '测试 配置'
            note = vault / '00_收件箱/我的灵感.md'
            note.write_text('# 我的灵感\n\n保留原文。\n')
            env = {**os.environ, 'KB_INSTALL_SUPPORT': str(support), 'KB_INSTALL_PYTHON': sys.executable}
            for _ in range(2):
                result = subprocess.run(['/bin/zsh', str(vault / '安装.command'), '--no-open'],
                                        cwd=root, env=env, capture_output=True, text=True, timeout=150)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            config = json.loads((vault / '.obsidian/plugins/kb-local-tools/data.json').read_text())
            python = Path(config['python'])
            self.assertTrue(python.is_file())
            self.assertTrue(python.is_relative_to(support))
            result = subprocess.run([str(support / 'bin/kb-shortcut'), 'idea'], input='另一条测试灵感',
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(any('另一条测试灵感' in p.read_text() for p in note.parent.glob('*.md')))
            self.assertEqual(note.read_text(), '# 我的灵感\n\n保留原文。\n')
            self.assertEqual((support / 'vault-path').read_text().strip(), str(vault.resolve()))
