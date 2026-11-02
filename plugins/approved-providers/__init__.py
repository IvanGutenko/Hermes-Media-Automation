from providers import register_provider, get_provider_profile
from providers.base import ProviderProfile

OPENROUTER_MODELS = ('nvidia/nemotron-3-ultra-550b-a55b:free',
                     'nvidia/nemotron-3.5-super-vl:free')
GROQ_MODELS = ('qwen/qwen3.8-27b',)


class FreeOpenRouter(ProviderProfile):
    def fetch_models(self, **kwargs):
        return list(OPENROUTER_MODELS)

    def build_api_kwargs_extras(self, *, model=None, **kwargs):
        if model not in OPENROUTER_MODELS:
            raise ValueError('Only the two approved free OpenRouter models are enabled')
        return get_provider_profile('openrouter').build_api_kwargs_extras(model=model, **kwargs)

    def build_extra_body(self, **kwargs):
        body = get_provider_profile('openrouter').build_extra_body(**kwargs)
        body['provider'] = {**body.get('provider',{}),
            'max_price':{'prompt':0,'completion':0,'request':0,'image':0}}
        return body


class Groq(ProviderProfile):
    def create_client(self, **kwargs):
        from .groq_client import create_client
        return create_client(**kwargs)

    def fetch_models(self, **kwargs):
        return list(GROQ_MODELS)

    def supported_reasoning_efforts(self, model=None):
        return ('low','medium','high')

    def build_api_kwargs_extras(self, *, model=None, reasoning_config=None, **kwargs):
        if model not in GROQ_MODELS:
            raise ValueError('Only Groq Qwen 3.8 27B is enabled')
        enabled = (reasoning_config or {}).get('enabled',True)
        return {'reasoning_format':'parsed'}, {'reasoning_effort':'high' if enabled else 'none'}


register_provider(FreeOpenRouter(name='openrouter-free',display_name='OpenRouter — selected free models',
    env_vars=('HERMES_OPENROUTER_FREE_API_KEY',),base_url='https://openrouter.ai/api/v1',
    models_url='https://openrouter.ai/api/v1/models',default_max_tokens=32768,
    fallback_models=OPENROUTER_MODELS,default_aux_model=OPENROUTER_MODELS[1]))
register_provider(Groq(name='groq',display_name='Groq Qwen 3.8 27B (last resort)',
    env_vars=('GROQ_API_KEY',),base_url='https://api.groq.com/openai/v1',
    default_max_tokens=4096,fallback_models=GROQ_MODELS,supports_vision=False,
    default_aux_model=GROQ_MODELS[0]))
