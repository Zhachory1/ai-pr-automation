#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('review_worker', str(ROOT/'scripts/hermes-pr-review-council-worker.py'))
spec = importlib.util.spec_from_loader(loader.name, loader)
worker = importlib.util.module_from_spec(spec)
loader.exec_module(worker)


class CouncilWorkerTest(unittest.TestCase):
    def test_stale_pending_head_releases_capacity_without_posting(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); op='pr-review-'+'a'*64; workspace=root/op
            workspace.mkdir(mode=0o700)
            owner={'route':'council-v2','operation_id':op,'repo':'example/repo',
                   'number':17,'head_sha':'a'*40,'title':'synthetic'}
            worker.INGRESS.ENQUEUE.immutable(workspace/'request.json',worker.INGRESS.ENQUEUE.canonical(owner))
            (root/'authority').write_text('repos:\n  - example/repo\n')
            config=worker.INGRESS.Config(root,root/'authority',root,root/'hermes',{},
                                          council_install=root/'runtime',council_enabled=True)
            class GitHub:
                def state(self,*_):return {'state':'open','head_sha':'e'*40,'base_sha':'b'*40}
                def reviews_for_head(self,*_):return []
                def comments(self,*_):return []
            with mock.patch.object(worker.INGRESS,'validate_council_runtime'),\
                 mock.patch.object(worker.INGRESS.GITHUB,'GitHub',return_value=GitHub()),\
                 mock.patch.object(worker.INGRESS,'invoke_council',side_effect=AssertionError('stale post')):
                self.assertEqual(worker.tick(config)['status'],'superseded')
            self.assertEqual(json.loads((workspace/'review-outcome.json').read_text())['status'],'superseded')
            worker.INGRESS.council_capacity(config,'pr-review-'+'b'*64)

    def test_revisits_pending_owner_without_another_discovery_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            operation = 'pr-review-' + 'a'*64
            workspace = root/operation
            workspace.mkdir(mode=0o700)
            owner = {'route':'council-v2','operation_id':operation,'repo':'example/repo',
                     'number':17,'head_sha':'a'*40,'title':'synthetic'}
            (root/'authority').write_text('repos:\n  - example/repo\n')
            worker.INGRESS.ENQUEUE.immutable(workspace/'request.json',worker.INGRESS.ENQUEUE.canonical(owner))
            config = worker.INGRESS.Config(root,root/'authority',root,root/'hermes',{},
                                            council_install=root/'runtime',council_enabled=True)
            result = {'kind':'pr-review','board':'pr-review','operation_id':operation,
                      'task_id':'t_00000001','status':'active'}
            class GitHub:
                def state(self,*_):return {'state':'open','head_sha':'a'*40,'base_sha':'b'*40}
            with mock.patch.object(worker.INGRESS,'validate_council_runtime'),\
                 mock.patch.object(worker.INGRESS.GITHUB,'GitHub',return_value=GitHub()),\
                 mock.patch.object(worker.LOCAL,'resolve',return_value=root/'checkout') as located,\
                 mock.patch.object(worker.INGRESS,'invoke_council',return_value=result) as invoked:
                self.assertEqual(worker.tick(config)['status'],'active')
            invoked.assert_called_once()
            request = invoked.call_args.args[1]
            located.assert_called_once_with('example/repo','a'*40)
            self.assertEqual(invoked.call_args.args[2],root/'checkout')
            self.assertEqual(request['operation_id'],operation)
            self.assertEqual(request['url'],'https://github.com/example/repo/pull/17')
            self.assertFalse((workspace/'review-outcome.json').exists())


if __name__ == '__main__':
    unittest.main()
