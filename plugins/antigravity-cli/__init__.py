from providers import register_provider
from providers.base import ProviderProfile


class AntigravityCLIProfile(ProviderProfile):
    def supported_reasoning_efforts(self, model=None):
        return ('low','medium','high')

    def create_client(self, **kwargs):
        from .bridge import AntigravityCLIClient
        return AntigravityCLIClient(**kwargs)

    def fetch_models(self, **kwargs):
        # Explicit pins only; never import the CLI's weaker/default models.
        from .bridge import available_models
        return available_models()


register_provider(AntigravityCLIProfile(
    name='antigravity-cli', display_name='Google Antigravity CLI',
    description='Gemini through the official signed-in CLI; subscription quotas apply',
    api_mode='chat_completions', auth_type='external_process',
    process_command='agy', process_args=(),
    process_command_env_vars=('HERMES_ANTIGRAVITY_COMMAND',),
    base_url='acp://antigravity-cli', env_vars=(),
    supports_vision=False, supports_streaming=False, supports_health_check=False,
    fallback_models=('gemini-3.8-flash-high',),
))
