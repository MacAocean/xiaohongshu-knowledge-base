#!/bin/zsh
set -euo pipefail
folder=${0:A:h}/快捷指令
shortcuts=("$folder"/*.shortcut(N))
if (( ! ${#shortcuts} )); then
  print -u2 '请从发布页下载完整 ZIP，或按快捷指令说明手动配置。'
  exit 1
fi
/usr/bin/open -R "$shortcuts[1]"
print '在访达中分别打开两个 .shortcut 文件，点击「添加快捷指令」。'
print '在快捷指令的「详细信息」中，分别绑定 ⌃⌥⌘I 与 ⌃⌥⌘X。'
