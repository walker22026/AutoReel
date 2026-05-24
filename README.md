# Media Organizer

轻量级电影/剧集自动整理工具,专为自托管 NAS 设计。

## 设计思路

```
下载文件名混乱
      ↓
  规则清洗 (parser.py)
      ↓
  TMDB 查询 ──┐
      ↓        │ 命中→直接使用
      │ 未命中  │
      ↓        │
  LLM 兜底 ────┘  ← Gemini Flash via LiteLLM
   (提取标题/年份)
      ↓
  TMDB 二次查询
      ↓
  移动/硬链接/复制 + 标准命名
      ↓
  Emby/Jellyfin/Plex 完美刮削
```

## 与 MoviePilot / NAStool 的差异

| 特性 | MoviePilot | NAStool | 本项目 |
|---|---|---|---|
| 整理重命名 | ✅ | ✅ | ✅ |
| PT 站集成 | ✅ 重 | ✅ 重 | ❌ |
| 资源订阅 | ✅ | ✅ | ❌ |
| 刷流养站 | ❌ | ✅ | ❌ |
| **LLM 兜底识别** | ❌ | ❌ | ✅ |
| DS218plus 内存占用 | ~500MB | ~600MB | ~50MB |
| 代码量 | ~50k 行 | ~80k 行 | ~600 行 |

如果你只需要整理重命名(不需要订阅下载、PT 刷流),并且已经有 LiteLLM 基础设施,这个方案的边际成本几乎为零,且识别准确率(尤其国产剧、合集、压制组冷门命名)更高。

## 部署

### 1. 准备 TMDB API Key
免费申请: https://www.themoviedb.org/settings/api

### 2. 配置
```bash
cp .env.example .env
vim .env  # 填入 TMDB_API_KEY,Telegram 可选
```

### 3. 修改 docker-compose.yml
重点检查 `volumes` 段:
- `WATCH_DIR` 必须挂载你 NAS 上的下载目录(qBittorrent 下载目标)
- 默认 `FILE_ACTION=move`,会把下载目录里的源文件移动/重命名到媒体库
- 如果改用 `FILE_ACTION=hardlink`, `OUTPUT_ROOT` 必须与下载目录**在同一个 volume**,否则硬链接会失败降级为复制

### 4. 启动
```bash
docker compose up -d
docker compose logs -f
```

### 5. 打开 Web 控制台
浏览器访问:

```text
http://NAS-IP:8000
```

控制台支持:
- 查看 watcher、扫描、Dry Run、文件处理模式
- 手动扫描监控目录
- 手动处理单个文件
- 对无法识别的文件进行人工修正后处理
- 测试文件名解析规则
- 查看最近整理记录

### 6. 首次运行
建议先 `DRY_RUN=true` 跑一次扫存量,看日志确认识别和目标路径正确,再切回 `false`。

## 输出目录结构

```
/media/
├── Movies/
│   ├── 流浪地球2 (2023)/
│   │   ├── 流浪地球2 (2023).mkv
│   │   └── 流浪地球2 (2023).zh.srt
│   └── Oppenheimer (2023)/
│       └── Oppenheimer (2023).mkv
└── TV/
    └── 三体 (2023)/
        └── Season 01/
            ├── 三体 - S01E01.mp4
            └── 三体 - S01E02.mp4
```

Emby/Jellyfin/Plex 对这种格式刮削率接近 100%。

## 自定义扩展

- **改命名格式**: 编辑 `organizer.py` 的 `build_target_path()`
- **加新清洗规则**: 编辑 `parser.py` 的 `JUNK_TAGS`
- **换 LLM 模型**: 在 Web 控制台的 LLM 配置里修改 `LITELLM_MODEL`
- **接入 Sonarr/Radarr webhook**: 可改造成 webhook 模式,见 `main.py`

## 文件处理模式

通过 `FILE_ACTION` 控制整理动作:

- `move`: 默认值。直接移动/重命名源文件到媒体库目标路径。适合不需要继续做种的下载目录。
- `hardlink`: 保留下载源文件,在媒体库创建硬链接。适合 PT 做种,且不额外占空间。
- `copy`: 复制一份到媒体库。最安全,但会占双倍空间。

使用 `move` 时,`/downloads` 不能只读挂载,否则容器无法移动源文件。

## LLM 配置

LLM 通过 LiteLLM 兼容 OpenAI Chat Completions 接口调用。配置位置是 Web 配置文件,默认路径为 `/config/settings.json`,也就是 compose 挂载的 `./config/settings.json`。

推荐直接在 Web 控制台填写并保存:
- LiteLLM 地址
- API Key
- 模型名称

保存后会立即写入配置文件,并刷新当前运行中的 LLM 客户端。

配置文件格式如下:

```json
{
  "LITELLM_BASE": "http://litellm:4000",
  "LITELLM_KEY": "你的 LiteLLM API Key",
  "LITELLM_MODEL": "gemini-flash"
}
```

如果 LiteLLM 和本服务在同一个 Docker network,`LITELLM_BASE` 可以写 `http://litellm:4000`。如果 LiteLLM 跑在 NAS 宿主机或另一台机器,改成对应地址,例如 `http://192.168.1.10:4000`。

## 当前识别规则

- 自动过滤常见分辨率、片源、编码、音轨、容器、发布组标签
- 支持 `S01E01`、`1x01`、`EP01`、`E01`、`[01]`、`第1季第1集`、`第01话`
- 中英双标题混合时,优先提取中文标题片段,例如 `三体.Three-Body.2023.S01E01...` 会先查 `三体`
- 启动后会用 SQLite 记录已处理源文件,避免重复整理
- 无法识别的文件会进入 Web 控制台的待人工处理列表
- 字幕会跟随同名视频输出,并保留 `.zh.srt` 这类语言后缀

## 已知边界

- `move` 模式会改变下载目录内容,可能影响 qBittorrent、Transmission 等下载器继续做种。
- 跨设备硬链接会自动降级为复制,占双倍空间。使用 `hardlink` 时务必保证下载目录和媒体库目录在同一文件系统。
- 合集/纪录片(多 part)、原盘 ISO 当前未特殊处理。
- 字幕语言识别简单(直接保留后缀),没做语言代码标准化。
- Web 控制台中的 LLM 配置保存后会立即生效;其他环境变量配置修改后仍需要重启容器。
