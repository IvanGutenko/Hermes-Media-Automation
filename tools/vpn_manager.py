#!/usr/bin/env python3
"""EOS-only subscription refresh and service-aware Gemini failover for Mihomo."""
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import yaml

ROOT = Path.home() / '.config/hermes-vpn'
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({
    'http': 'http://127.0.0.1:17890', 'https': 'http://127.0.0.1:17890'}))
PROBE_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({
    'http': 'http://127.0.0.1:17891', 'https': 'http://127.0.0.1:17891'}))


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text)
    temp.chmod(0o600)
    os.replace(temp, path)


def api(path, data=None, method='GET'):
    secret = (ROOT / 'controller.secret').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:19090' + path,
        data=None if data is None else json.dumps(data).encode(), method=method,
        headers={'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'})
    with DIRECT.open(req, timeout=15) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def parse_subscription(data, prefix):
    parsed = yaml.safe_load(data)
    if isinstance(parsed, dict) and isinstance(parsed.get('proxies'), list):
        nodes = parsed['proxies']
    else:
        text = data.decode().strip()
        if '://' not in text:
            text = base64.b64decode(text + '=' * (-len(text) % 4), validate=True).decode()
        nodes = []
        for line in text.splitlines():
            p = urllib.parse.urlsplit(line.strip())
            if p.scheme != 'vless':
                raise ValueError('Unsupported subscription protocol')
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(p.query).items()}
            node = {'name': urllib.parse.unquote(p.fragment) or p.hostname,
                    'type': 'vless', 'server': p.hostname, 'port': p.port,
                    'uuid': urllib.parse.unquote(p.username or ''), 'udp': True}
            if q.get('flow'): node['flow'] = q['flow']
            transport = q.get('type', 'tcp')
            node['network'] = transport
            if transport == 'grpc':
                node['grpc-opts'] = {'grpc-service-name': q.get('serviceName', '')}
            elif transport == 'ws':
                node['ws-opts'] = {'path': q.get('path', '/'),
                                   'headers': {'Host': q.get('host', p.hostname)}}
            elif transport != 'tcp':
                raise ValueError('Unsupported VLESS transport')
            if q.get('security') in ('tls', 'reality'):
                node.update({'tls': True, 'servername': q.get('sni', p.hostname),
                             'client-fingerprint': q.get('fp', 'chrome')})
            if q.get('security') == 'reality':
                node['reality-opts'] = {'public-key': q['pbk'], 'short-id': q.get('sid', '')}
            nodes.append(node)
    clean = []
    for i, original in enumerate(nodes):
        node = dict(original)
        if node.get('type') != 'vless' or not node.get('server') or not node.get('uuid'):
            continue
        node['name'] = prefix + ': ' + str(node.get('name', i))
        clean.append(node)
    if not clean:
        raise ValueError('No usable nodes')
    return clean


def configuration(providers, secret):
    uses = list(providers.keys())
    health = {'url': 'https://www.gstatic.com/generate_204', 'interval': 120,
              'lazy': False, 'timeout': 6000, 'expected-status': 204}
    return {
        'mixed-port': 17890, 'bind-address': '127.0.0.1', 'allow-lan': False,
        'mode': 'rule', 'log-level': 'warning', 'ipv6': False,
        'external-controller': '127.0.0.1:19090', 'secret': secret,
        'profile': {'store-selected': True},
        'dns': {
            'enable': True, 'ipv6': False, 'enhanced-mode': 'redir-host',
            'default-nameserver': ['8.8.8.8', '1.1.1.1'],
            'nameserver': ['https://1.1.1.1/dns-query', 'https://8.8.8.8/dns-query'],
            'proxy-server-nameserver': ['8.8.8.8', '1.1.1.1'],
            'nameserver-policy': {
                'geosite:google': ['https://dns.google/dns-query#ANTIGRAVITY', 'https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:openai': ['https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:anthropic': ['https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:tiktok': ['https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:youtube': ['https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:twitter': ['https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:instagram': ['https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:reddit': ['https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:telegram': ['https://1.1.1.1/dns-query#ANTIGRAVITY'],
                'geosite:category-ru': ['77.88.8.8', '1.1.1.1'],
                'geosite:yandex': ['77.88.8.8', '1.1.1.1'],
                'geosite:vk': ['77.88.8.8', '1.1.1.1'],
                'geosite:dzen': ['77.88.8.8', '1.1.1.1'],
                'geosite:bybit': ['77.88.8.8', '1.1.1.1'],
                'geosite:sberbank': ['77.88.8.8', '1.1.1.1'],
                'geosite:avito': ['77.88.8.8', '1.1.1.1']
            }
        },
        'proxy-providers': providers,
        'listeners': [{'name': 'health-probe', 'type': 'http', 'listen': '127.0.0.1',
                       'port': 17891, 'proxy': 'PROBE'},
                      {'name': 'antigravity', 'type': 'http', 'listen': '127.0.0.1',
                       'port': 17892, 'proxy': 'ANTIGRAVITY'},
                      {'name': 'media-browser', 'type': 'http', 'listen': '127.0.0.1',
                       'port': 17893, 'proxy': 'MEDIA'}],
        'proxy-groups': [
            {'name': 'PROBE', 'type': 'select', 'use': uses},
            # Verified working nodes for Google Antigravity Code Assist:
            # Germany 6, Germany 5, Finland 1, and Taynik AI.
            {'name': 'ANTIGRAVITY', 'type': 'select', 'proxies': ['REJECT'],
              'use': uses},
            # Browser eligibility is checked independently of Code Assist.
            # A media route change must not move the agents' live API sessions.
            {'name': 'MEDIA', 'type': 'select', 'proxies': ['ANTIGRAVITY'], 'use': uses},
            {'name': 'AI-AUTO', 'type': 'fallback', 'use': uses, **health},
            {'name': 'AI', 'type': 'select', 'proxies': ['ANTIGRAVITY', 'AI-AUTO'], 'use': uses},
            {'name': 'GEMINI-AUTO', 'type': 'fallback', 'use': uses,
             **{**health, 'url': 'https://gemini.google.com/', 'expected-status': '200/302'}},
            {'name': 'GEMINI', 'type': 'select', 'proxies': ['ANTIGRAVITY', 'GEMINI-AUTO'], 'use': uses}],
        'rules': [
            # Local & Private Networks (DIRECT)
            'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve',
            'IP-CIDR,100.64.0.0/10,DIRECT,no-resolve',
            'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
            'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
            'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve',

            # Whitelisted Russian services & platforms where VPN harms / gets banned (DIRECT)
            'DOMAIN-SUFFIX,max.ru,DIRECT',
            'DOMAIN-SUFFIX,vk.com,DIRECT',
            'DOMAIN-SUFFIX,vk.ru,DIRECT',
            'DOMAIN-SUFFIX,vkvideo.ru,DIRECT',
            'DOMAIN-SUFFIX,userapi.com,DIRECT',
            'DOMAIN-SUFFIX,dzen.ru,DIRECT',
            'DOMAIN-SUFFIX,dzeninfra.ru,DIRECT',
            'DOMAIN-SUFFIX,yandex.ru,DIRECT',
            'DOMAIN-SUFFIX,ya.ru,DIRECT',
            'DOMAIN-SUFFIX,yandex.net,DIRECT',
            'DOMAIN-SUFFIX,yastatic.net,DIRECT',
            'DOMAIN-SUFFIX,2gis.ru,DIRECT',
            'DOMAIN-SUFFIX,2gis.com,DIRECT',
            'DOMAIN-SUFFIX,kwork.ru,DIRECT',
            'DOMAIN-SUFFIX,fl.ru,DIRECT',
            'DOMAIN-SUFFIX,freelance.ru,DIRECT',
            'DOMAIN-SUFFIX,habr.com,DIRECT',
            'DOMAIN-SUFFIX,workspace.ru,DIRECT',
            'DOMAIN-SUFFIX,hh.ru,DIRECT',
            'DOMAIN-SUFFIX,headhunter.ru,DIRECT',
            'DOMAIN-SUFFIX,tbank.ru,DIRECT',
            'DOMAIN-SUFFIX,tinkoff.ru,DIRECT',
            'DOMAIN-SUFFIX,sberbank.ru,DIRECT',
            'DOMAIN-SUFFIX,sber.ru,DIRECT',
            'DOMAIN-SUFFIX,avito.ru,DIRECT',
            'DOMAIN-SUFFIX,ozon.ru,DIRECT',
            'DOMAIN-SUFFIX,wildberries.ru,DIRECT',
            'DOMAIN-SUFFIX,bybit.com,DIRECT',
            'DOMAIN-SUFFIX,gosuslugi.ru,DIRECT',
            'DOMAIN-SUFFIX,nalog.gov.ru,DIRECT',
            'DOMAIN-SUFFIX,cbr.ru,DIRECT',
            'DOMAIN-SUFFIX,1tv.ru,DIRECT',
            'DOMAIN-SUFFIX,rt.com,DIRECT',
            'DOMAIN-SUFFIX,ria.ru,DIRECT',
            'DOMAIN-SUFFIX,tass.ru,DIRECT',
            'DOMAIN-SUFFIX,rbc.ru,DIRECT',
            'DOMAIN-SUFFIX,ru,DIRECT',
            'DOMAIN-SUFFIX,su,DIRECT',
            'DOMAIN-SUFFIX,xn--p1ai,DIRECT',
            'GEOSITE,category-ru,DIRECT',
            'GEOSITE,yandex,DIRECT',
            'GEOSITE,vk,DIRECT',
            'GEOSITE,dzen,DIRECT',
            'GEOSITE,sberbank,DIRECT',
            'GEOSITE,avito,DIRECT',
            'GEOSITE,bybit,DIRECT',

            # Restricted / AI / Blocked services (ANTIGRAVITY)
            'DOMAIN-SUFFIX,cloudcode-pa.googleapis.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,daily-cloudcode-pa.googleapis.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,alkalimakersuite-pa.googleapis.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,aicode.googleapis.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,antigravity-unleash.goog,ANTIGRAVITY',
            'DOMAIN-SUFFIX,googleapis.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,gemini.google.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,aistudio.google.com,ANTIGRAVITY',
            'GEOSITE,google,ANTIGRAVITY',
            'DOMAIN-SUFFIX,openai.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,chatgpt.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,oaistatic.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,oaiusercontent.com,ANTIGRAVITY',
            'GEOSITE,openai,ANTIGRAVITY',
            'DOMAIN-SUFFIX,anthropic.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,claude.ai,ANTIGRAVITY',
            'GEOSITE,anthropic,ANTIGRAVITY',
            'DOMAIN-SUFFIX,groq.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,openrouter.ai,ANTIGRAVITY',
            'DOMAIN-SUFFIX,deepseek.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,opencode.ai,ANTIGRAVITY',
            'DOMAIN-SUFFIX,opencode.net,ANTIGRAVITY',
            'DOMAIN-SUFFIX,midjourney.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,elevenlabs.io,ANTIGRAVITY',
            'DOMAIN-SUFFIX,perplexity.ai,ANTIGRAVITY',
            'GEOSITE,huggingface,ANTIGRAVITY',
            'DOMAIN-SUFFIX,tiktok.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,byteoversea.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,ibytedtos.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,tiktokv.com,ANTIGRAVITY',
            'GEOSITE,tiktok,ANTIGRAVITY',
            'DOMAIN-SUFFIX,youtube.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,googlevideo.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,ytimg.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,youtu.be,ANTIGRAVITY',
            'GEOSITE,youtube,ANTIGRAVITY',
            'DOMAIN-SUFFIX,instagram.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,cdninstagram.com,ANTIGRAVITY',
            'GEOSITE,instagram,ANTIGRAVITY',
            'DOMAIN-SUFFIX,x.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,twitter.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,t.co,ANTIGRAVITY',
            'DOMAIN-SUFFIX,twimg.com,ANTIGRAVITY',
            'GEOSITE,twitter,ANTIGRAVITY',
            'DOMAIN-SUFFIX,reddit.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,redd.it,ANTIGRAVITY',
            'GEOSITE,reddit,ANTIGRAVITY',
            'DOMAIN-SUFFIX,telegram.org,ANTIGRAVITY',
            'DOMAIN-SUFFIX,t.me,ANTIGRAVITY',
            'GEOSITE,telegram,ANTIGRAVITY',
            'DOMAIN-SUFFIX,g2.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,capterra.com,ANTIGRAVITY',
            'DOMAIN-SUFFIX,notion.so,ANTIGRAVITY',
            'DOMAIN-SUFFIX,notion.site,ANTIGRAVITY',
            'GEOSITE,notion,ANTIGRAVITY',
            'DOMAIN-SUFFIX,gumroad.com,ANTIGRAVITY',
            'GEOSITE,discord,ANTIGRAVITY',

            # Default: Direct (all unclassified traffic stays native Russian IP)
            'MATCH,DIRECT'
        ]}


def refresh():
    urls = json.loads((ROOT / 'subscriptions.json').read_text())
    if not isinstance(urls, dict) or not urls:
        raise ValueError('Expected non-empty subscriptions mapping')
    providers, changed = {}, []
    for i, (label, url) in enumerate(urls.items()):
        path = ROOT / f'provider-{i}.yaml'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'clash.meta'})
            with DIRECT.open(req, timeout=30) as response:
                data = response.read(4 * 1024 * 1024)
            nodes = parse_subscription(data, label)
            candidate = path.with_suffix('.candidate.yaml')
            write_private(candidate, yaml.safe_dump({'proxies': nodes}, allow_unicode=True))
            changed.append((candidate, path))
            print(json.dumps({'subscription': label, 'nodes': len(nodes)}))
        except Exception as error:
            # Exceptions can contain the secret subscription URL. Log only the class.
            print(json.dumps({'subscription': label, 'refresh_error': type(error).__name__,
                              'using_previous': path.exists()}))
            if not path.exists():
                continue
        providers[label] = {'type': 'file', 'path': str(path),
                            'health-check': {'enable': True,
                            'url': 'https://www.gstatic.com/generate_204',
                            'interval': 300, 'timeout': 6000, 'lazy': False}}
    if not providers:
        raise RuntimeError('No subscriptions available')
    candidate_config = configuration(providers, (ROOT / 'controller.secret').read_text().strip())
    # Validate every new provider with Mihomo before replacing its working copy.
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        validation = json.loads(json.dumps(candidate_config))
        replacements = {str(dest): str(src) for src, dest in changed}
        for provider in validation['proxy-providers'].values():
            provider['path'] = replacements.get(provider['path'], provider['path'])
        test_config = Path(directory) / 'config.yaml'
        write_private(test_config, yaml.safe_dump(validation, allow_unicode=True))
        result = subprocess.run([str(Path.home()/'.local/bin/mihomo'), '-t',
                                 '-d', str(ROOT), '-f', str(test_config)],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Mihomo rejected new configuration; working files preserved')
    for src, dest in changed:
        os.replace(src, dest)
    write_private(ROOT / 'config.yaml', yaml.safe_dump(candidate_config, allow_unicode=True))
    try:
        api('/configs?force=true', {'path': str(ROOT / 'config.yaml')}, 'PUT')
    except urllib.error.URLError:
        pass  # Initial refresh may run before the daemon is started.


def configured_api_model(config):
    primary = config.get('model') or {}
    if primary.get('provider') == 'gemini':
        return primary.get('default', 'gemini-3.8-flash')
    for fallback in config.get('fallback_providers') or []:
        if fallback.get('provider') == 'gemini':
            return fallback['model']
    return 'gemini-3.8-flash'


def probe_gemini(*, verify_generation=False, opener=PROXY):
    key = ''
    env = Path.home()/'.hermes/.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(('GEMINI_API_KEY=', 'GOOGLE_API_KEY=')):
                key = line.split('=', 1)[1].strip().strip('\"\'') or key
    if key and verify_generation:
        config = yaml.safe_load((Path.home()/'.hermes/config.yaml').read_text()) or {}
        # Even with a CLI primary, verify the actual API fallback/vision route.
        model = configured_api_model(config)
        body = {'contents': [{'parts': [{'text': 'Call check_connection.'}]}],
                'tools': [{'functionDeclarations': [{'name':'check_connection',
                    'description':'Check connectivity; no external effects',
                    'parameters': {'type':'object', 'properties':{}}}]}],
                'toolConfig': {'functionCallingConfig': {'mode':'ANY'}},
                'generationConfig': {'maxOutputTokens': 2048,
                                     'thinkingConfig': {'thinkingLevel': 'high'}}}
        req = urllib.request.Request(
            'https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',
            data=json.dumps(body).encode(),
            headers={'x-goog-api-key': key, 'Content-Type': 'application/json'})
    elif key:
        req = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models',
                                     headers={'x-goog-api-key': key})
    else:
        req = urllib.request.Request('https://gemini.google.com/',
                                     headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with opener.open(req, timeout=45 if verify_generation else 20) as response:
            body = response.read(2*1024*1024).decode(errors='replace')
            final_url = response.url
        if key:
            if verify_generation:
                data = json.loads(body)
                parts = [p for c in data.get('candidates', []) for p in c.get('content', {}).get('parts', [])]
                if not any(p.get('functionCall', {}).get('name') == 'check_connection' for p in parts):
                    return 'bad', 'empty_generation'
                return 'good', 'gemini_generation:'+str(data.get('modelVersion') or model)
            if not json.loads(body).get('models'):
                return 'bad', 'empty_models'
            return 'good', 'gemini_api_models'
        if '/unsupported' in final_url or 'not available in your country' in body.lower():
            return 'bad', 'unsupported_region'
        return 'good', 'gemini_public_only'
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors='replace').lower()
        if any(x in body for x in ('location is not supported', 'unsupported country',
                                  'not available in your country')):
            return 'bad', 'unsupported_region'
        if error.code in (400, 401, 403) and any(x in body for x in
                ('api key not valid', 'api_key_invalid', 'unregistered callers', 'api key expired')):
            return 'credentials', 'invalid_api_key'
        if error.code == 429:
            return 'quota', 'rate_limit_or_quota'
        return 'bad', 'http_' + str(error.code)
    except Exception as error:
        return 'bad', type(error).__name__


def probe_telegram(*, opener=PROXY):
    token = ''
    for line in (Path.home()/'.hermes/.env').read_text().splitlines():
        if line.startswith('TELEGRAM_BOT_TOKEN='):
            token = line.split('=', 1)[1].strip().strip('\"\'')
    if not token:
        return 'credentials', 'missing_telegram_token'
    try:
        with opener.open('https://api.telegram.org/bot'+token+'/getMe', timeout=20) as response:
            body = json.load(response)
        return ('good', 'telegram_get_me') if body.get('ok') else ('bad', 'telegram_api_failed')
    except urllib.error.HTTPError as error:
        if error.code in (401, 429):
            return 'quota' if error.code == 429 else 'credentials', 'http_'+str(error.code)
        return 'bad', 'http_'+str(error.code)
    except Exception as error:
        return 'bad', type(error).__name__


def check_group(name, probe):
    group = api('/proxies/' + name)
    original = group['now']
    status, reason = probe()
    cache_path = ROOT/'generation-health.json'
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    cfg = yaml.safe_load((Path.home()/'.hermes/config.yaml').read_text()) or {}
    model = configured_api_model(cfg)
    def confirm_generation(node, opener=PROXY):
        status, reason = probe_gemini(verify_generation=True, opener=opener)
        if status == 'good':
            write_private(cache_path, json.dumps({'node':node, 'model':model,
                'time':time.time(), 'probe':reason},ensure_ascii=False))
        return status, reason
    # Test the actual configured model on each new route and every four hours.
    # Routine two-minute checks use /models to avoid consuming the free generation quota.
    if name == 'GEMINI' and status == 'good' and (
            cache.get('node') != original or cache.get('model') != model
            or time.time()-cache.get('time',0) > 4*3600):
        status, reason = confirm_generation(original)
    report = {'time': time.time(), 'selection': original, 'status': status, 'probe': reason}
    if status == 'bad':
        nodes = {}
        for provider in (api('/providers/proxies') or {}).get('providers', {}).values():
            for node in provider.get('proxies', []):
                nodes[node['name']] = node
        def priority(node):
            health = nodes.get(node, {})
            history = health.get('history') or [{}]
            delay = history[-1].get('delay') or float('inf')
            return (health.get('alive') is False, node != cache.get('node'),
                    not any(x in node.lower() for x in ('ai)', 'сша', 'usa')), delay, node)
        candidates = sorted((p for p in group['all'] if p not in (original, name+'-AUTO')),
                            key=priority)
        for node in candidates:
            # Probe candidates on a separate inbound. Keep active requests on the
            # existing route until a replacement has actually passed the checks.
            api('/proxies/PROBE', {'name': node}, 'PUT')
            status, reason = probe(opener=PROBE_PROXY)
            if name == 'GEMINI' and status == 'good':
                status, reason = confirm_generation(node, opener=PROBE_PROXY)
            if status != 'bad':
                if status == 'good':
                    api('/proxies/' + name, {'name': node}, 'PUT')
                report.update(selection=node if status == 'good' else original,
                              status=status, probe=reason, switched=status == 'good')
                break
        else:
            report.update(status='bad', probe='no_working_node', switched=False)
    return report


def check():
    from antigravity_vpn import check_cli
    reports = {'gemini': check_group('GEMINI', probe_gemini),
               'telegram': check_group('AI', probe_telegram),
               'antigravity': check_cli(ROOT, api, write_private)}
    write_private(ROOT/'health.json', json.dumps(reports, ensure_ascii=False, indent=2))
    print(json.dumps(reports, ensure_ascii=False))
    return int(any(r['status'] == 'bad' for r in reports.values()))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['refresh','check','antigravity-discover','antigravity-recover'])
    args = parser.parse_args()
    with (ROOT/'manager.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action.startswith('antigravity-'):
            from antigravity_vpn import discover, check_cli
            if args.action=='antigravity-discover':
                discover(ROOT,api,write_private)
            else:
                print(json.dumps(check_cli(ROOT,api,write_private,force=True),ensure_ascii=False))
            return 0
        return refresh() if args.action == 'refresh' else check()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('VPN manager failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
