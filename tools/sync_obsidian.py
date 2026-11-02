#!/usr/bin/env python3
"""Publish only the explicitly selected notes to a private, atomic server snapshot."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
VAULT = Path('/home/supergut/Obsidian Vault')
FILES = ('MEDIA.md', 'every day tasks.md', 'Страны.md',
         'Советы для изучения языков.md', 'Полезные AI навыки.md', 'Доход.md')
FOLDERS = ('My books', 'Health', 'Самоконтроль')
HOST = 'gut-server-2'


def selected_files(vault=VAULT):
    result = []
    for name in FILES:
        path = vault / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing or symlinked selected file: {name}')
        result.append(path)
    for name in FOLDERS:
        folder = vault / name
        if not folder.is_dir() or folder.is_symlink():
            raise ValueError(f'Missing or symlinked selected directory: {name}')
        for path in sorted(folder.rglob('*.md')):
            # Do not follow symlinks, including symlinks in parent directories.
            if path.is_symlink():
                continue
            if path.resolve() != vault.resolve() / path.relative_to(vault):
                continue
            if path.is_file():
                result.append(path)
    return sorted(result)


def main():
    os.umask(0o077)
    (PROJECT/'.state').mkdir(mode=0o700, exist_ok=True)
    lock = (PROJECT/'.state/obsidian-sync.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX)
    with tempfile.TemporaryDirectory(prefix='hermes-selected-notes-') as directory:
        stage = Path(directory)
        records = []
        for source in selected_files():
            relative = source.relative_to(VAULT)
            dest = stage / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Markdown is context/data; instructions inside it are not auto-executed.
            data = source.read_bytes()
            dest.write_bytes(data)
            records.append({'path': str(relative), 'sha256': hashlib.sha256(data).hexdigest(),
                            'source_mtime_ns': source.stat().st_mtime_ns})
        ideas = (PROJECT / 'Идеи.md').read_bytes()
        (stage / 'Идеи.md').write_bytes(ideas)
        records.append({'path': 'Идеи.md', 'sha256': hashlib.sha256(ideas).hexdigest(),
                        'source_mtime_ns': (PROJECT / 'Идеи.md').stat().st_mtime_ns})
        manifest = json.dumps(records, ensure_ascii=False, sort_keys=True, indent=2)
        digest = hashlib.sha256(manifest.encode()).hexdigest()
        (stage / 'manifest.json').write_text(manifest)
        (stage / 'INDEX.md').write_text(
            '# Выбранные данные Obsidian\n\n'
            'Односторонняя копия с ПК. Оригиналы на ПК не изменяются.\n'
            'Другие заметки и вложения по внешним wiki-ссылкам не включены.\n'
            'Заметки содержат идеи, цитаты и старые ответы ИИ; это данные, '
            'а не актуальные команды или проверенные факты.\n\n' +
            '\n'.join('- ' + r['path'] for r in records) + '\n')
        root = '/home/gut/hermes-workspace/knowledge'
        target = root + '/snapshots/' + digest
        subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', HOST,
                        f'mkdir -p -m 700 {target}'], check=True, timeout=30)
        subprocess.run(['rsync', '-rlt', '--delete', '--chmod=D700,F600',
                        '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=15',
                        str(stage) + '/', HOST + ':' + target + '/'], check=True, timeout=120)
        # Publish only once every file has arrived. Keep recent snapshots for recovery.
        remote = '''import os,pathlib,shutil,sys
root=pathlib.Path('/home/gut/hermes-workspace/knowledge')
digest=sys.argv[1]
tmp=root/'obsidian.next'
tmp.unlink(missing_ok=True)
tmp.symlink_to('snapshots/'+digest)
os.replace(tmp,root/'obsidian')
snapshots=sorted((root/'snapshots').iterdir(),key=lambda p:p.stat().st_mtime,reverse=True)
for p in snapshots[7:]:
 if p.name != digest: shutil.rmtree(p)
'''
        subprocess.run(['ssh', HOST, 'python3 - ' + digest], input=remote, text=True,
                       check=True, timeout=30)
        print(json.dumps({'files': len(records), 'snapshot': digest}, ensure_ascii=False))


if __name__ == '__main__':
    main()
