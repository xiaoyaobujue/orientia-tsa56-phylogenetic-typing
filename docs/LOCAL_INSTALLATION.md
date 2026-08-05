# Local installation

## Choose a runtime

| Situation | Recommended path |
|---|---|
| No scientific software installed | Docker Desktop/Engine with Compose |
| Windows publication user | Docker Desktop |
| macOS/Linux publication user | Docker Desktop or Docker Engine with Compose |
| Developer with Python, Node.js, MAFFT and IQ-TREE already installed | Native advanced path |

## Environment check

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_environment.ps1
```

macOS/Linux:

```sh
sh ./scripts/check_environment.sh
```

The report checks Docker/Compose, Python, Node.js, MAFFT, IQ-TREE, logical CPU count and local ports 3200/8200. It does not install system software silently.

## Docker path

Install Docker from the official documentation, start the Docker engine, then run the matching `start_docker` script. The first run downloads/builds pinned runtime layers and therefore requires internet access. Later starts can use `start_docker.ps1 -NoBuild` on Windows when the images already exist.

The container includes Python 3.12, MAFFT 7.525, IQ-TREE 3.0.1 and the pinned Python/Node dependencies. Both published ports bind only to `127.0.0.1`.

Stop without deleting results:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_docker.ps1
```

```sh
sh ./scripts/stop_docker.sh
```

## IQ-TREE CPU allocation

The default is `PHYLO_PUBLIC_IQTREE_THREADS=AUTO`. IQ-TREE adapts to the current computer. To set a local integer limit, copy `.env.example` to `.env` and change, for example:

```text
PHYLO_PUBLIC_IQTREE_THREADS=8
```

Do not change the fixed model, 1,000-replicate UFBoot, BNNI or reference panel for article-standard results.

## Native advanced path

Windows developers may run `scripts/setup_native.ps1` to create a project-local Python environment and install frontend dependencies. MAFFT 7.525 and IQ-TREE 3.0.1 must already be on `PATH` or available through explicitly configured `PHYLO_PUBLIC_MAFFT_WSL_BINARY` and `PHYLO_PUBLIC_IQTREE_WSL_BINARY` paths. Run `scripts/start_local.ps1` afterward.

The native path is not the zero-environment publication path and may require platform-specific bioinformatics installation. Docker is the reproducibility baseline.
