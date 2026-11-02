#!/usr/bin/env python3
"""Read credentials without logging them; report actual provider API reachability."""
import json
from pathlib import Path
import urllib.request
import urllib.error

env = {}
for line in (Path.home()/'.hermes/.env').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('\"\'')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({
    'https': 'http://127.0.0.1:17890', 'http': 'http://127.0.0.1:17890'}))
report = {}
for label, url, headers in [
    ('gemini', 'https://generativelanguage.googleapis.com/v1beta/models',
     {'x-goog-api-key': env.get('GEMINI_API_KEY', '')}),
    ('telegram', 'https://api.telegram.org/bot'+env.get('TELEGRAM_BOT_TOKEN', '')+'/getMe', {})]:
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=45) as response:
            result = json.load(response)
        if label == 'telegram':
            report[label] = {'ok': result.get('ok'), 'username': result.get('result',{}).get('username')}
        else:
            models = result.get('models', result.get('data', []))
            names = [m.get('name', m.get('id')) for m in models]
            report[label] = {'ok': True, 'models': names}
    except urllib.error.HTTPError as error:
        # Provider response messages can include user data; save only public API error text.
        data = error.read().decode(errors='replace')
        try: data = json.loads(data).get('error', {})
        except Exception: data = {'message': 'Non-JSON error'}
        report[label] = {'ok': False, 'http': error.code, 'error': data}
    except Exception as error:
        report[label] = {'ok': False, 'error_class': type(error).__name__}
path = Path.home()/'.hermes/provider-probe.json'
path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
path.chmod(0o600)
for label, result in report.items():
    item = dict(result)
    if 'models' in item:
        item['model_count'] = len(item['models'])
        item['models'] = [x for x in item['models'] if any(s in x.lower()
                          for s in ('gemini', 'glm', 'deepseek', 'kimi', 'qwen', 'gpt-oss'))]
    print(label, json.dumps(item, ensure_ascii=False))
