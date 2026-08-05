# 恙虫东方体 TSA56 系统发育分型 V1

[English](README.md) | [简体中文](README.zh-CN.md)

这是一个用于恙虫东方体（*Orientia tsutsugamushi*）TSA56 系统发育分型的可复现本地网页工具。软件支持 Sanger AB1 峰图和 FASTA 序列输入，输出参考比对、IQ-TREE 系统发育树、基因型判定及可下载的分析记录。

## 分析界面

![恙虫东方体 TSA56 系统发育分型分析界面](docs/images/analysis-interface.png)

## 分析流程

1. AB1 输入先进行峰图质控、方向判断与双向拼接；FASTA 输入直接从比对开始。
2. 使用 `mafft --add --keeplength` 将样本加入整理后的 60 条参考序列比对。
3. IQ-TREE 固定使用 `TVM+F+R5`、1,000 次 ultrafast bootstrap 和 `-bnni`，线程由 `-T AUTO` 按用户电脑自动分配。
4. 根据参考锚定的系统发育证据判定基因型；支持不足或证据不一致的结果需人工复核。

完整分析规范见[分析方法](docs/zh-CN/METHODS.md)。

## 安装

推荐使用 Docker Desktop 或带 Compose 的 Docker Engine。容器已配置应用运行环境、MAFFT 7.525 和 IQ-TREE 3.0.1。

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

## 文档

[本地安装](docs/zh-CN/LOCAL_INSTALLATION.md) · [分析方法](docs/zh-CN/METHODS.md) · [输入与输出](docs/zh-CN/INPUT_OUTPUT.md) · [验证说明](docs/zh-CN/VALIDATION.md) · [故障排查](docs/zh-CN/TROUBLESHOOTING.md) · [引用](docs/zh-CN/CITATION.md)

## 许可

项目自有代码使用 MIT License。MAFFT、IQ-TREE 和参考序列分别适用其原有许可或数据库条款，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
