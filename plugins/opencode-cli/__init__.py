from pathlib import Path
from providers import register_provider
from providers.base import ProviderProfile

MODELS = ('opencode/deepseek-v4.1-flash-free','opencode/glm-5.3-flash-free')


class OpenCodeProfile(ProviderProfile):
    def fetch_models(self, **kwargs):
        return list(MODELS)

    def create_client(self, **kwargs):
        from .bridge import OpenCodeClient
        return OpenCodeClient(**kwargs)


register_provider(OpenCodeProfile(name='opencode-cli',display_name='OpenCode — approved free models',
    auth_type='external_process',process_command=str(Path.home()/'.local/bin/hermes-opencode'),
    process_args=('acp',),base_url='acp://opencode-cli',api_mode='chat_completions',
    env_vars=(),supports_vision=False,supports_streaming=False,supports_health_check=False,
    fallback_models=MODELS))
