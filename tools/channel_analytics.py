#!/usr/bin/env python3
"""Hermes entry point for measured cross-platform media feedback."""
import re,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'video_factory/scripts'))
from content_feedback import DATA,tick,format_report

if __name__=='__main__':
    path=DATA/'content_feedback.json'
    if not path.exists() or time.time()-path.stat().st_mtime>7200:tick()
    print(re.sub(r'<[^>]+>','',format_report()))
