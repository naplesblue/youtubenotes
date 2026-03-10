# 🔧 原理安装与部署配置指南 (Setup & Configuration)

## 一、系统级依赖要求

在开始配置项目前，请确保操作系统包含如下组件：

1. **Python `3.10`+**: 需使用高阶语法和特定包兼容性。
2. **`ffmpeg`**: 操作音频和提取音频通道必须的命令依赖。
   - MacOS: `brew install ffmpeg`
   - Linux: `sudo apt-get install ffmpeg`
3. **`yt-dlp`**: 需要位于 `PATH` 内，用于 Youtube RSS 拉取原始媒介。推荐更新到最新版。

## 二、运行依赖配置

建议在项目根使用 Virtual Environment 安装依赖。
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

如果采用现代的构建方式，也可以基于仓库内置的 `pyproject.toml` 执行 `pip install .`。

## 三、环境配置 (`.env` 和 `config.yaml`)

项目依赖两类配置：**敏感宏观运行环境（`.env`）**及 **Obsidian同步路由设定（`config.yaml`）**。

### 1. 配置运行时密钥 `.env`

通过根目录模板克隆: `cp .env.example .env`。核心值含：

- `DASHSCOPE_API_KEY`: 这是强制请求项。因为目前 Qwen 模型能够完美胜任中文字幕提示词组装。在阿里云 DashScope 平台申请。
- `LLM_BASE_URL`: 默认指向 DashScope 的 OpenAI 兼容模式（`https://dashscope.aliyuncs.com/compatible-mode/v1`）。
- `LLM_MODEL_NAME`: 使用 `qwen-plus` 等 Qwen 家族模型最配适提示词。
- `YTDLP_USE_COOKIES`: 设为 `1` 并通过 `YTDLP_COOKIES_PATH` 指向你的 netscape cookie（可以使用包含的 `tools/extract_cookies.py` 从本地浏览器提取并写入 `data/youtube_cookies.txt`）以规避 Youtube 账户拦截限制。
- `FUNASR_CHUNK_SECONDS`: 设置内存受限时（建议60s-300s）的长音频分割阈值，用于 ASR 本地转录。

### 2. 配置跨包分析映射 `config.yaml`

通过模板克隆： `cp config.example.yaml config.yaml`。核心关注 `paths.vault` 字段：

```yaml
paths:
  # 此处填入你实际的 Obsidian 知识库 (Vault) 绝对路径
  vault: "/Volume/Drive/My_Obsidian_Vault"
  
  # 然后分配此知识库内的子目录结构，如：
  folders:
    videos: "02-Video-Notes"               # 存放主观提取 JSON 内容
    transcripts: "05-Transcripts"          # 存放巨型时间线转录流
    price_levels: "03-Price-Levels"        # 存放原子操作点位
```

## 四、自定义 RSS 频道列表 (`channels.yaml`)

通过模板克隆： `cp channels.example.yaml channels.yaml`。编辑 `channels.yaml` 监控目标：

```yaml
channels:
  # 通用配置（按需覆盖全局默认设定）
  - id: "UCH-example"
    name: "示例财经频道"
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCH-example"
    max_entries: 5   # 拉取最近 5 条
```

## 五、验证检查

环境准备完备后，可以通过 `run_pipeline.py` 进行干跑验证（Dry-run）：
```bash
python run_pipeline.py --dry-run
```

确认计划后：
```bash
# 全功能启动
python run_pipeline.py
```
