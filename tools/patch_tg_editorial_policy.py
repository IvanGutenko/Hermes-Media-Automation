"""Apply the owner's topic/footer policy to the existing WrongDev post queue."""
import ast
import os
import shutil
from pathlib import Path

ROOT=Path('/home/gut/hermes-workspace/tg_autoposter')
BACKUP=Path.home()/'.hermes/backups/media-topic-policy-20261028'


def change(path,transform):
    source=path.read_text();target=BACKUP/path.relative_to(ROOT)
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not target.exists():
        target.write_text(source);target.chmod(0o600)
    revised=transform(source)
    compile(revised,str(path),'exec')
    temp=path.with_suffix('.policy.tmp');temp.write_text(revised);temp.replace(path)


def once(source,old,new):
    if new in source:return source
    if source.count(old)!=1:raise RuntimeError('Unexpected existing code; patch refused')
    return source.replace(old,new,1)


def main_file(source):
    source=once(source,'from core.dedup import dedup','from core.dedup import dedup\nfrom core.editorial_policy import allowed')
    source=once(source,'from core.processor import processor\n','from core.processor import processor, strip_source_footer\n')
    anchor='                raw_text = post.get("original_text", "")\n'
    source=once(source,anchor,anchor+'''                if not allowed(raw_text):
                    storage.mark_policy_skipped(db_id)
                    logger.info("Post %s excluded by owner topic policy", db_id)
                    continue
''')
    source=once(source,'                html_content = post.get("processed_text", "")\n',
        '                html_content = strip_source_footer(post.get("processed_text", ""))\n')
    anchor='                # Проверяем дедупликацию перед самой публикацией\n'
    return once(source,anchor,'''                if not allowed(title + " " + html_content + " " + post.get("original_text", "")):
                    storage.mark_policy_skipped(db_id)
                    logger.info("Queued post %s excluded by owner topic policy", db_id)
                    continue

'''+anchor)


def processor_file(source):
    source=once(source,'from .dedup import dedup','from .dedup import dedup\nfrom .editorial_policy import allowed, EDITORIAL_SCOPE')
    marker='def strip_source_footer('
    if marker not in source:
        anchor='def extract_json('
        source=source.replace(anchor,'''SYSTEM_PROMPT += "\\nНе добавляй подпись Источник или Source внизу поста. Полезные ссылки на сервис или репозиторий оставляй естественно в тексте. Политика тем владельца имеет приоритет над одобрением любой AI-новости.\\n" + EDITORIAL_SCOPE


def strip_source_footer(text):
    return re.sub(r"(?im)^\\s*(?:<[^>]+>)*(?:Источники?|Source)(?:</[^>]+>)*\\s*:\\s*[^\\n]*(?:\\n|$)", "", text).strip()


'''+anchor,1)
    source=once(source,'        html = re.sub(r"<br\\s*/?>", "\\n", html, flags=re.IGNORECASE)',
        '        html = strip_source_footer(html)\n        html = re.sub(r"<br\\s*/?>", "\\n", html, flags=re.IGNORECASE)')
    if 'return {"status": "skip_policy", "reason": "owner_topic_policy"}' not in source:
        tree=ast.parse(source)
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PostProcessor')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='process')
        first=method.body[0]
        line=first.end_lineno if isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str) else first.lineno-1
        lines=source.splitlines(keepends=True)
        lines.insert(line,'        if not allowed(raw_text):\n            return {"status": "skip_policy", "reason": "owner_topic_policy"}\n')
        source=''.join(lines)
    return source


def storage_file(source):
    if 'def mark_policy_skipped(' in source:return source
    return once(source,'    def mark_skipped(','''    def mark_policy_skipped(self, post_id):
        with self.transaction() as conn:
            conn.execute("UPDATE posts SET status='skipped_policy', skip_reason='owner_topic_policy' WHERE id=?", (post_id,))

    def mark_skipped(''')


def poster_file(source):
    source=once(source,'from .config import config','from .config import config\nfrom .editorial_policy import allowed')
    anchor='        if not self.bot:\n            await self.init()\n'
    return once(source,anchor,'''        if not allowed(title + " " + re.sub(r"<[^>]+>", " ", html_text)):
            raise ValueError("Post excluded by owner topic policy")
'''+anchor)


if __name__=='__main__':
    os.umask(0o077)
    shutil.copyfile(Path(__file__).with_name('editorial_policy.py'),ROOT/'core/editorial_policy.py')
    for path,transform in [('main.py',main_file),('core/processor.py',processor_file),
            ('core/storage.py',storage_file),('core/bot_poster.py',poster_file)]:
        change(ROOT/path,transform)
    print('WrongDev topic gates and source-footer filter deployed; private backups retained')
