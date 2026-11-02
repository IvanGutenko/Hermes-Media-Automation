"""Groq uses HTTP 413 for per-minute token capacity, distinct from context length."""
def is_tpm_capacity_error(status, body):
    error = body.get('error',body) if isinstance(body,dict) else {}
    if not isinstance(error,dict):
        return False
    return (status==413 and error.get('code')=='rate_limit_exceeded'
            and 'tokens per minute' in str(error.get('message','')).lower())


class GroqCapacityError(RuntimeError):
    status_code = 429


def create_client(**kwargs):
    from openai import OpenAI, APIStatusError
    client=OpenAI(**kwargs)
    create=client.chat.completions.create
    def completion(**request):
        try:
            return create(**request)
        except APIStatusError as error:
            if is_tpm_capacity_error(error.status_code,error.body):
                raise GroqCapacityError('Groq per-minute token capacity exceeded; use a provider with a larger limit') from error
            raise
    client.chat.completions.create=completion
    client.HERMES_SKIP_TRANSPORT_WRAP=True
    client.HERMES_SKIP_ASYNC_WRAP=True
    return client
