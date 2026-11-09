"""Install the measured theme selector in WrongDev's existing pending-post reader."""
import ast
import os
from pathlib import Path

ROOT = Path('/home/gut/hermes-workspace')


def main():
    path = ROOT/'tg_autoposter/core/storage.py'
    source = path.read_text()
    if 'growth_actions import choose' in source:
        print('WrongDev measured post selection already installed');return
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'get_pending_posts')
    lines = source.splitlines(keepends=True)
    method = ''.join(lines[node.lineno-1:node.end_lineno])
    assert method.count('(limit,),')==1 and method.count('            return result')==1
    method = method.replace('(limit,),', '(max(limit, 8) if limit == 1 else limit,),')
    method = method.replace('            return result', '''            if limit == 1 and result:
                import sys
                sys.path.insert(0, '/home/gut/hermes-workspace/video_factory/scripts')
                from growth_actions import choose
                picked = choose('wrongdev', result, platform='telegram', kind='post', consumer='post_queue_selection')
                return [picked] if picked is not None else result[:limit]
            return result''')
    revised = ''.join(lines[:node.lineno-1])+method+'\n'+''.join(lines[node.end_lineno:])
    ast.parse(revised)
    backup = ROOT/'output/analyst-loop-20261107/before/storage.py'
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():backup.write_text(source);backup.chmod(0o600)
    tmp = path.with_suffix('.growth.tmp')
    tmp.write_text(revised);os.chmod(tmp, path.stat().st_mode & 0o777);tmp.replace(path)
    print('WrongDev measured post selection installed; posting interval unchanged')


if __name__ == '__main__':main()
