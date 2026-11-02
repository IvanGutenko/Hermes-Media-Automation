"""Install the image hook without replacing unrelated live autoposter changes."""
from pathlib import Path
import ast
ROOT=Path('/home/gut/hermes-workspace/tg_autoposter')
BACKUP=Path.home()/'.hermes/backups/post-images-20261028'


def update(path,old,new):
    text=path.read_text()
    if new in text:return
    if text.count(old)!=1:raise RuntimeError('Unexpected live code at '+str(path))
    backup=BACKUP/path.relative_to(ROOT);backup.parent.mkdir(parents=True,exist_ok=True)
    if not backup.exists():backup.write_text(text);backup.chmod(0o600)
    text=text.replace(old,new,1);ast.parse(text)
    temp=path.with_suffix('.images.tmp');temp.write_text(text);temp.replace(path)


def main():
    path=ROOT/'core/bot_poster.py'
    update(path,'        media_urls: Optional[List[Any]] = None\n',
        '        media_urls: Optional[List[Any]] = None,\n        source_id: Optional[str] = None\n')
    anchor='        try:\n            # 1. Публикация ВИДЕО\n'
    update(path,anchor,'''        if not media_bytes:
            # The stable DB source id shares the same generated asset with VK.
            # Generation failures keep the post queued instead of sending bare text.
            import sys
            from pathlib import Path
            sys.path.insert(0, '/home/gut/hermes-workspace/video_factory/scripts')
            from post_images import ensure_image
            if not source_id:
                import hashlib
                source_id = 'text-' + hashlib.sha256(html_text.encode()).hexdigest()
            image_path = await asyncio.to_thread(ensure_image, 'wrongdev', source_id, title, html_text)
            media_bytes = Path(image_path).read_bytes()
            media_type = 'photo'

'''+anchor)
    path=ROOT/'main.py'
    update(path,'                    media_urls=media_urls\n',
        '                    media_urls=media_urls,\n                    source_id="post-" + str(db_id)\n')
    path=ROOT/'core/processor.py'
    helper='''def audience_instructions():
    import sys
    sys.path.insert(0, '/home/gut/hermes-workspace/video_factory/scripts')
    from content_feedback import editorial_brief
    return editorial_brief('wrongdev')


'''
    update(path,'def strip_source_footer(',helper+'def strip_source_footer(')
    update(path,'{SYSTEM_PROMPT}\\n\\nПОСТ ДЛЯ ОБРАБОТКИ:',
        '{SYSTEM_PROMPT}{audience_instructions()}\\n\\nПОСТ ДЛЯ ОБРАБОТКИ:')
    text=path.read_text()
    old='{"role": "system", "content": SYSTEM_PROMPT}'
    new='{"role": "system", "content": SYSTEM_PROMPT + audience_instructions()}'
    if old in text:
        if text.count(old)!=2:raise RuntimeError('Unexpected fallback prompt count')
        text=text.replace(old,new);ast.parse(text);path.write_text(text)
    path=ROOT/'core/analytics.py';source=path.read_text();tree=ast.parse(source)
    method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='generate_telegram_report')
    replacement='''    async def generate_telegram_report(self) -> str:
        """Show measured multi-platform feedback, without invented strategic claims."""
        import sys
        sys.path.insert(0, '/home/gut/hermes-workspace/video_factory/scripts')
        from content_feedback import format_report
        return format_report()
'''
    lines=source.splitlines(keepends=True)
    revised=''.join(lines[:method.lineno-1])+replacement+''.join(lines[method.end_lineno:])
    if revised!=source:
        backup=BACKUP/'core/analytics.py';backup.parent.mkdir(parents=True,exist_ok=True)
        if not backup.exists():backup.write_text(source);backup.chmod(0o600)
        ast.parse(revised);path.write_text(revised)
    print('Post image hook installed; private live-code backups retained')


if __name__=='__main__':main()
