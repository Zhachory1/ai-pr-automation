#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('review_dashboard_submit',ROOT/'scripts/hermes-review-dashboard-submit.py')
client=importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class SubmitTest(unittest.TestCase):
    def test_board_key_reuses_active_card_and_archived_card_can_be_recreated(self):
        repo='example/widget';head='a'*40;base='c'*40
        operation=client.identity('pr-review',repo,7,head)['operation_id']
        env={'PR_REVIEW_WORK_ROOT':'/Users/example/.local/share/ai-pr-automation/pr-kanban'}
        class Dashboard:
            def __init__(self):self.active={};self.created=[];self.posts=0
            def create_task(self,board,task):
                self.posts+=1
                if task['idempotency_key'] in self.active:return self.active[task['idempotency_key']]
                card={'id':f't_{len(self.created)+1:08x}',**task,'status':'ready'}
                self.created.append((board,task));self.active[task['idempotency_key']]=card
                return card
            def find_tasks(self,*_):return []
        dashboard=Dashboard()
        with mock.patch.dict(os.environ,env),mock.patch.object(client,'Client',return_value=dashboard):
            first=client.submit(repo,7,'Synthetic',head,base)
            self.assertEqual((first['status'],first['task_id']),('ready','t_00000001'))
            dashboard.active[operation]['status']='running'
            second=client.submit(repo,7,'Synthetic',head,base)
            self.assertEqual((second['status'],second['task_id']),('existing','t_00000001'))
            self.assertEqual((dashboard.posts,len(dashboard.created)),(2,1))
            board,task=dashboard.created[0]
            self.assertEqual((board,task['assignee'],task['model_override'],task['idempotency_key']),
                             ('pr-review','pr-review-v1','gpt-6-sol',operation))
            self.assertEqual(task['workspace_path'],env['PR_REVIEW_WORK_ROOT']+'/'+operation)
            self.assertEqual((json.loads(task['body'])['head_sha'],json.loads(task['body'])['base_sha']),
                             (head,base))
            del dashboard.active[operation]
            self.assertEqual(client.submit(repo,7,'Synthetic',head,base)['task_id'],'t_00000002')
            other='b'*40
            self.assertEqual(client.submit(repo,7,'Synthetic',other,base)['task_id'],'t_00000003')

    def test_terminal_task_response_is_not_a_new_admission(self):
        class Dashboard:
            def __init__(self,status):self.status=status
            def create_task(self,board,task):return {'id':'t_12345678',**task,'status':self.status}
        with mock.patch.dict(os.environ,{'PR_REVIEW_WORK_ROOT':'/Users/example/.local/share/ai-pr-automation/pr-kanban'}):
            with mock.patch.object(client,'Client',return_value=Dashboard('archived')):
                with self.assertRaisesRegex(ValueError,'archived'):
                    client.submit('example/widget',7,'Synthetic','a'*40,'c'*40)
            for status in ('done','blocked'):
                with mock.patch.object(client,'Client',return_value=Dashboard(status)):
                    self.assertEqual(client.submit('example/widget',7,'Synthetic','a'*40,'c'*40)['status'],'existing')

    def test_uncertain_create_reads_back_without_blind_retry(self):
        env={'PR_REVIEW_WORK_ROOT':'/Users/example/.local/share/ai-pr-automation/pr-kanban'}
        class Dashboard:
            def __init__(self):self.posts=0;self.lookups=0;self.matches=[]
            def create_task(self,board,task):
                self.posts+=1;self.task=task
                raise client.AmbiguousCreate('lost response')
            def find_tasks(self,*_):self.lookups+=1;return self.matches
        dashboard=Dashboard()
        with mock.patch.dict(os.environ,env),mock.patch.object(client,'Client',return_value=dashboard):
            with self.assertRaisesRegex(ValueError,'review card creation is uncertain'):
                client.submit('example/widget',7,'Synthetic','a'*40,'c'*40)
            self.assertEqual((dashboard.posts,dashboard.lookups),(1,1))
            dashboard.matches=[{'id':'t_12345678',**dashboard.task,'status':'archived'}]
            with self.assertRaisesRegex(ValueError,'review card creation is uncertain'):
                client.submit('example/widget',7,'Synthetic','a'*40,'c'*40)
            dashboard.matches=[{'id':'t_12345678',**dashboard.task,'status':'ready'}]
            self.assertEqual(client.submit('example/widget',7,'Synthetic','a'*40,'c'*40)['task_id'],'t_12345678')
            self.assertEqual((dashboard.posts,dashboard.lookups),(3,3))


if __name__=='__main__':unittest.main()
