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
