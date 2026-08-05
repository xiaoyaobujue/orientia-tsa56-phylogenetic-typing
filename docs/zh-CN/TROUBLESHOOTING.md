# 故障排查

## 环境检查显示 Docker 不可用

从 Docker 官方网站安装带 Compose 的 Docker Desktop/Engine，启动 Docker 后重新运行检查。只安装命令行客户端但 Docker Engine 未启动，仍然不能运行容器。

## 3200 或 8200 端口被占用

先停止占用端口的其他本地服务。本版本有意使用固定本机端口，保证前端/API 契约简单且可审计。

## 第一次 Docker 启动很慢

第一次运行需要下载基础镜像并安装固定科学软件和运行依赖，可能耗时数分钟且必须联网。后续运行会复用本地镜像。

## 网页打开但显示服务未就绪

运行 `docker compose ps` 和 `docker compose logs api`。API 健康检查要求固定参考完整、结果目录可写且 MAFFT/IQ-TREE 可执行。

## IQ-TREE 占用核心过多

在 `.env` 中把 `PHYLO_PUBLIC_IQTREE_THREADS` 设置为正整数，然后重新构建/启动。默认 `AUTO` 会按设备适配；普通工作站应保持 `PHYLO_PUBLIC_MAX_CONCURRENT_JOBS=1`。

## 分析长时间停留在高进度

IQ-TREE 可能在最终树拓扑优化或 bootstrap 一致树阶段耗时较长。应查看当前/可下载的 IQ-TREE 日志和系统 CPU 活动；只有进程无响应或日志异常长时间不更新时才停止任务。

## 清空结果

停止容器不会删除 Docker 结果卷。删除结果卷不可恢复；执行任何删除卷命令前必须先下载并备份需要保留的结果。
