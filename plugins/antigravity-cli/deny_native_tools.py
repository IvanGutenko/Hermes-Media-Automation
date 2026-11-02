"""Workspace hook: Hermes executes actions, agy only produces model responses."""
import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
name = (payload.get('toolCall') or {}).get('name')
allowed = name == 'finish'
# Keep an argument-free trace so real tool denials can be audited without prompts.
if not allowed:
    with (Path(__file__).parent/'native-tool-denials.jsonl').open('a') as log:
        log.write(json.dumps({'tool':name,'decision':'deny'})+'\n')
print(json.dumps({'decision':'allow' if allowed else 'deny',
    'reason':'Hermes owns action execution; return its tool_call contract instead.'}))
