#!/usr/bin/env python3
import base64
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import hermes_inbox_gmail_drafts as drafts_api


class Req:
    def __init__(self,value):self.value=value
    def execute(self):return self.value


class FakeApi:
    def __init__(self):self.creates=[];self.gets=[]
    def users(self):return self
    def drafts(self):return self
    def getProfile(self,**kwargs):return Req({'emailAddress':'zhachory1@gmail.com'})
    def create(self,**kwargs):
        self.creates.append(kwargs)
        return Req({'id':'draft1','message':{'id':'message1','threadId':'thread1'}})
    def get(self,**kwargs):
        self.gets.append(kwargs)
        return Req({'id':'draft1','message':{'id':'message1','threadId':'thread1','labelIds':['DRAFT'],
            'payload':{'headers':[{'name':'From','value':'zhachory1@gmail.com'},
                                  {'name':'To','value':'them@example.com'},
                                  {'name':'Subject','value':'Re: Synthetic'},
                                  {'name':'Message-ID','value':'<stable@example.com>'},
                                  {'name':'In-Reply-To','value':'<source@example.com>'}]}}})


class DraftApiTest(unittest.TestCase):
    def test_draft_create_never_uses_send_api(self):
        api=FakeApi();client=drafts_api.GmailDraftClient(api)
        mime=b'From: zhachory1@gmail.com\r\nTo: them@example.com\r\n\r\nHello\r\n'
        self.assertEqual(client.profile(),{'emailAddress':'zhachory1@gmail.com'})
        self.assertEqual(client.create(mime,'thread1'),'draft1')
        request=api.creates[0]
        self.assertEqual(request['userId'],'me')
        self.assertEqual(request['body']['message']['threadId'],'thread1')
        self.assertEqual(base64.urlsafe_b64decode(request['body']['message']['raw']+'=='),mime)
        self.assertEqual(client.get('draft1')['message']['threadId'],'thread1')
        self.assertEqual(len(api.gets),1)
        self.assertNotIn('metadataHeaders',api.gets[0])

    def test_compose_token_must_be_owner_only_and_exact_scope(self):
        with tempfile.TemporaryDirectory() as root:
            token=pathlib.Path(root)/'compose.json'
            scopes=['https://www.googleapis.com/auth/gmail.readonly',
                    'https://www.googleapis.com/auth/gmail.compose']
            token.write_text(__import__('json').dumps({'scopes':scopes,'refresh_token':'fixture'}))
            token.chmod(0o600)
            with mock.patch.object(drafts_api,'_build',return_value=FakeApi()) as build:
                self.assertIsInstance(drafts_api.connect(token),drafts_api.GmailDraftClient)
                build.assert_called_once()
            token.write_text(__import__('json').dumps({'scopes':[scopes[0]],'refresh_token':'fixture'}))
            with self.assertRaises(ValueError):drafts_api.connect(token)


if __name__=='__main__':unittest.main()
