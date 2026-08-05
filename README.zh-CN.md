# 恙虫东方体 TSA56 系统发育分型 V1

[English](README.md) | [简体中文](README.zh-CN.md)

这是一个在用户电脑本地运行、用于论文复现与科研共享的恙虫东方体（*Orientia tsutsugamushi*）TSA56 系统发育分型工具。论文公开版只提供文章锁定的 V1.0 流程，不提供快速分型、BLAST 判型或 placement 判型。

## 分析流程

1. AB1 输入先进行峰图质控、方向判断与双向拼接；FASTA 输入直接从比对开始。
2. 使用 `mafft --add --keeplength` 将样本加入固定 60 条参考序列比对。
3. IQ-TREE 固定使用 `TVM+F+R5`、1,000 次 ultrafast bootstrap 和 `-bnni`，线程由 `-T AUTO` 按用户电脑自动分配。
4. 最终基因型只能由参考锚定的系统发育树证据判定；支持不足或证据不一致时必须人工复核。

相似性比较只可用于 AB1 方向和拼接质控，不能参与最终基因型判定。详见[分析方法](docs/zh-CN/METHODS.md)。

## 推荐本地安装方式：Docker

如果用户电脑没有 Python、Node.js、MAFFT 或 IQ-TREE，只需要安装 Docker Desktop。第一次构建容器需要联网，之后分析在用户自己的电脑上运行，不需要公网 IP。

### Windows

1. 安装并启动 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)。
2. 从 GitHub Releases 下载带版本号的 ZIP 并解压。
3. 在解压目录打开 PowerShell，运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_environment.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_docker.ps1
```

### macOS 或 Linux

安装带 Compose 的 Docker Engine/Desktop 后运行：

```sh
sh ./scripts/check_environment.sh
sh ./scripts/start_docker.sh
```

浏览器访问 `http://localhost:3200/`。API 仅监听本机 `127.0.0.1:8200`。使用对应的 `stop_docker` 脚本停止容器，停止时不会删除结果卷。

完整说明和高级原生运行方式见[本地安装](docs/zh-CN/LOCAL_INSTALLATION.md)，常见问题见[故障排查](docs/zh-CN/TROUBLESHOOTING.md)。

## 输入与结果

- 单样本 AB1：1–2 个 `.ab1` 文件。
- 批量 AB1：多个 `.ab1` 文件，按文件名归组后联合建树。
- FASTA：一个含 1–100 条序列的 `.fa/.fas/.fasta/.fna` 文件。
- 可审计结果：JSON/HTML 报告、固定参考比对、IQ-TREE 原始树/报告/日志/UFBoot/一致树、展示 Newick 和 SVG 树图。

详见[输入与输出](docs/zh-CN/INPUT_OUTPUT.md)。本工具仅供科研使用，自动结果不能替代人工树图复核。

## 中英文切换

网站右上角提供 `EN / 中文` 切换，并在浏览器中记住用户选择。非中文浏览器默认显示英文。完整资料提供[英文版](docs/)和[中文版](docs/zh-CN/)。

## 发布形式

GitHub 仓库及带版本号的 Releases 用于分发源代码、固定参考、校验值、容器、说明和测试。用户下载后在本地运行，不需要 GitHub Pages 或公网分析服务器。可以把 GitHub Release 连接到 Zenodo 生成软件 DOI。

论文发表前需要在 `CITATION.cff` 中补齐正式作者、仓库地址和文章/预印本 DOI。详见[引用与论文写法](docs/zh-CN/CITATION.md)和[验证说明](docs/zh-CN/VALIDATION.md)。

## 许可

项目自有代码使用 MIT License。MAFFT、IQ-TREE 和参考序列分别适用其原有许可或数据库条款，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
