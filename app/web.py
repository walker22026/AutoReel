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
        self.observer = Observer()
        self.scan_lock = threading.Lock()
        self.scan_running = False
        self.last_scan = None

    def start(self):
        if not Path(settings.WATCH_DIR).exists():
            log.warning(f"监控目录不存在, watcher 未启动: {settings.WATCH_DIR}")
            return
        self.observer.schedule(self.handler, settings.WATCH_DIR, recursive=True)
        self.observer.start()
        log.info("Web 控制台已启动 watcher")

    def stop(self):
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=10)

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


def mask_secret(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


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
      <dt>Watcher</dt><dd class="ok">运行中</dd>
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

    settings.WEB_CONFIG = config
    settings.LITELLM_BASE = config.get("LITELLM_BASE", "")
    settings.LITELLM_MODEL = config.get("LITELLM_MODEL", "")
    settings.LITELLM_KEY = config.get("LITELLM_KEY", "")
    state.organizer.llm.configure(settings.LITELLM_BASE, settings.LITELLM_KEY, settings.LITELLM_MODEL)

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
