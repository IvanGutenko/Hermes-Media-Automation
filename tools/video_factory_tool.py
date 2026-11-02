#!/usr/bin/env python3
"""Hermes Shorts CLI: script, fresh Flow clip, scene-based preview, explicit upload."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path('/home/gut/hermes-workspace/video_factory')
SCRIPTS_DIR = BASE_DIR / 'scripts'
PYTHON_BIN = '/home/gut/hermes-workspace/tg_autoposter/.venv/bin/python'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('status')
    script = commands.add_parser('script')
    script.add_argument('topic', nargs='?')
    generate = commands.add_parser('generate')
    generate.add_argument('platform', choices=['flow', 'vids'])
    generate.add_argument('prompt')
    generate.add_argument('title', nargs='?', default='Shorts Video')
    run = commands.add_parser('run', help='Save a preview; --publish explicitly enables upload')
    run.add_argument('topic', nargs='?')
    run.add_argument('platform', nargs='?', choices=['flow'], default='flow')
    run.add_argument('--post-id', type=int)
    run.add_argument('--script-json', type=Path)
    run.add_argument('--publish', action='store_true')
    run.add_argument('--resume')
    upload = commands.add_parser('upload')
    upload.add_argument('video')
    upload.add_argument('title')
    upload.add_argument('description', nargs='?', default='')
    upload.add_argument('privacy', nargs='?', default='public')
    args = parser.parse_args()
    if args.action == 'status':
        result = subprocess.run(['docker', 'ps', '--filter', 'name=video_factory_auth', '--format', '{{.Names}} {{.Status}}'])
        return result.returncode
    if args.action == 'script':
        cmd = [PYTHON_BIN, str(SCRIPTS_DIR / 'script_generator.py')]
        if args.topic:
            cmd.append(args.topic)
    elif args.action == 'generate':
        cmd = ['docker', 'exec', 'video_factory_auth', 'python3', '-u', '/app/scripts/'+args.platform+'_automator.py', args.prompt, args.title]
    elif args.action == 'run':
        cmd = [PYTHON_BIN, '-u', str(SCRIPTS_DIR / 'autonomous_video_pipeline.py')]
        if args.topic:
            cmd.append(args.topic)
        if args.post_id is not None:
            cmd += ['--post-id', str(args.post_id)]
        if args.script_json:
            cmd += ['--script-json', str(args.script_json)]
        if args.publish:
            cmd.append('--publish')
        if args.resume:
            cmd += ['--resume', args.resume]
    else:
        cmd = [PYTHON_BIN, str(SCRIPTS_DIR / 'youtube_uploader.py'), args.video, args.title,
               args.description, args.privacy]
    return subprocess.run(cmd).returncode


if __name__ == '__main__':
    sys.exit(main())
