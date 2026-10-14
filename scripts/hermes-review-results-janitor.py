#!/usr/bin/env python3
"""Remove only day-old specialist result files from the private review directory."""
import os
from pathlib import Path
import re
import stat
import time

RESULTS_ROOT=Path.home()/'.local/share/ai-pr-automation/pr-review-results'
RESULT_NAME=re.compile(r't_[0-9a-f]{8}-(?:reliability|mvp|security)\.json\Z')


def sweep(now=None):
    try:info=RESULTS_ROOT.lstat()
    except FileNotFoundError:return 0
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid()
            or stat.S_IMODE(info.st_mode)!=0o700):
        raise ValueError('review results directory must be private')
    cutoff=(time.time() if now is None else now)-86400
    removed=0
    fd=os.open(RESULTS_ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        with os.scandir(fd) as entries:
            for entry in entries:
                if not RESULT_NAME.fullmatch(entry.name):continue
                file_info=entry.stat(follow_symlinks=False)
                # The private parent also protects a file left at the native writer's default 0644.
                if (not stat.S_ISREG(file_info.st_mode) or file_info.st_uid!=os.geteuid()
                        or file_info.st_nlink!=1 or stat.S_IMODE(file_info.st_mode) not in (0o600,0o644)
                        or file_info.st_mtime>=cutoff):
                    continue
                os.unlink(entry.name,dir_fd=fd)
                removed+=1
    finally:os.close(fd)
    return removed


if __name__=='__main__':
    print(f'removed {sweep()} expired review result files')
