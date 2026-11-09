"""Keep the signed-in author separate from the publication destination."""
import re


def normalize(value):
    match=re.fullmatch(r'(r|u)[_/]([A-Za-z0-9_][A-Za-z0-9_-]{1,20})',str(value or ''))
    if not match:raise ValueError('Reddit target must be an explicit r_community or u_profile')
    return match[1]+'_'+match[2]


def destination(data):
    # Historical jobs had only username. They retain their original profile;
    # changing configuration must never move an already submitted item.
    return normalize(data.get('target') or 'u_'+data['username'])


def configured(cfg):
    target=normalize(cfg['target'])
    username=cfg.get('username') or (target[2:] if target.startswith('u_') else None)
    if not username or not re.fullmatch(r'[A-Za-z0-9_-]{3,20}',username):
        raise ValueError('Reddit community publishing needs a separate username')
    return target,username


def same(left,right):return normalize(left).casefold()==normalize(right).casefold()


def home(data,old=False):
    target=destination(data);prefix='r/' if target.startswith('r_') else 'user/'
    return ('https://old.reddit.com/' if old else 'https://www.reddit.com/')+prefix+target[2:]


def permalink(data,identity,old=False):return home(data,old=old)+'/comments/'+identity+'/'


def cursor_name(target):
    target=normalize(target)
    return 'reddit_'+(target.casefold() if target.startswith('r_') else target[2:])+'_cursor.json'


def matches_community(target,observed):
    expected=normalize(target)
    if not expected.startswith('r_'):return True  # historical profile verification also checks its author
    value=str(observed or '')
    if not value.startswith(('r_','r/')):value='r_'+value
    try:return same(expected,value)
    except ValueError:return False
