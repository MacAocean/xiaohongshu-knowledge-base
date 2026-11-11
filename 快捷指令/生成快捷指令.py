#!/usr/bin/env python3
"""Generate path-independent workflow files and sign them for public import."""
from pathlib import Path
import plistlib
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4


WORKFLOWS = (
    ('个人知识库-T 随手记灵感', '想记下什么？', 'idea'),
    ('个人知识库-T 导入小红书', '粘贴一条小红书公开分享链接或分享文案', 'xhs'),
)


def action(name, **parameters):
    return {'WFWorkflowActionIdentifier': f'is.workflow.actions.{name}',
            'WFWorkflowActionParameters': parameters}


def workflow(prompt, mode):
    ask_id = str(uuid4()).upper()
    answer = {'WFSerializationType': 'WFTextTokenAttachment',
              'Value': {'Type': 'ActionOutput', 'OutputUUID': ask_id, 'OutputName': 'Provided Input'}}
    script = f'exec "$HOME/Library/Application Support/personal-knowledge-base-t/bin/kb-shortcut" {mode}'
    return {
        'WFWorkflowActions': [
            action('ask', WFAskActionPrompt=prompt, WFInputType='Text', UUID=ask_id),
            action('runshellscript', Script=script, Shell='/bin/zsh', Input=answer,
                   InputMode='to stdin', RunAsRoot=False),
        ],
        'WFWorkflowClientVersion': '2600.0.0',
        'WFWorkflowMinimumClientVersion': 900,
        'WFWorkflowMinimumClientVersionString': '900',
        'WFWorkflowIcon': {'WFWorkflowIconStartColor': 946986751, 'WFWorkflowIconGlyphNumber': 59507},
        'WFWorkflowTypes': ['NCWidget', 'WatchKit'],
        'WFWorkflowInputContentItemClasses': [],
        'WFWorkflowImportQuestions': [],
        'WFWorkflowHasShortcutInputVariables': False,
    }


def main():
    folder = Path(__file__).resolve().parent
    with TemporaryDirectory(prefix='kb-shortcut-') as tmp:
        for title, prompt, mode in WORKFLOWS:
            raw = Path(tmp) / f'{title}.shortcut'
            raw.write_bytes(plistlib.dumps(workflow(prompt, mode), fmt=plistlib.FMT_BINARY))
            subprocess.run(['/usr/bin/shortcuts', 'sign', '--mode', 'anyone', '--input', str(raw),
                            '--output', str(folder / raw.name)], check=True)


if __name__ == '__main__':
    main()
