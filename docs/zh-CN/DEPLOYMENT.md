# GitHub 发布与本地分发

## 支持的发布形式

1. GitHub 公开仓库保存源代码、固定参考、校验值、中英文资料和测试。
2. 带版本号的 GitHub Release 提供可下载 ZIP 和版本说明。
3. 用户下载后使用 Docker Compose 在自己的电脑运行。
4. 网站和 API 绑定 `127.0.0.1`，上传序列不离开用户电脑。
5. 可由 Zenodo 归档 GitHub Release 并生成软件 DOI。

该方案不需要公网 IP、GitHub Pages 分析后端或公共计算服务器。GitHub Pages 不能运行 Python、MAFFT 或 IQ-TREE，因此不属于本版本架构。

## 发布检查表

- 核对 `data/manifest.json` 中固定参考校验值。
- 运行后端测试、前端构建/测试、Compose 配置校验和参考衍生真实端到端分析。
- 确认 OpenAPI 无 V2 路由，配置只公开 `v1_strict_article`。
- 确认报告写明 `typing_source=phylogenetic_tree`，相似性判型字段为空。
- 确认默认 `PHYLO_PUBLIC_IQTREE_THREADS=AUTO`，模型/bootstrap/BNNI 不变。
- 从干净的 Release ZIP 测试 Windows、macOS/Linux 环境检查和启停脚本。
- 在 `CITATION.cff` 中补齐正式仓库、软件 DOI、文章 DOI、作者和单位。
- 发布带版本标签的 release，保存测试报告和容器镜像摘要。

不得发布本地结果、上传样本、任务日志、缓存、WSL 私有路径或隐私文件名。
