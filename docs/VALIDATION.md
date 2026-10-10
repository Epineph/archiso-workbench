# Validation record

Date: 7 September 2026.

## Executed here

Environment: Ubuntu 24.04, Python 3, Bash. No Arch host package installation,
physical block-device access, partitioning, or ISO writing was performed.

Seven offline regression tests passed:

1. Reject common secret filenames and symlinks in additions.
2. Reject copying through an inherited destination symlink.
3. Stage a synthetic releng fixture while preserving boot configuration and the
   original base profile; verify signatures, archive URL, executable metadata,
   the live pacman configuration, and the generated manifest.
4. Preview target configuration changes without mutation, then verify backed-up
   replacement and ownership selection from the target passwd file. Mount-point
   and ownership operations are mocked; copying and backup inspection are real.
5. Reject internal disks, partitions, read-only devices, missing serials, and
   mounted USB descendants using mocked lsblk responses. No dd invocation.
6. Reject parent traversal in the target path.
7. Check the builder's help entry point.

All Bash scripts passed `bash -n`. Python sources passed AST parsing. All authored
script lines meet the 81-column limit. ShellCheck and shfmt were unavailable in
this environment and were not run.

Re-run the offline tests with:

```bash
python3 -m unittest discover -s tests -v
```

## Build-script correction, 6 October 2026

Environment confirmed as x86_64 Arch Linux, with Archiso 91 and pacman 7.1.
Removed the broken post-build unofficial repository setup; retained the user's
personal package additions. Post-build inventory and ZFS package queries now use
`pacman --config /dev/null -Q` to avoid opening deleted sync databases.

Verification performed:

- All 12 offline regression tests passed. The new inventory regression uses real
  pacman against a synthetic local database with no sync databases; privileged
  commands and ISO creation are mocked.
- Staged the installed releng profile successfully: 177 requested packages.
- All requested packages resolved through official repositories using the host's
  cached metadata (`pacman -Sp`); no databases were refreshed or packages installed.
- Bash syntax, ShellCheck, shfmt, Ruff, and `git diff --check` passed for the
  relevant changed files. Authored script lines stay within 82 columns.
- A query against a retained real build returned the same 685 installed packages
  with and without the fix; the fixed query emitted no missing-database warnings.

No new privileged ISO build, ZFS compilation, or VM boot was performed in this
correction. Those checks remain required below.

## ZFS build integration, 10 October 2026

Environment: x86_64 Arch Linux, Archiso 91, pacman 7.1, installed devtools.
The retained run's log ends at package retrieval without a recorded error;
it contains no ZFS transaction. Local ZFS directories contain reviewed source
recipes rather than compiled archives.

Verification performed:

- All 45 regression tests passed. Bash syntax, ShellCheck, shfmt, Ruff and
  whitespace checks passed for the relevant changed and new files.
- Real installed-releng staging: rescue 142 requested packages, personal 177,
  and rescue-ZFS 151 with genuine synthetic package archives. Official package
  selections resolved against cached repository metadata with `pacman -Sp`.
- Real recipe metadata plan for both local OpenZFS 2.4.4 checkouts, without
  executing recipes, creating build directories or downloading sources.
- Offline helper tests cover clean-chroot prerequisites, package order and
  utilities injection, private snapshot/core settings, stale metadata and
  archive failures, failure propagation, and retained diagnostics. Privileged
  tools and compilation are mocked.
- Archive tests use actual bsdtar, pacman, vercmp and repo-add. They check
  version constraints, architecture, duplicate/symlink rejection, repository
  contents and hashes, executable modes, service masks and separate live config.
- Module tests use isolated filesystem trees and mocked package/kmod tools.
  They check headers, dependency indexes, missing/wrong modules and failed
  dependency resolution, and verify that the image tree remains unchanged.
- Wrapper integration tests use real synthetic archives and mocked package
  compilation/ISO creation. Failed compilation prevents staging; failed ZFS
  verification retains a candidate without publishing an ISO or checksum.

No host packages or reviewed recipes were modified. Actual source compilation,
privileged ISO creation and VM module loading remain required. The sandbox's
no-new-privileges restriction prevents real sudo execution in this session.

## Required on the build/test host

- Real current releng profile staging and dependency resolution.
- Complete mkarchiso execution with official package signature verification.
- Local package trust configuration and any snapshot-specific dependencies.
- Actual ZFS DKMS compilation, post-build module checks, and live module loading.
- UEFI boot using matching OVMF firmware; BIOS boot if required.
- Actual locale generation, live scripts, and configuration payload visibility.
- Trial target installation/config application in a disposable VM.
- Physical USB write/read-back, if that optional helper is used.

A source test is not evidence that a bootable ISO has been produced. This package
contains no prebuilt ISO. The full build and hardware-dependent checks remain
unverified. The wrapper retains candidate images when validation fails.
