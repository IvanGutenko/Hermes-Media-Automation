"""Pure helpers for Instagram publication: caption, media geometry and receipt rules."""
import html
import re
from urllib.parse import urlparse

CAPTION_LIMIT=2200
MAX_HASHTAGS=30
# Instagram feed photos accept 4:5 (portrait) up to 1.91:1 (landscape); Reels are 9:16.
MIN_RATIO=0.8
MAX_RATIO=1.91
REEL_MAX_SECONDS=180
REEL_MIN_SECONDS=3
PORTRAIT=(1080,1350)
LANDSCAPE=(1080,566)


LINK_OR_MENTION=re.compile(r'https?://|www\.|(?<![\w.])@[A-Za-z0-9_.]+')


def drop_promo_sentences(text):
    """Remove whole sentences that point to a link or another account. Deleting only the @name would
    leave a dangling "в канале :" in the caption."""
    lines=[]
    for line in text.split('\n'):
        kept=[s for s in re.split(r'(?<=[.!?])\s+',line) if not LINK_OR_MENTION.search(s)]
        lines.append(' '.join(kept).strip())
    return '\n'.join(lines)


LINK_LINE='Подробности и ссылки — в Telegram: '


def caption(title,text,link=None):
    """Plain caption: no links and no @mentions from the source text (an @name would tag an unrelated
    user). The owner's own Telegram channel link, when given, closes the caption and is never cut."""
    body=html.unescape(re.sub(r'<[^>]+>',' ',text or ''))
    body=drop_promo_sentences(body)
    body=re.sub(r'https?://\S+|www\.\S+','',body)
    body=re.sub(r'(?<![\w.])@[A-Za-z0-9_.]+','',body)
    body=re.sub(r'[ \t ]+',' ',body)
    body=re.sub(r' *\n *','\n',body)
    body=re.sub(r'\n{3,}','\n\n',body).strip()
    head=re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',title or ''))).strip()
    if head and head.casefold() not in body.casefold()[:len(head)+40]:
        body=(head+'\n\n'+body).strip()
    tags=re.findall(r'#\w+',body)
    if len(tags)>MAX_HASHTAGS:
        for tag in tags[MAX_HASHTAGS:]:body=body.replace(tag,'',1)
        body=re.sub(r'[ \t]{2,}',' ',body).strip()
    tail=LINK_LINE+link if link else ''
    limit=CAPTION_LIMIT-(len(tail)+2 if tail else 0)
    if len(body)>limit:
        cut=body[:limit-1]
        edge=max(cut.rfind('\n\n'),cut.rfind('. '),cut.rfind('! '),cut.rfind('? '))
        body=(cut[:edge+1] if edge>limit//2 else cut.rsplit(' ',1)[0]).rstrip()+'…'
    if not body:raise ValueError('Instagram caption would be empty')
    return body+'\n\n'+tail if tail else body


def photo_target(width,height):
    """None when the image already fits Instagram's feed range, else the canvas to place it on."""
    ratio=width/height
    if MIN_RATIO<=ratio<=MAX_RATIO:return None
    return PORTRAIT if ratio<MIN_RATIO else LANDSCAPE


def fit_filter(width,height):
    """ffmpeg -filter_complex that centres the image on a blurred copy of itself, or None if it fits."""
    target=photo_target(width,height)
    if not target:return None
    w,h=target
    return (f'[0:v]split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=30:5[bg];'
            f'[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuvj420p')


FRAME_TOLERANCE=0.03


def frame_problem(window,source):
    """Reason the composer preview does not show the whole media, or None.

    `window` is the visible preview (width, height) in pixels; `source` the media's own
    (width, height). Instagram's web composer opens uploads on a square 1:1 crop, which cuts the
    top and bottom off a 9:16 Reel, so a window whose aspect differs from the source's is a crop."""
    if not window or min(window)<=0 or not source or min(source)<=0:
        return 'Instagram preview frame could not be measured'
    shown,actual=window[0]/window[1],source[0]/source[1]
    if abs(shown/actual-1)>FRAME_TOLERANCE:
        return 'Instagram would crop the media: preview %dx%d does not match the %dx%d source'%(*window,*source)
    return None


def reel_problem(width,height,seconds):
    """Reason a video cannot be a Reel, or None."""
    if height<=width or abs(width/height-9/16)>0.02:return 'Reel video must be vertical 9:16'
    if not REEL_MIN_SECONDS<=seconds<=REEL_MAX_SECONDS:return 'Reel duration is outside Instagram limits'
    return None


def may_resubmit(state):
    """A post that may already have been shared is never uploaded again."""
    return state.get('status') in ('new','uploading') and not state.get('share_attempted_at')


def blocks_new_submission(state):
    """Another post for the same account must wait while this one may already be public."""
    return bool(state.get('share_attempted_at')) and state.get('status') in ('publishing','posted_unverified','share_failed_visible')


def profile_codes(hrefs,username):
    """Post/reel shortcodes of the profile's own grid, newest first, without duplicates."""
    found=[]
    for href in hrefs:
        parsed=urlparse(href or '')
        if parsed.hostname not in (None,'instagram.com','www.instagram.com'):continue
        path=parsed.path
        match=re.fullmatch(r'/(?:'+re.escape(username)+r'/)?(?:p|reel)/([A-Za-z0-9_-]+)/?',path)
        if match and match[1] not in found:found.append(match[1])
    return found


def new_codes(before,after):
    return [code for code in after if code not in set(before)]


def permalink(code,kind):
    return 'https://www.instagram.com/'+('reel' if kind=='video' else 'p')+'/'+code+'/'


def caption_visible(visible,expected):
    """The expected caption must be fully present; Instagram folds long captions behind 'more'."""
    flat=lambda value:re.sub(r'\s+',' ',value).strip()
    return flat(expected) in flat(visible)
