#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import json
import pathlib
import sqlite3
import hashlib
import sys
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
path=ROOT/'scripts/hermes-pr-review-council-publish.py'
loader=importlib.machinery.SourceFileLoader('publisher',str(path));spec=importlib.util.spec_from_loader(loader.name,loader)
publisher=importlib.util.module_from_spec(spec);loader.exec_module(publisher)
from hermes_direct_pr_journal import identity


class FakeGitHub:
    def __init__(self):
        self.head='a'*40;self.base='b'*40;self.reviews=[];self.posts=[]
        self.fail_after_post=False;self.change_on_lookup=False;self.change_after_post=False
        self.pr_author='pr-author';self.comments_data=[];self.hide_post=False
    def actor(self):return 'review-bot'
    def author(self,repo,number):return self.pr_author
    def state(self,repo,number):return {'state':'open','head_sha':self.head,'base_sha':self.base}
    def reviews_for_head(self,repo,number):
        if self.change_on_lookup:self.head='e'*40
        return list(self.reviews[:-1] if self.hide_post and self.posts else self.reviews)
    def comments(self,repo,number):return list(self.comments_data)
    def post(self,repo,number,head,event,body):
        self.posts.append((repo,number,head,event,body))
        review={'id':7,'commit_id':head,'state':event,'body':body,'author':'review-bot'}
        self.reviews.append(review)
        if self.change_after_post:self.head='e'*40
        if self.fail_after_post:raise TimeoutError('synthetic timeout after GitHub accepted')
        return review


class PublisherTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=pathlib.Path(self.tmp.name)/'effects.sqlite'
        self.gh=FakeGitHub()
        op=identity('pr-review','example/repo',17,'a'*40)['operation_id']
        self.request={'operation_id':op,'repo':'example/repo','number':17,
                      'head_sha':'a'*40,'base_sha':'b'*40,'artifact_digest':'d'*64}
        self.body='Reviewed synthetic change. No required changes.'
        self.plan={'operation_id':op,'artifact_digest':'d'*64,'head_sha':'a'*40,'base_sha':'b'*40,
                   'specialists':{'generalist':'g','reliability':'r','mvp':'m'},
                   'synthesis':{'profile':'pr-review-synthesis-v2'},'changed_paths':['src/file.py']}
        self.outputs={role:{'operation_id':op,'artifact_digest':'d'*64,'role':role,'verdict':'clear','findings':[]}
                      for role in self.plan['specialists']}
        self.synthesis={'operation_id':op,'artifact_digest':'d'*64,'role':'synthesis','verdict':'approve','findings':[]}
        self.body=publisher.render(self.synthesis)
    def post(self,event='APPROVE'):
        return publisher.publish(self.db,self.gh,self.request,event,self.body,
                                 self.plan,self.outputs,self.synthesis)
    def test_clean_approval_posts_once_with_existing_marker(self):
        result=self.post()
        self.assertEqual(result['status'],'verified')
        self.assertEqual(len(self.gh.posts),1)
        self.assertEqual(self.gh.posts[0][2:4],('a'*40,'APPROVE'))
        self.assertIn('<!-- ai-pr-automation head='+'a'*40+' -->',self.gh.posts[0][4])
        self.assertEqual(self.post(),result)
        self.assertEqual(len(self.gh.posts),1)
    def test_self_authored_pr_uses_comment_with_verdict_instead_of_illegal_review(self):
        self.gh.pr_author='review-bot'
        result=self.post()
        self.assertEqual(result['event'],'COMMENT')
        self.assertEqual(self.gh.posts[0][3],'COMMENT')
        self.assertIn('Recommendation: APPROVE',self.gh.posts[0][4])
        self.assertEqual(self.post(),result)
        self.assertEqual(len(self.gh.posts),1)

    def test_pending_handoff_never_reaches_github_publisher(self):
        from types import SimpleNamespace
        states={}
        for role,profile in [*self.plan['specialists'].items(),('synthesis','pr-review-synthesis-v2')]:
            metadata=self.outputs.get(role,self.synthesis)
            states[role]=(SimpleNamespace(status='ready',assignee=profile),
                          [SimpleNamespace(outcome='completed',profile=profile,metadata=metadata)])
        class KB:
            def get_task(self,conn,id):return states[id][0]
            def list_runs(self,conn,id):return states[id][1]
        self.assertIsNone(publisher.settle(KB(),None,{role:role for role in states},self.plan,
                                           self.db,self.gh,self.request,pathlib.Path(self.tmp.name)))
        self.assertEqual(self.gh.posts,[])

    def test_done_handoffs_post_one_review_and_purge_private_snapshot(self):
        from types import SimpleNamespace
        diff=b'diff --git a/src/file.py b/src/file.py\n+new\n'
        context=b'{"checks":[],"intent":"synthetic","prior_feedback":[]}'
        data={'operation_id':self.request['operation_id'],'repo':self.request['repo'],
              'number':self.request['number'],'head_sha':self.request['head_sha'],
              'base_sha':self.request['base_sha'],'diff_digest':hashlib.sha256(diff).hexdigest(),
              'context_digest':hashlib.sha256(context).hexdigest(),'changed_paths':['src/file.py']}
        self.plan=publisher.COUNCIL.plan(data)
        self.request['artifact_digest']=self.plan['artifact_digest']
        for output in self.outputs.values():output['artifact_digest']=self.plan['artifact_digest']
        self.synthesis['artifact_digest']=self.plan['artifact_digest']
        workspace=pathlib.Path(self.tmp.name)/'operation'
        publisher.COUNCIL.prepare_snapshot(workspace,self.plan,diff,context)
        states={role:(SimpleNamespace(status='done',assignee=profile),
                      [SimpleNamespace(outcome='completed',profile=profile,
                                       metadata=self.outputs.get(role,self.synthesis))])
                for role,profile in [*self.plan['specialists'].items(),
                                     ('synthesis',self.plan['synthesis']['profile'])]}
        ids={role:'t_%08x'%index for index,role in enumerate(states,1)}
        by_id={ids[role]:value for role,value in states.items()}
        class KB:
            def get_task(self,conn,id):return by_id[id][0]
            def list_runs(self,conn,id):return by_id[id][1]
        result=publisher.settle(KB(),None,ids,self.plan,self.db,self.gh,self.request,workspace)
        self.assertEqual(result['status'],'verified')
        self.assertEqual(len(self.gh.posts),1)
        self.assertFalse((workspace/'snapshot/diff.patch').exists())
        self.assertTrue((workspace/'review-outcome.json').exists())

    def test_private_snapshot_is_removed_only_after_verified_effect(self):
        diff=b'diff --git a/src/file.py b/src/file.py\n+new\n'
        context=b'{"checks":[],"intent":"synthetic","prior_feedback":[]}'
        data={'operation_id':self.request['operation_id'],'repo':self.request['repo'],
              'number':self.request['number'],'head_sha':self.request['head_sha'],
              'base_sha':self.request['base_sha'],'diff_digest':hashlib.sha256(diff).hexdigest(),
              'context_digest':hashlib.sha256(context).hexdigest(),'changed_paths':['src/file.py']}
        spec=publisher.COUNCIL.plan(data)
        self.plan=spec
        self.request['artifact_digest']=spec['artifact_digest']
        for output in self.outputs.values():output['artifact_digest']=spec['artifact_digest']
        self.synthesis['artifact_digest']=spec['artifact_digest']
        root=pathlib.Path(self.tmp.name)/'snapshot'
        publisher.COUNCIL.prepare_snapshot(root,spec,diff,context)
        with self.assertRaises(ValueError):publisher.purge_verified(self.db,root,spec,'t_00000001')
        self.assertTrue((root/'snapshot/diff.patch').exists())
        self.post()
        context_file=root/'input/context.json'
        context_file.chmod(0o600);context_file.write_bytes(b'tampered');context_file.chmod(0o400)
        with self.assertRaises(ValueError):publisher.purge_verified(self.db,root,spec,'t_00000001')
        self.assertTrue((root/'snapshot/diff.patch').exists())
        context_file.chmod(0o600);context_file.write_bytes(context);context_file.chmod(0o400)
        (root/'snapshot/diff.patch').unlink()
        publisher.purge_verified(self.db,root,spec,'t_00000001')
        publisher.purge_verified(self.db,root,spec,'t_00000001')
        self.assertEqual(json.loads((root/'review-outcome.json').read_text())['task_id'],'t_00000001')
        self.assertFalse((root/'snapshot/diff.patch').exists())
        self.assertFalse((root/'input/context.json').exists())
        self.assertFalse((root/'input/identity.json').exists())

    def test_review_body_must_render_all_synthesis_evidence(self):
        self.synthesis['findings']=[{'severity':'nit','required':False,'path':'src/file.py','line':7,
                                     'claim':'naming inconsistency','evidence':'new variable name',
                                     'suggestion':'consider a consistent name'}]
        with self.assertRaises(ValueError):self.post()
        self.body=publisher.render(self.synthesis)
        self.post()
        self.assertIn('naming inconsistency',self.gh.posts[0][4])
        self.assertIn('new variable name',self.gh.posts[0][4])

    def test_missing_or_conflicting_synthesis_cannot_publish(self):
        with self.assertRaises((TypeError,ValueError)):
            publisher.publish(self.db,self.gh,self.request,'APPROVE',self.body)
        self.outputs.pop('mvp')
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(self.gh.posts,[])
        self.outputs['mvp']={'operation_id':self.plan['operation_id'],'artifact_digest':'d'*64,
                             'role':'mvp','verdict':'clear','findings':[]}
        with self.assertRaises(ValueError):self.post('REQUEST_CHANGES')
        self.assertEqual(self.gh.posts,[])

    def test_changed_head_or_closed_pr_never_posts(self):
        self.gh.head='e'*40
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(self.gh.posts,[])
        self.gh.head='a'*40
        self.gh.state=lambda *_: {'state':'closed','head_sha':'a'*40,'base_sha':'b'*40}
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(self.gh.posts,[])
    def test_timeout_after_remote_accept_reconciles_without_second_post(self):
        self.gh.fail_after_post=True
        with self.assertRaises(TimeoutError):self.post()
        self.assertEqual(len(self.gh.posts),1)
        result=self.post()
        self.assertEqual(result['status'],'verified')
        self.assertEqual(len(self.gh.posts),1)
    def test_head_moves_after_prior_review_lookup_no_post(self):
        self.gh.change_on_lookup=True
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(self.gh.posts,[])

    def test_head_moves_after_post_result_reports_superseded(self):
        self.gh.change_after_post=True
        result=self.post()
        self.assertEqual(result['status'],'superseded')
        self.assertEqual(len(self.gh.posts),1)

    def test_prior_comment_marker_or_missing_post_readback_blocks_duplicate_effect(self):
        marker='<!-- ai-pr-automation head='+'a'*40+' -->'
        self.gh.comments_data=[{'body':'other writer\n'+marker}]
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(self.gh.posts,[])
        self.gh.comments_data=[]
        self.gh.hide_post=True
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(len(self.gh.posts),1)
        self.gh.hide_post=False
        self.assertEqual(self.post()['status'],'verified')
        self.assertEqual(len(self.gh.posts),1)

    def test_other_writer_marker_blocks_a_second_review(self):
        marker='<!-- ai-pr-automation head='+'a'*40+' -->'
        self.gh.reviews.append({'id':3,'commit_id':'a'*40,'state':'APPROVE',
                                'body':self.body+'\n\n'+marker,'author':'other-bot'})
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(self.gh.posts,[])

    def test_marker_on_unexpected_commit_blocks_another_review(self):
        marker='<!-- ai-pr-automation head='+'a'*40+' -->'
        self.gh.reviews.append({'id':11,'commit_id':'e'*40,'state':'COMMENT',
                                'body':'prior review\n'+marker,'author':'review-bot'})
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(self.gh.posts,[])

    def test_accepted_timeout_reconciles_after_head_moves(self):
        self.gh.fail_after_post=True
        with self.assertRaises(TimeoutError):self.post()
        self.gh.head='e'*40
        result=self.post()
        self.assertEqual(result['status'],'superseded')
        self.assertEqual(len(self.gh.posts),1)

    def test_artifact_drift_cannot_reuse_effect_receipt(self):
        self.post()
        self.request['artifact_digest']='e'*64
        with self.assertRaises(ValueError):self.post()
        self.assertEqual(len(self.gh.posts),1)

    def test_ambiguous_without_remote_proof_is_never_retried(self):
        self.gh.post=lambda *_: (_ for _ in ()).throw(TimeoutError('unknown outcome'))
        with self.assertRaises(TimeoutError):self.post()
        with self.assertRaises(ValueError):self.post()


if __name__=='__main__':unittest.main()
