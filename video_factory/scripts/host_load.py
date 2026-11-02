"""Is the host short of memory or disk bandwidth right now? Used to keep optional browser work off a stalling server.

gut-server-2 has 4.9 GB of RAM and a 5400 rpm disk that also holds the swap file. Measured on 2026-11-01:
once Chrome and a few helpers outgrow RAM, kswapd and the journal saturate the disk (I/O pressure ~98 %),
every service waits, and even ssh needs 100 s. Optional work (hourly analytics pass, LVL renders) therefore
waits for a calm moment instead of adding to the stall. Production (Flow/Vids generation, publishing) never
asks this module: it is the work being protected."""
import re
import time
from pathlib import Path

MIN_AVAILABLE_MIB = 1400   # MemAvailable below this: a Chrome page of a heavy site would push the host into swap
MAX_MEMORY_PRESSURE = 25.0  # percent of the last 60 s in which some task waited for memory
MAX_IO_PRESSURE = 45.0      # same for disk I/O; idle readings on this host are 3-35 %, stalls 85-98 %


def _pressure(kind, window='avg60'):
    try:
        some = Path('/proc/pressure/' + kind).read_text().splitlines()[0]
        return float(re.search(window + r'=([\d.]+)', some).group(1))
    except (OSError, ValueError, AttributeError, IndexError):
        return None


def available_mib():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def reading():
    return {'available_mib': available_mib(), 'memory_pressure': _pressure('memory'), 'io_pressure': _pressure('io')}


def busy(snapshot=None, min_available=MIN_AVAILABLE_MIB, memory_pressure=MAX_MEMORY_PRESSURE, io_pressure=MAX_IO_PRESSURE):
    """Reason string when optional work should wait, otherwise None. Unknown readings never block."""
    snap = snapshot or reading()
    avail, mem, io = snap.get('available_mib'), snap.get('memory_pressure'), snap.get('io_pressure')
    if avail is not None and avail < min_available:
        return f'{avail} MiB available, need {min_available}'
    if mem is not None and mem > memory_pressure:
        return f'memory pressure {mem}% over the last minute'
    if io is not None and io > io_pressure:
        return f'disk I/O pressure {io}% over the last minute'
    return None


def wait_calm(max_wait, poll=15, read=None, sleep=time.sleep, clock=time.monotonic, **limits):
    """Block until busy() clears; returns None when calm, or the last reason after max_wait seconds."""
    deadline = clock() + max_wait
    while True:
        reason = busy((read or reading)(), **limits)
        if reason is None or clock() >= deadline:
            return reason
        sleep(poll)
