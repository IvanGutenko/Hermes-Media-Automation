"""Pure TikTok helpers. Caption, video and receipt rules are shared with Instagram (instagram_format)."""
import re
from urllib.parse import urlparse

HOSTS=(None,'tiktok.com','www.tiktok.com')


def video_ids(hrefs,handle):
    """Video ids of the profile's own grid, in page order, without duplicates and without other accounts."""
    found=[]
    for href in hrefs:
        parsed=urlparse(href or '')
        if parsed.hostname not in HOSTS:continue
        match=re.fullmatch(r'/@'+re.escape(handle)+r'/video/(\d{10,25})/?',parsed.path,re.I)
        if match and match[1] not in found:found.append(match[1])
    return found


def new_ids(before,after):
    return [value for value in after if value not in set(before)]


def video_url(handle,video_id):
    return 'https://www.tiktok.com/@'+handle+'/video/'+video_id
