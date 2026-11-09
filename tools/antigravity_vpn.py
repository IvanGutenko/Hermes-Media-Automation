"""Validate EOS routes with the signed-in official CLI before promoting them."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

MODEL = 'gemini-3.8-flash-high'


def classify_result(stdout, returncode):
    result = None
    init = {}
    for line in stdout.splitlines():
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        if frame.get('event') == 'result':
            result = frame.get('result') or {}
        if frame.get('event') == 'init':
            init = frame.get('init') or {}
    if (result and not returncode and result.get('status') == 'SUCCESS'
            and result.get('response','').strip() == 'READY'
            and init.get('model')==MODEL and init.get('agent')=='hermes-bridge'):
        return 'good', 'antigravity_generation:'+MODEL
    error = str((result or {}).get('error') or '')
    lowered = error.lower()
    if any(s in lowered for s in ('quota', 'resource_exhausted', 'rate limit')):
        return 'quota', 'subscription_quota'
    if any(s in lowered for s in ('authentication', 'sign in', 'signed in', 'credentials')):
        return 'credentials', 'cli_authentication'
    if 'location' in lowered or 'region' in lowered:
        return 'bad', 'unsupported_region'
    return 'bad', 'cli_generation_failed'


def generate(proxy):
    env = os.environ.copy()
    for key in ('GEMINI_API_KEY','GOOGLE_API_KEY','AGY_ADC_AUTH'):
        env.pop(key, None)
    for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','grpc_proxy','ALL_PROXY','all_proxy'):
        env[key] = proxy
    args = [str(Path.home()/'.local/bin/agy'), '--model', MODEL, '--effort','high',
            '--agent','hermes-bridge', '--input-format','stream-json',
            '--output-format','stream-json','--print-timeout','50s']
    try:
        response = subprocess.run(args, input=json.dumps({'event':'user',
            'message':{'content':'Reply with exactly: READY'}})+'\n',
            capture_output=True, text=True, env=env,
            cwd=Path.home()/'.cache/hermes-antigravity/bridge', timeout=65)
        return classify_result(response.stdout, response.returncode)
    except subprocess.TimeoutExpired:
        return 'bad', 'cli_generation_timeout'
    except OSError:
        return 'credentials', 'cli_unavailable'


def transport(proxy):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy,'http':proxy}))
    req = urllib.request.Request('https://daily-cloudcode-pa.googleapis.com/',
                                 headers={'User-Agent':'HermesAgent/0.22.5'})
    try:
        with opener.open(req, timeout=12):
            pass
    except urllib.error.HTTPError as error:
        # A reachable API root commonly returns 404; this checks transport only.
        if error.code not in (400,401,403,404):
            return 'bad', 'transport_http_'+str(error.code)
    except Exception as error:
        return 'bad', 'transport_'+type(error).__name__
    return 'good', 'transport_only_cached_generation'


def load_state(root):
    try:
        state = json.loads((root/'antigravity-routes.json').read_text())
    except (OSError, ValueError):
        state = {}
    state.setdefault('nodes', {})
    return state


def candidates(controller, state, exclude=()):
    prov_data = controller('/providers/proxies')['providers']
    nodes = []
    for p_name, p_info in prov_data.items():
        if p_info.get('vehicleType') in ('File', 'HTTP'):
            nodes.extend([p for p in p_info.get('proxies', []) if p['name'] not in exclude])
    preferred = ('Германия - 6', 'Германия - 5', 'ChatGPT, Gemini, AI', 'Финляндия - 1',
                 'Германия - 8', 'Германия - 3', 'Германия - 4',
                 'Нидерланды - 1', 'Нидерланды - 2', 'Франция - 1', 'Основной сервер - 4')
    def order(node):
        previous = state['nodes'].get(node['name'], {})
        rank = next((i for i, p in enumerate(preferred) if p in node['name']), len(preferred))
        has_ai = any(k in node['name'] for k in ('AI', 'Gemini', 'ChatGPT', '🤖', 'Нейросети'))
        return (previous.get('status') != 'good', not has_ai, node.get('alive') is False, rank, node['name'])
    return [n['name'] for n in sorted(nodes, key=order)
            if not (state['nodes'].get(n['name'], {}).get('status') == 'bad'
                    and time.time() - state['nodes'][n['name']].get('time', 0) < 12 * 3600)]


def record(state, node, status, reason):
    state['nodes'][node] = {'status':status,'probe':reason,'time':time.time(),'model':MODEL}


def discover(root, controller, write_private, *, minimum=3, max_attempts=40):
    state = load_state(root)
    tested = []
    original = controller('/proxies/PROBE')['now']
    try:
        for node in candidates(controller,state):
            if len([v for v in state['nodes'].values() if v.get('status')=='good'
                    and time.time()-v.get('time',0)<4*3600]) >= minimum:
                break
            if len(tested) >= max_attempts:
                break
            previous = state['nodes'].get(node,{})
            if previous.get('status')=='good' and time.time()-previous.get('time',0)<4*3600:
                continue
            controller('/proxies/PROBE',{'name':node},'PUT')
            status, reason = transport('http://127.0.0.1:17891')
            if status=='good':
                status, reason = generate('http://127.0.0.1:17891')
            record(state,node,status,reason)
            tested.append({'node':node,'status':status,'probe':reason})
            write_private(root/'antigravity-routes.json',json.dumps(state,ensure_ascii=False,indent=2))
            print(json.dumps(tested[-1],ensure_ascii=False),flush=True)
            if status in ('quota','credentials'):
                break
    finally:
        controller('/proxies/PROBE',{'name':original},'PUT')
    return tested


def check_cli(root, controller, write_private, *, force=False):
    state = load_state(root)
    selected = controller('/proxies/ANTIGRAVITY')['now']
    if force and time.time()-state.get('last_recovery',0)<60:
        return {'selection':selected,'status':'deferred','probe':'recovery_debounce','time':time.time()}
    if force:
        state['last_recovery'] = time.time()
    cached = state['nodes'].get(selected,{})
    status, reason = transport('http://127.0.0.1:17892')
    stale = cached.get('status')!='good' or time.time()-cached.get('time',0)>4*3600
    if status=='good' and (force or stale):
        status, reason = generate('http://127.0.0.1:17892')
        record(state,selected,status,reason)
    elif status=='bad':
        record(state,selected,status,reason)
    report = {'selection':selected,'status':status,'probe':reason,'time':time.time(),
              'generation_verified_at':state['nodes'].get(selected,{}).get('time') if status=='good' else None}
    if status=='bad':
        original_probe = controller('/proxies/PROBE')['now']
        try:
            for node in candidates(controller,state,exclude=(selected,))[:10]:
                controller('/proxies/PROBE',{'name':node},'PUT')
                status, reason = generate('http://127.0.0.1:17891')
                record(state,node,status,reason)
                if status=='good':
                    controller('/proxies/ANTIGRAVITY',{'name':node},'PUT')
                    report.update(selection=node,status=status,probe=reason,switched=True,
                                  generation_verified_at=state['nodes'][node]['time'])
                    break
                if status in ('quota','credentials'):
                    report.update(status=status,probe=reason,switched=False)
                    break
            else:
                report.update(probe='no_working_cli_route',switched=False)
        finally:
            controller('/proxies/PROBE',{'name':original_probe},'PUT')
    write_private(root/'antigravity-routes.json',json.dumps(state,ensure_ascii=False,indent=2))
    write_private(root/'antigravity-health.json',json.dumps(report,ensure_ascii=False,indent=2))
    return report
