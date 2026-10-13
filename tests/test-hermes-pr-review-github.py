#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hermes_pr_review_github import GitHub


class GitHubTest(unittest.TestCase):
    def test_paginates_prior_reviews_and_normalizes_remote_events(self):
        pages = ([{'id':1,'user':{'login':'bot'},'commit_id':'a'*40,'state':'APPROVED','body':'earlier'}],
                 [{'id':2,'user':{'login':'bot'},'commit_id':'a'*40,'state':'COMMENTED','body':'marker'}])
        def run(command, **_):
            self.assertIn('--paginate',command)
            return subprocess.CompletedProcess(command,0,('\n'.join(json.dumps(page) for page in pages)).encode(),b'')
        with mock.patch('hermes_pr_review_github.subprocess.run',side_effect=run):
            values=GitHub().reviews_for_head('example/repo',17)
        self.assertEqual([item['state'] for item in values],['APPROVE','COMMENT'])
        self.assertEqual(values[-1]['body'],'marker')

    def test_posts_one_head_pinned_review_with_json_stdin(self):
        def run(command,**kwargs):
            self.assertIn('--input',command)
            self.assertEqual(json.loads(kwargs['input']),{'commit_id':'a'*40,'event':'APPROVE','body':'synthetic'})
            body={'id':7,'user':{'login':'bot'},'commit_id':'a'*40,'state':'APPROVED','body':'synthetic'}
            return subprocess.CompletedProcess(command,0,json.dumps(body).encode(),b'')
        with mock.patch('hermes_pr_review_github.subprocess.run',side_effect=run):
            result=GitHub().post('example/repo',17,'a'*40,'APPROVE','synthetic')
        self.assertEqual(result['state'],'APPROVE')

    def test_truncated_paginated_input_fails_closed(self):
        with mock.patch('hermes_pr_review_github.subprocess.run',return_value=
                        subprocess.CompletedProcess([],0,b'[{"id":1',b'')):
            with self.assertRaises(ValueError):GitHub().reviews_for_head('example/repo',17)


if __name__=='__main__':unittest.main()
