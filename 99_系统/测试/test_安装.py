"""Exercise the public installer in a fresh vault and per-user directory."""
import hashlib
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

    def test_full_release_validates_and_installs_bundled_files(self):
        with tempfile.TemporaryDirectory(prefix='kb-full-') as tmp:
            root = Path(tmp).resolve()
            vault = root / '测试 仓库'
            shutil.copytree(ROOT, vault, ignore=shutil.ignore_patterns('.git', '__pycache__', '.kb-lock', '.安装资源'))
            support = root / '应用支持'
            bundle = vault / '.安装资源'
            files = {
                'bin/media': b'media fixture',
                'bin/whisper-cli': b'whisper fixture',
                'models/ggml-small-q5_1.bin': b'whisper weights fixture',
                'models/e5-small/model_quantized.onnx': b'e5 weights fixture',
                'models/e5-small/tokenizer.json': b'tokenizer fixture',
                'models/e5-small/revision.txt': b'761b726dd34fb83930e26aab4e9ac3899aa1fa78\n',
            }
            for name, content in files.items():
                path = bundle / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            (bundle / 'manifest.json').write_text(json.dumps({
                'arch': 'arm64', 'minimum_macos': '15.0',
                'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            }))
            if os.uname().machine != 'arm64' or int(os.uname().release.split('.')[0]) < 24:
                self.skipTest('完整安装包需要 Apple Silicon 和 macOS 15')
            env = {**os.environ, 'KB_INSTALL_SUPPORT': str(support), 'KB_INSTALL_PYTHON': sys.executable}
            command = ['/bin/zsh', str(vault / '安装.command'), '--no-open']
            broken = bundle / 'models/e5-small/model_quantized.onnx'
            broken.write_bytes(b'corrupted payload')
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=150)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('SHA-256', result.stdout + result.stderr)
            self.assertFalse((support / 'bin/whisper-cli').exists())
            broken.write_bytes(files['models/e5-small/model_quantized.onnx'])
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=150)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name, content in files.items():
                self.assertEqual((support / name).read_bytes(), content)
            self.assertTrue(os.access(support / 'bin/whisper-cli', os.X_OK))
            python = support / 'venv/bin/python3'
            subprocess.run([str(python), '-c', 'import numpy, onnxruntime, tokenizers'], check=True)
