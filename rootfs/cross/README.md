# Cross-compiling for the Lyra Zero W

`cross.sh` builds programs for the board in Docker. The board has no compiler,
no `/usr/include` and no static libc, so you can't build on it.

## Target

These values come from the board's own `busybox`:

| Property | Value |
| --- | --- |
| Architecture | ARMv7-A, Cortex-A7 (`Tag_CPU_arch: v7`) |
| Float | hard float, VFPv4, arguments in VFP registers |
| ELF | ELF32, little endian, EABI5, flags `0x5000400` |
| C library | glibc 2.38 |
| Interpreter | `/lib/ld-linux-armhf.so.3` |

`cross.sh` passes `-march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard` for you.

## Build and install

1. Compile in your source directory:

   ```sh
   /path/to/cross.sh cc -O2 hello.c -o hello
   ```

   `c++` and `make` work the same way. `make` sets `CC`, `CXX`, `AR`, `STRIP`
   and the flags.
2. Check the binary will load on the board:

   ```sh
   /path/to/cross.sh check hello
   ```

   `check` compares the ELF flags, interpreter and highest `GLIBC_` symbol
   version with the board's. It exits 1 on a mismatch.
3. Run it under emulation against the board's own libraries:

   ```sh
   /path/to/cross.sh run hello
   ```

   This loads `ld.so` and libc from a tree unpacked with
   `../ubifs.sh unpack`, so run that first. Run both steps 2 and 3: `check`
   reads only the header, and only `run` finds a missing shared library.
4. Copy it to the board:

   ```sh
   scp hello root@192.168.123.100:/usr/bin/
   ```

   To ship it in an image, put it in `../../sdk/config/overlay/usr/bin/` and
   rebuild the root filesystem with the SDK.

## glibc version

glibc is backward compatible only. A binary that needs `GLIBC_2.39` loads on
the board's 2.38 and then dies.

The container uses Debian bookworm (glibc 2.36), so a call to anything newer
than the board's glibc fails at link time:

| Toolchain base | Trivial program | `pidfd_spawn`, a 2.39 symbol |
| --- | --- | --- |
| bookworm, glibc 2.36 | builds, runs on the board | link error |
| trixie, glibc 2.41 | builds, runs on the board | builds, `check` reports FAIL |

Static linking avoids the problem.

## Prebuilt binaries

A Debian armhf binary usually passes `check`, because Debian's armhf baseline
is also ARMv7 hard float. It usually fails on missing shared libraries. Build
it from source, link it statically, or copy each library it needs. `run`
names the first missing library, so repeat until it starts.
