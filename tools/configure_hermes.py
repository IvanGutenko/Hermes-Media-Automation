#!/usr/bin/env python3
"""Apply the personal-agent configuration without copying secrets into YAML."""
from pathlib import Path
import argparse
import yaml
from model_policy import apply_model_policy, APPROVED_MODELS

root = Path.home()/'.hermes'
path = root/'config.yaml'
data = yaml.safe_load(path.read_text()) or {}
parser = argparse.ArgumentParser()
parser.add_argument('--provider', choices=tuple(APPROVED_MODELS))
args = parser.parse_args()
saved = data.get('model') or {}
provider = args.provider or (saved.get('provider') if saved.get('provider') in APPROVED_MODELS else 'antigravity-cli')
allowed_models = APPROVED_MODELS[provider]
primary = saved.get('default') if saved.get('provider') == provider and saved.get('default') in allowed_models else None
def slot(parent, name):
    if not isinstance(parent.get(name), dict):
        parent[name] = {}
    return parent[name]
backup = root/'config.before-personal-setup.yaml'
if not backup.exists():
    backup.write_text(path.read_text()); backup.chmod(0o600)
apply_model_policy(data, provider=provider, primary=primary)
slot(data, 'terminal').update(backend='local', cwd='/home/gut/hermes-workspace')
slot(data, 'gateway')['unauthorized_dm_behavior'] = 'ignore'
slot(slot(slot(data, 'platforms'), 'telegram'), 'extra').update(
    drop_pending_on_cold_boot=False, unauthorized_dm_behavior='ignore')
slot(data, 'memory')['memory_enabled'] = True
slot(slot(data, 'telemetry'), 'shared_metrics')['enabled'] = False
slot(data, 'onboarding')['profile_build'] = 'off'
slot(data, 'auxiliary')['title_generation'] = {'model_upgrade_enabled': False}
path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False));path.chmod(0o600)
memories = root/'memories';memories.mkdir(exist_ok=True,mode=0o700)
user = memories/'USER.md'
if not user.exists() or not user.read_text().strip():
    user.write_text((root/'USER.seed.md').read_text());user.chmod(0o600)
print('Configured approved models at ultra effort, workspace, private Telegram and user context')
