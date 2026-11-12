"""Validate and install the local components shipped in the complete Mac release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile


ARCH = 'arm64'
MINIMUM_MACOS = '15.0'
MODEL_HASHES = {
    'models/ggml-small-q5_1.bin': 'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb',
    'models/e5-small/model_quantized.onnx': 'f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193',
    'models/e5-small/tokenizer.json': '0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39',
    'models/e5-small/revision.txt': '18703917b6bc8785f65c93b8cba3e15c28753656d3f46f9953aecaa498760c79',
}
EXECUTABLES = ('bin/media', 'bin/whisper-cli')
ASSETS = (*EXECUTABLES, *MODEL_HASHES)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def install(bundle, support):
    if (platform.system() != 'Darwin' or platform.machine() != ARCH
            or int(platform.mac_ver()[0].split('.')[0]) < int(MINIMUM_MACOS.split('.')[0])):
        raise ValueError('完整安装包需要 Apple Silicon Mac 和 macOS 15 或更新版本')
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('arch') != ARCH or manifest.get('minimum_macos') != MINIMUM_MACOS:
        raise ValueError('完整安装包的平台清单不匹配')
    hashes = manifest.get('sha256', {})
    if set(hashes) != set(ASSETS):
        raise ValueError('完整安装包的文件清单不完整')
    for name in ASSETS:
        source = bundle / name
        if not source.is_file() or source.is_symlink() or sha256(source) != hashes[name]:
            raise ValueError(f'SHA-256 校验失败：{name}。请重新下载完整安装包。')
    for name in ASSETS:
        destination = support / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        mode = 0o700 if name in EXECUTABLES else 0o600
        if destination.is_file() and not destination.is_symlink() and sha256(destination) == hashes[name]:
            destination.chmod(mode)
            continue
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
            temporary = Path(temp.name)
        try:
            shutil.copyfile(bundle / name, temporary)
            if sha256(temporary) != hashes[name]:
                raise ValueError(f'SHA-256 校验失败：{name}。请重新下载完整安装包。')
            temporary.chmod(mode)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('support', type=Path)
    args = parser.parse_args()
    try:
        install(args.bundle, args.support)
    except (OSError, ValueError) as error:
        print(f'安装本地模型失败：{error}', file=sys.stderr)
        raise SystemExit(1) from error
