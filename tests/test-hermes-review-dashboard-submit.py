#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('review_dashboard_submit',ROOT/'scripts/hermes-review-dashboard-submit.py')
client=importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class SubmitTest(unittest.TestCase):
    def test_exact_head_cache_and_legacy_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();cache=root/'state';cache.mkdir(mode=0o700)
            exclusion=root/'excluded.json';exclusion.write_text('[]')
            repo='example/widget';head='a'*40
            operation=client.identity('pr-review',repo,7,head)['operation_id']
            env={'PR_REVIEW_RECENT_FILE':str(cache/'recent.json'),
                 'PR_REVIEW_LEGACY_OWNERS_FILE':str(exclusion),
                 'PR_REVIEW_WORK_ROOT':'/Users/example/.local/share/ai-pr-automation/pr-kanban'}
            class Dashboard:
                created=[]
                def create_task(self,board,task):
                    self.created.append((board,task))
                    return {'id':'t_12345678',**task}
                def find_tasks(self,*_):return []
            dashboard=Dashboard()
            with mock.patch.dict(os.environ,env),mock.patch.object(client,'Client',return_value=dashboard):
                first=client.submit(repo,7,'Synthetic',head)
                self.assertEqual((first['status'],first['task_id']),('ready','t_12345678'))
                self.assertEqual(client.submit(repo,7,'Synthetic',head)['status'],'cached')
                self.assertEqual(len(dashboard.created),1)
                board,task=dashboard.created[0]
                self.assertEqual((board,task['assignee'],task['model_override'],task['idempotency_key']),
                                 ('pr-review','pr-review-v1','gpt-6-sol',operation))
                self.assertEqual(task['workspace_path'],env['PR_REVIEW_WORK_ROOT']+'/'+operation)
                self.assertEqual(json.loads(task['body'])['head_sha'],head)
                other='b'*40;old=client.identity('pr-review',repo,7,other)['operation_id']
                exclusion.write_text(json.dumps([old]))
                self.assertEqual(client.submit(repo,7,'Synthetic',other)['status'],'excluded')
                exclusion.write_text('[]')
                self.assertEqual(client.submit(repo,7,'Synthetic',other)['status'],'ready')
                self.assertEqual(len(dashboard.created),2)

    def test_prior_uncertain_create_reads_back_without_another_post(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();state=root/'state';state.mkdir(mode=0o700)
            excluded=root/'excluded.json';excluded.write_text('[]')
            repo='example/widget';head='a'*40
            operation=client.identity('pr-review',repo,7,head)['operation_id']
            cache=client.RecentRequests(state/'recent.json')
            def unknown():raise client.AmbiguousCreate('lost response')
            with self.assertRaises(client.AmbiguousCreate):cache.submit(operation,unknown)
            class Dashboard:
                lookups=0
                def create_task(self,*_):raise AssertionError('uncertain create must not post again')
                def find_tasks(self,*_):self.lookups+=1;return getattr(self,'card',[])
            dashboard=Dashboard()
            env={'PR_REVIEW_RECENT_FILE':str(state/'recent.json'),
                 'PR_REVIEW_LEGACY_OWNERS_FILE':str(excluded),
                 'PR_REVIEW_WORK_ROOT':'/Users/example/.local/share/ai-pr-automation/pr-kanban'}
            with mock.patch.dict(os.environ,env),mock.patch.object(client,'Client',return_value=dashboard):
                with self.assertRaisesRegex(ValueError,'review card creation is uncertain'):
                    client.submit(repo,7,'Synthetic',head)
            self.assertEqual(dashboard.lookups,1)
            dashboard.card=[{'id':'t_12345678','title':f'{repo}#7 @ {head[:8]}',
                             'body':json.dumps({'operation_id':operation,'repo':repo,'number':7,
                                                'url':f'https://github.com/{repo}/pull/7',
                                                'head_sha':head,'title':'Synthetic'},
                                               sort_keys=True,separators=(',',':')),
                             'assignee':'pr-review-v1','tenant':operation,'idempotency_key':operation,
                             'workspace_kind':'dir','workspace_path':env['PR_REVIEW_WORK_ROOT']+'/'+operation,
                             'model_override':'gpt-6-sol','provider_override':'openai-codex',
                             'max_runtime_seconds':1800}]
            with mock.patch.dict(os.environ,env),mock.patch.object(client,'Client',return_value=dashboard):
                self.assertEqual(client.submit(repo,7,'Synthetic',head)['status'],'ready')
                self.assertEqual(client.submit(repo,7,'Synthetic',head)['status'],'cached')
            self.assertEqual(dashboard.lookups,2)


if __name__=='__main__':unittest.main()
