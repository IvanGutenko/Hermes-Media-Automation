"""Explicit model floor: never use Lite or an unselected model as automatic fallback."""
# The tested 3.7 alias resolves to 3.8 and shares its quota. Do not retry it
# as an independent fallback after 3.8 fails.
API_MODELS = ('gemini-3.6-flash', 'gemini-3.8-flash')
CLI_MODELS = ('gemini-3.8-flash-high',)
OPENCODE_MODELS = ('opencode/deepseek-v4.1-flash-free','opencode/glm-5.3-flash-free')
OPENROUTER_MODELS = ('nvidia/nemotron-3-ultra-550b-a55b:free','nvidia/nemotron-3.5-super-vl:free')
GROQ_MODELS = ('qwen/qwen3.8-27b',)
APPROVED_MODELS = {'gemini':API_MODELS,'antigravity-cli':CLI_MODELS,
                   'opencode-cli':OPENCODE_MODELS,'openrouter-free':OPENROUTER_MODELS,
                   'groq':GROQ_MODELS}
# User's quality preference: subscription, Nemotron, OpenCode, Qwen last.
# Native Gemini API remains available for vision and explicit selection only.
FALLBACKS = [('antigravity-cli',CLI_MODELS[0]),
             *[('openrouter-free',m) for m in OPENROUTER_MODELS],
             *[('opencode-cli',m) for m in OPENCODE_MODELS],
             ('groq',GROQ_MODELS[0])]
RETIRED_PROVIDERS = ('nvidia', 'openrouter', 'modelscope')


def apply_model_policy(config, *, provider='antigravity-cli', primary=None):
    if provider in RETIRED_PROVIDERS:
        raise ValueError('This provider has been removed')
    if provider not in APPROVED_MODELS:
        raise ValueError('Provider is outside the approved personal setup')
    if primary is None:
        primary = API_MODELS[-1] if provider == 'gemini' else APPROVED_MODELS[provider][0]
    if primary not in APPROVED_MODELS[provider]:
        raise ValueError('Primary model is outside the approved set')
    providers = config.get('providers')
    if not isinstance(providers, dict):
        providers = {}; config['providers'] = providers
    for retired in RETIRED_PROVIDERS:
        providers.pop(retired, None)
    if not providers:
        config.pop('providers', None)
    config['model'] = {'provider': provider, 'default': primary}
    config['fallback_providers'] = [{'provider':p,'model':m} for p,m in FALLBACKS
                                    if (p,m)!=(provider,primary)]
    if not isinstance(config.get('agent'), dict):
        config['agent'] = {}
    config['agent']['reasoning_effort'] = 'ultra'
    overrides = config['agent'].get('reasoning_overrides') or {}
    overrides.pop('openai/gpt-oss-120b', None)
    for model in (*[m for models in APPROVED_MODELS.values() for m in models], primary):
        overrides[model] = 'ultra'
    config['agent']['reasoning_overrides'] = overrides
    auxiliary = config.get('auxiliary')
    if not isinstance(auxiliary, dict):
        auxiliary = {}; config['auxiliary'] = auxiliary
    for task in ('vision', 'web_extract', 'session_search', 'compression', 'background_review', 'curator'):
        task_provider, task_model = ('gemini',API_MODELS[-1]) if task == 'vision' else (provider,primary)
        auxiliary[task] = {**(auxiliary.get(task) or {}),
                           'provider': task_provider, 'model': task_model, 'reasoning_effort': 'ultra'}
    return config
