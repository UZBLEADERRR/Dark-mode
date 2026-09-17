#!/usr/bin/env bash
#
# Build the ffmpeg and ffprobe the phone runs.
#
# The app shells out to ffmpeg for every frame it draws, so the phone build needs
# one — and there is no package manager on Android to install it from. This
# cross-compiles a static pair against the NDK, which is the whole of the phone
# build that cannot be expressed in Gradle.
#
# Two constraints shape it:
#
# **Static.** Each binary is linked against its own copy of x264, freetype and
# libass, because Android has no place to put a shared library that another
# process would find, and `lib*.so` in the package's library folder is the only
# file the kernel will let this app execute at all.
#
# **libass without fontconfig.** There is no font database on Android to ask, so
# libass is built with only its directory provider and the app names a folder in
# `SUBTITLE_FONTSDIR`. harfbuzz is left out with it: it shapes Arabic and the
# Indic scripts, it no longer builds with autotools, and the languages this app
# is written in are set in Latin and Cyrillic.
#
# Usage:  ANDROID_NDK_HOME=/path/to/ndk scripts/build-ffmpeg-android.sh out/
# Result: out/libffmpeg.so and out/libffprobe.so, both arm64-v8a executables.

set -euo pipefail

mkdir -p "${1:-out}"
OUT_DIR="$(cd "${1:-out}" && pwd)"
WORK="${WORK_DIR:-$(pwd)/.ffmpeg-build}"

X264_TAG="${X264_TAG:-stable}"
FREETYPE_VERSION="${FREETYPE_VERSION:-2.13.3}"
LIBASS_VERSION="${LIBASS_VERSION:-0.17.3}"
FFMPEG_VERSION="${FFMPEG_VERSION:-7.1}"

# 26 is the app's own minSdk. Raising it here without raising it there produces
# binaries that refuse to load on devices the app otherwise installs on.
API="${ANDROID_API:-26}"
ARCH=aarch64
TRIPLE=aarch64-linux-android

: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the NDK you want to build against}"

HOST_TAG=linux-x86_64
TOOLCHAIN="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/$HOST_TAG"
[ -d "$TOOLCHAIN" ] || { echo "no toolchain at $TOOLCHAIN" >&2; exit 1; }

PREFIX="$WORK/prefix"
mkdir -p "$WORK" "$PREFIX" "$OUT_DIR"

export PATH="$TOOLCHAIN/bin:$PATH"
export CC="$TOOLCHAIN/bin/${TRIPLE}${API}-clang"
export CXX="$TOOLCHAIN/bin/${TRIPLE}${API}-clang++"
export AR="$TOOLCHAIN/bin/llvm-ar"
export RANLIB="$TOOLCHAIN/bin/llvm-ranlib"
export STRIP="$TOOLCHAIN/bin/llvm-strip"
export NM="$TOOLCHAIN/bin/llvm-nm"
export SYSROOT="$TOOLCHAIN/sysroot"
export CFLAGS="-O3 -fPIC -DANDROID -I$PREFIX/include"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-L$PREFIX/lib"
# Only our own prefix, never the build machine's: a host .pc file found here
# links the phone binary against a library that is not on the phone.
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"

JOBS="$(nproc 2>/dev/null || echo 4)"

fetch() {  # fetch <url> <directory-it-unpacks-to>
  local url="$1" dir="$2" file="${1##*/}"
  if [ -d "$WORK/$dir" ]; then echo "→ have $dir"; return; fi
  echo "→ fetching $file"
  curl -fsSL --retry 4 --retry-delay 3 -o "$WORK/$file" "$url"
  tar -xf "$WORK/$file" -C "$WORK"
}

# ── x264 ──────────────────────────────────────────────────────────────────────
if [ ! -f "$PREFIX/lib/libx264.a" ]; then
  echo "══ x264"
  rm -rf "$WORK/x264"
  git clone --depth 1 --branch "$X264_TAG" https://code.videolan.org/videolan/x264.git "$WORK/x264"
  (
    cd "$WORK/x264"
    # `--disable-cli` because only the library is wanted, and the command line
    # tool is what pulls in the dependencies that do not cross-compile.
    ./configure \
      --prefix="$PREFIX" \
      --host="$TRIPLE" \
      --enable-static \
      --enable-pic \
      --disable-cli \
      --disable-opencl \
      --extra-cflags="$CFLAGS"
    make -j"$JOBS"
    make install
  )
fi

# ── freetype ──────────────────────────────────────────────────────────────────
if [ ! -f "$PREFIX/lib/libfreetype.a" ]; then
  echo "══ freetype"
  fetch "https://download.savannah.gnu.org/releases/freetype/freetype-$FREETYPE_VERSION.tar.xz" \
        "freetype-$FREETYPE_VERSION"
  (
    cd "$WORK/freetype-$FREETYPE_VERSION"
    # Everything optional is off: this build renders glyphs from a .ttf and does
    # nothing else, and each `--with` here is a library that would have to be
    # cross-compiled first.
    ./configure \
      --prefix="$PREFIX" \
      --host="$TRIPLE" \
      --enable-static --disable-shared \
      --with-zlib=no --with-bzip2=no --with-png=no \
      --with-harfbuzz=no --with-brotli=no
    make -j"$JOBS"
    make install
  )
fi

# ── libass ────────────────────────────────────────────────────────────────────
if [ ! -f "$PREFIX/lib/libass.a" ]; then
  echo "══ libass"
  fetch "https://github.com/libass/libass/releases/download/$LIBASS_VERSION/libass-$LIBASS_VERSION.tar.gz" \
        "libass-$LIBASS_VERSION"
  (
    cd "$WORK/libass-$LIBASS_VERSION"
    # `--disable-require-system-font-provider` is the one that matters: without
    # it configure refuses a build that has no way to look a font family up, and
    # the directory provider this app uses is exactly that build.
    ./configure \
      --prefix="$PREFIX" \
      --host="$TRIPLE" \
      --enable-static --disable-shared \
      --disable-fontconfig \
      --disable-harfbuzz \
      --disable-fribidi \
      --disable-require-system-font-provider
    make -j"$JOBS"
    make install
  )
fi

# ── ffmpeg ────────────────────────────────────────────────────────────────────
echo "══ ffmpeg"
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" "ffmpeg-$FFMPEG_VERSION"
(
  cd "$WORK/ffmpeg-$FFMPEG_VERSION"
  [ -f config.h ] || ./configure \
    --prefix="$PREFIX" \
    --target-os=android \
    --arch="$ARCH" \
    --cpu=armv8-a \
    --enable-cross-compile \
    --cross-prefix="$TOOLCHAIN/bin/llvm-" \
    --cc="$CC" --cxx="$CXX" --ar="$AR" --ranlib="$RANLIB" --nm="$NM" --strip="$STRIP" \
    --sysroot="$SYSROOT" \
    --pkg-config=pkg-config --pkg-config-flags=--static \
    --extra-cflags="$CFLAGS" \
    --extra-ldflags="$LDFLAGS" \
    --extra-libs="-lm" \
    --enable-static --disable-shared \
    --enable-pic \
    --enable-gpl --enable-version3 \
    --enable-libx264 \
    --enable-libass --enable-libfreetype \
    --disable-doc --disable-debug \
    --disable-avdevice --disable-indevs --disable-outdevs \
    --disable-postproc \
    --disable-vulkan \
    --disable-ffplay \
    --enable-ffmpeg --enable-ffprobe
  make -j"$JOBS"
)

cp "$WORK/ffmpeg-$FFMPEG_VERSION/ffmpeg" "$OUT_DIR/libffmpeg.so"
cp "$WORK/ffmpeg-$FFMPEG_VERSION/ffprobe" "$OUT_DIR/libffprobe.so"
"$STRIP" "$OUT_DIR/libffmpeg.so" "$OUT_DIR/libffprobe.so"

echo
echo "built into $OUT_DIR:"
ls -lh "$OUT_DIR"
file "$OUT_DIR/libffmpeg.so" || true
