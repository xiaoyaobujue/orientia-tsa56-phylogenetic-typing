# Troubleshooting

## Environment check says Docker is unavailable

Install Docker Desktop/Engine with Compose from the official Docker documentation. Start Docker and rerun the environment check. Merely installing the command-line client is insufficient if the Docker engine is stopped.

## Port 3200 or 8200 is occupied

Stop the other local service before starting this tool. The release intentionally uses fixed localhost ports so the frontend/API contract remains simple and auditable.

## The first Docker start is slow

The first run downloads base images and installs pinned scientific/runtime dependencies. This requires internet access and can take several minutes. Later runs reuse the local images.

## Website opens but the service is unavailable

Run `docker compose ps` and `docker compose logs api`. The API health check requires the fixed references, a writable result directory, MAFFT and IQ-TREE.

## IQ-TREE uses too many cores

Set `PHYLO_PUBLIC_IQTREE_THREADS` to a positive integer in `.env`, then rebuild/restart. The default `AUTO` is device-adaptive. Keep `PHYLO_PUBLIC_MAX_CONCURRENT_JOBS=1` for ordinary workstations.

## Analysis appears to remain at a high percentage

IQ-TREE may spend substantial time optimizing the final topology or completing bootstrap consensus work. Inspect the downloadable/current IQ-TREE log and system CPU activity. Stop the task only if the process is unresponsive or the log has not changed for an unexpectedly long interval.

## Resetting results

Stopping containers preserves the Docker result volume. Deleting the volume is irreversible; copy any required result downloads before using a volume-removal command.
