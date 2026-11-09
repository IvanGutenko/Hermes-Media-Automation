"""Owner exclusions apply even to old jobs and stale per-brand configuration."""

# Reddit was removed from the factory by the owner on 2026-11-06.
# A readback, retry, or account recovery must never enable it again.
DISABLED_PLATFORMS = frozenset({'reddit'})


def disabled_result(platform):
    return {'status': 'disabled', 'platform': platform, 'reason': 'disabled_by_owner'}
