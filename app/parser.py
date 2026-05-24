"""
文件名清洗与初步识别
处理国内常见的混乱命名:
  [字幕组][剧集名][S01E01][1080p].mkv
  剧集名.2024.S01E01.WEB-DL.1080p.H264-XXX.mkv
  Movie.Name.2023.2160p.UHD.BluRay.x265.HDR-GROUP.mkv
"""
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


# 需要剥离的标签 (压制组、来源、编码等)
JUNK_TAGS = [
    # 分辨率
    r'\b(4k|2160p|1080p|1080i|720p|480p|576p|uhd|hd|sd)\b',
    # 来源
    r'\b(bluray|blu-ray|bdrip|bdremux|remux|web|web-?dl|webrip|hdtv|dvdrip|hdrip|hdtc|cam|ts)\b',
    # 编码
    r'\b(h\.?264|h\.?265|x264|x265|hevc|avc|xvid|divx|av1|10bit|8bit|hdr|dv|hdr10\+?|dolby ?vision)\b',
    # 音轨
    r'\b(ddp?5\.?1|ddp?7\.?1|atmos|dts(-hd)?(\.ma)?|truehd|aac|ac3|flac|opus|mp3|2\.0|5\.1|7\.1)\b',
    # 容器
    r'\b(mkv|mp4|avi|ts|m2ts)\b',
    # 其他
    r'\b(repack|proper|extended|director\'?s ?cut|theatrical|remastered|imax|unrated|criterion)\b',
    r'\b(internal|limited|festival|complete|multi|dual ?audio)\b',
]

# 字幕组/发布组标记 [xxx] / -GROUP
RELEASE_GROUP_PATTERNS = [
    r'\[[^\]]+\]',           # [字幕组]
    r'\b(?:h\.?264|h\.?265|x264|x265|hevc|aac|ac3|ddp?5\.?1|ddp?7\.?1|dts|truehd)-[A-Za-z0-9]+$',
    r'(?<=[\s._])-[A-Za-z0-9]+$',  # -GROUP at end, but keep title hyphens like Spider-Man
    r'@\w+',                 # @group
]

# 季集模式
SEASON_EPISODE_PATTERNS = [
    (r's(\d{1,2})e(\d{1,3})', 'sxxexx'),       # S01E01
    (r'第(\d{1,2})季.*?第(\d{1,3})集', 'cn'),    # 第1季第1集
    (r'第(\d{1,2})季.*?第(\d{1,3})[话話]', 'cn'), # 第1季第1话
    (r'season\s*(\d{1,2}).*?episode\s*(\d{1,3})', 'sxxexx'),
    (r'(\d{1,2})x(\d{1,3})', 'sxxexx'),         # 1x01
    (r'[\[\(【](\d{1,3})(?:v\d+)?[\]\)】]', 'epxx'), # [01] / (01v2)
    (r'第(\d{1,3})[集话話]', 'epxx'),            # 第01集 / 第01话
    (r'\b(\d{1,3})[集话話]\b', 'epxx'),          # 01集 / 01话
    (r'ep?\.?\s*(\d{1,3})', 'epxx'),            # EP01 / E01 (无季号)
]

YEAR_PATTERN = re.compile(r'\b(19\d{2}|20\d{2})\b')


@dataclass
class ParsedName:
    title: str          # 清洗后的疑似标题
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    is_tv: bool = False
    raw: str = ""       # 原始文件名(无后缀)


def clean_filename(name: str) -> str:
    """剥离压制信息、字幕组标签,返回可能的片名字符串"""
    # 去后缀
    name = Path(name).stem

    # 去字幕组/发布组标记
    for pat in RELEASE_GROUP_PATTERNS:
        name = re.sub(pat, ' ', name, flags=re.IGNORECASE)

    # 去技术标签
    for pat in JUNK_TAGS:
        name = re.sub(pat, ' ', name, flags=re.IGNORECASE)

    # 把分隔符统一成空格
    name = re.sub(r'[._\-\+]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def extract_year(text: str) -> Optional[int]:
    """提取年份(优先靠后的,因为压制年份可能在前)"""
    years = YEAR_PATTERN.findall(text)
    if not years:
        return None
    # 取最后一个 1900-2099 区间的年份
    for y in reversed(years):
        yi = int(y)
        if 1900 <= yi <= 2099:
            return yi
    return None


def extract_season_episode(text: str):
    """提取季和集号"""
    lower = text.lower()
    for pat, kind in SEASON_EPISODE_PATTERNS:
        m = re.search(pat, lower)
        if m:
            if kind == 'epxx':
                return None, int(m.group(1))
            return int(m.group(1)), int(m.group(2))
    return None, None


def prefer_cjk_title(title: str) -> str:
    """中英双标题时优先使用较完整的中文标题片段。"""
    has_cjk = re.search(r'[\u4e00-\u9fff]', title)
    has_latin = re.search(r'[A-Za-z]', title)
    if not (has_cjk and has_latin):
        return title

    cjk_parts = re.findall(r'[\u4e00-\u9fff][\u4e00-\u9fff0-9：:·\s]*', title)
    cjk_parts = [re.sub(r'\s+', ' ', p).strip(' :：·') for p in cjk_parts]
    cjk_parts = [p for p in cjk_parts if len(p) >= 2]
    if not cjk_parts:
        return title
    return max(cjk_parts, key=len)


def parse(filename: str) -> ParsedName:
    """主入口:解析文件名"""
    raw = Path(filename).stem
    cleaned = clean_filename(filename)

    season, episode = extract_season_episode(raw)
    year = extract_year(raw)

    # 把年份从标题里去掉
    title = cleaned
    if year:
        title = re.sub(rf'\b{year}\b', ' ', title)
    # 去季集标记
    title = re.sub(r's\d{1,2}e\d{1,3}', ' ', title, flags=re.IGNORECASE)
    title = re.sub(r'\d{1,2}x\d{1,3}', ' ', title)
    title = re.sub(r'第\d+季|第\d+[集话話]|\d+[集话話]|ep?\.?\s*\d+', ' ', title, flags=re.IGNORECASE)
    title = re.sub(r'\s+', ' ', title).strip()
    title = prefer_cjk_title(title)

    is_tv = season is not None or episode is not None

    return ParsedName(
        title=title,
        year=year,
        season=season,
        episode=episode,
        is_tv=is_tv,
        raw=raw,
    )


if __name__ == "__main__":
    # 自测
    samples = [
        "[VCB-Studio] 进击的巨人 [01][Ma10p_1080p][x265_flac].mkv",
        "Oppenheimer.2023.2160p.UHD.BluRay.x265.10bit.HDR.DDP5.1-GROUP.mkv",
        "三体.Three-Body.2023.S01E01.WEB-DL.4K.HEVC.AAC-OurTV.mp4",
        "The.Bear.S03E10.1080p.WEB.H264-SuccessfulCrab.mkv",
        "流浪地球2.2023.BluRay.1080p.x265.10bit.AC3.mkv",
    ]
    for s in samples:
        p = parse(s)
        print(f"{s}\n  -> title='{p.title}', year={p.year}, S{p.season}E{p.episode}, tv={p.is_tv}\n")
