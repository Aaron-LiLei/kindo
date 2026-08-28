"""Parser：路径线索纯解析层（v0.3 决策三，阶段 2a）。

从 auto_group 抽出的纯函数（行为不变，test_auto_group 回归保证）+ 组合入口
parse_path_clues，供 Identity Matcher 使用。本模块不做任何存储访问。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 集号模式（按优先级；"第N季"不是集号——集/话才计）
_EP_RANGE = re.compile(r"第\s*(\d{1,4})\s*[-–~至]+\s*\d{1,4}\s*[集话話]")
_EP_DANJI = re.compile(r"第\s*(\d{1,4})\s*[集话話]")
_EP_SEASON_EP = re.compile(r"(?:^|[^0-9A-Za-z])S(\d{1,2})\s*E(\d{1,4})(?![0-9])", re.IGNORECASE)
_EP_EP = re.compile(r"(?:^|[^0-9A-Za-z])EP?\.?\s*(\d{1,4})(?![0-9])", re.IGNORECASE)
_EP_LEADING = re.compile(r"^(\d{1,4})[_.\s\u2010-\u2015-]+")  # 001_x / "02. " / "03 "

# 季号模式（目录名或文件名）
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_SEASON_PATTERNS = [
    re.compile(r"第\s*(\d{1,2})\s*季"),
    re.compile(r"第?\s*([一二三四五六七八九十]{1,3})\s*[阶季]"),
    re.compile(r"(?:^|[^0-9A-Za-z])S(\d{2})(?![0-9A-Za-z])"),
    re.compile(r"(?:^|[^0-9A-Za-z])Season\s*(\d{1,2})(?![0-9A-Za-z])", re.IGNORECASE),
]

# 容器目录名（整名就是结构性标记，锚定匹配）
_CONTAINER_PATTERNS = [
    re.compile(r"^第\s*\d{1,2}\s*[季部周]$"),
    re.compile(r"^第\s*[一二三四五六七八九十]{1,3}\s*[阶季]$"),
    re.compile(r"^[一二三四五六七八九十]{1,3}\s*阶$"),
    re.compile(r"^\d{1,2}\s*[-–~]\s*\d{1,2}\s*季$"),
    re.compile(r"^[Ss]\d{1,2}(\s|$)"),
    re.compile(r"^[Ss]eason\s*\d{1,2}(\s*[-–~]\s*\d{1,2})?$", re.IGNORECASE),
    re.compile(r"^[Ll]\d{1,2}$"),
    re.compile(r"^\d{1,4}$"),
    re.compile(r"^\d{1,4}\s*[-–~至]\s*\d{1,4}$"),
    re.compile(r"^special(\s+episodes?)?$", re.IGNORECASE),
    re.compile(r"^特别[篇版]"),
    re.compile(r"^(SP|OVA|OAD)$", re.IGNORECASE),
    re.compile(r"[（(]\s*[Pp]\s*\d{1,2}\s*[）)]$"),
    re.compile(r"^\d{1,2}\s*级"),
]

# 展示名回退：通用词（取上位目录名）与版本词（上位目录名 + 版本词）
_GENERIC_NAMES = {"动画片", "视频", "动画", "影片", "正片", "内容", "资源", "媒体", "合集"}
_VERSION_WORDS = {"英文版", "英语版", "国语版", "中文版", "中英双版", "双语版", "英音版", "美音版",
                  "中文配音", "英文配音"}

# 目录名前缀序号（"1-xxx" / "1. xxx" / "01 xxx" / "A. xxx"）——展示名去掉机械编号
_DIR_INDEX_PREFIX = re.compile(r"^\d{1,4}\s*[.、\-_–\s]?|^[A-Za-z]\s*[.、\-]\s*")

# 结构记号（用于"剧名 + 结构尾巴"判定）
_STRUCT_MARK = re.compile(
    r"第\s*\d{1,2}\s*[季部阶]|第\s*[一二三四五六七八九十]{1,3}\s*[阶季]"
    r"|\d{1,2}\s*[-–~]\s*\d{1,2}\s*季|[Ss]eason[.\s]*\d{1,2}"
    r"|(?<![A-Za-z])[Ss]\d{1,2}(?![A-Za-z])"
    r"|特别[篇版]|[視视][頻频]\s*$|[音頻频]\s*(mp3)?\s*$"
)

MAX_EPISODE_NO = 9999


def _norm_text(s: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", s.lower())


@dataclass(frozen=True)
class PathClues:
    """Matcher 输入：一条文件路径可提取的全部作品线索。"""

    title_guess: str = ""          # 清洗后的标题猜测（文件名/目录名）
    season_no: int | None = None
    episode_no: int | None = None
    year: int | None = None
    is_version_dir: bool = False   # 位于版本词目录（国语版…）
    is_generic_dir: bool = False   # 位于通用词目录（动画片…）
    raw_parts: list[str] = field(default_factory=list)


def parse_episode_no(filename: str) -> tuple[int | None, int | None]:
    """从文件名解析 (episode_no, season_no)。都没有则 (None, None)。

    先剥扩展名再匹配，避免"1917.mp4"的扩展名分隔符被当作集号分隔。
    """
    stem, dot, ext = filename.rpartition(".")
    if dot and 0 < len(ext) <= 5 and ext.isalnum():
        filename = stem
    m = _EP_RANGE.search(filename)
    if m is None:
        m = _EP_DANJI.search(filename)
    if m is not None:
        return min(int(m.group(1)), MAX_EPISODE_NO), None
    m = _EP_SEASON_EP.search(filename)
    if m is not None:
        return min(int(m.group(2)), MAX_EPISODE_NO), min(int(m.group(1)), 99)
    m = _EP_EP.search(filename)
    if m is not None:
        return min(int(m.group(1)), MAX_EPISODE_NO), None
    m = _EP_LEADING.match(filename)
    if m is not None:
        return min(int(m.group(1)), MAX_EPISODE_NO), None
    return None, None


def parse_leading_no(text: str) -> int | None:
    """目录名起始编号（"01" / "001-050" / "1. 精选儿歌"）。"""
    m = _EP_LEADING.match(text) or re.match(r"^(\d{1,4})(?=$|[-_.\s/])", text)
    if m is not None:
        return min(int(m.group(1)), MAX_EPISODE_NO)
    return None


def _cn_season(text: str) -> int | None:
    """中文数字季/阶（一阶→1，十阶→10；复杂组合不猜）。"""
    if len(text) == 1:
        return _CN_NUM.get(text)
    if len(text) == 2 and text[0] == "十":
        return 10 + _CN_NUM.get(text[1], 0)
    return None


def parse_season_no(text: str) -> int | None:
    for pat in _SEASON_PATTERNS:
        m = pat.search(text)
        if m is not None:
            g = m.group(1)
            n = int(g) if g.isdigit() else _cn_season(g)
            if n is not None:
                return min(n, 99)
    return None


def is_container_dir(name: str, ancestor_norm: frozenset[str] = frozenset()) -> bool:
    """容器目录：不独立成系列，向上并入。

    ① 整名是结构性标记（第N季/S01/Season N/001-050/纯编号/级别…）；
    ② 「剧名 + 结构尾巴(季/特别版/視頻/音頻…)」且剧名为空/通用词/与祖先目录名
      重复——季包/衍生包目录（"汪汪队 第10季 4K""Yakka Dee 特别版視頻"）；
      剧名是全新信息（"国家地理…第1季"）则是系列目录，不是容器——2026-08-21
      修复：此前对含"第N季"的名字一律 search 命中即判容器，把"剧名自带季号"
      的系列整目录打成了散电影。
    """
    if any(p.search(name) for p in _CONTAINER_PATTERNS):
        return True
    m = _STRUCT_MARK.search(name)
    if m is None:
        return False
    core = clean_series_title(name[: m.start()])
    if not core or core in _GENERIC_NAMES or core in _VERSION_WORDS:
        return True
    n = _norm_text(core)
    return any(n and (n == a or n in a or a in n) for a in ancestor_norm)


def clean_series_title(dir_name: str) -> str:
    """合集目录名 → 展示名。

    去掉机械前缀（序号/字母序号/【…】宣传前缀）、季阶范围尾巴（"1-5季+特别版"
    "1-7季146集（英文字幕）1080P"）、清晰度尾巴；剥空则回退原名。
    """
    name = dir_name.strip()
    stripped = _DIR_INDEX_PREFIX.sub("", name, count=1).strip()
    if stripped:
        name = stripped
    name = re.sub(r"^【[^】]*】", "", name).strip()
    name = re.sub(r"\d{1,4}\s*[-–~]\s*\d{1,4}\s*[季阶部].*$", "", name).strip()
    name = re.sub(r"\s*\d{3,4}[pP]\s*$", "", name).strip()
    name = re.sub(r"\s*4[Kk]\s*$", "", name).strip()
    name = re.sub(r"\s*\d+(?:\.\d+)?\s*[GM]B$", "", name, flags=re.IGNORECASE).strip()
    return name or dir_name


_YEAR = re.compile(r"(?:^|[^0-9])(19\d{2}|20\d{2})(?![0-9])")


def parse_path_clues(path_key: str) -> PathClues:
    """组合入口（决策三）：从 path_key 提取 Matcher 所需线索。

    title_guess 取最深"有信息量"目录名（通用词/版本词/纯编号向上回退）；
    年份取路径中首个独立四位年份；集/季沿用既有解析规则。
    """
    parts = [p for p in path_key.split("/") if p]
    if not parts:
        return PathClues(raw_parts=parts)
    filename = parts[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    ep, fn_season = parse_episode_no(filename)

    title_guess = ""
    for part in reversed(parts[:-1]):  # 目录优先，从深到浅
        cand = clean_series_title(part)
        if cand and cand not in _GENERIC_NAMES and cand not in _VERSION_WORDS \
                and not parse_leading_no(part + " "):
            title_guess = cand
            break
    if not title_guess:
        title_guess = clean_series_title(stem)
        # 文件名剥集号尾巴后作为标题猜测
        if ep is not None:
            for pat in (_EP_SEASON_EP, _EP_EP, _EP_DANJI, _EP_RANGE):
                m = pat.search(title_guess)
                if m is not None:
                    title_guess = clean_series_title(title_guess[: m.start()] or title_guess)
                    break

    year = None
    m = _YEAR.search(path_key)
    if m is not None:
        year = int(m.group(1))

    parent = parts[-2] if len(parts) >= 2 else ""
    return PathClues(
        title_guess=title_guess,
        season_no=fn_season if fn_season is not None else (
            parse_season_no(parent) if parent else None),
        episode_no=ep,
        year=year,
        is_version_dir=parent in _VERSION_WORDS,
        is_generic_dir=parent in _GENERIC_NAMES,
        raw_parts=parts,
    )


def norm_title(s: str) -> str:
    """标题归一化（Matcher exact/likely 判定用）。"""
    return _norm_text(s)
