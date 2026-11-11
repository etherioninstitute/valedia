#!/usr/bin/env python3
"""Per-job admission; inherited flock protects a running pipeline after parent SIGKILL."""
import fcntl
import os
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit('wrapper required')
    state = os.path.join(os.environ['HOME'], '.openclaw', 'state')
    os.makedirs(state, exist_ok=True)
    fd = os.open(os.path.join(state, 'mirofish_primary.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('MiroFish already running; no fallback or completion marker', file=sys.stderr)
        return 75
    env = os.environ.copy()
    env['MIROFISH_LOCKED'] = '1'
    env['MIROFISH_LOCK_FD'] = str(fd)
    # pass_fds makes the lock survive exec into bash and its pipeline descendants.
    child = subprocess.Popen(['/bin/bash', *sys.argv[1:]], env=env, pass_fds=(fd,))
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.terminate()
        return child.wait()
    finally:
        os.close(fd)


if __name__ == '__main__':
    sys.exit(main())
