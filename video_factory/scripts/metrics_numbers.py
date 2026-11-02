"""Counts from English/Russian platform UIs, with distinct decimal/thousands separators."""
import re


def parse_count(value):
    text=value.strip().replace('\xa0','').replace(' ','').lower()
    if re.fullmatch(r'\d{1,3}(?:,\d{3})+',text):return int(text.replace(',',''))
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+',text):return int(text.replace('.',''))
    text=text.replace(',','.')
    match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*(k|m|тыс\.?|млн\.?)?',text)
    if not match:return None
    return int(float(match[1])*({'k':1000,'тыс':1000,'тыс.':1000,'m':1000000,'млн':1000000,'млн.':1000000}.get(match[2],1)))


def dzen_dashboard(body,items):
    """Read explicitly labelled counters; impressions never become views."""
    result={}
    for item in items:
        if item.get('platform')!='dzen' or not item.get('title') or item['title'] not in body:continue
        # The dashboard repeats a title in its graph legend, then its card.
        lines=[]
        for line in body.rsplit(item['title'],1)[1].splitlines():
            line=line.strip()
            if not line:continue
            if not (re.fullmatch(r'[\d\s.,]+(?:%|[KMкмчдмснг])?',line,re.I) or
                    re.fullmatch(r'показ(?:ов|а)?|дочитали|лайк(?:ов|а)?|комментари(?:ев|я|й)|CTR в ленте',line)):
                break
            lines.append(line)
        tail='\n'.join(lines)
        counters={}
        for name,label in [('impressions',r'показ(?:ов|а)?'),('read_completions',r'дочитали'),
                           ('likes',r'лайк(?:ов|а)?'),('comments',r'комментари(?:ев|я|й)')]:
            match=re.search(r'(?m)^([\d\s.,KMкмтыс]+)\n'+label+r'\s*$',tail,re.I)
            if match:counters[name]=parse_count(match[1])
        ctr=re.search(r'([\d.,]+)%\s*\nCTR в ленте',tail)
        if ctr:counters['feed_ctr_percent']=float(ctr[1].replace(',','.'))
        if counters:result[item['url']]=counters
    return result


def match_tiles(platform,items,rows,now):
    """Views for our own Instagram/TikTok posts from profile-grid tiles: rows are {'href', 'text'} as shown to the owner.

    A tile matches a post when the post's id (TikTok video id, Instagram shortcode) is in the tile link; the view count is the
    first count-like token of the tile text. Unmatched tiles and tiles without a count are skipped, never reported as zero."""
    pattern={'tiktok':r'/video/(\d+)','instagram':r'/(?:reels?|p)/([A-Za-z0-9_-]+)'}[platform]
    by_id={}
    for row in rows:
        found=re.search(pattern,row.get('href') or '')
        token=re.search(r'\d[\d.,\s\xa0]*(?:[kKmM]|тыс\.?|млн\.?)?',row.get('text') or '')
        views=parse_count(token[0]) if token else None
        if found and views is not None:by_id[found[1]]=views
    out={}
    for item in items:
        if item['platform']!=platform:continue
        found=re.search(pattern,item['url'])
        if found and found[1] in by_id:
            out[item['url']]={'views':by_id[found[1]],'observed_at':now,'platform':platform,'window':'since publication',
                'source':('TikTok' if platform=='tiktok' else 'Instagram')+' own profile grid tile'}
    return out
