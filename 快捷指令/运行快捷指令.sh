#!/bin/zsh
set -euo pipefail

support=${KB_INSTALL_SUPPORT:-$HOME/Library/Application Support/personal-knowledge-base-t}
python_bin="$support/venv/bin/python3"
if [[ ! -r "$support/vault-path" || ! -x "$python_bin" ]]; then
  print -u2 '请先运行知识库内的「安装.command」。'
  exit 1
fi
IFS= read -r vault_dir < "$support/vault-path"
if [[ ! -f "$vault_dir/99_系统/程序/知识库.py" ]]; then
  print -u2 '找不到知识库。请在移动后的目录重新运行「安装.command」。'
  exit 1
fi
case ${1:-} in
  idea)
    exec "$python_bin" -X utf8 "$vault_dir/99_系统/程序/知识库.py" --root "$vault_dir" collect --text --topics 灵感 ;;
  xhs)
    vault_id=$("$python_bin" - "$vault_dir" <<'PY'
import json
from pathlib import Path
import sys

vault = Path(sys.argv[1]).resolve()
registry = Path.home() / 'Library/Application Support/obsidian/obsidian.json'
try:
    known = json.loads(registry.read_text()).get('vaults', {})
except (OSError, ValueError):
    known = {}
for identifier, item in known.items():
    if item.get('path') and Path(item['path']).resolve() == vault:
        print(identifier)
        break
else:
    sys.exit('请先在 Obsidian 打开这个知识库，再运行「导入小红书」。')
PY
    )
    response=$("$python_bin" -X utf8 "$vault_dir/99_系统/程序/小红书导入.py" --root "$vault_dir" shortcut-url --vault "$vault_id")
    uri=$("$python_bin" -X utf8 -c 'import json, sys; print(json.load(sys.stdin)["uri"])' <<< "$response")
    exec /usr/bin/open "$uri" ;;
  *) print -u2 '用法：kb-shortcut idea|xhs'; exit 2 ;;
esac
