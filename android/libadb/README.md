# libadb (vendored)

The adb client library the ArrowBot app uses to reach the phone's own adbd (Wireless debugging,
or `adb tcpip`): [libadb-android](https://github.com/MuntashirAkon/libadb-android) 3.1.1, commit
`c849886ebc6d48e7b46d967e78a6bb65c90c3b74` (the latest release, December 2025), module `libadb`.

It's copied here, instead of downloaded from JitPack, so that faults in it can be fixed. Upstream is
licensed `GPL-3.0-or-later OR Apache-2.0` (see `COPYING`); it's used here under Apache-2.0. Parts
come from AdbLib (BSD-3-Clause) and other projects (MIT): see each file's header and `LICENSES/`.

## Changes from upstream

None yet: this commit is upstream's code as released.
