"""Bound the container worker itself, not only the docker client waiting for it, and share the one browser fairly."""
import fcntl
from contextlib import contextmanager
import os
import subprocess
import time
from pathlib import Path

BASE_DIR=Path(os.getenv('VIDEO_FACTORY_DIR',Path(__file__).resolve().parent.parent))
WAITING_FRESH_SECONDS=120


class BrowserBusy(RuntimeError):
    """The shared browser stayed leased for the whole wait. Not a publishing failure: the next tick tries again."""


def run(command, *, timeout, **kwargs):
    if command[:3]!=['docker','exec','video_factory_auth'] or timeout<31:
        raise ValueError('Expected a bounded media container command')
    # GNU timeout terminates the worker and its Playwright driver process group.
    # The separately running, persistent Chrome and its saved profile survive.
    bounded=command[:3]+['timeout','--kill-after=10s',str(timeout-30)+'s']+command[3:]
    return subprocess.run(bounded,timeout=timeout,**kwargs)


def lease_file(base=None):
    return Path(base or BASE_DIR)/'output/.flow-generation.lock'


def waiting_marker(base=None):
    return Path(base or BASE_DIR)/'output/.publisher-waiting'


def publisher_waiting(base=None,now=None):
    """True while a publisher wants the browser: generation then does not start its next scene."""
    try:return (now or time.time())-waiting_marker(base).stat().st_mtime<WAITING_FRESH_SECONDS
    except OSError:return False


@contextmanager
def render_lease(base=None):
    """Keep the LVL encoder's memory/disk burst out of active browser operations."""
    lease_file(base).parent.mkdir(parents=True,exist_ok=True)
    # Give an already waiting publisher one grace window, then join the
    # kernel's lock queue. A continuously refreshed marker used to starve a
    # completed scene indefinitely behind recurring image/browser tasks.
    deadline=time.monotonic()+WAITING_FRESH_SECONDS
    while publisher_waiting(base) and time.monotonic()<deadline:
        time.sleep(2)
    fd=os.open(lease_file(base),os.O_RDONLY|os.O_CREAT,0o644)
    with os.fdopen(fd) as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def wait_for_lease(max_wait,base=None,poll=5):
    """Block until the browser lease is free; True when it was seen free, False after max_wait.

    Publishers inside the container take the lease without waiting, and Flow holds it for up to 14 minutes per scene.
    Measured 2026-11-02: VK and Instagram attempts died within a second on that lock at every 30-minute tick, so a
    video only got published when the tick happened to land between scenes. The lease is only observed here, then released."""
    path=lease_file(base)
    if not path.parent.is_dir():return True
    deadline=time.monotonic()+max_wait
    # Read-only on purpose: the file belongs to root in the container and this runs on the host. flock works on any open descriptor.
    try:fd=os.open(path,os.O_RDONLY|os.O_CREAT,0o644)
    except OSError:return True  # unobservable: the publisher's own non-blocking lock still protects the browser
    with os.fdopen(fd) as lock:
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                fcntl.flock(lock,fcntl.LOCK_UN)
                return True
            except BlockingIOError:
                if time.monotonic()>=deadline:return False
                try:waiting_marker(base).touch()
                except OSError:pass
                time.sleep(poll)
