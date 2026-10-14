#!/usr/bin/env python3
import json
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hermes_kanban_http as transport


class Dashboard(BaseHTTPRequestHandler):
    token = 'a' * 32
    requests = []
    tasks = []
    changed_title = False
    changed_model = False
    reject_after_create = False
    redirect_root = False
    redirects = 0
    drop_get = False
    drop_post = False
    root_gets = 0
    post_attempts = 0
    def log_message(self, *_args): pass
    def do_GET(self):
        if self.path == '/' and self.redirect_root:
            self.send_response(302);self.send_header('Location','/redirect-target');self.end_headers();return
        if self.path == '/redirect-target':
            type(self).redirects+=1;self.send_error(404);return
        if self.path == '/':
            type(self).root_gets+=1
            if self.drop_get:
                self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            body=('<script>window.__HERMES_SESSION_TOKEN__="'+self.token+'";</script>').encode()
        elif self.path.startswith('/api/plugins/kanban/board?'):
            if self.headers.get('X-Hermes-Session-Token') != self.token:
                self.send_error(401);return
            path,query=self.path.split('?',1);values=urllib.parse.parse_qs(query)
            if (path!='/api/plugins/kanban/board' or values.get('board')!=['pr-review']
                    or values.get('include_archived')!=['true'] or len(values.get('tenant',[]))!=1
                    or set(values)!={'board','tenant','include_archived'}):
                self.send_error(400);return
            tasks=[task for task in self.tasks if task['tenant']==values['tenant'][0]]
            body=json.dumps({'columns':[{'name':'archived','tasks':tasks},
                                         {'name':'done','tasks':[]}] }).encode()
        else:
            self.send_error(404);return
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        type(self).post_attempts+=1
        if self.headers.get('X-Hermes-Session-Token') != self.token:
            self.send_error(401);return
        self.requests.append((self.path,self.headers.get('Host'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
        payload=self.requests[-1][2]
        body=json.dumps({'task':{'id':'t_12345678','idempotency_key':payload['idempotency_key'],
                                 'title':'wrong title' if self.changed_title else payload['title'],
                                 'assignee':payload['assignee'],'body':payload['body'],
                                 'tenant':payload['tenant'],'status':'ready',
                                 'model_override':'other-model' if self.changed_model else payload.get('model_override'),
                                 'provider_override':payload.get('provider_override'),
                                 'workspace_kind':payload.get('workspace_kind','scratch'),
                                 'workspace_path':payload.get('workspace_path'),
                                 'max_runtime_seconds':payload.get('max_runtime_seconds')}}).encode()
        self.tasks.append(json.loads(body)['task'])
        if self.reject_after_create:
            self.send_error(400);return
        if self.drop_post:
            self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)


class HTTPClientTest(unittest.TestCase):
    def setUp(self):
        Dashboard.requests=[];Dashboard.tasks=[];Dashboard.token='a'*32
        Dashboard.changed_title=False;Dashboard.changed_model=False;Dashboard.reject_after_create=False
        Dashboard.redirect_root=False;Dashboard.redirects=0
        Dashboard.drop_get=False;Dashboard.drop_post=False;Dashboard.root_gets=0;Dashboard.post_attempts=0
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Dashboard)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join);self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.client=transport.Client(f'http://127.0.0.1:{self.server.server_port}')
    def test_auto_token_and_exact_task_result(self):
        task={'title':'Synthetic PR','body':'{}','assignee':'pr-review-v1','tenant':'pr-review-'+'a'*64,
              'idempotency_key':'pr-review-'+'a'*64}
        result=self.client.create_task('pr-review',task)
        self.assertEqual(result['id'],'t_12345678')
        self.assertEqual(Dashboard.requests,[(f'/api/plugins/kanban/tasks?board=pr-review',
                                              f'127.0.0.1:{self.server.server_port}',task)])
        Dashboard.token='b'*32
        self.assertEqual(self.client.create_task('pr-review',task)['id'],'t_12345678')
        self.assertEqual(len(Dashboard.requests),2)
        self.assertEqual((Dashboard.root_gets,Dashboard.post_attempts),(2,3))

    def test_binding_drift_and_redirect_fail_closed(self):
        key='pr-review-'+'a'*64
        task={'title':'Synthetic PR','body':'{}','assignee':'pr-review-v1','tenant':key,
              'idempotency_key':key}
        Dashboard.changed_title=True
        with self.assertRaises(transport.AmbiguousCreate):self.client.create_task('pr-review',task)
        Dashboard.changed_title=False;Dashboard.changed_model=True
        with self.assertRaises(transport.AmbiguousCreate):self.client.create_task('pr-review',task)
        Dashboard.changed_model=False;Dashboard.redirect_root=True;self.client.token=None
        with self.assertRaises(transport.NoRequestSent):self.client.create_task('pr-review',task)
        self.assertEqual(Dashboard.redirects,0)

    def test_invalid_task_fields_never_reach_dashboard(self):
        key='pr-review-'+'a'*64
        task={'title':'Synthetic PR','body':'{}','assignee':'pr-review-v1','tenant':key,
              'idempotency_key':key}
        for invalid in ({**task,'unexpected':True},{**task,'title':123},
                        {k:v for k,v in task.items() if k!='assignee'},
                        {**task,'model_override':None},{**task,'provider_override':'openai-codex'},
                        {**task,'workspace_kind':'worktree'},{**task,'max_runtime_seconds':True},
                        {**task,'max_runtime_seconds':1.0}):
            with self.assertRaises(transport.NoRequestSent):self.client.create_task('pr-review',invalid)
        self.assertEqual((Dashboard.root_gets,Dashboard.post_attempts),(0,0))

    def test_missing_task_readback_is_not_proof_of_rejected_post(self):
        key='pr-review-'+'a'*64
        self.assertEqual(self.client.find_tasks('pr-review',key,key),[])
        self.assertEqual((Dashboard.root_gets,Dashboard.post_attempts),(1,0))

    def test_archived_task_readback_does_not_hide_duplicate_keys(self):
        key='pr-review-'+'a'*64
        task={'title':'Synthetic PR','body':'{}','assignee':'pr-review-v1','tenant':key,
              'idempotency_key':key}
        self.client.create_task('pr-review',task)
        Dashboard.tasks[0]['status']='archived';Dashboard.token='b'*32
        self.assertEqual(len(self.client.find_tasks('pr-review',key,key)),1)
        self.assertEqual(Dashboard.root_gets,2)
        Dashboard.tasks.append({**Dashboard.tasks[0],'id':'t_deadbeef'})
        with self.assertRaisesRegex(transport.AmbiguousCreate,'multiple'):
            self.client.find_tasks('pr-review',key,key)

    def test_overlapping_cron_processes_share_one_persisted_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'recent.json';events=Path(directory)/'created'
            script='''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from hermes_kanban_http import RecentRequests
def send():
 with open(sys.argv[3],'a') as out:out.write('sent\\n')
 time.sleep(.1)
 return {'id':'t_12345678'}
print(bool(RecentRequests(Path(sys.argv[2])).submit('pr-review-'+'a'*64,send,now=1000)))
'''
            command=[sys.executable,'-B','-c',script,str(Path(__file__).resolve().parents[1]/'scripts'),
                     str(path),str(events)]
            processes=[subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                       for _ in range(2)]
            results=[process.communicate(timeout=5) for process in processes]
            self.assertTrue(all(process.returncode==0 for process in processes),results)
            self.assertEqual(sorted(output.strip() for output,_ in results),['False','True'])
            self.assertEqual(events.read_text().splitlines(),['sent'])

    def test_http_400_after_create_is_ambiguous_not_a_safe_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            key='pr-review-'+'a'*64;task={'title':'Synthetic PR','body':'{}',
                 'assignee':'pr-review-v1','tenant':key,'idempotency_key':key}
            cache=transport.RecentRequests(Path(directory)/'recent.json')
            Dashboard.reject_after_create=True
            with self.assertRaises(transport.AmbiguousCreate):
                cache.submit(key,lambda:self.client.create_task('pr-review',task),now=1000)
            self.assertEqual((len(Dashboard.tasks),Dashboard.post_attempts),(1,1))
            with self.assertRaisesRegex(ValueError,'reconciliation'):
                cache.submit(key,lambda:self.client.create_task('pr-review',task),now=1000+4*86400)
            self.assertEqual(Dashboard.post_attempts,1)

    def test_cache_rejects_writable_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            parent=Path(directory)
            try:
                for mode in (0o755,0o777):
                    parent.chmod(mode)
                    with self.assertRaisesRegex(ValueError,'owner-only'):
                        transport.RecentRequests(parent/'recent.json')
            finally:parent.chmod(0o700)

    def test_preflight_failure_does_not_suppress_next_cron(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'recent.json';key='pr-review-'+'a'*64
            task={'title':'Synthetic PR','body':'{}','assignee':'pr-review-v1','tenant':key,
                  'idempotency_key':key}
            cache=transport.RecentRequests(path)
            Dashboard.token=''
            with self.assertRaises(transport.NoRequestSent):
                cache.submit(key,lambda:self.client.create_task('pr-review',task),now=1000)
            Dashboard.token='a'*32
            self.assertEqual(cache.submit(key,lambda:self.client.create_task('pr-review',task),now=1001)['id'],
                             't_12345678')
            self.assertEqual(len(Dashboard.requests),1)

    def test_disconnected_get_is_safe_and_disconnected_post_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'recent.json';key='pr-review-'+'a'*64
            task={'title':'Synthetic PR','body':'{}','assignee':'pr-review-v1','tenant':key,
                  'idempotency_key':key}
            cache=transport.RecentRequests(path);Dashboard.drop_get=True
            with self.assertRaises(transport.NoRequestSent):
                cache.submit(key,lambda:self.client.create_task('pr-review',task),now=1000)
            Dashboard.drop_get=False;Dashboard.drop_post=True
            with self.assertRaises(transport.AmbiguousCreate):
                cache.submit(key,lambda:self.client.create_task('pr-review',task),now=1001)
            self.assertEqual((Dashboard.post_attempts,len(Dashboard.tasks)),(1,1))
            with self.assertRaisesRegex(ValueError,'reconciliation'):
                transport.RecentRequests(path).submit(key,lambda:self.client.create_task('pr-review',task),
                                                        now=1001+4*86400)
            self.assertEqual(Dashboard.post_attempts,1)

    def test_cache_ttl_persists_and_uncertain_request_never_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'recent.json';key='pr-review-'+'a'*64
            cache=transport.RecentRequests(path);calls=[]
            def submit():calls.append(1);return {'id':'t_12345678'}
            self.assertEqual(cache.submit(key,submit,now=1000),{'id':'t_12345678'})
            self.assertIsNone(transport.RecentRequests(path).submit(key,submit,now=1001))
            self.assertEqual(cache.submit(key,submit,now=1000+72*3600),{'id':'t_12345678'})
            self.assertEqual(len(calls),2)
            def unknown():raise transport.AmbiguousCreate('lost response')
            other='pr-review-'+'b'*64
            with self.assertRaises(transport.AmbiguousCreate):cache.submit(other,unknown,now=2000)
            with self.assertRaisesRegex(ValueError,'reconciliation'):
                transport.RecentRequests(path).submit(other,submit,now=2000+4*86400)
            self.assertEqual(len(calls),2)
            expected={'title':'Synthetic PR','body':'{}','assignee':'pr-review-v1','tenant':other,
                      'idempotency_key':other}
            card={'id':'t_12345678',**expected,'model_override':None,'provider_override':None,
                  'workspace_kind':'scratch','workspace_path':None,'max_runtime_seconds':None}
            with self.assertRaisesRegex(ValueError,'proof'):
                cache.confirm(other,{**card,'title':'Other PR'},expected)
            with self.assertRaisesRegex(ValueError,'proof'):
                cache.confirm(other,{**card,'max_runtime_seconds':True},
                              {**expected,'max_runtime_seconds':1})
            with self.assertRaisesRegex(ValueError,'proof'):
                cache.confirm(other,card,{**expected,'max_runtime_seconds':None})
            cache.confirm(other,card,expected)
            self.assertIsNone(cache.submit(other,submit,now=2001))
            self.assertEqual(len(calls),2)
            path.write_text('{bad');path.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'invalid'):
                cache.submit('pr-review-'+'c'*64,submit,now=3000)
            self.assertEqual(len(calls),2)


if __name__=='__main__': unittest.main()
