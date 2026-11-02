#!/usr/bin/env python3
"""Probe requested full Gemini models with the provider's maximum thinking level."""
import concurrent.futures
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def credentials():
    result = {}
    for line in (Path.home()/'.hermes/.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            result[k.strip()] = v.strip().strip('\"\'')
    return result


def probe(model):
    start = time.monotonic()
    env = credentials()
    key = env.get('GOOGLE_API_KEY') or env['GEMINI_API_KEY']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({
        'https': 'http://127.0.0.1:17890'}))
    function = {'name': 'read_note', 'description': 'Read a selected personal note',
        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}},
                       'required': ['path']}}
    body = {'contents': [{'role': 'user', 'parts': [{'text':
        'Прочитай заметку every day tasks.md: сначала вызови read_note. Не придумывай её содержимое.'}]}],
        'tools': [{'functionDeclarations': [function]}],
        'generationConfig': {'maxOutputTokens': 8192,
                             'thinkingConfig': {'thinkingLevel': 'high'}}}
    url = 'https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent'
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={'x-goog-api-key': key, 'Content-Type': 'application/json'})
    result = {'model': model, 'thinking_level': 'high'}
    try:
        with opener.open(req, timeout=90) as response:
            data = json.load(response)
        calls = [part['functionCall'] for c in data.get('candidates', [])
                 for part in c.get('content', {}).get('parts', []) if 'functionCall' in part]
        result.update(ok=bool(calls), returned_model=data.get('modelVersion'), calls=calls,
                      usage=data.get('usageMetadata'), finish=[c.get('finishReason') for c in data.get('candidates', [])])
    except urllib.error.HTTPError as error:
        try:
            data = json.loads(error.read()).get('error', {})
        except Exception:
            data = {}
        result.update(ok=False, http=error.code, status=data.get('status'), message=data.get('message'))
    except Exception as error:
        result.update(ok=False, error_type=type(error).__name__)
    result['elapsed_s'] = round(time.monotonic()-start, 2)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, ['gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.5-flash']))
    path = Path.home()/'.hermes/gemini-quality-probes.json'
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2));path.chmod(0o600)
