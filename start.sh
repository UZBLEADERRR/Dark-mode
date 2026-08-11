#!/usr/bin/env sh
# Start Sarideo on this machine. macOS and Linux; Windows has start.bat.
#
#   ./start.sh            start it
#   ./start.sh flow       start it with the Flow Agent backend as well
#   ./start.sh stop       stop it
#   ./start.sh update     pull the latest code and rebuild
#
# It prints the address to open on this laptop and the one to open on a phone on
# the same Wi-Fi, because driving it from a phone while the laptop does the work
# is the whole point.
set -e
cd "$(dirname "$0")"

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker topilmadi."
  echo "Docker Desktop'ni o'rnating: https://docs.docker.com/get-docker/"
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ .env yaratildi. Kalitlaringizni shu faylga yozing (yoki ilova ichida,"
  echo "  Kutubxona → API kalitlari dan qo'shing) va qaytadan ishga tushiring."
  echo
fi

case "${1:-up}" in
  stop|down) docker compose --profile flow down; exit 0 ;;
  update)
    git pull --ff-only || true
    docker compose build --pull
    set -- up
    ;;
esac

PROFILE=""
[ "${1:-}" = "flow" ] && PROFILE="--profile flow"

# shellcheck disable=SC2086
docker compose $PROFILE up -d --build

PORT="$(grep -E '^PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2)"
PORT="${PORT:-8000}"

# The address a phone on the same network can reach. Several ways to ask,
# because none of them works everywhere.
LAN="$(ipconfig getifaddr en0 2>/dev/null \
  || hostname -I 2>/dev/null | awk '{print $1}' \
  || ip -4 addr show scope global 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)"

echo
echo "  Sarideo ishlayapti."
echo "  Shu noutbukda:  http://localhost:${PORT}"
[ -n "$LAN" ] && echo "  Telefonda:      http://${LAN}:${PORT}   (bir xil Wi-Fi'da)"
[ "${1:-}" = "flow" ] && echo "  Flow Agent:     http://localhost:8001"
echo
echo "  Jurnal:   docker compose logs -f sarideo"
echo "  To'xtatish: ./start.sh stop"
echo "  Hamma fayllar: ./data"
