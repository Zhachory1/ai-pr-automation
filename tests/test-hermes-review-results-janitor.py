#!/usr/bin/env python3
import importlib.util
import os
import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('review_janitor',ROOT/'scripts/hermes-review-results-janitor.py')
janitor=importlib.util.module_from_spec(spec)
spec.loader.exec_module(janitor)


class JanitorTest(unittest.TestCase):
    def test_daily_schedule_does_not_run_on_install(self):
        path=ROOT/'launchd/com.example.ai-pr-automation-review-results-janitor.plist.template'
        job=plistlib.loads(path.read_bytes())
        self.assertEqual(job['StartCalendarInterval'],{'Hour':4,'Minute':0})
        self.assertFalse(job['RunAtLoad'])
        self.assertIn('hermes-review-results-janitor.py',job['ProgramArguments'][-1])

    def test_only_expired_expert_results_are_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'results';root.mkdir(mode=0o700)
            name='t_00000001-mvp.json'
            old=root/name;old.write_text('{}');old.chmod(0o600)
            default_mode=root/'t_00000002-mvp.json'
            default_mode.write_text('{}');default_mode.chmod(0o644)
            recent=root/'t_00000003-reliability.json'
            recent.write_text('{}');recent.chmod(0o600)
            boundary=root/'t_00000004-security.json'
            boundary.write_text('{}');boundary.chmod(0o600)
            other=root/'request.json';other.write_text('{}')
            outside=Path(temp)/'outside';outside.write_text('keep')
            linked=root/'t_00000005-security.json';linked.symlink_to(outside)
            now=200000
            os.utime(old,(now-86401,now-86401))
            os.utime(default_mode,(now-86401,now-86401))
            os.utime(recent,(now-86399,now-86399))
            os.utime(boundary,(now-86400,now-86400))
            with mock.patch.object(janitor,'RESULTS_ROOT',root):
                self.assertEqual(janitor.sweep(now),2)
            self.assertFalse(old.exists() or default_mode.exists())
            self.assertTrue(recent.exists() and boundary.exists() and other.exists() and linked.is_symlink())
            self.assertEqual(outside.read_text(),'keep')

    def test_unsafe_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'results';root.mkdir(mode=0o755)
            with mock.patch.object(janitor,'RESULTS_ROOT',root), self.assertRaisesRegex(ValueError,'private'):
                janitor.sweep(200000)


if __name__=='__main__':unittest.main()
