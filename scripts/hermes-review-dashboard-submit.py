#!/usr/bin/env python3
"""Admit one exact PR head to native Kanban without a host ingress broker."""
import json
import os
from pathlib import Path
import re
import sys

from hermes_direct_pr_journal import identity
from hermes_kanban_http import AmbiguousCreate, Client, task_matches


def submit(repo,number,title,head,base):
    resolved=identity('pr-review',repo,number,head)
    operation=resolved['operation_id']
    if not isinstance(title,str) or not 0<len(title)<=256:
        raise ValueError('invalid PR title')
    if not isinstance(base,str) or not re.fullmatch(r'[0-9a-f]{40}',base):
        raise ValueError('invalid PR base SHA')
    root=Path(os.environ['PR_REVIEW_WORK_ROOT'])
    if not root.is_absolute() or root.name!='pr-kanban':
        raise ValueError('invalid host review work root')
    request={'operation_id':operation,'repo':repo,'number':number,
             'url':f'https://github.com/{repo}/pull/{number}',
             'title':title,'head_sha':head,'base_sha':base}
    body=json.dumps(request,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    task={'title':f'{repo}#{number} @ {head[:8]}','body':body,'assignee':'pr-review-v1',
          'tenant':operation,'idempotency_key':operation,'workspace_kind':'dir',
          'workspace_path':str(root/operation),'model_override':'gpt-6-sol',
          'provider_override':'openai-codex','max_runtime_seconds':1800}
    dashboard=Client()
    try:
        card=dashboard.create_task('pr-review',task)
    except AmbiguousCreate:
        found=[card for card in dashboard.find_tasks('pr-review',operation,operation)
               if card.get('status')!='archived']
        if len(found)!=1 or not task_matches(found[0],task):
            raise ValueError('review card creation is uncertain; reconcile before retry') from None
        card=found[0]
    status=card.get('status')
    if status=='archived':raise ValueError('archived review task cannot be admitted')
    if status not in {'triage','todo','scheduled','ready','running','blocked','review','done'}:
        raise ValueError('invalid review task status')
    return {'status':'ready' if status=='ready' else 'existing',
            'operation_id':operation,'task_id':card['id']}


def main():
    if len(sys.argv)!=6:raise ValueError('usage: review-dashboard-submit REPO NUMBER TITLE HEAD BASE')
    print(json.dumps(submit(sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4],sys.argv[5]),
                     sort_keys=True,separators=(',',':')))


if __name__=='__main__':
    try:main()
    except (OSError,TypeError,ValueError,KeyError) as error:
        raise SystemExit(f'Direct review submission failed: {error}') from None
