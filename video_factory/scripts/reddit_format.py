"""Keep Telegram CTAs from becoming unrelated Reddit user mentions."""
import re


def platform_body(text):
    for handle in set(re.findall(r'https?://t\.me/([A-Za-z0-9_]+)(?=$|[/?#\s])',text,re.I)):
        text=re.sub(r'(?<!\w)@'+re.escape(handle)+r'(?!\w)','',text,flags=re.I)
    return re.sub(r'[ \t]+\n','\n',text).strip()
