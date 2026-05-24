# AutoReel

轻量级 NAS 影视文件自动整理工具。启动后自动扫描并监听输入目录，通过本地规则和 TMDB 识别电影/剧集，再按 Emby/Jellyfin/Plex 友好的目录结构移动、复制或硬链接文件。

## 执行流程

```
输入目录
  ├── 根目录视频文件  → 单文件识别 → 成功则整理，失败则移入未识别目录
  ├── 根目录子目录    → 目录批次识别 → 全部视频成功才整理，任一失败则整个目录移入未识别目录
  ├── _unrecognized  → 未识别/重复目录，扫描和监听时跳过
  └── _pending_delete → 无媒体文件的空壳目录，扫描和监听时跳过
```

识别流程（目录批次）：

```
剧集目录
  → 目录名经 parser.py 规则清洗 + TMDB 查询，确定剧名
  → 每个视频文件名只提取季集号，不再用 01/02 这类文件名搜索电影
  → 生成目标路径（剧名 - S01E01.ext）
  → move / copy / hardlink

电影目录
  → 单视频目录优先用目录名识别电影
  → 多视频目录按每个视频文件分别识别，适合电影合集
  → move / copy / hardlink
  → 字幕/附属文件跟随迁移，旧目录删除
```

识别流程（单文件）：

```
文件名
  → parser.py 规则清洗 + TMDB 查询
  → 生成目标路径
  → move / copy / hardlink + 字幕跟随
```

## 部署到 NAS

### 前置条件

- NAS 已安装 Docker（Synology: Container Manager；QNAP: Container Station）
- NAS 可通过 SSH 登录
- NAS 能访问 `api.themoviedb.org`（大陆网络可能需要代理）

### 1. 在 NAS 上获取代码

SSH 登录 NAS，然后：

```bash
# 首次部署
cd /volumeUSB1/usbshare          # 换成你的实际路径
git clone https://github.com/walker22026/AutoReel.git
cd AutoReel

# 日后更新
git pull
```

没有 git 时，把整个项目文件夹通过文件管理器上传到 NAS 也可以。

### 2. 配置 docker-compose.yml

用文本编辑器（或 `vi`）打开 `docker-compose.yml`，按实际目录填写：

```yaml
services:
  media-organizer:
    build: .
    container_name: AutoReel
    restart: unless-stopped
    environment:
      # === 路径配置（容器内路径，与 volumes 对应）===
      WATCH_DIR: /host/emby/source       # 待整理的下载目录
      MOVIE_DIR: /host/emby/电影          # 电影输出目录
      TV_DIR: /host/emby/剧集             # 剧集输出目录
      UNRECOGNIZED_DIR_NAME: _unrecognized   # 未识别目录名（在 WATCH_DIR 下自动创建）
      DUPLICATE_DIR_NAME: _duplicates        # 重复文件目录名（在未识别目录下）
      PENDING_DELETE_DIR_NAME: _pending_delete  # 无媒体空壳目录（在 WATCH_DIR 下）

      # === TMDB（必填）===
      TMDB_API_KEY: "填你的 Key"   # v3 API Key，不是 v4 Read Access Token
      TMDB_LANG: zh-CN

      # === 行为 ===
      FILE_ACTION: move          # move=移动, copy=复制, hardlink=硬链接
      DRY_RUN: "true"            # 首次建议 true，观察识别结果无误后改 false
      SCAN_ON_START: "true"
      QUIET_SECONDS: "10"        # 文件落盘后等待秒数，避免复制未完成就处理
      MIN_FILE_SIZE_MB: "100"    # 小于此大小的视频跳过（过滤样片）

      # === Telegram 通知（可选）===
      TELEGRAM_BOT_TOKEN: ""
      TELEGRAM_CHAT_ID: ""

      LOG_LEVEL: INFO

    volumes:
      # 左边=NAS 真实路径，右边=容器内路径（与上面 environment 保持一致）
      - /volumeUSB1/usbshare/emby:/host/emby
      - ./config:/config     # 状态数据库存放位置，建议保留
```

**路径对照示例：**

```
NAS 真实路径                              容器内路径（environment 中填这个）
/volumeUSB1/usbshare/emby/source    →   /host/emby/source
/volumeUSB1/usbshare/emby/电影      →   /host/emby/电影
/volumeUSB1/usbshare/emby/剧集      →   /host/emby/剧集
```

### 3. 获取 TMDB API Key

1. 注册 [TMDB](https://www.themoviedb.org/) 账号
2. 进入「设置 → API」
3. 申请 API Key（选 Developer，填写用途说明）
4. 复制 **API 密钥（v3 auth）**，粘贴到 `docker-compose.yml`

### 4. 启动

```bash
cd /path/to/AutoReel
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

停止：

```bash
docker compose down
```

### 5. Synology Container Manager 图形界面操作（可选）

如果不习惯命令行，在 Container Manager 中：

1. 打开 Container Manager → 项目 → 新建项目
2. 路径选择 AutoReel 文件夹
3. 直接导入 `docker-compose.yml`，点击「下一步」直到完成
4. 项目启动后在「日志」标签查看运行情况

---

## 首次运行建议

**第一步：Dry Run 验证**

保持 `DRY_RUN: "true"` 启动，观察日志输出：

```
[DRY-RUN][move] /host/emby/source/某电影.mkv -> /host/emby/电影/某电影 (2023)/某电影 (2023).mkv
```

确认识别正确、目标路径合理后，再改为 `DRY_RUN: "false"` 重启。

**第二步：关闭 Dry Run**

```yaml
DRY_RUN: "false"
```

```bash
docker compose down
docker compose up -d
```

---

## docker-compose.yml 配置说明

| 参数 | 说明 | 默认值 |
|---|---|---|
| `WATCH_DIR` | 监听的输入目录（容器内路径） | `/host/emby/source` |
| `MOVIE_DIR` | 电影输出目录 | `/host/emby/电影` |
| `TV_DIR` | 剧集输出目录 | `/host/emby/剧集` |
| `UNRECOGNIZED_DIR_NAME` | 未识别目录名，在 `WATCH_DIR` 下创建 | `_unrecognized` |
| `DUPLICATE_DIR_NAME` | 重复文件目录名，在未识别目录下创建 | `_duplicates` |
| `PENDING_DELETE_DIR_NAME` | 无媒体文件空壳目录，在 `WATCH_DIR` 下创建 | `_pending_delete` |
| `TMDB_API_KEY` | TMDB v3 API Key（必填） | 空 |
| `TMDB_LANG` | TMDB 返回语言 | `zh-CN` |
| `FILE_ACTION` | `move` / `copy` / `hardlink` | `move` |
| `DRY_RUN` | 演练模式，不实际移动文件 | `true` |
| `SCAN_ON_START` | 启动时扫描已有文件 | `true` |
| `QUIET_SECONDS` | 文件落盘后等待秒数 | `10` |
| `MIN_FILE_SIZE_MB` | 小于此大小的视频跳过 | `100` |
| `TELEGRAM_BOT_TOKEN` | Telegram 通知 Token（可选） | 空 |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID（可选） | 空 |
| `LOG_LEVEL` | 日志级别 `DEBUG` / `INFO` / `WARNING` | `INFO` |

如需把某个目录放到 `WATCH_DIR` 以外，可以用完整路径覆盖：

```yaml
UNRECOGNIZED_DIR: /host/emby/未识别     # 覆盖默认位置
PENDING_DELETE_DIR: /host/emby/待删除
```

---

## 文件处理规则

### 目录批次（推荐下载方式）

程序用**目录名**识别媒体标题，用**文件名**提取季集号。

输入：

```
/host/emby/source/低智商犯罪 (2026)/
  ├── 01 4K.mp4
  ├── 01 4K.zh.srt
  ├── 02 4K.mp4
  └── poster.jpg
```

整理后：

```
/host/emby/剧集/低智商犯罪 (2026)/
  ├── Season 01/
  │   ├── 低智商犯罪 - S01E01.mp4
  │   ├── 低智商犯罪 - S01E01.zh.srt
  │   └── 低智商犯罪 - S01E02.mp4
  └── poster.jpg              # 附属文件移到剧集根目录
```

原始目录自动清除，不留空壳。

### 单文件（根目录视频）

```
/host/emby/source/Oppenheimer.2023.2160p.BluRay.x265.mkv
→ /host/emby/电影/Oppenheimer (2023)/Oppenheimer (2023).mkv
```

### 识别失败

```
/host/emby/source/_unrecognized/某剧合集/
  ├── 原始文件...
  └── 目录某剧合集无法识别.txt    # 说明原因
```

把文件或目录从 `_unrecognized` 移回输入目录，修改名称后程序会重新处理。

### 重复文件

目标文件已存在时，整个目录进入重复区：

```
/host/emby/source/_unrecognized/_duplicates/某剧合集/
  └── 目录中ep01.mp4目标文件已存在.txt
```

### 纯字幕目录（无视频）

整个目录移入 `_unrecognized`，等待手动配对视频后重新处理。

### 无媒体文件目录（既无视频也无字幕）

整个目录移入 `_pending_delete`，由你定期手动清理或删除。

---

## 输出目录结构

电影：

```
电影/
└── 流浪地球2 (2023)/
    ├── 流浪地球2 (2023).mkv
    └── 流浪地球2 (2023).zh.srt
```

剧集：

```
剧集/
└── 三体 (2023)/
    └── Season 01/
        ├── 三体 - S01E01.mkv
        └── 三体 - S01E01.zh.srt
```

---

## FILE_ACTION 说明

| 值 | 行为 | 适用场景 |
|---|---|---|
| `move` | 移动并重命名，原文件消失 | 不需要保留原文件，节省空间 |
| `copy` | 复制到目标，原文件保留 | 下载目录用于做种，需要双份 |
| `hardlink` | 硬链接，零额外空间，两处都可见 | 同一磁盘分区，下载做种 + 媒体库并存的最佳方案 |

> `hardlink` 要求源目录和目标目录在同一个文件系统分区。跨分区会自动降级为 `copy`。

---

## 已知边界

- TMDB 必须可访问（大陆网络可能需要为 NAS 容器配置代理）
- 剧集目录必须能从文件名提取集数；如 `01 4K.mp4`、`第01集.mp4`、`S01E01.mkv`
- 合集/多 Part、原盘 ISO 暂未特殊处理
- `move` 模式会移走下载目录中的文件，如需做种请使用 `hardlink`
