#!/usr/bin/env python3
"""Authorize an isolated publishing account in the server's persistent browser."""
import argparse
import json
import os
import webbrowser
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from playwright.sync_api import sync_playwright

class ServerBrowser:
    def open(self, url, new=0, autoraise=True):
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp('http://127.0.0.1:9222')
            page = browser.contexts[0].new_page()
            page.goto(url, wait_until='domcontentloaded')
            page.bring_to_front()
        return True
    open_new = open
    open_new_tab = open

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--brand', required=True, choices=['wrongdev','migratoria'])
    args=parser.parse_args()
    base=Path(__file__).resolve().parent
    target=base/'credentials'/f'{args.brand}_youtube.json'
    if target.exists():
        raise SystemExit('Account already authorized; refusing to overwrite it')
    target.parent.mkdir(exist_ok=True)
    target.parent.chmod(0o700)
    webbrowser.register('server-chromium',None,ServerBrowser())
    flow=InstalledAppFlow.from_client_secrets_file(str(base/'client_secret.json'), scopes=[
        'https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly'],
        autogenerate_code_verifier=True)
    creds=flow.run_local_server(host='127.0.0.1', port=8085, open_browser=True,
        browser='server-chromium', prompt='consent select_account', access_type='offline',
        authorization_prompt_message='Google authorization opened in the server browser.',
        success_message='Authorization saved on the server. You may close this tab.', timeout_seconds=86400)
    if not creds.refresh_token:
        raise SystemExit('No offline refresh token received')
    temp=target.with_suffix('.tmp'); temp.write_text(creds.to_json()); temp.chmod(0o600);temp.replace(target)
    owner=base.stat();os.chown(target.parent,owner.st_uid,owner.st_gid);os.chown(target,owner.st_uid,owner.st_gid)
    print(json.dumps({'status':'authorized','brand':args.brand}),flush=True)
