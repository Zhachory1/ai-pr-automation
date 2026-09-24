#!/usr/bin/env python3
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProducerConfigTest(unittest.TestCase):
    def test_host_timer_uses_same_queue_mode_and_scope_as_compose(self):
        config = (ROOT / 'scripts/configure-hermes-role-env.sh').read_text()
        assignments = config.split('cat >> "$tmp" <<EOF\n', 1)[1].split('\nEOF', 1)[0]
        assignments = '\n'.join(line for line in assignments.splitlines() if line.startswith(
            ('PR_SAFETY_QUEUE_ENGINE=', 'PR_SAFETY_MERGED_PR_AUTHORS=', 'PR_SAFETY_ALLOWED_ORGS=', 'HERMES_AUTHORITY_FILE=')))
        pattern = config.split("grep -vE '", 1)[1].split("'", 1)[0]
        for mode in ('postgres', 'kanban'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                selected = root / 'selected authority.yaml'; selected.write_text('repos: [pilot/repo]\n')
                installed = root / 'installed.yaml'; installed.write_text('repos: [broad/*]\n')
                stale = f'PR_SAFETY_QUEUE_ENGINE=stale\nPR_SAFETY_MERGED_PR_AUTHORS=stale\nPR_SAFETY_ALLOWED_ORGS=stale\nHERMES_AUTHORITY_FILE={installed}\n'
                filtered = subprocess.run(['grep', '-vE', pattern], input=stale,
                                          text=True, capture_output=True)
                self.assertEqual(filtered.stdout, '')
                env = os.environ | {'PR_SAFETY_QUEUE_ENGINE': mode,
                                    'PR_SAFETY_MERGED_PR_AUTHORS': 'author-one,author-two',
                                    'PR_SAFETY_ALLOWED_ORGS': 'AllowedOrg', 'HERMES_HOME': td,
                                    'HERMES_AUTHORITY_FILE': str(selected), 'AUTHORITY': str(selected)}
                rendered = subprocess.check_output(['bash', '-c', 'cat <<EOF\n' + assignments + '\nEOF'],
                                                   env=env, text=True)
                (root / '.env').write_text(rendered)
                producer = root / 'hermes-pr-safety-producer'
                producer.write_text('#!/usr/bin/env python3\nimport os,json,pathlib\n'
                                    'pathlib.Path(os.environ["HERMES_HOME"], "called.json").write_text('
                                    'json.dumps({k:os.environ[k] for k in '
                                    '["PR_SAFETY_QUEUE_ENGINE","PR_SAFETY_MERGED_PR_AUTHORS","PR_SAFETY_ALLOWED_ORGS","HERMES_AUTHORITY_FILE"]}))\n')
                producer.chmod(0o700)
                template = (ROOT / 'launchd/com.example.ai-pr-automation-pr-safety-producer.plist.template').read_text()
                template = template.replace('__SUPPORT_ROOT__', td).replace('__INTERVAL_SECONDS__', '60')
                args = plistlib.loads(template.encode())['ProgramArguments']
                # Ambient values disagree; the rendered service env must win.
                subprocess.run(args, env=env | {'PR_SAFETY_QUEUE_ENGINE': 'stale',
                                               'HERMES_AUTHORITY_FILE': str(installed)}, check=True)
                if mode == 'postgres':
                    self.assertFalse((root / 'called.json').exists())
                else:
                    self.assertEqual(json.loads((root / 'called.json').read_text()),
                                     {key: env[key] for key in ('PR_SAFETY_QUEUE_ENGINE',
                                      'PR_SAFETY_MERGED_PR_AUTHORS', 'PR_SAFETY_ALLOWED_ORGS', 'HERMES_AUTHORITY_FILE')})


if __name__ == '__main__':
    unittest.main()
