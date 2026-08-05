#!/usr/bin/env sh
set -eu

project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project"
if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' 'Docker is not installed: https://docs.docker.com/engine/install/' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  printf '%s\n' 'Docker Engine or Compose is not ready.' >&2
  exit 1
fi

docker compose up --build --detach
attempt=0
until curl --fail --silent http://127.0.0.1:3200/ >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 90 ]; then
    docker compose ps
    printf '%s\n' "The local site did not become ready. Run 'docker compose logs'." >&2
    exit 1
  fi
  sleep 2
done

printf '%s\n' 'Local publication tool is ready: http://localhost:3200/'
printf '%s\n' '本地论文分型工具已就绪：http://localhost:3200/'
