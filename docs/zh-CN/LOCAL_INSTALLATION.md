# 本地安装

## 选择运行方式

| 用户环境 | 推荐方式 |
|---|---|
| 没有安装科研分析软件 | 带 Compose 的 Docker Desktop/Engine |
| Windows 用户 | Docker Desktop |
| macOS/Linux 用户 | Docker Desktop 或 Docker Engine + Compose |
| 已有 Python、Node.js、MAFFT、IQ-TREE 的开发者 | 高级原生运行 |

## 环境检查

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_environment.ps1
```

macOS/Linux：

```sh
sh ./scripts/check_environment.sh
```

检查内容包括 Docker/Compose、Python、Node.js、MAFFT、IQ-TREE、逻辑 CPU 数和本地 3200/8200 端口。脚本不会静默安装系统级软件。

## Docker 方式

按 Docker 官方说明安装并启动 Docker Engine，然后运行相应的 `start_docker` 脚本。第一次运行会下载基础镜像并构建固定依赖，需要联网；镜像已存在时，Windows 可使用 `start_docker.ps1 -NoBuild` 快速启动。

容器包括 Python 3.12、MAFFT 7.525、IQ-TREE 3.0.1 以及固定 Python/Node 依赖。对外端口只绑定 `127.0.0.1`。

停止服务但保留结果：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_docker.ps1
```

```sh
sh ./scripts/stop_docker.sh
```

## IQ-TREE 线程

默认设置为 `PHYLO_PUBLIC_IQTREE_THREADS=AUTO`，由 IQ-TREE 适配当前电脑。需要限制资源时，可将 `.env.example` 复制为 `.env` 后设置正整数，例如：

```text
PHYLO_PUBLIC_IQTREE_THREADS=8
```

复现分析时应保持指定模型、1,000 次 UFBoot、BNNI 和参考序列集不变。

## 高级原生运行

Windows 开发者可运行 `scripts/setup_native.ps1` 创建项目自己的 Python 环境并安装前端依赖。MAFFT 7.525 与 IQ-TREE 3.0.1 必须已经位于 `PATH`，或通过 `PHYLO_PUBLIC_MAFFT_WSL_BINARY` 和 `PHYLO_PUBLIC_IQTREE_WSL_BINARY` 显式配置 WSL 路径，随后运行 `scripts/start_local.ps1`。

原生方式可能需要按操作系统单独安装生物信息学软件；Docker 是推荐的复现基线。
