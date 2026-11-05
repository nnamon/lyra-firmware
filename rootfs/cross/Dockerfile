# Cross toolchain for the Luckfox Lyra Zero W userspace.
#
# Bookworm's armhf cross target has glibc 2.36, older than the board's 2.38.
# A call to anything newer than the board has fails at link time here.
#
# On trixie (glibc 2.41) the same call links, and the binary dies on the
# board. `./cross.sh check` catches that, if you run it.
FROM debian:bookworm

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      crossbuild-essential-armhf pkg-config make cmake file \
      qemu-user-static binutils-arm-linux-gnueabihf \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
