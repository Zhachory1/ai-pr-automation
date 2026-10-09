#!/usr/bin/env python3
import pathlib
import plistlib
import unittest


ROOT=pathlib.Path(__file__).resolve().parents[1]


class InboxScheduleTest(unittest.TestCase):
    def test_daily_new_york_host_schedule_never_runs_at_load(self):
        template=(ROOT/'launchd/com.example.ai-pr-automation-inbox-drafts.plist.template').read_text()
        rendered=template.replace('__HOME__','/Users/fixture').replace('__REPO__','/private/fixture-repo') \
                         .replace('__HERMES_PYTHON__','/private/fixture-hermes/python')
        value=plistlib.loads(rendered.encode())
        self.assertEqual(value['Label'],'ai.hermes.inbox-drafts')
        self.assertEqual(value['StartCalendarInterval'],{'Hour':8,'Minute':0})
        self.assertIs(value['RunAtLoad'],False)
        self.assertNotIn('KeepAlive',value)
        self.assertEqual(value['ProgramArguments'],['/private/fixture-hermes/python','-B',
            '/private/fixture-repo/scripts/hermes_inbox_daily_run.py'])
        self.assertNotIn('send',rendered.lower())


if __name__=='__main__':unittest.main()
