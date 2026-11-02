#!/usr/bin/env python3
"""Compatibility entry point. Cached avatar scenes have been retired."""
from autonomous_video_pipeline import run_pipeline

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='Original post or topic for fresh video generation')
    args = parser.parse_args()
    run_pipeline(args.source)
