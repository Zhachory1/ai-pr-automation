#!/usr/bin/env python3
from contextlib import nullcontext
from datetime import date, datetime, timezone
import json
import pathlib
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import hermes_inbox_daily as daily
import hermes_inbox_daily_run as run


class DailyRunTest(unittest.TestCase):
    def setUp(self):
        self.board=sqlite3.connect(':memory:');self.addCleanup(self.board.close)
        self.board.row_factory=sqlite3.Row
        self.board.execute('CREATE TABLE tasks(id TEXT,body TEXT,status TEXT,assignee TEXT,created_by TEXT,created_at INTEGER)')
        self.day=date(2026,10,8)
        def card(task_id,status,assignee,source_day,preview=False):
            body={'source_day':source_day,'intent_id':task_id,'account':'zhachory1@gmail.com'} if preview else {'source_day':source_day,'route':'job','account':'zhachory1@gmail.com'}
            self.board.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',
                               (task_id,json.dumps(body),status,assignee,'inbox-intake',1))
        card('t_new','blocked','inbox-sol','2026-10-08')
        card('t_existing','blocked',None,'2026-10-08',True)
        card('t_archived','archived',None,'2026-10-08',True)
        self.board.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',
                           ('t_legacy',json.dumps({'intent_id':'t_legacy'}),'blocked',None,'inbox-intake',1))
        card('t_old','blocked','inbox-sol','2026-10-07')
        self.board.commit()
        self.clients={'zhachory1@gmail.com':(object(),object())}

    def test_drains_prior_admitted_cards_but_never_unarchives(self):
        drafted=[];created=[]
        def stage(board,_ledger,task_id,*_args,**_kwargs):
            drafted.append(task_id)
            original=json.loads(board.execute('SELECT body FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
            board.execute('UPDATE tasks SET assignee=NULL, body=? WHERE id=?',
                          (json.dumps({'source_day':original['source_day'],'intent_id':task_id,'account':original['account']}),task_id))
            board.commit()
        with (mock.patch.object(run.controller,'process',side_effect=stage),
              mock.patch.object(run.publisher,'create',side_effect=lambda board,ledger,task,reader,writer:created.append(task))):
            self.assertEqual(run.run_cards(self.board,pathlib.Path('/fake/drafts.sqlite'),self.clients,
                                           '/fake/hermes',pathlib.Path('/tmp'),self.day),(2,3,0))
        self.assertEqual(set(drafted),{'t_new','t_old'})
        self.assertEqual(set(created),{'t_new','t_existing','t_old'})
        self.assertEqual(self.board.execute("SELECT status FROM tasks WHERE id='t_archived'").fetchone()[0],'archived')

    def test_cards_use_the_source_accounts_reader_and_draft_token(self):
        self.board.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',
                           ('t_second',json.dumps({'source_day':'2026-10-08','account':'zhackymoto@gmail.com',
                                                   'route':'help'}),'blocked','inbox-sol','inbox-intake',2))
        self.board.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',
                           ('t_second_draft',json.dumps({'source_day':'2026-10-08',
                                                         'account':'zhackymoto@gmail.com',
                                                         'intent_id':'t_second_draft'}),
                            'blocked',None,'inbox-intake',2))
        self.board.commit()
        second=(object(),object())
        self.clients['zhackymoto@gmail.com']=second
        staged=[];created=[]
        def stage(board,_ledger,task_id,reader,*_args):
            staged.append((task_id,reader))
        def publish(board,_ledger,task_id,reader,drafts):
            created.append((task_id,reader,drafts))
        with (mock.patch.object(run.controller,'process',side_effect=stage),
              mock.patch.object(run.publisher,'create',side_effect=publish)):
            self.assertEqual(run.run_cards(self.board,pathlib.Path('/fake/drafts.sqlite'),self.clients,
                                           '/fake/hermes',pathlib.Path('/tmp'),self.day),(3,2,0))
        self.assertIn(('t_second',second[0]),staged)
        self.assertIn(('t_existing',*self.clients['zhachory1@gmail.com']),created)
        self.assertIn(('t_second_draft',*second),created)

    def test_unconfigured_card_cannot_use_another_accounts_credentials(self):
        self.board.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',
                           ('t_unknown',json.dumps({'source_day':'2026-10-08','account':'unknown@gmail.com',
                                                    'route':'help'}),'blocked','inbox-sol','inbox-intake',2))
        self.board.commit()
        with (mock.patch.object(run.controller,'process') as stage,
              mock.patch.object(run.publisher,'create')):
            result=run.run_cards(self.board,pathlib.Path('/fake/drafts.sqlite'),self.clients,
                                 '/fake/hermes',pathlib.Path('/tmp'),self.day)
        self.assertEqual(result[2],1)
        self.assertNotIn('t_unknown',[call.args[2] for call in stage.call_args_list])

    def test_orchestration_lock_refuses_overlap_before_gmail(self):
        with tempfile.TemporaryDirectory() as root:
            with daily.lock_manifest(pathlib.Path(root)/'runner'):
                with self.assertRaises(RuntimeError):
                    run.run_today(pathlib.Path(root),'/fake/hermes',
                                  datetime(2026,10,9,12,tzinfo=timezone.utc))

    def test_overlap_does_not_emit_false_failure_notice(self):
        with tempfile.TemporaryDirectory() as root:
            with (daily.lock_manifest(pathlib.Path(root)/'runner'),
                  mock.patch.object(run.daily,'kanban_notice') as notice):
                self.assertIsNone(run.main(pathlib.Path(root),'/fake/hermes',
                                            datetime(2026,10,9,12,tzinfo=timezone.utc)))
            notice.assert_not_called()
            self.assertFalse((pathlib.Path(root)/'last-run.json').exists())

    def test_malformed_card_does_not_stop_other_cards(self):
        self.board.execute("INSERT INTO tasks VALUES ('t_bad','not-json','blocked','inbox-sol','inbox-intake',1)")
        self.board.commit()
        drafted=[];created=[]
        def stage(board,_ledger,task_id,*_args,**_kwargs):
            drafted.append(task_id)
            source=json.loads(board.execute('SELECT body FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
            board.execute('UPDATE tasks SET assignee=NULL,body=? WHERE id=?',
                          (json.dumps({'source_day':source['source_day'],'intent_id':task_id,'account':source['account']}),task_id))
            board.commit()
        with (mock.patch.object(run.controller,'process',side_effect=stage),
              mock.patch.object(run.publisher,'create',side_effect=lambda board,ledger,task,reader,writer:created.append(task))):
            result=run.run_cards(self.board,pathlib.Path('/fake/drafts.sqlite'),self.clients,
                                 '/fake/hermes',pathlib.Path('/tmp'),self.day)
        self.assertEqual(result,(2,3,1))
        self.assertNotIn('t_bad',drafted)
        self.assertEqual(len(created),3)

    def test_deadline_leaves_cards_untouched_and_reports_incomplete(self):
        with (mock.patch.object(run.controller,'process') as stage,
              mock.patch.object(run.publisher,'create') as draft):
            result=run.run_cards(self.board,pathlib.Path('/fake/drafts.sqlite'),self.clients,
                                 '/fake/hermes',pathlib.Path('/tmp'),self.day,deadline=0)
        self.assertEqual(result[:2],(0,0))
        self.assertGreater(result[2],0)
        stage.assert_not_called();draft.assert_not_called()

    def test_second_inbox_is_opt_in_and_starts_with_yesterday(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)/'inbox'
            root.mkdir(mode=0o700)
            with daily.open_manifest(root/'manifest.sqlite') as state:
                state.execute("INSERT INTO account_binding VALUES (1,'zhachory1@gmail.com')")
                state.execute("INSERT INTO days(day,status) VALUES ('2026-10-01','complete')")
                state.commit()
            home=pathlib.Path(tmp)
            (home/'.hermes').mkdir()
            (home/'.hermes/config.yaml').write_text('{}')
            board=object()
            board_db=types.SimpleNamespace(connect_closing=lambda **_kw:nullcontext(board))
            fail_token=fail_intake=False
            calls=[]
            def connect(path):
                if fail_token and path.parent.name=='zhackymoto@gmail.com':
                    raise ValueError('synthetic second credential failure')
                return types.SimpleNamespace(profile=lambda:{'emailAddress':
                    'zhackymoto@gmail.com' if path.parent.name=='zhackymoto@gmail.com'
                    else 'zhachory1@gmail.com'})
            def intake_run(_manifest,_reader,_binary,account,*_args,**_kwargs):
                calls.append(('intake',account))
                if fail_intake and account=='zhackymoto@gmail.com':
                    raise ValueError('synthetic second scan failure')
                return 1
            def drain(_board,_ledger,clients,*_args,**_kwargs):
                calls.append(('cards',tuple(clients)))
                return 0,0,0
            now=datetime(2026,10,9,12,tzinfo=timezone.utc)
            def execute():
                with (mock.patch.dict(sys.modules,{'hermes_cli':types.SimpleNamespace(kanban_db_connect=board_db)}),
                      mock.patch.object(run.Path,'home',return_value=home),
                      mock.patch.object(run.Path,'resolve',return_value=pathlib.Path('/America/New_York')),
                      mock.patch.object(run.gmail,'connect',side_effect=connect),
                      mock.patch.object(run.draft_api,'connect',side_effect=connect),
                      mock.patch.object(run.publisher,'recover_started',return_value=0),
                      mock.patch.object(run.pipeline,'run_once',side_effect=intake_run) as intake,
                      mock.patch.object(run,'run_cards',side_effect=drain) as cards):
                    run._run_locked(root,'/fake/hermes',now)
                    return intake.call_args_list,cards.call_args_list
            intake,cards=execute()
            self.assertEqual(len(intake),1)
            self.assertEqual(len(cards[0].args[2]),1)
            extra=root/'zhackymoto@gmail.com'
            extra.mkdir(mode=0o700)
            intake,cards=execute()
            self.assertEqual(len(intake),1)
            (extra/'enabled').touch(mode=0o600)
            intake,cards=execute()
            self.assertEqual(len(intake),2)
            self.assertEqual(intake[0].args[0],root/'manifest.sqlite')
            self.assertEqual(intake[1].args[0],extra/'manifest.sqlite')
            self.assertEqual(intake[1].args[3],'zhackymoto@gmail.com')
            self.assertEqual(intake[1].args[4],date(2026,10,8))
            self.assertEqual(len(cards[0].args[2]),2)
            fail_token=True
            calls.clear()
            with self.assertRaisesRegex(RuntimeError,'inbox account intake'):
                execute()
            self.assertEqual(calls,[('intake','zhachory1@gmail.com'),
                                    ('cards',('zhachory1@gmail.com',))])
            fail_token=False
            fail_intake=True
            calls.clear()
            with self.assertRaisesRegex(RuntimeError,'inbox account intake'):
                execute()
            self.assertEqual(calls,[('intake','zhachory1@gmail.com'),
                                    ('intake','zhackymoto@gmail.com'),
                                    ('cards',('zhachory1@gmail.com','zhackymoto@gmail.com'))])

    def test_startup_failure_records_status_and_tries_board_notice(self):
        with tempfile.TemporaryDirectory() as root:
            day=datetime(2026,10,9,12,tzinfo=timezone.utc)
            with (mock.patch.object(run,'run_today',side_effect=ValueError('synthetic missing token')),
                  mock.patch.object(run.daily,'kanban_notice') as notice):
                with self.assertRaises(SystemExit):
                    run.main(pathlib.Path(root),'/fake/hermes',day)
            status=json.loads((pathlib.Path(root)/'last-run.json').read_text())
            self.assertEqual(status['status'],'failed')
            self.assertEqual(status['day'],'2026-10-08')
            notice.assert_called_once_with('/fake/hermes','2026-10-08','runner-error')

    def test_board_notice_failure_keeps_marker_and_attempts_local_notification(self):
        with tempfile.TemporaryDirectory() as root:
            with (mock.patch.object(run,'run_today',side_effect=ValueError('synthetic failure')),
                  mock.patch.object(run.daily,'kanban_notice',side_effect=RuntimeError('board offline')),
                  mock.patch.object(run.subprocess,'run') as notification):
                with self.assertRaises(SystemExit):
                    run.main(pathlib.Path(root),'/fake/hermes',
                             datetime(2026,10,9,12,tzinfo=timezone.utc))
            self.assertEqual(json.loads((pathlib.Path(root)/'last-run.json').read_text())['status'],'failed')
            self.assertEqual(notification.call_args.args[0][0],'/usr/bin/osascript')

    def test_one_failed_card_does_not_strand_other_cards(self):
        drafted=[];created=[]
        def stage(board,_ledger,task_id,*_args,**_kwargs):
            if task_id=='t_new':raise ValueError('synthetic card failure')
            drafted.append(task_id)
            board.execute('UPDATE tasks SET assignee=NULL, body=? WHERE id=?',
                          (json.dumps({'source_day':'2026-10-07','intent_id':task_id,'account':'zhachory1@gmail.com'}),task_id))
            board.commit()
        with (mock.patch.object(run.controller,'process',side_effect=stage),
              mock.patch.object(run.publisher,'create',side_effect=lambda board,ledger,task,reader,writer:created.append(task))):
            self.assertEqual(run.run_cards(self.board,pathlib.Path('/fake/drafts.sqlite'),self.clients,
                                           '/fake/hermes',pathlib.Path('/tmp'),self.day),(1,2,1))
        self.assertEqual(drafted,['t_old'])
        self.assertEqual(set(created),{'t_old','t_existing'})


if __name__=='__main__':unittest.main()
