"""Separate long polling from channel sends without replacing live autoposter code."""
from pathlib import Path
import ast


def apply(root=Path('/home/gut/hermes-workspace/tg_autoposter')):
    path=root/'main.py';text=path.read_text()
    old='        request = HTTPXRequest(proxy=config.proxy, connect_timeout=15.0, read_timeout=30.0)'
    new='''        # Long polling must not occupy the pool used by channel publication,
        # analytics, and admin responses. Each client owns its own lifecycle.
        request = HTTPXRequest(proxy=config.proxy, connect_timeout=15.0,
            read_timeout=30.0, connection_pool_size=8, pool_timeout=15.0)
        polling_request = HTTPXRequest(proxy=config.proxy, connect_timeout=15.0,
            read_timeout=40.0, connection_pool_size=1, pool_timeout=15.0)'''
    if new in text:return False
    if text.count(old)!=1 or text.count('.get_updates_request(request)')!=1:
        raise ValueError('Unexpected autoposter transport; live file preserved')
    revised=text.replace(old,new).replace('.get_updates_request(request)','.get_updates_request(polling_request)')
    ast.parse(revised)
    backup=root/'main.before-transport-20261108.py'
    if not backup.exists():backup.write_text(text);backup.chmod(0o600)
    temp=path.with_suffix('.transport.tmp');temp.write_text(revised);temp.replace(path)
    return True


if __name__=='__main__':print('transport_changed',apply())
