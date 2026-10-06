#!/usr/bin/env python3
"""Offline builder parallelism checks; no sudo or real ISO build."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


class BuildCoresTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.root = Path(self.temp.name)
    self.base = self.root / 'base'
    self.base.mkdir()
    (self.base / 'airootfs').mkdir()
    (self.base / 'profiledef.sh').write_text(
      'declare -A file_permissions=()\n'
      "airootfs_image_type='squashfs'\n"
      "airootfs_image_tool_options=('-comp' 'xz')\n")
    (self.base / 'packages.x86_64').write_text('linux\nmkinitcpio\n')
    (self.base / 'pacman.conf').write_text('[options]\n')
    self.bin = self.root / 'bin'
    self.bin.mkdir()
    self.command('nproc', '#!/bin/sh\nprintf "%s\\n" "$TEST_CPUS"\n')
    self.env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                'TEST_CPUS': '16'}
    self.runs = 0

  def tearDown(self):
    self.temp.cleanup()

  def command(self, name, text):
    path = self.bin / name
    path.write_text(text)
    path.chmod(0o755)

  def run_builder(self, *options):
    self.runs += 1
    output = self.root / f'build-{self.runs}'
    result = subprocess.run(
      ['bash', str(KIT / 'build-iso.sh'), '--base-profile', str(self.base),
       '--output', str(output), *options], env=self.env,
      capture_output=True, text=True, check=False)
    return result, output

  def check_profile(self, expected, *options):
    result, output = self.run_builder(*options)
    self.assertEqual(result.returncode, 0, result.stderr)
    run = next(output.glob('run-*'))
    profile = run / 'profile/profiledef.sh'
    values = subprocess.check_output(
      ['bash', '-c', ('source "$1"; '
       'printf "%s\\n" "${airootfs_image_tool_options[@]}"'),
       'test-profile', str(profile)], text=True).splitlines()
    self.assertEqual(values, ['-comp', 'xz', '-processors', str(expected)])
    manifest = json.loads((run / 'manifest.json').read_text())
    self.assertEqual(manifest['cores'], expected)
    self.assertEqual(manifest['sha256']['profiledef.sh'],
                     hashlib.sha256(profile.read_bytes()).hexdigest())
    self.assertIn(f'Build cores: {expected}', result.stdout)
    return run

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  def test_default_uses_half_available_cpus(self):
    for available, expected in [(1, 1), (2, 1), (3, 1), (8, 4), (16, 8)]:
      with self.subTest(available=available):
        self.env['TEST_CPUS'] = str(available)
        self.check_profile(expected)

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  def test_explicit_core_counts(self):
    for options, expected in [(['--cores', '3'], 3), (['-j6'], 6),
                              (['--cores=5'], 5), (['-j', '2'], 2),
                              (['-j3', '--cores', '7'], 7)]:
      with self.subTest(options=options):
        self.check_profile(expected, *options)

  def test_invalid_core_counts_create_no_build(self):
    for options in [['--cores'], ['-j'], ['--cores', '0'], ['-j0'],
                    ['-j-1'], ['-jfoo'], ['--cores', ''], ['--cores='],
                    ['--cores', '1.5'], ['--cores', '--build']]:
      with self.subTest(options=options):
        result, output = self.run_builder(*options)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Error:', result.stderr)
        self.assertFalse(output.exists())

  @unittest.skipIf(os.geteuid() == 0, 'Builder requires a normal user')
  @unittest.skipUnless(Path('/etc/arch-release').exists() and
                       shutil.which('taskset'), 'Arch/taskset build preflight')
  def test_build_limits_cpu_affinity_and_make_jobs(self):
    # Replace privileged commands; exercise the real taskset and env invocation.
    self.command('sudo', '''#!/bin/sh
case "$1" in
  taskset) exec "$@" ;;
  arch-chroot) printf 'linux fixture\\n' ;;
  chown) exit 0 ;;
  *) exit 1 ;;
esac
''')
    for name in ['arch-chroot', 'xorriso']:
      self.command(name, '#!/bin/sh\nexit 0\n')
    self.command('mkarchiso', '''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
work = Path(args[args.index('-w') + 1])
output = Path(args[args.index('-o') + 1])
(work / 'x86_64/airootfs').mkdir(parents=True)
(output / 'fixture.iso').write_text('offline fixture')
(work.parent / 'cpu-check.json').write_text(json.dumps({
  'cpus': sorted(os.sched_getaffinity(0)),
  'makeflags': os.environ['MAKEFLAGS']}))
''')
    run = self.check_profile(2, '--build', '-j2')
    observed = json.loads((run / 'cpu-check.json').read_text())
    self.assertEqual(observed['cpus'], sorted(os.sched_getaffinity(0))[:2])
    self.assertEqual(observed['makeflags'], '-j2')
    self.assertTrue((run / 'out/fixture.iso').is_file())


if __name__ == '__main__':
  unittest.main()
