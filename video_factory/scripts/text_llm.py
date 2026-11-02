"""Ordered text-model failover for the media factory.

Owner policy (model_policy.py): the Antigravity subscription first, the approved
free OpenRouter models next, Groq last. A provider that reports quota or fails is
skipped for a cooldown instead of being retried by every one-minute tick, so one
exhausted free tier never stalls publication; a total outage never counts as a pass.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
import requests
from model_policy import CLI_MODELS, OPENROUTER_MODELS

BASE_DIR = Path(os.getenv('VIDEO_FACTORY_DIR', Path(__file__).resolve().parent.parent))
COOLDOWNS = BASE_DIR / 'data' / 'llm_cooldowns.json'
ORDER = tuple(os.getenv('MEDIA_TEXT_LLM_ORDER', 'agy,openrouter,groq').split(','))
PROXY_URL = os.getenv('HTTP_PROXY', 'http://127.0.0.1:17890')
PROXIES = {'http': PROXY_URL, 'https': PROXY_URL}
GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
AGY_BIN = os.getenv('AGY_BIN', '/home/gut/.local/bin/agy')
# Antigravity has its own route (README): it is not the general media proxy.
AGY_PROXY = os.getenv('AGY_PROXY', 'http://127.0.0.1:17892')
# Measured on the server: a one-word reply takes ~9 s, a constrained Russian rewrite at high effort ~94 s and over 150 s under load.
AGY_TIMEOUT = int(os.getenv('MEDIA_AGY_TIMEOUT', '300'))
# One argv element is limited to 128 KiB; leave room for the CLI's own arguments.
AGY_MAX_PROMPT_BYTES = 110_000


class Unavailable(RuntimeError):
    """One model could not answer. retry_after seconds is the cooldown; 0 means none."""
    def __init__(self, message, retry_after=300):
        super().__init__(message)
        self.retry_after = retry_after


def _cooldowns():
    try:return json.loads(COOLDOWNS.read_text())
    except (OSError, ValueError):return {}


def _cool(key, seconds):
    """Best effort: a cooldown that cannot be saved must never break the job."""
    state = {k: v for k, v in _cooldowns().items() if v > time.time()}
    state[key] = time.time() + seconds
    try:
        COOLDOWNS.parent.mkdir(exist_ok=True)
        tmp = COOLDOWNS.with_name(f'{COOLDOWNS.name}.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(state))
        tmp.replace(COOLDOWNS)
    except OSError:pass


def retry_delay(message, headers=None):
    """Seconds the provider asked us to wait ('try again in 12m57.168s'); None if unstated."""
    try:return float((headers or {}).get('retry-after', ''))
    except (TypeError, ValueError, AttributeError):pass
    match = re.search(r'try again in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)', message or '')
    if not match or not match[1]:return None
    return sum(float(n) * {'h': 3600, 'm': 60, 's': 1}[u] for n, u in re.findall(r'([\d.]+)([hms])', match[1]))


def json_text(text):
    """The JSON object inside a reply, tolerating code fences and surrounding prose."""
    text = (text or '').strip()
    fence = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if fence:text = fence[1].strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:raise ValueError('No JSON object in the model reply')
    candidate = text[start:end + 1]
    if not isinstance(json.loads(candidate), dict):raise ValueError('The model reply is not a JSON object')
    return candidate


def _flatten(messages):
    """The CLI takes one prompt: keep the roles so corrective turns stay legible."""
    system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
    turns = [('SOURCE/USER' if m['role'] == 'user' else 'YOUR PREVIOUS REPLY') + ':\n' + m['content']
             for m in messages if m['role'] != 'system']
    return system + '\n\n' + '\n\n'.join(turns) + '\n\nReply with the JSON object only.'


def agy_homes():
    """{provider label: CLI home} for every signed-in account; quotas belong to the account.

    data/agy_accounts.json lists the extra homes; each was signed in once with HOME pointing at it."""
    homes = {'agy': str(Path.home())}
    try:
        for number, home in enumerate(json.loads((BASE_DIR / 'data' / 'agy_accounts.json').read_text()), 2):
            homes[f'agy{number}'] = str(home)
    except (OSError, ValueError, TypeError):pass
    return homes


def _agy(model, messages, *, max_tokens, temperature, limit_file, home=None):
    if not os.access(AGY_BIN, os.X_OK):raise Unavailable('Antigravity CLI is not installed', 3600)
    prompt = _flatten(messages)
    if len(prompt.encode()) > AGY_MAX_PROMPT_BYTES:raise Unavailable('Prompt is too large for the CLI', 0)
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': home or str(Path.home()),
           'http_proxy': AGY_PROXY, 'https_proxy': AGY_PROXY}
    try:
        # The CLI refuses --effort for every model but this one: the others carry the level in their name or have none.
        effort = ['--effort', 'high'] if model == 'gemini-3.8-flash-high' else []
        done = subprocess.run([AGY_BIN, '-p', prompt, '--model', model, *effort, '--output-format', 'json'],
                              capture_output=True, text=True, env=env, timeout=AGY_TIMEOUT)
    except subprocess.TimeoutExpired:raise Unavailable('Antigravity CLI timed out', 300) from None
    try:outer = json.loads(done.stdout)
    except ValueError:raise Unavailable('Antigravity CLI returned no JSON envelope (exit %s)' % done.returncode, 300) from None
    if done.returncode or outer.get('status') != 'SUCCESS':
        detail = str(outer.get('error') or outer.get('status'))[:200]
        # A content-safety block answers this prompt only; pausing the subscription would starve every other call.
        if re.search(r'content safety|safety filter', detail, re.I):raise Unavailable('Antigravity CLI: ' + detail, 0)
        # Subscription windows are five hours and a week: a quota is not worth probing every minute.
        # The CLI names the reset ("Resets in 44h4m14s"): probing earlier only burns a call and a slot in the chain.
        reset = re.search(r'Resets in ((?:\d+h)?(?:\d+m)?(?:\d+s)?)', detail)
        wait = sum(int(n) * {'h': 3600, 'm': 60, 's': 1}[u] for n, u in re.findall(r'(\d+)([hms])', reset[1])) if reset else 0
        raise Unavailable('Antigravity CLI: ' + detail, wait or (1800 if re.search(r'quota|exhaust|limit', detail, re.I) else 300))
    return outer.get('response', '')


def _openrouter(model, messages, *, max_tokens, temperature, limit_file):
    key = os.getenv('HERMES_OPENROUTER_FREE_API_KEY') or os.getenv('OPENROUTER_API_KEY')
    if not key:raise Unavailable('OpenRouter key is not configured', 3600)
    # Policy: only the two named free models, never a paid route.
    if model not in OPENROUTER_MODELS or not model.endswith(':free'):raise ValueError('Model is outside the approved free set')
    try:
        r = requests.post(OPENROUTER_URL, headers={'Authorization': 'Bearer ' + key}, proxies=PROXIES, timeout=150,
            json={'model': model, 'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens or 8000,
                  'response_format': {'type': 'json_object'}})
    except requests.RequestException as exc:raise Unavailable('OpenRouter transport: ' + type(exc).__name__, 300) from None
    if r.status_code != 200:
        raise Unavailable(f'HTTP {r.status_code}', (retry_delay(r.text, r.headers) or 900) + 30)
    payload = r.json()
    if (payload.get('usage') or {}).get('cost') not in (None, 0, 0.0):
        raise Unavailable('Unexpected nonzero cost on a free endpoint', 3600)
    if not payload.get('choices'):
        raise Unavailable('OpenRouter reply has no choices: ' + str(payload.get('error') or '')[:120], 300)
    return (payload['choices'][0]['message'].get('content') or '')


def _groq(model, messages, *, max_tokens, temperature, limit_file):
    key = os.getenv('GROQ_API_KEY')
    if not key:raise Unavailable('Groq key is not configured', 3600)
    for attempt in range(3):
        body = {'model': model, 'messages': messages, 'temperature': temperature,
                'response_format': {'type': 'json_object'}}
        if max_tokens:body['max_completion_tokens'] = max_tokens
        r = requests.post(GROQ_URL, headers={'Authorization': 'Bearer ' + key}, json=body, proxies=PROXIES, timeout=90)
        if r.status_code != 429:break
        try:message = r.json().get('error', {}).get('message', '')
        except ValueError:message = 'Non-JSON quota response'
        if limit_file:
            try:Path(limit_file).write_text(json.dumps({'model': model, 'http_status': 429, 'message': message[:900],
                                                        'at': time.time(), 'time': time.time()}))
            except OSError:pass
        delay = retry_delay(message, r.headers)
        # A short TPM window is worth waiting out; a daily (TPD) limit is not.
        if attempt == 2 or not delay or delay > 60:
            raise Unavailable('HTTP 429', (delay or 1800) + 30)
        time.sleep(delay + 1)
    if r.status_code != 200:
        try:detail = r.json().get('error', {}).get('message', '')
        except ValueError:detail = ''
        # A rejected JSON generation is the model's fault, not an outage: just try the next one.
        raise Unavailable(f'HTTP {r.status_code}', 0 if detail.startswith('Failed to generate JSON')
                          else 900 if r.status_code in (500, 502, 503, 504) else 3600 if r.status_code in (401, 403) else 300)
    return r.json()['choices'][0]['message']['content']


# Each model of the subscription has its own quota: flash-high ran out while these still answered a real script.
AGY_MODELS = (*CLI_MODELS, 'claude-sonnet-4-6', 'claude-opus-4-6-thinking', 'gemini-3.8-flash-medium', 'gemini-3.8-flash-low')
FIXED_MODELS = {'agy': AGY_MODELS, 'openrouter': OPENROUTER_MODELS}


def candidates(groq_models):
    """Approved (provider, model) pairs in policy order, minus those cooling down."""
    now, cool, out = time.time(), _cooldowns(), []
    for provider in ORDER:
        if provider == 'agy':
            # The better model on the other account comes before a weaker one on this account.
            out += [(label, m) for m in AGY_MODELS for label in agy_homes() if cool.get(f'{label}:{m}', 0) <= now]
            continue
        models = tuple(groq_models) if provider == 'groq' else FIXED_MODELS.get(provider, ())
        out += [(provider, m) for m in models if cool.get(f'{provider}:{m}', 0) <= now]
    return out


def cooling_summary():
    now = time.time()
    return ', '.join(f'{k} for {int(v - now)}s' for k, v in sorted(_cooldowns().items()) if v > now) or 'none'


def call(provider, model, messages, *, max_tokens=None, temperature=0.3, limit_file=None):
    """One model, one JSON reply. Records the cooldown on failure and re-raises Unavailable."""
    func = _agy if provider.startswith('agy') else {'openrouter': _openrouter, 'groq': _groq}[provider]
    account = {'home': agy_homes()[provider]} if provider.startswith('agy') and provider != 'agy' else {}
    try:
        text = func(model, messages, max_tokens=max_tokens, temperature=temperature, limit_file=limit_file, **account)
        try:return json_text(text)
        except ValueError:raise Unavailable('invalid JSON reply', 0 if provider == 'groq' else 120) from None
    except Unavailable as exc:
        print(f'text_llm: {provider}:{model} unavailable ({exc}); paused {exc.retry_after}s', file=sys.stderr, flush=True)
        if exc.retry_after > 0:_cool(f'{provider}:{model}', exc.retry_after)
        raise


def complete(messages, *, groq_models, max_tokens=None, temperature=0.3, limit_file=None):
    """First valid JSON object from any approved model; (dict, 'provider:model')."""
    errors = []
    for provider, model in candidates(groq_models):
        try:return json.loads(call(provider, model, messages, max_tokens=max_tokens,
                                    temperature=temperature, limit_file=limit_file)), f'{provider}:{model}'
        except Unavailable as exc:errors.append(f'{provider}:{model}: {exc}')
    raise Unavailable('No approved text model answered (' + '; '.join(errors or ['all cooling down: ' + cooling_summary()]) + ')', 0)
