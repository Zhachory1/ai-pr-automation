#!/usr/bin/env python3
"""Admit one exact PR head to native Kanban without a host ingress broker."""
import json
import os
from pathlib import Path
import re
import stat
import sys

from hermes_direct_pr_journal import identity
from hermes_kanban_http import AmbiguousCreate, Client, NoRequestSent, RecentRequests, task_matches


def _excluded(operation):
    path=Path(os.environ['PR_REVIEW_LEGACY_OWNERS_FILE'])
    info=path.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size>8192
            or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o022):
        raise ValueError('unsafe old-council exclusion list')
    try:owners=json.loads(path.read_text())
    except (UnicodeError,ValueError) as error:raise ValueError('invalid old-council exclusion list') from error
    if (not isinstance(owners,list)
            or any(not isinstance(item,str) or not re.fullmatch(r'pr-review-[0-9a-f]{64}',item)
                   for item in owners) or len(owners)!=len(set(owners))):
        raise ValueError('invalid old-council exclusion list')
    return operation in owners


def submit(repo,number,title,head):
    resolved=identity('pr-review',repo,number,head)
    operation=resolved['operation_id']
    if not isinstance(title,str) or not 0<len(title)<=256:
        raise ValueError('invalid PR title')
    if _excluded(operation):return {'status':'excluded','operation_id':operation,'task_id':None}
    root=Path(os.environ['PR_REVIEW_WORK_ROOT'])
    if not root.is_absolute() or root.name!='pr-kanban':
        raise ValueError('invalid host review work root')
    request={'operation_id':operation,'repo':repo,'number':number,
             'url':f'https://github.com/{repo}/pull/{number}',
             'title':title,'head_sha':head}
    body=json.dumps(request,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    task={'title':f'{repo}#{number} @ {head[:8]}','body':body,'assignee':'pr-review-v1',
          'tenant':operation,'idempotency_key':operation,'workspace_kind':'dir',
          'workspace_path':str(root/operation),'model_override':'gpt-6-sol',
          'provider_override':'openai-codex','max_runtime_seconds':1800}
    dashboard=Client()
    cache=RecentRequests(Path(os.environ['PR_REVIEW_RECENT_FILE']))
    try:
        card=cache.submit(operation,lambda:dashboard.create_task('pr-review',task))
    except NoRequestSent:
        raise
    except ValueError as error:
        if not isinstance(error,AmbiguousCreate) and str(error)!='request outcome requires reconciliation':
            raise
        found=dashboard.find_tasks('pr-review',operation,operation)
        if len(found)!=1 or not task_matches(found[0],task):
            raise ValueError('review card creation is uncertain; reconcile before retry') from None
        cache.confirm(operation,found[0],task)
        card=found[0]
    if card is None:return {'status':'cached','operation_id':operation,'task_id':None}
    return {'status':'ready','operation_id':operation,'task_id':card['id']}


def main():
    if len(sys.argv)!=5:raise ValueError('usage: review-dashboard-submit REPO NUMBER TITLE HEAD')
    print(json.dumps(submit(sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4]),
                     sort_keys=True,separators=(',',':')))


if __name__=='__main__':
    try:main()
    except (OSError,TypeError,ValueError,KeyError) as error:
        raise SystemExit(f'Direct review submission failed: {error}') from None
