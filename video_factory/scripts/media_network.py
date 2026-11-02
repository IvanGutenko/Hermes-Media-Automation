"""Recover the isolated media egress without interrupting active browser jobs."""
import fcntl,json,sys,time
from pathlib import Path
import requests


def probe(port):
    proxy={'http':f'http://127.0.0.1:{port}','https':f'http://127.0.0.1:{port}'}
    try:
        r=requests.get('https://www.google.com/generate_204',proxies=proxy,timeout=7)
        if r.status_code!=204:return False
        r=requests.get('https://www.youtube.com/robots.txt',proxies=proxy,timeout=7)
        return r.status_code==200 and 'User-agent:' in r.text
    except requests.RequestException:return False


def check():
    sys.path.insert(0,'/home/gut/hermes-workspace/tools')
    from vpn_manager import api,ROOT,write_private
    if probe(17893):return {'status':'reachable','checked_at':time.time()}
    report={'status':'unavailable','checked_at':time.time()}
    with (ROOT/'manager.lock').open('a') as vpn_lock:
        try:fcntl.flock(vpn_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {**report,'recovery':'waiting_for_vpn_manager'}
        with Path('/home/gut/hermes-workspace/video_factory/output/.flow-generation.lock').open('r') as browser_lock:
            try:fcntl.flock(browser_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return {**report,'recovery':'waiting_for_active_browser_job'}
            group=api('/proxies/MEDIA');original=group['now']
            previous_probe=api('/proxies/PROBE')['now']
            us=[n for n in group['all'] if n!=original and ('🇺🇸' in n or 'США' in n or 'USA' in n)]
            eu=[n for n in group['all'] if n!=original and ('🇩🇪' in n or '🇳🇱' in n)]
            candidates=us[:2]+eu[:2]
            try:
                for node in candidates[:4]:
                    api('/proxies/PROBE',{'name':node},'PUT')
                    if not probe(17891):continue
                    api('/proxies/MEDIA',{'name':node},'PUT')
                    # Expired sockets must not keep using the failed node.
                    for conn in api('/connections').get('connections',[]):
                        if conn['metadata'].get('inboundName')=='media-browser' or 'MEDIA' in conn.get('chains',[]):
                            api('/connections/'+conn['id'],method='DELETE')
                    if not probe(17893):
                        api('/proxies/MEDIA',{'name':original},'PUT')
                        continue
                    report.update(status='recovered',selection=node,previous=original,switched_at=time.time())
                    break
            finally:api('/proxies/PROBE',{'name':previous_probe},'PUT')
    write_private(ROOT/'media-network-health.json',json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':print(json.dumps(check(),ensure_ascii=False))
