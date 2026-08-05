#!/usr/bin/env sh
set -u

has() { command -v "$1" >/dev/null 2>&1; }
version_line() {
  if has "$1"; then "$1" --version 2>&1 | sed -n '1p'; else printf '%s\n' 'not found'; fi
}

docker_ready=false
if has docker && docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then docker_ready=true; fi

iqtree_command=""
for candidate in iqtree3 iqtree2 iqtree; do
  if has "$candidate"; then iqtree_command="$candidate"; break; fi
done

native_ready=false
if (has python3 || has python) && has node && has mafft && [ -n "$iqtree_command" ]; then native_ready=true; fi

printf '%s\n' 'Orientia TSA56 environment check'
printf 'CPU logical processors: %s\n' "$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '?')"
printf 'Docker ready: %s\n' "$docker_ready"
printf 'Native toolchain ready: %s\n' "$native_ready"
printf 'Node.js: %s\n' "$(version_line node)"
printf 'MAFFT: %s\n' "$(version_line mafft)"
if [ -n "$iqtree_command" ]; then printf 'IQ-TREE: %s\n' "$(version_line "$iqtree_command")"; else printf '%s\n' 'IQ-TREE: not found'; fi
printf '%s\n' 'IQ-TREE threads: AUTO'

if [ "$docker_ready" = true ]; then
  printf '%s\n' 'Docker is ready. Run ./scripts/start_docker.sh.'
  printf '%s\n' 'Docker 环境已就绪，请运行 ./scripts/start_docker.sh。'
elif [ "$native_ready" = true ]; then
  printf '%s\n' 'Native tools are available; Docker is still recommended for article reproduction.'
  printf '%s\n' '原生工具已齐全；论文复现仍建议使用 Docker。'
else
  printf '%s\n' 'Install Docker Desktop/Engine with Compose: https://docs.docker.com/engine/install/'
  printf '%s\n' '请安装 Docker Desktop/Engine 与 Compose：https://docs.docker.com/engine/install/'
fi
