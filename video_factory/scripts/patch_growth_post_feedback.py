"""Attribute new WrongDev drafts to their actual text-post editorial direction."""
import ast
import shutil
from pathlib import Path

ROOT=Path('/home/gut/hermes-workspace')


def main():
    folder=ROOT/'tg_autoposter'
    before=ROOT/'output/adaptive-factory-20261107/before'
    before.mkdir(parents=True,exist_ok=True)
    path=folder/'core/processor.py';source=path.read_text()
    if 'growth_post_receipt' not in source:
        tree=ast.parse(source)
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='audience_instructions')
        lines=source.splitlines(keepends=True)
        helper='''from contextvars import ContextVar
growth_post_receipt = ContextVar('growth_post_receipt', default={})


def audience_instructions():
    import sys
    sys.path.insert(0, '/home/gut/hermes-workspace/video_factory/scripts')
    from content_feedback import _feedback_brief
    from growth_actions import context
    editorial = context('wrongdev', platform='telegram', kind='post')
    growth_post_receipt.set(editorial['receipt'])
    return _feedback_brief('wrongdev') + editorial['prompt']
'''
        revised=''.join(lines[:node.lineno-1])+helper+'\n'+''.join(lines[node.end_lineno:])
        anchor='            "provider": provider_name,\n'
        assert revised.count(anchor)==1
        revised=revised.replace(anchor,anchor+'            "growth_actions": growth_post_receipt.get(),\n')
        ast.parse(revised)
        shutil.copy2(path,before/'wrongdev-processor.py')
        path.write_text(revised)
    path=folder/'main.py';source=path.read_text()
    if 'growth_post_draft' not in source:
        anchor='\n            # 3. Публикация очереди pending'
        assert source.count(anchor)==1
        addition='''
                # growth_post_draft: only a newly written, persisted draft is
                # attributed; historical queued posts are never retro-tagged.
                try:
                    from growth_actions import record
                    record('wrongdev', 'post_draft', result.get('growth_actions', {}), source_id='post-' + str(db_id))
                except Exception as exc:
                    logger.warning('Editorial assignment record unavailable: %s', type(exc).__name__)
'''
        revised=source.replace(anchor,addition+anchor)
        ast.parse(revised)
        shutil.copy2(path,before/'wrongdev-main.py')
        path.write_text(revised)
    print('WrongDev new text drafts use and record their own editorial scope; cadence unchanged')


if __name__=='__main__':main()
