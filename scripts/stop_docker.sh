#!/usr/bin/env sh
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project"
docker compose down
printf '%s\n' 'Local containers stopped; the result volume was preserved.'
printf '%s\n' '本地容器已停止，结果卷已保留。'
