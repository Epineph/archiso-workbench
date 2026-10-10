#!/usr/bin/env python3
"""Offline clean-chroot orchestration checks; no sudo or network access."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
HELPER = KIT / 'bin/build-zfs-packages.sh'
CAN_BUILD = (os.geteuid() != 0 and Path('/etc/arch-release').is_file() and
             os.uname().machine == 'x86_64')


class ZfsPackageBuilderTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name)
    self.sources = self.root / 'reviewed sources'
    self.output = self.root / 'packages'
    self.work = self.root / 'build work'
    self.commands = self.root / 'commands.jsonl'
    self.bin = self.root / 'bin'
    self.bin.mkdir()
    self.env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                'TEST_COMMAND_LOG': str(self.commands), 'TEST_CPUS': '8'}
    for name in ('zfs-utils', 'zfs-dkms'):
      recipe = self.sources / name
      recipe.mkdir(parents=True)
      (recipe / '.SRCINFO').write_text(
        f'pkgbase = {name}\n\tpkgver = 2.4.4\n\tpkgrel = 1\n'
        f'\tarch = x86_64\npkgname = {name}\n')
      (recipe / 'PKGBUILD').write_text('exit 93 # plan must not execute me\n')
      (recipe / 'support.patch').write_text('reviewed local support file\n')
    self.command('nproc', '#!/bin/sh\nprintf "%s\\n" "$TEST_CPUS"\n')
    self.command('sudo', '''#!/bin/sh
while [ "${1#--}" != "$1" ]; do shift; done
exec "$@"
''')
    self.command('mkarchroot', '''#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path
args = sys.argv[1:]
record = {'command': 'mkarchroot', 'args': args}
with open(os.environ['TEST_COMMAND_LOG'], 'a') as log:
  log.write(json.dumps(record) + '\\n')
if os.environ.get('TEST_FAIL_CHROOT'):
  print('fixture chroot failure', file=sys.stderr)
  sys.exit(31)
root = Path(args[args.index('-c') + 2])
# Real mkarchroot canonicalizes with readlink -f before mkdir.
assert root.parent.is_dir(), 'mkarchroot requires an existing parent'
assert not root.exists(), 'mkarchroot requires a nonexistent root'
(root / 'etc').mkdir(parents=True)
for flag, name in (('-C', 'pacman.conf'), ('-M', 'makepkg.conf')):
  shutil.copyfile(args[args.index(flag) + 1], root / 'etc' / name)
''')
    self.command('makepkg', '''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
with open(os.environ['TEST_COMMAND_LOG'], 'a') as log:
  log.write(json.dumps({'command': 'makepkg', 'args': sys.argv[1:],
                       'cwd': str(Path.cwd())}) + '\\n')
if os.environ.get('TEST_FAIL_METADATA'):
  print('fixture metadata failure', file=sys.stderr)
  sys.exit(29)
assert '--printsrcinfo' in sys.argv
text = Path('.SRCINFO').read_text()
if os.environ.get('TEST_STALE_METADATA'):
  text = text.replace('pkgver = 2.4.4', 'pkgver = 2.4.3')
print(text, end='')
''')
    self.command('makechrootpkg', '''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
args = sys.argv[1:]
name = Path.cwd().name
fields = ('MAKEPKG_CONF', 'PKGDEST', 'LOGDEST', 'SRCDEST', 'SRCPKGDEST',
          'MAKEFLAGS', 'NPROC', 'PKGEXT')
record = {'command': 'makechrootpkg', 'args': args, 'recipe': name,
          'cwd': str(Path.cwd()),
          'env': {field: os.environ[field] for field in fields}}
if '-I' in args:
  record['injected'] = json.loads(Path(args[args.index('-I') + 1]).read_text())
with open(os.environ['TEST_COMMAND_LOG'], 'a') as log:
  log.write(json.dumps(record) + '\\n')
Path(os.environ['LOGDEST'], 'fixture-build.log').write_text('build log')
if os.environ.get('TEST_FAIL_BUILD') == name:
  print('fixture package build failure', file=sys.stderr)
  sys.exit(37)
if os.environ.get('TEST_NO_ARTIFACT') == name:
  sys.exit(0)
version = os.environ.get('TEST_ARCHIVE_VERSION', '2.4.4-1')
package = os.environ.get('TEST_ARCHIVE_NAME', name)
target = Path(os.environ['PKGDEST'])
(target / (name + '-opaque.pkg.tar.zst')).write_text(
  json.dumps({'name': package, 'version': version}))
(target / (name + '-debug.pkg.tar.zst')).write_text(
  json.dumps({'name': name + '-debug', 'version': version}))
''')
    self.command('pacman', '''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
with open(os.environ['TEST_COMMAND_LOG'], 'a') as log:
  log.write(json.dumps({'command': 'pacman', 'args': sys.argv[1:]}) + '\\n')
assert sys.argv[1:5] == ['--config', '/dev/null', '-Qp', '--']
if os.environ.get('TEST_INVALID_ARCHIVE'):
  print('fixture archive failure', file=sys.stderr)
  sys.exit(43)
data = json.loads(Path(sys.argv[-1]).read_text())
print(data['name'], data['version'])
''')

  def tearDown(self):
    self.temp.cleanup()

  def command(self, name, text):
    path = self.bin / name
    path.write_text(text)
    path.chmod(0o755)

  def run_helper(self, *options):
    return subprocess.run(
      ['bash', str(HELPER), '--sources', str(self.sources),
       '--output', str(self.output), '--work', str(self.work), *options],
      env=self.env, capture_output=True, text=True, check=False)

  def records(self):
    return [json.loads(line) for line in self.commands.read_text().splitlines()]

  def test_plan_executes_no_recipe_and_creates_no_directories(self):
    result = self.run_helper('--cores', '3', '--snapshot', '2026/10/06')
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn('Build cores: 3', result.stdout)
    self.assertIn('archive.archlinux.org/repos/2026/10/06', result.stdout)
    self.assertIn('makechrootpkg', result.stdout)
    self.assertIn('--printsrcinfo', result.stdout)
    self.assertIn('Plan only', result.stdout)
    self.assertFalse(self.output.exists())
    self.assertFalse(self.work.exists())
    self.assertFalse(self.commands.exists())

  def test_default_cores_uses_half_available_cpus(self):
    for available, expected in [('1', 1), ('3', 1), ('8', 4)]:
      with self.subTest(available=available):
        self.env['TEST_CPUS'] = available
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'Build cores: {expected}', result.stdout)

  def test_plan_rejects_missing_and_mismatched_metadata(self):
    metadata = self.sources / 'zfs-dkms/.SRCINFO'
    metadata.write_text(metadata.read_text().replace('2.4.4', '2.3.2'))
    result = self.run_helper()
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('recipe versions do not match', result.stderr)
    metadata.unlink()
    result = self.run_helper()
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('missing', result.stderr)
    self.assertFalse(self.commands.exists())
    self.assertFalse(self.work.exists())

  def test_rejects_invalid_arguments_without_mutation(self):
    for options in [('--cores', '0'), ('--cores', '-1'), ('--cores', 'x'),
                    ('--cores',), ('--cores=',), ('--snapshot=',),
                    ('--snapshot', '2026/02/31'),
                    ('--snapshot', '2026-10-06'), ('--unknown',)]:
      with self.subTest(options=options):
        result = self.run_helper(*options)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Error:', result.stderr)
        self.assertFalse(self.work.exists())

  def test_rejects_nonempty_and_overlapping_directories(self):
    self.output.mkdir()
    (self.output / 'keep').write_text('unrelated output')
    result = self.run_helper()
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('must be empty', result.stderr)
    self.assertEqual((self.output / 'keep').read_text(), 'unrelated output')
    for target in (self.sources, self.sources / 'zfs-utils/output', self.work):
      result = self.run_helper('--output', str(target))
      self.assertNotEqual(result.returncode, 0)
      self.assertIn('Error:', result.stderr)

  @unittest.skipUnless(CAN_BUILD, 'Build requires a normal Arch x86_64 user')
  def test_build_orders_packages_and_injects_only_verified_utils(self):
    # Host package-destination settings must not redirect chroot artifacts.
    self.env.update({'PKGDEST': '/unrelated/output', 'MAKEFLAGS': '-j99',
                     'PKGEXT': '.pkg.tar.xz'})
    original = (self.sources / 'zfs-utils/PKGBUILD').read_bytes()
    result = self.run_helper('--build', '--cores', '3',
                             '--snapshot', '2026/10/06')
    self.assertEqual(result.returncode, 0, result.stderr)
    records = self.records()
    builds = [item for item in records if item['command'] == 'makechrootpkg']
    self.assertEqual([item['recipe'] for item in builds],
                     ['zfs-utils', 'zfs-dkms'])
    self.assertNotIn('-I', builds[0]['args'])
    self.assertEqual(builds[1]['injected'],
                     {'name': 'zfs-utils', 'version': '2.4.4-1'})
    for item in builds:
      self.assertIn('-c', item['args'])
      self.assertIn('-U', item['args'])
      self.assertEqual(item['env']['MAKEFLAGS'], '-j3')
      self.assertEqual(item['env']['NPROC'], '3')
      self.assertEqual(item['env']['PKGEXT'], '.pkg.tar.zst')
      self.assertEqual(item['env']['MAKEPKG_CONF'],
                       str(self.work / 'makepkg.conf'))
      self.assertTrue(Path(item['cwd']).is_relative_to(self.work / 'recipes'))
    chroot = next(item for item in records if item['command'] == 'mkarchroot')
    self.assertEqual(chroot['args'][-6:],
                     ['base-devel', 'dkms', 'libaio', 'libtirpc',
                      'openssl', 'zlib'])
    pacman_conf = (self.work / 'chroot/root/etc/pacman.conf').read_text()
    self.assertIn('repos/2026/10/06/$repo/os/$arch', pacman_conf)
    self.assertNotIn('Include', pacman_conf)
    self.assertNotIn('aur', pacman_conf.lower())
    makepkg_conf = (self.work / 'chroot/root/etc/makepkg.conf').read_text()
    self.assertIn('MAKEFLAGS="-j3"\nNPROC=3', makepkg_conf)
    self.assertIn("PKGEXT='.pkg.tar.zst'", makepkg_conf)
    self.assertEqual(len(list(self.output.iterdir())), 2)
    self.assertFalse(any('debug' in item.name for item in self.output.iterdir()))
    self.assertEqual((self.sources / 'zfs-utils/PKGBUILD').read_bytes(), original)
    self.assertEqual((self.work / 'recipes/zfs-utils/support.patch').read_text(),
                     'reviewed local support file\n')

  @unittest.skipUnless(CAN_BUILD, 'Build requires a normal Arch x86_64 user')
  def test_metadata_execution_failure_precedes_chroot_creation(self):
    self.env['TEST_FAIL_METADATA'] = '1'
    result = self.run_helper('--build')
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('fixture metadata failure', result.stderr)
    self.assertIn('Retained work/logs', result.stderr)
    self.assertFalse(any(item['command'] == 'mkarchroot'
                         for item in self.records()))

  @unittest.skipUnless(CAN_BUILD, 'Build requires a normal Arch x86_64 user')
  def test_stale_metadata_precedes_chroot_creation(self):
    self.env['TEST_STALE_METADATA'] = '1'
    result = self.run_helper('--build')
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('.SRCINFO is stale', result.stderr)
    self.assertFalse(any(item['command'] == 'mkarchroot'
                         for item in self.records()))

  @unittest.skipUnless(CAN_BUILD, 'Build requires a normal Arch x86_64 user')
  def test_failed_chroot_preserves_driver_log_and_exit_status(self):
    self.env['TEST_FAIL_CHROOT'] = '1'
    result = self.run_helper('--build')
    self.assertEqual(result.returncode, 31, result.stderr)
    self.assertIn('fixture chroot failure',
                  (self.work / 'logs/mkarchroot.log').read_text())
    self.assertFalse(any(item['command'] == 'makechrootpkg'
                         for item in self.records()))

  @unittest.skipUnless(CAN_BUILD, 'Build requires a normal Arch x86_64 user')
  def test_failed_dkms_preserves_completed_utils_and_logs(self):
    self.env['TEST_FAIL_BUILD'] = 'zfs-dkms'
    result = self.run_helper('--build')
    self.assertEqual(result.returncode, 37, result.stderr)
    self.assertEqual(len(list(self.output.iterdir())), 1)
    self.assertIn('fixture package build failure',
                  (self.work / 'logs/zfs-dkms/driver.log').read_text())
    self.assertTrue((self.work / 'logs/zfs-dkms/fixture-build.log').is_file())

  @unittest.skipUnless(CAN_BUILD, 'Build requires a normal Arch x86_64 user')
  def test_missing_utils_archive_prevents_dkms_build(self):
    self.env['TEST_NO_ARTIFACT'] = 'zfs-utils'
    result = self.run_helper('--build')
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('no built zfs-utils archive', result.stderr)
    builds = [item for item in self.records()
              if item['command'] == 'makechrootpkg']
    self.assertEqual([item['recipe'] for item in builds], ['zfs-utils'])

  @unittest.skipUnless(CAN_BUILD, 'Build requires a normal Arch x86_64 user')
  def test_incorrect_archive_version_prevents_dkms_build(self):
    self.env['TEST_ARCHIVE_VERSION'] = '2.3.2-1'
    result = self.run_helper('--build')
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('differs from recipe', result.stderr)
    self.assertEqual(list(self.output.iterdir()), [])
    self.assertTrue((self.work / 'artifacts/zfs-utils').is_dir())

  @unittest.skipUnless(CAN_BUILD, 'Build requires a normal Arch x86_64 user')
  def test_unreadable_archive_fails_package_selection(self):
    self.env['TEST_INVALID_ARCHIVE'] = '1'
    result = self.run_helper('--build')
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('cannot read built package archive', result.stderr)
    self.assertEqual(list(self.output.iterdir()), [])


if __name__ == '__main__':
  unittest.main()
