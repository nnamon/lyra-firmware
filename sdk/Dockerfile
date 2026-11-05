# check=skip=FromPlatformFlagConstDisallowed
# That parser directive must stay on the first line. It silences the warning
# about the fixed platform below.
#
# Build environment for the Luckfox Lyra SDK. It must be amd64: the SDK ships
# x86_64 host tools under prebuilts/gcc/linux-x86/, and Buildroot needs a host
# compiler that emits 32-bit binaries.
FROM --platform=linux/amd64 ubuntu:22.04

ARG UID=1000
ARG GID=1000
ARG UNAME=builder

ENV DEBIAN_FRONTEND=noninteractive
ENV LC_ALL=C.UTF-8

# 1. The SDK's preflight checks: check-sdk.sh wants python3, rsync, gcc, g++
#    and findmnt (util-linux); check-buildroot.sh wants make >= 4.0 and
#    unbuffer (expect).
# 2. Luckfox's list from wiki.luckfox.com/Luckfox-Lyra/SDK-Image-Compilation,
#    unpruned: a missing package fails the build hours in. It leaves out
#    python-is-python3, which owns /usr/bin/python and conflicts with the
#    python2 symlink the same page asks for.
# 3. The rest of the Buildroot and kernel build set.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 rsync gcc g++ make util-linux expect expect-dev \
      git ssh libssl-dev liblz4-tool patchelf chrpath gawk texinfo diffstat \
      binfmt-support qemu-user-static live-build bison flex fakeroot cmake \
      gcc-multilib g++-multilib unzip device-tree-compiler ncurses-dev \
      libgucharmap-2-90-dev bzip2 expat gpgv2 cpp-aarch64-linux-gnu \
      libgmp-dev libmpc-dev bc python2 curl file bsdmainutils scons \
      cpio gzip lz4 patch perl wget xz-utils zstd ca-certificates sudo \
      libncurses-dev libc6-dev u-boot-tools \
      python3-dev python3-pip libsqlite3-dev bsdextrautils locales \
 && rm -rf /var/lib/apt/lists/*

# Some Buildroot packages call `python` and mean Python 2, as Luckfox's page
# assumes. If a package needs Python 3 under that name, run
# `sudo ln -sf /usr/bin/python3 /usr/bin/python` in the container.
RUN ln -sf /usr/bin/python2 /usr/bin/python

# The SDK requires the current user to own the tree, and Buildroot's configure
# scripts refuse root.
RUN groupadd -g ${GID} -o ${UNAME} \
 && useradd -m -u ${UID} -g ${GID} -o -s /bin/bash ${UNAME} \
 && echo "${UNAME} ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/${UNAME} \
 && mkdir -p /sdk && chown ${UID}:${GID} /sdk

USER ${UNAME}
WORKDIR /sdk
CMD ["/bin/bash", "--login"]
