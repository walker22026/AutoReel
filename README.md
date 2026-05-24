# AutoReel

轻量级 NAS 影视文件自动整理工具。启动后自动扫描并监听输入目录,通过本地规则和 TMDB 识别电影/剧集,再按 Emby/Jellyfin/Plex 友好的目录结构移动、复制或硬链接文件。

## 执行流程

```text
输入目录
  ├── 根目录视频文件 -> 单文件识别 -> 成功则整理,失败则移入未识别目录
  ├── 根目录子目录   -> 目录批次识别 -> 全部视频成功才整理,任一失败则整个目录移入未识别目录
  └── _unrecognized -> 未识别/重复目录,扫描和监听时跳过
```

识别流程:

```text
文件名
  -> parser.py 规则清洗
  -> TMDB 查询
  -> 命中后生成目标路径
  -> move / copy / hardlink
```

当前版本不使用 LLM,不提供 Web 配置界面。所有配置集中在 `docker-compose.yml` 中。

## 部署

### 1. 拉取代码

```bash
git clone https://github.com/walker22026/AutoReel.git
cd AutoReel
```

更新已有部署:

```bash
git pull
```

### 2. 配置 docker-compose.yml

所有配置都在 `docker-compose.yml` 中完成。默认挂载:

```yaml
volumes:
  - /volumeUSB1/usbshare/emby:/host/emby
  - ./config:/config
```

左边是真实 NAS 路径,右边是容器内路径。容器只能访问已经挂载进来的目录。

你的目录示例:

```text
真实输入目录: /volumeUSB1/usbshare/emby/source
真实电影目录: /volumeUSB1/usbshare/emby/电影
真实剧集目录: /volumeUSB1/usbshare/emby/剧集
```

对应容器内配置:

```text
输入目录: /host/emby/source
电影目录: /host/emby/电影
剧集目录: /host/emby/剧集
```

然后在 `environment` 中填写 `TMDB_API_KEY` 等参数。TMDB 使用的是 **API 密钥 v3 auth**,不是 v4 Read Access Token。

### 3. 启动

```bash
docker compose up -d --build
docker compose logs -f
```

停止:

```bash
docker compose down
```

## docker-compose.yml 配置说明

核心配置示例:

```yaml
environment:
  WATCH_DIR: /host/emby/source
  MOVIE_DIR: /host/emby/电影
  TV_DIR: /host/emby/剧集
  UNRECOGNIZED_DIR_NAME: _unrecognized
  DUPLICATE_DIR_NAME: _duplicates
  TMDB_API_KEY: "你的 TMDB API Key"
  TMDB_LANG: zh-CN
  FILE_ACTION: move
  DRY_RUN: "true"
  SCAN_ON_START: "true"
  QUIET_SECONDS: "10"
  MIN_FILE_SIZE_MB: "100"
  TELEGRAM_BOT_TOKEN: ""
  TELEGRAM_CHAT_ID: ""
```

字段含义:

- `WATCH_DIR`: 输入目录。程序启动后会扫描并监听这个目录。
- `MOVIE_DIR`: 电影输出目录。
- `TV_DIR`: 剧集输出目录。
- `UNRECOGNIZED_DIR_NAME`: 未识别目录名。默认会创建在输入目录下,例如 `/host/emby/source/_unrecognized`。
- `DUPLICATE_DIR_NAME`: 重复文件目录名。默认在未识别目录下创建,例如 `/host/emby/source/_unrecognized/_duplicates`。
- `TMDB_API_KEY`: TMDB API Key v3。
- `TMDB_LANG`: TMDB 返回语言,默认 `zh-CN`。
- `FILE_ACTION`: 文件动作。可选 `move`、`copy`、`hardlink`。
- `DRY_RUN`: 演练模式。为 `true` 时只识别和打印日志,不移动文件、不创建目标目录、不写已处理记录。
- `SCAN_ON_START`: 启动后是否立即扫描输入目录。
- `QUIET_SECONDS`: 文件/目录进入输入目录后等待多少秒再处理,避免下载或复制未完成。
- `MIN_FILE_SIZE_MB`: 小于该大小的视频跳过,用于过滤样片、小广告、小片段。
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`: 可选通知配置。

## 文件处理规则

### 根目录单文件

输入:

```text
/host/emby/source/Oppenheimer.2023.2160p.BluRay.x265.mkv
```

识别成功后:

```text
/host/emby/电影/Oppenheimer (2023)/Oppenheimer (2023).mkv
```

识别失败后:

```text
/host/emby/source/_unrecognized/Oppenheimer.2023.2160p.BluRay.x265.mkv
/host/emby/source/_unrecognized/Oppenheimer.2023.2160p.BluRay.x265.mkv 未识别到.txt
```

如果识别成功但目标文件已经存在,源文件会移入重复目录:

```text
/host/emby/source/_unrecognized/_duplicates/Oppenheimer.2023.2160p.BluRay.x265.mkv
/host/emby/source/_unrecognized/_duplicates/Oppenheimer.2023.2160p.BluRay.x265.mkv 目标文件已存在.txt
```

如果源文件曾经被状态库记录为已处理,但它仍然出现在输入目录中,程序也会重新检查。只要目标文件已存在,仍会按重复文件处理,不会静默跳过。

### 根目录子目录

输入:

```text
/host/emby/source/某剧合集/
  ├── S01E01.mkv
  ├── S01E02.mkv
  └── S01E03.mkv
```

程序会把 `某剧合集` 作为一个批次处理:

- 只识别视频文件。
- 全部视频识别成功后,才逐个移动到电影/剧集目录。
- 只要任意一个视频识别失败,整个子目录都不会整理到媒体库,而是移入未识别目录。
- 只要任意一个视频识别后的目标文件已存在,整个子目录会移入重复目录。

失败时会生成原因文件:

```text
/host/emby/source/_unrecognized/某剧合集/
  ├── S01E01.mkv
  ├── S01E02.mkv
  ├── S01E03.mkv
  └── 目录中S01E02.mkv文件未识别到.txt
```

你可以手工修改目录名或文件名后,把它从 `_unrecognized` 移回输入目录,程序会重新扫描/监听处理。

目录中目标重复时会生成:

```text
/host/emby/source/_unrecognized/_duplicates/某剧合集/
  └── 目录中S01E02.mkv目标文件已存在.txt
```

## 输出目录结构

电影:

```text
电影/
└── 流浪地球2 (2023)/
    ├── 流浪地球2 (2023).mkv
    └── 流浪地球2 (2023).zh.srt
```

剧集:

```text
剧集/
└── 三体 (2023)/
    └── Season 01/
        ├── 三体 - S01E01.mp4
        └── 三体 - S01E02.mp4
```

## Dry Run

`DRY_RUN=true` 是安全演练模式。建议首次运行时保持开启。

开启时:

```text
会识别文件
会查询 TMDB
会在日志中显示计划目标路径
不会移动/复制/硬链接文件
不会创建目标目录
不会写入已处理记录
```

确认路径和识别结果无误后,在 `docker-compose.yml` 中改为:

```yaml
DRY_RUN: "false"
```

然后重启容器:

```bash
docker compose down
docker compose up -d
```

## 已知边界

- TMDB 访问必须在 NAS/容器网络中可用。
- 合集/多 Part、原盘 ISO 暂未特殊处理。
- 字幕语言识别较简单,会保留 `.zh.srt` 这类原后缀。
- `move` 模式会改变输入目录内容,可能影响下载器继续做种。
