#!/bin/zsh
set -euo pipefail
umask 077

vault_dir=${0:A:h}
support=${KB_INSTALL_SUPPORT:-$HOME/Library/Application Support/personal-knowledge-base-t}
python_bin="$support/venv/bin/python3"
bundle="$vault_dir/.安装资源"
open_vault=1
case ${1:-} in
  '') ;;
  --no-open) open_vault=0 ;;
  *) print -u2 '用法：安装.command [--no-open]'; exit 2 ;;
esac
trap 'print -u2 "安装未完成。请检查上方提示，处理后可重新运行。"' ZERR
[[ -f "$vault_dir/99_系统/程序/知识库.py" && -d "$vault_dir/.obsidian" ]]
if [[ -d "$bundle" && ( $(/usr/bin/uname -m) != arm64 || $(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1) -lt 15 ) ]]; then
  print -u2 '完整安装包需要 Apple Silicon Mac 和 macOS 15 或更新版本。'
  exit 1
fi
mkdir -p "$support/bin" "$vault_dir/.obsidian/plugins/kb-local-tools"

install_uv() {
  local platform digest temporary release=0.13.7
  case $(/usr/bin/uname -m) in
    arm64) platform=aarch64-apple-darwin; digest=81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731 ;;
    x86_64) platform=x86_64-apple-darwin; digest=20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7 ;;
    *) print -u2 '此安装器支持 Apple Silicon 与 Intel Mac。'; return 1 ;;
  esac
  temporary=$(/usr/bin/mktemp -d)
  (
    trap 'rm -rf -- "$temporary"' EXIT
    print '正在下载 Python 安装工具 uv…'
    /usr/bin/curl -fLsS --retry 2 --connect-timeout 15 --max-time 180 \
      "https://github.com/astral-sh/uv/releases/download/$release/uv-$platform.tar.gz" \
      -o "$temporary/uv.tar.gz" || exit 1
    print -r -- "$digest  $temporary/uv.tar.gz" | /usr/bin/shasum -a 256 -c --status || exit 1
    /usr/bin/tar -xzf "$temporary/uv.tar.gz" -C "$temporary" "uv-$platform/uv" || exit 1
    /usr/bin/install -m 700 "$temporary/uv-$platform/uv" "$support/bin/uv"
  )
}

# Terminal programs do not automatically inherit the macOS HTTPS proxy.
if [[ -z ${HTTPS_PROXY:-${https_proxy:-${ALL_PROXY:-${all_proxy:-}}}} ]]; then
  proxy=$(/usr/sbin/scutil --proxy | /usr/bin/awk '
    $1 == "HTTPSEnable" { enabled = $3 }
    $1 == "HTTPSProxy" { host = $3 }
    $1 == "HTTPSPort" { port = $3 }
    END { if (enabled == 1 && host ~ /^[A-Za-z0-9.-]+$/ && port ~ /^[0-9]+$/)
      printf "http://%s:%s", host, port }
  ')
  if [[ -n "$proxy" ]]; then export HTTPS_PROXY="$proxy"; fi
fi

print '正在配置个人知识库-T v1.0…'
if [[ ! -x "$python_bin" ]]; then
  host_python=${KB_INSTALL_PYTHON:-}
  if [[ -z "$host_python" && ${UV_MANAGED_PYTHON:-0} != 1 ]]; then
    for candidate in "${commands[python3.14]:-}" /Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14; do
      if [[ -x "$candidate" ]]; then host_python="$candidate"; break; fi
    done
  fi
  if [[ -n "$host_python" ]]; then
    "$host_python" -m venv "$support/venv"
  else
    [[ -x "$support/bin/uv" ]] || install_uv
    UV_PYTHON_INSTALL_DIR="$support/python" UV_CACHE_DIR="$support/cache" \
      "$support/bin/uv" venv --no-project --python 3.14 --seed "$support/venv"
  fi
fi
requirements="$vault_dir/99_系统/依赖.txt"
if [[ -d "$bundle" ]]; then
  print '正在校验并安装包内的 Whisper、E5 和媒体工具…'
  "$python_bin" "$vault_dir/99_系统/程序/安装资源.py" "$bundle" "$support"
  requirements="$vault_dir/99_系统/依赖-本地模型.txt"
fi
"$python_bin" -m pip install --disable-pip-version-check --only-binary=:all: -r "$requirements"
if [[ -d "$bundle" ]]; then
  "$python_bin" -c 'import numpy, onnxruntime, tokenizers'
fi
"$python_bin" "$vault_dir/99_系统/程序/知识库.py" --root "$vault_dir" check

/usr/bin/install -m 700 "$vault_dir/快捷指令/运行快捷指令.sh" "$support/bin/kb-shortcut"
"$python_bin" - "$vault_dir" "$support" <<'PY'
import json
import os
from pathlib import Path
import stat
import shutil
import sys
from tempfile import NamedTemporaryFile

vault, support = (Path(path).resolve() for path in sys.argv[1:])
config = vault / '.obsidian/plugins/kb-local-tools/data.json'
settings = json.loads(config.read_text()) if config.is_file() else {}
settings['python'] = sys.executable
workspace = vault / '.obsidian/workspace.json'
if not workspace.exists():
    shutil.copyfile(vault / '99_系统/默认工作区.json', workspace)
for path, value in ((config, json.dumps(settings, ensure_ascii=False, indent=2) + '\n'),
                    (support / 'vault-path', str(vault) + '\n')):
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as temporary:
        temporary.write(value)
    os.replace(temporary.name, path)
for relative in ('log.md', '99_系统/_运行数据/日志/系统日志.md',
                 '02_知识笔记/_演化/知识成长记录.md', '99_系统/创作记忆/创作记录.md'):
    log = vault / relative
    if log.is_file():
        os.chflags(log, log.stat().st_flags | stat.UF_APPEND)
PY

if [[ -d "$bundle" ]]; then
  print '配置完成。视频转写和语义检索的本地模型已就绪。'
else
  print '基础配置完成。需要内置模型时，请从 GitHub 发布页下载 Mac 完整安装包。'
fi
print '启用插件和添加快捷指令的步骤见「99_系统/观众上手指南.md」。'
if (( open_vault )); then
  launch_uri=$("$python_bin" - "$vault_dir" <<'PY'
import json
from pathlib import Path
import sys
from urllib.parse import quote

vault = Path(sys.argv[1]).resolve()
registry = Path.home() / 'Library/Application Support/obsidian/obsidian.json'
try:
    known = json.loads(registry.read_text()).get('vaults', {})
except (OSError, ValueError):
    known = {}
for entry in known.values():
    if entry.get('path') and Path(entry['path']).resolve() == vault:
        print('obsidian://open?path=' + quote(str(vault / '05_视图/开始使用.md'), safe=''))
        break
else:
    print('obsidian://choose-vault')
PY
  )
  if [[ "$launch_uri" == obsidian://choose-vault ]]; then
    print -r -- "第一次打开时，选择「打开文件夹作为仓库」并选中：$vault_dir"
    /usr/bin/open -R "$vault_dir" || true
  fi
  if ! /usr/bin/open "$launch_uri"; then
    print '请先安装 Obsidian：https://obsidian.md/download'
  fi
fi
