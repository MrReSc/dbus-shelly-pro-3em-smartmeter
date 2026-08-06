#!/usr/bin/env python3

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


PROJECT_DIR = Path(__file__).resolve().parent.parent


class ServiceScriptsTest(unittest.TestCase):
  def setUp(self):
    self._temporary_directory = tempfile.TemporaryDirectory()
    self.test_root = Path(self._temporary_directory.name)
    self.project = self.test_root / 'data/dbus-shelly-pro-3em-smartmeter'
    self.runtime_link = self.test_root / 'service/dbus-shelly-pro-3em-smartmeter'
    self.persistent_link = self.test_root / 'opt/victronenergy/service/dbus-shelly-pro-3em-smartmeter'
    self.rc_local = self.test_root / 'data/rc.local'
    self.svc_state = self.test_root / 'svc-state'
    self.svc_log = self.test_root / 'svc-log'
    self.fake_bin = self.test_root / 'bin'

    (self.project / 'service/log').mkdir(parents=True)
    self.runtime_link.parent.mkdir(parents=True)
    self.persistent_link.parent.mkdir(parents=True)
    self.fake_bin.mkdir()

    for name in ('install.sh', 'uninstall.sh'):
      source = (PROJECT_DIR / name).read_text()
      source = source.replace(
        'PERSISTENT_LINK="/opt/victronenergy/service/$SERVICE_NAME"',
        'PERSISTENT_LINK="%s/$SERVICE_NAME"' % self.persistent_link.parent,
      )
      source = source.replace(
        'RUNTIME_LINK="/service/$SERVICE_NAME"',
        'RUNTIME_LINK="%s/$SERVICE_NAME"' % self.runtime_link.parent,
      )
      source = source.replace(
        'RC_LOCAL=/data/rc.local',
        'RC_LOCAL="%s"' % self.rc_local,
      )
      destination = self.project / name
      destination.write_text(source)
      destination.chmod(0o755)

    for relative_path in ('service/run', 'service/log/run'):
      path = self.project / relative_path
      path.write_text('#!/bin/sh\nexit 0\n')
      path.chmod(0o755)

    (self.project / 'config.ini').write_text('test configuration\n')
    (self.project / 'current.log').write_text('obsolete log\n')
    self.rc_local.write_text(
      '#!/bin/sh\n\n%s/install.sh\n' % self.project
    )
    self.rc_local.chmod(0o755)
    self.runtime_link.symlink_to(self.project / 'service')
    self.persistent_link.symlink_to(self.project / 'service')
    self.svc_state.write_text('up\n')
    self.svc_log.write_text('')

    self._write_executable(
      self.fake_bin / 'svc',
      """#!/bin/sh
printf '%s %s\\n' "$1" "$2" >> "$TEST_SVC_LOG"
case "$1" in
  -d|-dx) printf 'down\\n' > "$TEST_SVC_STATE" ;;
  -u) printf 'up\\n' > "$TEST_SVC_STATE" ;;
  -t) ;;
  *) exit 2 ;;
esac
""",
    )
    self._write_executable(
      self.fake_bin / 'svstat',
      """#!/bin/sh
if [ "$(cat "$TEST_SVC_STATE")" = up ]; then
  printf '%s: up (pid 1) 1 seconds\\n' "$1"
else
  printf '%s: down 1 seconds, normally up\\n' "$1"
fi
""",
    )
    self._write_executable(self.fake_bin / 'mount', '#!/bin/sh\nexit 0\n')

    self.environment = os.environ.copy()
    self.environment['PATH'] = str(self.fake_bin) + os.pathsep + self.environment['PATH']
    self.environment['TEST_SVC_STATE'] = str(self.svc_state)
    self.environment['TEST_SVC_LOG'] = str(self.svc_log)

  def tearDown(self):
    self._temporary_directory.cleanup()

  @staticmethod
  def _write_executable(path, contents):
    path.write_text(contents)
    path.chmod(0o755)

  def _run(self, name):
    return subprocess.run(
      [str(self.project / name)],
      env=self.environment,
      text=True,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      check=False,
    )

  def _rc_install_count(self):
    return sum(
      line.strip() == str(self.project / 'install.sh')
      for line in self.rc_local.read_text().splitlines()
    )

  def test_uninstall_then_install_starts_service(self):
    config_before = (self.project / 'config.ini').read_bytes()

    uninstall = self._run('uninstall.sh')
    self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
    self.assertFalse(self.runtime_link.exists())
    self.assertFalse(self.persistent_link.exists())
    self.assertEqual(self._rc_install_count(), 0)
    self.assertEqual((self.project / 'config.ini').read_bytes(), config_before)

    calls = self.svc_log.read_text().splitlines()
    self.assertIn('-d %s' % self.runtime_link, calls)
    self.assertIn('-dx %s' % (self.project / 'service'), calls)
    self.assertIn('-dx %s' % (self.project / 'service/log'), calls)

    second_uninstall = self._run('uninstall.sh')
    self.assertEqual(second_uninstall.returncode, 0, second_uninstall.stderr)

    self.svc_log.write_text('')
    install = self._run('install.sh')
    self.assertEqual(install.returncode, 0, install.stderr)
    self.assertEqual(self.runtime_link.resolve(), self.project / 'service')
    self.assertEqual(self.persistent_link.resolve(), self.project / 'service')
    self.assertEqual(self._rc_install_count(), 1)
    self.assertEqual(self.svc_state.read_text().strip(), 'up')
    self.assertEqual(
      self.svc_log.read_text().splitlines(),
      ['-u %s' % self.runtime_link],
    )
    self.assertFalse((self.project / 'current.log').exists())
    self.assertEqual((self.project / 'config.ini').read_bytes(), config_before)
    self.assertEqual(stat.S_IMODE((self.project / 'config.ini').stat().st_mode), 0o600)
    self.assertEqual(stat.S_IMODE(self.project.stat().st_mode), 0o755)

  def test_install_restarts_running_service_without_duplicate_autostart(self):
    install = self._run('install.sh')
    self.assertEqual(install.returncode, 0, install.stderr)
    self.assertEqual(self.svc_state.read_text().strip(), 'up')
    self.assertEqual(
      self.svc_log.read_text().splitlines(),
      ['-t %s' % self.runtime_link],
    )
    self.assertEqual(self._rc_install_count(), 1)


if __name__ == '__main__':
  unittest.main()
