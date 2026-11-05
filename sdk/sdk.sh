#!/usr/bin/env bash
# Build the Luckfox Lyra SDK in Docker, for the Lyra Zero W on SPI NAND.
set -euo pipefail
cd "$(dirname "$0")"

TOOLS_IMAGE=lyra-sdk-tools
VOLUME=${LYRA_SDK_VOLUME:-lyra-sdk}
PLATFORM=linux/amd64

# Selected non-interactively, because ./build.sh accepts a defconfig name and
# that is less fragile than feeding numbers into the lunch menu. This one is
# the Zero W on SPI NAND with Buildroot, the same as lunch options 4, 1, 0.
DEFCONFIG=luckfox_lyra_zero-w_buildroot_spinand_defconfig

die() { echo "error: $*" >&2; exit 1; }

build_tools() {
  docker image inspect "$TOOLS_IMAGE" >/dev/null 2>&1 && return 0
  echo "Building $TOOLS_IMAGE (once, several minutes under emulation)"
  docker build --platform "$PLATFORM" -t "$TOOLS_IMAGE" .
}

have_sdk() {
  docker run --rm --platform "$PLATFORM" -v "$VOLUME:/sdk" "$TOOLS_IMAGE" \
    test -f /sdk/build.sh 2>/dev/null
}

# Run a script in the SDK tree. Extra docker arguments come before the image.
in_sdk() {
  local script=$1; shift
  # A plain string, not an array: macOS ships bash 3.2, where expanding an
  # empty array under `set -u` is an unbound variable error.
  local tty=""
  [ -t 0 ] && tty="-it"
  # shellcheck disable=SC2086
  docker run --rm $tty --platform "$PLATFORM" \
    -v "$VOLUME:/sdk" -w /sdk \
    -e "DEFCONFIG=$DEFCONFIG" \
    "$@" "$TOOLS_IMAGE" bash -eu -c "$script"
}

cmd_init() {
  local tgz=${1:-}
  [ -n "$tgz" ] || die "usage: ./sdk.sh init <luckfox-lyra-<date>.tar.gz>"
  [ -f "$tgz" ] || die "no such file: $tgz"
  tgz=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$tgz")
  build_tools
  docker volume create "$VOLUME" >/dev/null

  # The Lyra tarball ships only the .repo mirror, so the checkout has to run
  # before anything looks for build.sh. That is the order the vendor wiki
  # gives: tar, then `.repo/repo/repo sync -l`.
  in_sdk '
    sudo chown "$(id -u):$(id -g)" /sdk
    if [ -f /sdk/build.sh ] || [ -d /sdk/.repo ]; then
      echo "SDK already in the volume, skipping extraction"
    else
      echo "Extracting $(basename /in/sdk.tar.gz), this takes a while"
      tar -C /sdk --warning=no-unknown-keyword -xf /in/sdk.tar.gz
      # Some releases wrap everything in one top-level directory.
      if [ ! -f /sdk/build.sh ] && [ ! -d /sdk/.repo ]; then
        inner=$(find /sdk -maxdepth 2 \( -name build.sh -o -name .repo \) | head -1)
        [ -n "$inner" ] || { echo "no build.sh and no .repo in the tarball" >&2; exit 1; }
        d=$(dirname "$inner")
        if [ "$d" != /sdk ]; then
          echo "Moving $d up to /sdk"
          ( shopt -s dotglob; mv "$d"/* /sdk/ )
        fi
      fi
    fi

    if [ -d /sdk/.repo ] && [ ! -f /sdk/build.sh ]; then
      echo "Checking out the working tree from .repo"
      ./.repo/repo/repo sync -l
    fi
    [ -f /sdk/build.sh ] || { echo "still no build.sh after checkout" >&2; exit 1; }

    echo
    echo "Selecting $DEFCONFIG"
    if ! ./build.sh "$DEFCONFIG"; then
      defs=$(find device/rockchip -name "*_defconfig" -path "*3506*" \
        -printf "  %f\n" 2>/dev/null | sort || true)
      echo >&2
      if [ -n "$defs" ]; then
        echo "This SDK has no $DEFCONFIG. The rk3506 defconfigs it has:" >&2
        echo "$defs" >&2
      else
        echo "This SDK has no $DEFCONFIG, and no rk3506 defconfig at all." >&2
      fi
      echo >&2
      echo "Set DEFCONFIG at the top of sdk.sh to one of those, or run" >&2
      echo "./sdk.sh shell and then ./build.sh lunch to pick interactively." >&2
      exit 1
    fi
  ' -v "$tgz:/in/sdk.tar.gz:ro"

  echo
  echo "Ready. Next: ./sdk.sh apply, then ./sdk.sh build  (hours on a first build)"
}

cmd_shell() {
  build_tools
  have_sdk || die "no SDK in volume $VOLUME; run ./sdk.sh init <tarball> first"
  docker run --rm -it --platform "$PLATFORM" -v "$VOLUME:/sdk" -w /sdk \
    "$TOOLS_IMAGE" bash --login
}

# The SDK's build.sh exits 0 even when a build hook fails, so a failed build
# looks like a successful one. Check the artefact the target should produce.
# Images land in output/firmware (RK_FIRMWARE_DIR); rockdev is only a symlink
# to it, and mk-firmware.sh creates that symlink, so it is absent until a
# firmware build has run. They are symlinks into the build trees, so test -e
# (which follows them) rather than test -f on the link.
FW=output/firmware

expected_artefact() {
  case "$1" in
    rootfs)   echo rootfs.img ;;
    kernel)   echo boot.img ;;
    uboot)    echo uboot.img ;;
    firmware|"") echo update.img ;;
    *)        echo "" ;;
  esac
}

cmd_build() {
  build_tools
  have_sdk || die "no SDK in volume $VOLUME; run ./sdk.sh init <tarball> first"
  local args="$*"
  local want; want=$(expected_artefact "${1:-}")
  local before=""
  if [ -n "$want" ]; then
    before=$(in_sdk "stat -Lc %Y $FW/$want 2>/dev/null || echo none" 2>/dev/null | tr -d "\r")
  fi
  in_sdk "./build.sh $args"
  [ -n "$want" ] || return 0
  local after
  after=$(in_sdk "stat -Lc %Y $FW/$want 2>/dev/null || echo none" 2>/dev/null | tr -d "\r")
  if [ "$after" = "none" ]; then
    die "build reported success but $FW/$want does not exist"
  fi
  if [ "$after" = "$before" ]; then
    die "build reported success but $FW/$want was not rebuilt"
  fi
  in_sdk "ls -lL $FW/$want"
}

cmd_apply() {
  build_tools
  have_sdk || die "no SDK in volume $VOLUME; run ./sdk.sh init <tarball> first"
  # The script cd'd to its own directory at the top, so this is beside it.
  local cfg; cfg=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' config)
  [ -d "$cfg" ] || die "no config directory at $cfg"
  in_sdk "sh /config/apply.sh" -v "$cfg:/config:ro"
}

cmd_fetch() {
  # Default to the three partition images the template replaces.
  # output/firmware also holds MiniLoaderAll.bin and parameter.txt, and the
  # build starts from the vendor image and keeps the vendor loader, so copying
  # all of it over images/ would quietly discard that. `fetch all` opts in.
  local take_all=0
  if [ "${1:-}" = all ]; then take_all=1; shift; fi
  local dest=${1:-../images}
  mkdir -p "$dest"
  dest=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$dest")
  build_tools
  in_sdk '
    [ -d /sdk/output/firmware ] || { echo "nothing built yet: no output/firmware/" >&2; exit 1; }
    if [ "$TAKE_ALL" = 1 ]; then
      echo "Taking all of output/firmware/, including the SDK loader and parameter.txt."
      cp -Lv /sdk/output/firmware/* /out/
      exit 0
    fi
    copied=0
    for f in uboot.img boot.img rootfs.img; do
      if [ -f "/sdk/output/firmware/$f" ]; then
        cp -Lv "/sdk/output/firmware/$f" /out/
        copied=$((copied + 1))
      else
        echo "not built: $f"
      fi
    done
    if [ "$copied" = 0 ]; then
      echo "output/firmware/ holds none of the three partition images" >&2
      exit 1
    fi
    left=""
    for f in /sdk/output/firmware/*; do
      b=$(basename "$f")
      case "$b" in
        uboot.img|boot.img|rootfs.img) ;;
        *) left="$left $b" ;;
      esac
    done
    if [ -n "$left" ]; then
      echo "left behind:$left"
      echo "The vendor MiniLoaderAll.bin and parameter.txt stay as they are."
      echo "Run ./sdk.sh fetch all to take the SDK versions instead."
    fi
  ' -v "$dest:/out" -e "TAKE_ALL=$take_all"
  echo "copied into $dest"
}

cmd_get() {
  local path=${1:-} dest=${2:-.}
  [ -n "$path" ] || die "usage: ./sdk.sh get <path in the SDK> [destination]"
  mkdir -p "$dest"
  dest=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$dest")
  build_tools
  in_sdk "cp -Lrv \"/sdk/$path\" /out/" -v "$dest:/out"
}

cmd_put() {
  local src=${1:-} path=${2:-}
  [ -n "$src" ] && [ -n "$path" ] || die "usage: ./sdk.sh put <local file> <path in the SDK>"
  [ -f "$src" ] || die "no such file: $src"
  src=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$src")
  build_tools
  in_sdk "cp -v /in/file \"/sdk/$path\"" -v "$src:/in/file:ro"
}

cmd_status() {
  build_tools
  if ! have_sdk; then echo "volume $VOLUME holds no SDK"; return 0; fi
  # output/defconfig and device/rockchip/.chip are the symlinks the SDK itself
  # keeps, as RK_DEFCONFIG_LINK and RK_CHIP_DIR in its build.sh.
  in_sdk '
    if [ -L output/defconfig ]; then
      echo "defconfig: $(basename "$(readlink -f output/defconfig)")"
    else
      echo "defconfig: not selected"
    fi
    if [ -L device/rockchip/.chip ]; then
      echo "chip:      $(basename "$(readlink -f device/rockchip/.chip)")"
    fi
    echo "tree size: $(du -sh /sdk 2>/dev/null | cut -f1)"
    echo
    if [ -d /sdk/output/firmware ]; then echo "output/firmware/:"; ls -lL /sdk/output/firmware/;
    else echo "output/firmware/: nothing built yet"; fi
  '
}

cmd_clean() {
  # Docker reports the volume as still in use for a moment after a --rm
  # container exits, so retry rather than fail on the race.
  local i
  for i in 1 2 3 4 5; do
    if docker volume rm -f "$VOLUME" >/dev/null 2>&1; then
      echo "removed volume $VOLUME"
      return 0
    fi
    sleep 1
  done
  docker volume rm -f "$VOLUME"
}

usage() {
  cat <<USAGE
Usage: ./sdk.sh <command> [args]

  init <sdk.tar.gz>   extract the SDK into the work volume, check out the
                      working tree from .repo, and select the Zero W SPI NAND
                      Buildroot config
  apply               reapply this repo's SDK changes (device tree, mirrors,
                      root filesystem overlay). Run after init; idempotent
  build [target]      run ./build.sh in the SDK. With no target, build
                      everything. Useful targets:
                        rootfs            rebuild rootfs.img
                        kernel            rebuild boot.img
                        uboot             rebuild uboot.img and the loader
                        firmware          pack output/firmware/
                        buildroot-config  add or remove packages
                        kernel-config     change kernel options
  fetch [dir]         copy uboot.img, boot.img and rootfs.img out to dir
                      (default ../images), keeping the vendor loader and
                      parameter.txt that are already there
  fetch all [dir]     copy all of output/firmware/, SDK loader included
  get <path> [dir]    copy one path out of the SDK tree
  put <file> <path>   copy one file into the SDK tree
  shell               interactive shell in the SDK tree
  status              selected config, tree size, built images
  clean               delete the work volume and everything in it

The SDK tarball comes from Luckfox's Google Drive. This script will not
download it:
https://drive.google.com/drive/folders/1l2ixhfw53J3eZunyvHMnw2DYViH9d8Cx
USAGE
  exit 1
}

case "${1:-}" in
  init)   shift; cmd_init "$@" ;;
  apply)  shift; cmd_apply "$@" ;;
  build)  shift; cmd_build "$@" ;;
  fetch)  shift; cmd_fetch "$@" ;;
  get)    shift; cmd_get "$@" ;;
  put)    shift; cmd_put "$@" ;;
  shell)  shift; cmd_shell "$@" ;;
  status) shift; cmd_status "$@" ;;
  clean)  shift; cmd_clean "$@" ;;
  *) usage ;;
esac
