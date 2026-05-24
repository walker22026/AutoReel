import html
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from watchdog.observers import Observer

import parser as pname
from config import settings
from main import DownloadHandler, scan_existing
from organizer import MediaOrganizer

log = logging.getLogger("web")


class AppState:
    def __init__(self):
        self.organizer = MediaOrganizer()
        self.handler = DownloadHandler(self.organizer)
        self.observer = None
        self.scan_lock = threading.Lock()
        self.scan_running = False
        self.last_scan = None
        self.watcher_running = False

    def start(self):
        if not Path(settings.WATCH_DIR).exists():
            log.warning(f"监控目录不存在, watcher 未启动: {settings.WATCH_DIR}")
            self.watcher_running = False
            return
        if self.observer and self.observer.is_alive():
            return
        self.observer = Observer()
        self.observer.schedule(self.handler, settings.WATCH_DIR, recursive=True)
        self.observer.start()
        self.watcher_running = True
        log.info("Web 控制台已启动 watcher")

    def stop(self):
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=10)
        self.observer = None
        self.watcher_running = False

    def restart(self):
        self.stop()
        self.handler = DownloadHandler(self.organizer)
        self.start()

    def run_scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return
        self.scan_running = True
        try:
            scan_existing(self.organizer)
            self.last_scan = "完成"
        except Exception as e:
            self.last_scan = f"失败: {e}"
            log.exception("手动扫描失败")
        finally:
            self.scan_running = False
            self.scan_lock.release()


state = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global state
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    state = AppState()
    state.start()
    if settings.SCAN_ON_START:
        threading.Thread(target=state.run_scan, daemon=True).start()
    yield
    state.stop()


app = FastAPI(title="Media Organizer", lifespan=lifespan)


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def to_int(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def load_config_file() -> dict:
    path = Path(settings.CONFIG_FILE)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        log.warning(f"配置文件读取失败: {path}")
        return {}


def save_config_file(data: dict):
    path = Path(settings.CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def bool_value(value) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def apply_runtime_config(config: dict, restart_watcher: bool = False):
    settings.WEB_CONFIG = config
    settings.WATCH_DIR = config.get("WATCH_DIR", settings.WATCH_DIR)
    settings.OUTPUT_ROOT = config.get("OUTPUT_ROOT", settings.OUTPUT_ROOT)
    settings.MOVIE_DIR_NAME = config.get("MOVIE_DIR_NAME", settings.MOVIE_DIR_NAME)
    settings.TV_DIR_NAME = config.get("TV_DIR_NAME", settings.TV_DIR_NAME)
    settings.TMDB_API_KEY = config.get("TMDB_API_KEY", settings.TMDB_API_KEY)
    settings.TMDB_LANG = config.get("TMDB_LANG", settings.TMDB_LANG)
    settings.LITELLM_BASE = config.get("LITELLM_BASE", settings.LITELLM_BASE)
    settings.LITELLM_MODEL = config.get("LITELLM_MODEL", settings.LITELLM_MODEL)
    settings.LITELLM_KEY = config.get("LITELLM_KEY", settings.LITELLM_KEY)
    settings.DRY_RUN = bool_value(config.get("DRY_RUN", settings.DRY_RUN))
    settings.SCAN_ON_START = bool_value(config.get("SCAN_ON_START", settings.SCAN_ON_START))
    settings.FILE_ACTION = (config.get("FILE_ACTION") or "move").lower()
    if settings.FILE_ACTION not in {"move", "hardlink", "copy"}:
        settings.FILE_ACTION = "move"
    settings.USE_HARDLINK = settings.FILE_ACTION == "hardlink"
    settings.MIN_FILE_SIZE_MB = int(config.get("MIN_FILE_SIZE_MB") or settings.MIN_FILE_SIZE_MB or 100)
    settings.TELEGRAM_BOT_TOKEN = config.get("TELEGRAM_BOT_TOKEN", settings.TELEGRAM_BOT_TOKEN)
    settings.TELEGRAM_CHAT_ID = config.get("TELEGRAM_CHAT_ID", settings.TELEGRAM_CHAT_ID)

    state.organizer.tmdb.configure(settings.TMDB_API_KEY, settings.TMDB_LANG)
    state.organizer.llm.configure(settings.LITELLM_BASE, settings.LITELLM_KEY, settings.LITELLM_MODEL)
    if restart_watcher:
        state.restart()


def mask_secret(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


def selected(current, value) -> str:
    return " selected" if str(current) == str(value) else ""


def checked(value) -> str:
    return " checked" if bool(value) else ""


async def form_data(request: Request) -> dict:
    body = (await request.body()).decode("utf-8")
    data = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in data.items()}


def page(content: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Media Organizer</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #64748b;
      --line: #d8dee8;
      --accent: #0f766e;
      --accent-strong: #0b5f59;
      --danger: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      background: #102a43;
      color: white;
      padding: 18px 28px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }}
    h1 {{ font-size: 20px; margin: 0; }}
    main {{ max-width: 1180px; margin: 24px auto; padding: 0 18px 40px; }}
    h2 {{ font-size: 16px; margin: 0 0 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    .full {{ grid-column: 1 / -1; }}
    dl {{ display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 8px 12px; margin: 0; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    form {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    input, select {{
      min-width: min(460px, 100%);
      flex: 1;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      background: white;
    }}
    select {{ min-width: 130px; flex: 0 0 130px; }}
    .compact input {{ min-width: 120px; flex: 1 1 120px; }}
    .compact input[name="src"], .compact input[name="title"] {{ min-width: min(320px, 100%); flex: 2 1 260px; }}
    .settings-form {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: end; }}
    .field {{ display: flex; flex-direction: column; gap: 5px; min-width: 0; }}
    .field label {{ color: var(--muted); font-size: 12px; }}
    .field input, .field select {{ min-width: 0; width: 100%; flex: none; }}
    .checks {{ display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }}
    .checks label {{ display: flex; gap: 7px; align-items: center; color: var(--text); }}
    .checks input {{ min-width: 0; width: auto; flex: none; }}
    button, .button {{
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      padding: 9px 13px;
      font: inherit;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      min-height: 38px;
    }}
    button:hover, .button:hover {{ background: var(--accent-strong); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }}
    .muted {{ color: var(--muted); }}
    .ok {{ color: var(--accent-strong); font-weight: 600; }}
    .danger {{ color: var(--danger); font-weight: 600; }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .grid {{ grid-template-columns: 1fr; }}
      .settings-form {{ grid-template-columns: 1fr; }}
      dl {{ grid-template-columns: 1fr; }}
      input {{ min-width: 100%; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Media Organizer</h1>
    <div>已整理 <strong>{state.organizer.state.count()}</strong> 个文件</div>
  </header>
  <main>{content}</main>
</body>
</html>""")


@app.get("/", response_class=HTMLResponse)
async def index():
    recent = state.organizer.state.recent(30)
    failed = state.organizer.state.failed(30)
    llm_enabled = state.organizer.llm.enabled
    rows = "\n".join(
        f"<tr><td>{esc(item['processed_at'])}</td><td>{esc(item['media_type'])}</td>"
        f"<td>{esc(item['title'])} <span class='muted'>({esc(item['year'])})</span></td>"
        f"<td><code>{esc(item['src'])}</code></td><td><code>{esc(item['dst'])}</code></td></tr>"
        for item in recent
    ) or "<tr><td colspan='5' class='muted'>暂无记录</td></tr>"
    failed_rows = "\n".join(
        f"<tr><td>{esc(item['failed_at'])}</td><td><code>{esc(item['src'])}</code></td>"
        f"<td>{esc(item['reason'])}</td><td>{esc(item['detail'])}</td>"
        f"<td><form class='compact' method='post' action='/manual'>"
        f"<input type='hidden' name='src' value='{esc(item['src'])}'>"
        f"<select name='type'><option value='movie'>电影</option><option value='tv'>剧集</option></select>"
        f"<input name='title' placeholder='标题' required>"
        f"<input name='year' placeholder='年份'>"
        f"<input name='season' placeholder='季'>"
        f"<input name='episode' placeholder='集'>"
        f"<input name='tmdb_id' placeholder='TMDB ID'>"
        f"<button type='submit'>人工处理</button></form></td></tr>"
        for item in failed
    ) or "<tr><td colspan='5' class='muted'>暂无待处理文件</td></tr>"

    content = f"""
<section class="grid">
  <div class="panel">
    <h2>运行状态</h2>
    <dl>
      <dt>Watcher</dt><dd class="{'ok' if state.watcher_running else 'danger'}">{'运行中' if state.watcher_running else '未启动'}</dd>
      <dt>扫描状态</dt><dd>{'扫描中' if state.scan_running else '空闲'}</dd>
      <dt>最近扫描</dt><dd>{esc(state.last_scan or '尚无')}</dd>
      <dt>待处理队列</dt><dd>{len(state.handler.pending)}</dd>
      <dt>Dry Run</dt><dd>{esc(settings.DRY_RUN)}</dd>
      <dt>文件动作</dt><dd>{esc(settings.FILE_ACTION)}</dd>
    </dl>
  </div>
  <div class="panel">
    <h2>路径配置</h2>
    <dl>
      <dt>监控目录</dt><dd><code>{esc(settings.WATCH_DIR)}</code></dd>
      <dt>输出根目录</dt><dd><code>{esc(settings.OUTPUT_ROOT)}</code></dd>
      <dt>电影目录</dt><dd><code>{esc(settings.MOVIE_DIR_NAME)}</code></dd>
      <dt>剧集目录</dt><dd><code>{esc(settings.TV_DIR_NAME)}</code></dd>
      <dt>状态库</dt><dd><code>{esc(settings.STATE_DB)}</code></dd>
      <dt>最小文件</dt><dd>{esc(settings.MIN_FILE_SIZE_MB)} MB</dd>
    </dl>
  </div>
  <div class="panel">
    <h2>手动扫描</h2>
    <form method="post" action="/scan">
      <button type="submit">扫描监控目录</button>
      <span class="muted">会按当前规则处理存量视频</span>
    </form>
  </div>
  <div class="panel">
    <h2>处理单个文件</h2>
    <form method="post" action="/process">
      <input name="path" placeholder="/downloads/example.mkv">
      <button type="submit">处理</button>
    </form>
  </div>
  <div class="panel full">
    <h2>基础配置</h2>
    <form class="settings-form" method="post" action="/config/basic">
      <div class="field"><label>监控目录</label><input name="watch_dir" value="{esc(settings.WATCH_DIR)}" placeholder="/host/volume1/downloads"></div>
      <div class="field"><label>输出根目录</label><input name="output_root" value="{esc(settings.OUTPUT_ROOT)}" placeholder="/host/volume1/media"></div>
      <div class="field"><label>电影目录名</label><input name="movie_dir_name" value="{esc(settings.MOVIE_DIR_NAME)}"></div>
      <div class="field"><label>剧集目录名</label><input name="tv_dir_name" value="{esc(settings.TV_DIR_NAME)}"></div>
      <div class="field"><label>TMDB API Key</label><input name="tmdb_api_key" type="password" placeholder="留空则保持不变"></div>
      <div class="field"><label>TMDB 语言</label><input name="tmdb_lang" value="{esc(settings.TMDB_LANG)}"></div>
      <div class="field"><label>文件动作</label><select name="file_action">
        <option value="move"{selected(settings.FILE_ACTION, 'move')}>移动/重命名</option>
        <option value="hardlink"{selected(settings.FILE_ACTION, 'hardlink')}>硬链接</option>
        <option value="copy"{selected(settings.FILE_ACTION, 'copy')}>复制</option>
      </select></div>
      <div class="field"><label>最小文件 MB</label><input name="min_file_size_mb" value="{esc(settings.MIN_FILE_SIZE_MB)}"></div>
      <div class="field"><label>Telegram Bot Token</label><input name="telegram_bot_token" type="password" placeholder="留空则保持不变"></div>
      <div class="field"><label>Telegram Chat ID</label><input name="telegram_chat_id" value="{esc(settings.TELEGRAM_CHAT_ID)}"></div>
      <div class="field checks">
        <label><input type="checkbox" name="dry_run"{checked(settings.DRY_RUN)}> Dry Run</label>
        <label><input type="checkbox" name="scan_on_start"{checked(settings.SCAN_ON_START)}> 启动时扫描</label>
      </div>
      <div class="field"><button type="submit">保存并重启监听</button></div>
    </form>
  </div>
  <div class="panel full">
    <h2>LLM 配置</h2>
    <form method="post" action="/config/llm">
      <input name="litellm_base" placeholder="LiteLLM 地址" value="{esc(settings.LITELLM_BASE)}">
      <input name="litellm_model" placeholder="模型" value="{esc(settings.LITELLM_MODEL)}">
      <input name="litellm_key" type="password" placeholder="API Key 留空则保持不变">
      <button type="submit">保存配置</button>
      <span class="muted">配置文件: <code>{esc(settings.CONFIG_FILE)}</code> | Key: {esc(mask_secret(settings.LITELLM_KEY))} | 状态: {'启用' if llm_enabled else '未启用'}</span>
    </form>
  </div>
  <div class="panel full">
    <h2>规则测试</h2>
    <form method="post" action="/parse">
      <input name="filename" placeholder="三体.Three-Body.2023.S01E01.WEB-DL.4K.HEVC.AAC-OurTV.mp4">
      <button type="submit">解析</button>
    </form>
  </div>
  <div class="panel full">
    <h2>待人工处理</h2>
    <table>
      <thead><tr><th>时间</th><th>源文件</th><th>原因</th><th>详情</th><th>人工修正</th></tr></thead>
      <tbody>{failed_rows}</tbody>
    </table>
  </div>
  <div class="panel full">
    <h2>最近整理</h2>
    <table>
      <thead><tr><th>时间</th><th>类型</th><th>标题</th><th>源文件</th><th>目标</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>
"""
    return page(content)


@app.post("/scan")
async def scan():
    if not state.scan_running:
        threading.Thread(target=state.run_scan, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.post("/process", response_class=HTMLResponse)
async def process(request: Request):
    data = await form_data(request)
    path = data.get("path", "").strip()
    if not path:
        return RedirectResponse("/", status_code=303)
    result = state.organizer.process(path)
    return page(f"""
<section class="panel">
  <h2>处理结果</h2>
  <dl>
    <dt>源文件</dt><dd><code>{esc(path)}</code></dd>
    <dt>状态</dt><dd>{esc((result or {}).get('status'))}</dd>
    <dt>原因</dt><dd>{esc((result or {}).get('reason'))}</dd>
    <dt>目标</dt><dd><code>{esc((result or {}).get('dst'))}</code></dd>
  </dl>
  <p><a class="button" href="/">返回</a></p>
</section>
""")


@app.post("/config/llm", response_class=HTMLResponse)
async def save_llm_config(request: Request):
    data = await form_data(request)
    config = load_config_file()

    base = data.get("litellm_base", "").strip()
    model = data.get("litellm_model", "").strip()
    key = data.get("litellm_key", "").strip()

    config["LITELLM_BASE"] = base
    config["LITELLM_MODEL"] = model
    if key:
        config["LITELLM_KEY"] = key
    elif "LITELLM_KEY" not in config:
        config["LITELLM_KEY"] = settings.LITELLM_KEY

    save_config_file(config)
    apply_runtime_config(config)

    return page(f"""
<section class="panel">
  <h2>LLM 配置已保存</h2>
  <dl>
    <dt>配置文件</dt><dd><code>{esc(settings.CONFIG_FILE)}</code></dd>
    <dt>LiteLLM 地址</dt><dd><code>{esc(settings.LITELLM_BASE)}</code></dd>
    <dt>模型</dt><dd>{esc(settings.LITELLM_MODEL)}</dd>
    <dt>API Key</dt><dd>{esc(mask_secret(settings.LITELLM_KEY))}</dd>
    <dt>状态</dt><dd>{'启用' if state.organizer.llm.enabled else '未启用'}</dd>
  </dl>
  <p><a class="button" href="/">返回</a></p>
</section>
""")


@app.post("/config/basic", response_class=HTMLResponse)
async def save_basic_config(request: Request):
    data = await form_data(request)
    config = load_config_file()

    config["WATCH_DIR"] = data.get("watch_dir", "").strip()
    config["OUTPUT_ROOT"] = data.get("output_root", "").strip()
    config["MOVIE_DIR_NAME"] = data.get("movie_dir_name", "").strip() or "Movies"
    config["TV_DIR_NAME"] = data.get("tv_dir_name", "").strip() or "TV"
    config["TMDB_LANG"] = data.get("tmdb_lang", "").strip() or "zh-CN"
    config["FILE_ACTION"] = data.get("file_action", "move")
    config["MIN_FILE_SIZE_MB"] = data.get("min_file_size_mb", "").strip() or "100"
    config["DRY_RUN"] = "true" if data.get("dry_run") else "false"
    config["SCAN_ON_START"] = "true" if data.get("scan_on_start") else "false"
    config["TELEGRAM_CHAT_ID"] = data.get("telegram_chat_id", "").strip()

    tmdb_key = data.get("tmdb_api_key", "").strip()
    telegram_token = data.get("telegram_bot_token", "").strip()
    if tmdb_key:
        config["TMDB_API_KEY"] = tmdb_key
    elif "TMDB_API_KEY" not in config:
        config["TMDB_API_KEY"] = settings.TMDB_API_KEY
    if telegram_token:
        config["TELEGRAM_BOT_TOKEN"] = telegram_token
    elif "TELEGRAM_BOT_TOKEN" not in config:
        config["TELEGRAM_BOT_TOKEN"] = settings.TELEGRAM_BOT_TOKEN

    save_config_file(config)
    apply_runtime_config(config, restart_watcher=True)

    return page(f"""
<section class="panel">
  <h2>基础配置已保存</h2>
  <dl>
    <dt>配置文件</dt><dd><code>{esc(settings.CONFIG_FILE)}</code></dd>
    <dt>监控目录</dt><dd><code>{esc(settings.WATCH_DIR)}</code></dd>
    <dt>输出根目录</dt><dd><code>{esc(settings.OUTPUT_ROOT)}</code></dd>
    <dt>TMDB Key</dt><dd>{esc(mask_secret(settings.TMDB_API_KEY))}</dd>
    <dt>文件动作</dt><dd>{esc(settings.FILE_ACTION)}</dd>
    <dt>Dry Run</dt><dd>{esc(settings.DRY_RUN)}</dd>
    <dt>Watcher</dt><dd>{'运行中' if state.watcher_running else '未启动,请检查监控目录是否存在或已挂载'}</dd>
  </dl>
  <p><a class="button" href="/">返回</a></p>
</section>
""")


@app.post("/manual", response_class=HTMLResponse)
async def manual(request: Request):
    data = await form_data(request)
    src = data.get("src", "").strip()
    info = {
        "type": data.get("type", "movie"),
        "id": to_int(data.get("tmdb_id")),
        "title": data.get("title", "").strip(),
        "year": data.get("year", "").strip() or None,
        "season": to_int(data.get("season")),
        "episode": to_int(data.get("episode")),
    }
    result = state.organizer.process_manual(src, info)
    return page(f"""
<section class="panel">
  <h2>人工处理结果</h2>
  <dl>
    <dt>源文件</dt><dd><code>{esc(src)}</code></dd>
    <dt>类型</dt><dd>{esc(info.get('type'))}</dd>
    <dt>标题</dt><dd>{esc(info.get('title'))}</dd>
    <dt>年份</dt><dd>{esc(info.get('year'))}</dd>
    <dt>季/集</dt><dd>{esc(info.get('season'))} / {esc(info.get('episode'))}</dd>
    <dt>状态</dt><dd>{esc((result or {}).get('status'))}</dd>
    <dt>原因</dt><dd>{esc((result or {}).get('reason'))}</dd>
    <dt>目标</dt><dd><code>{esc((result or {}).get('dst'))}</code></dd>
  </dl>
  <p><a class="button" href="/">返回</a></p>
</section>
""")


@app.post("/parse", response_class=HTMLResponse)
async def parse(request: Request):
    data = await form_data(request)
    filename = data.get("filename", "").strip()
    parsed = pname.parse(filename)
    return page(f"""
<section class="panel">
  <h2>解析结果</h2>
  <dl>
    <dt>文件名</dt><dd><code>{esc(filename)}</code></dd>
    <dt>标题</dt><dd>{esc(parsed.title)}</dd>
    <dt>年份</dt><dd>{esc(parsed.year)}</dd>
    <dt>季</dt><dd>{esc(parsed.season)}</dd>
    <dt>集</dt><dd>{esc(parsed.episode)}</dd>
    <dt>是否剧集</dt><dd>{esc(parsed.is_tv)}</dd>
  </dl>
  <p><a class="button" href="/">返回</a></p>
</section>
""")


@app.get("/api/status")
async def api_status():
    return {
        "watch_dir": settings.WATCH_DIR,
        "output_root": settings.OUTPUT_ROOT,
        "dry_run": settings.DRY_RUN,
        "file_action": settings.FILE_ACTION,
        "scan_running": state.scan_running,
        "pending": len(state.handler.pending),
        "processed_count": state.organizer.state.count(),
    }


@app.get("/api/recent")
async def api_recent(limit: int = 20):
    return state.organizer.state.recent(limit)


@app.get("/api/failed")
async def api_failed(limit: int = 50):
    return state.organizer.state.failed(limit)


@app.get("/health")
async def health():
    watch_exists = Path(settings.WATCH_DIR).exists()
    output_exists = Path(settings.OUTPUT_ROOT).exists()
    status = 200 if watch_exists and output_exists else 503
    return JSONResponse(
        {"ok": watch_exists and output_exists, "watch_dir": watch_exists, "output_root": output_exists},
        status_code=status,
    )
