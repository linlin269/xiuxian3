"""修仙帮助站点。"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse


XIUXIAN_DIR = Path(__file__).resolve().parent.parent
ROOT_DOC_GROUP = "根目录"
HELP_BASE_PATH = "/xiuxian/help"
COMMAND_COLORS: tuple[str, ...] = (
    "#FFE9A8",
    "#FFD987",
    "#AFEBC0",
    "#B5DDF8",
    "#CDBBFA",
    "#FFF09D",
    "#F6B8D5",
    "#BBA5F2",
    "#FFC49E",
    "#E7B1D4",
    "#BCE7D0",
    "#B9E5EA",
    "#F7DFA1",
    "#CBEABF",
)

router = APIRouter()
_site: "HelpSite | None" = None


@dataclass(frozen=True)
class HelpDoc:
    """一份 Markdown 文档。"""

    slug: str
    title: str
    group: str
    source: str
    updated_at: str
    content: str
    summary: str
    headings: tuple[tuple[int, str, str], ...]


@dataclass(frozen=True)
class CommandSection:
    """从组件说明中提取出的主要命令分组。"""

    title: str
    color: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class HelpSite:
    """启动时收集好的帮助站数据。"""

    docs: tuple[HelpDoc, ...]
    groups: tuple[tuple[str, tuple[HelpDoc, ...]], ...]
    command_sections: tuple[CommandSection, ...]

    @property
    def by_slug(self) -> dict[str, HelpDoc]:
        return {doc.slug: doc for doc in self.docs}


def build_help_site() -> HelpSite:
    """递归读取修仙组件里的 Markdown 文档。"""

    docs: list[HelpDoc] = []
    for index, path in enumerate(_markdown_paths(), start=1):
        content = path.read_text(encoding="utf-8")
        source = path.relative_to(XIUXIAN_DIR).as_posix()
        group = _doc_group(path)
        title = _title(content, path)
        slug = f"doc-{index}"
        docs.append(
            HelpDoc(
                slug=slug,
                title=title,
                group=group,
                source=source,
                updated_at=_file_updated_at(path),
                content=content,
                summary=_summary(content),
                headings=tuple(_headings(content)),
            )
        )
    grouped: OrderedDict[str, list[HelpDoc]] = OrderedDict()
    for doc in docs:
        grouped.setdefault(doc.group, []).append(doc)
    return HelpSite(
        docs=tuple(docs),
        groups=tuple((group, tuple(items)) for group, items in grouped.items()),
        command_sections=_command_sections_from_docs(docs),
    )


def load_help_site() -> HelpSite:
    """刷新全局帮助站缓存。"""

    global _site
    _site = build_help_site()
    return _site


def current_site() -> HelpSite:
    """读取帮助站缓存；未经过启动回调时兜底构建一次。"""

    global _site
    if _site is None:
        _site = build_help_site()
    return _site


@router.get("/xiuxian/help", response_class=HTMLResponse)
async def help_index() -> HTMLResponse:
    """帮助首页。"""

    return HTMLResponse(render_index(current_site()))


@router.get("/xiuxian/help/docs/{slug}", response_class=HTMLResponse)
async def help_doc(slug: str) -> HTMLResponse:
    """帮助文档详情。"""

    site = current_site()
    doc = site.by_slug.get(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail="help doc not found")
    return HTMLResponse(render_doc(site, doc))


def render_index(site: HelpSite) -> str:
    """渲染 Vexos 风格的文档流首页。"""

    component_groups = tuple((group, docs) for group, docs in site.groups if group != ROOT_DOC_GROUP)
    setting_docs = tuple(doc for doc in site.docs if doc.group == ROOT_DOC_GROUP)
    component_cards = "\n".join(
        _doc_card(doc, group_anchor=f"group-{quote(group)}" if index == 0 else "")
        for group, docs in component_groups
        for index, doc in enumerate(docs)
    )
    setting_cards = "\n".join(_doc_card(doc) for doc in setting_docs)
    component_group_links = "\n".join(
        f'<a class="tag-code" href="#group-{quote(group)}">{escape(group)} · {len(docs)}</a>'
        for group, docs in component_groups
    )
    body = f"""
<main class="app-body home-layout">
  <div class="home-main">
    <section class="top-tools">
      <p>{len(site.command_sections)} 个命令分组 · {len(component_groups)} 个组件文档分组 · {len(setting_docs)} 份设定文档</p>
    </section>
    {_search_results_shell()}
    {_starter_panel()}
    {_command_overview(site.command_sections)}
    <section class="home-panel" id="component-docs">
      <div class="section-heading">
        <h2>组件文档</h2>
        <p>按二级组件整理说明，适合查某个玩法的详细规则。</p>
      </div>
      <div class="tags">{component_group_links}</div>
{component_cards}
    </section>
    <section class="home-panel" id="setting-docs">
      <div class="section-heading">
        <h2>设定文档</h2>
        <p>完整设定、架构和富文本规则集中放在这里。</p>
      </div>
{setting_cards}
    </section>
  </div>
  {_home_catalog(len(site.command_sections), len(component_groups), len(setting_docs))}
</main>
{_search_index(site)}
{SEARCH_SCRIPT}
"""
    return _layout("修仙帮助", body, active="main-commands", include_search=True)


def render_doc(site: HelpSite, doc: HelpDoc) -> str:
    """渲染单篇文档详情。"""

    catalog = _catalog(doc)
    content = _render_markdown(doc.content)
    body = f"""
<div id="article-banner">
  <h2>{escape(doc.title)}</h2>
  <p class="post-date">来源：{escape(doc.source)} · 更新：{escape(doc.updated_at)}</p>
</div>
<main class="app-body flex-box">
  <article class="post-article">
    <section class="markdown-content">
{content}
    </section>
    <nav class="article-nav">
      <a class="more" href="{HELP_BASE_PATH}">返回帮助首页</a>
    </nav>
  </article>
  {catalog}
</main>
"""
    return _layout(doc.title, body, active="component-docs")


def _markdown_paths() -> list[Path]:
    """递归收集修仙组件里的 Markdown 文档。"""

    return sorted(XIUXIAN_DIR.rglob("*.md"), key=lambda item: item.relative_to(XIUXIAN_DIR).as_posix())


def _command_sections_from_docs(docs: list[HelpDoc]) -> tuple[CommandSection, ...]:
    """从各组件说明的 `## 命令` 代码块生成主要命令分组。"""

    sections: list[CommandSection] = []
    seen_groups: set[str] = set()
    for doc in docs:
        if doc.group == ROOT_DOC_GROUP or doc.group in seen_groups:
            continue
        commands = _extract_commands(doc.content)
        if not commands:
            continue
        sections.append(
            CommandSection(
                title=doc.group,
                color=COMMAND_COLORS[len(sections) % len(COMMAND_COLORS)],
                commands=commands,
            )
        )
        seen_groups.add(doc.group)
    return tuple(sections)


def _extract_commands(content: str) -> tuple[str, ...]:
    """提取 `## 命令` 下第一个代码块；没有代码块时退回到列表行。"""

    lines = content.splitlines()
    start = _find_command_heading(lines)
    if start is None:
        return ()
    section_lines = _section_after_heading(lines, start)
    code_commands = _commands_from_first_code_block(section_lines)
    if code_commands:
        return code_commands
    return _commands_from_list_items(section_lines)


def _find_command_heading(lines: list[str]) -> int | None:
    """定位命令章节标题。"""

    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r"#{2,6}\s*(命令|指令|命令总览)\s*", stripped):
            return index
    return None


def _section_after_heading(lines: list[str], heading_index: int) -> list[str]:
    """截取命令标题到下一个同级或更高级标题之间的内容。"""

    heading = lines[heading_index].lstrip()
    level = len(heading) - len(heading.lstrip("#"))
    result: list[str] = []
    for line in lines[heading_index + 1 :]:
        stripped = line.lstrip()
        next_heading = re.match(r"^(#{1,6})\s+", stripped)
        if next_heading and len(next_heading.group(1)) <= level:
            break
        result.append(line)
    return result


def _commands_from_first_code_block(lines: list[str]) -> tuple[str, ...]:
    """读取章节内第一个代码块里的命令。"""

    commands: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                break
            in_code = True
            continue
        if in_code and stripped:
            commands.append(stripped)
    return _dedupe(commands)


def _commands_from_list_items(lines: list[str]) -> tuple[str, ...]:
    """读取列表形式的命令说明。"""

    commands: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            value = stripped[2:].strip(" `")
            if value:
                commands.append(value)
    return _dedupe(commands)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    """保序去重。"""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _file_updated_at(path: Path) -> str:
    """格式化文档文件最后修改时间。"""

    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _doc_group(path: Path) -> str:
    """根据路径推断组件分组。"""

    relative = path.relative_to(XIUXIAN_DIR)
    if len(relative.parts) == 1:
        return ROOT_DOC_GROUP
    return relative.parts[0]


def _title(content: str, path: Path) -> str:
    """读取文档标题。"""

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or path.stem
    return path.stem


def _summary(content: str) -> str:
    """提取文档摘要。"""

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "```", "|", "- ")):
            continue
        return stripped[:160]
    return "暂无摘要。"


def _headings(content: str) -> list[tuple[int, str, str]]:
    """提取二三级目录。"""

    result: list[tuple[int, str, str]] = []
    used: set[str] = set()
    for line in content.splitlines():
        match = re.match(r"^(#{2,3})\s+(.+)$", line.strip())
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        anchor = _anchor(title, used)
        result.append((level, title, anchor))
    return result


def _anchor(title: str, used: set[str]) -> str:
    """生成稳定锚点。"""

    base = re.sub(r"\s+", "-", title.strip()).strip("-") or "section"
    anchor = quote(base, safe="-")
    candidate = anchor
    index = 2
    while candidate in used:
        candidate = f"{anchor}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def _doc_card(doc: HelpDoc, *, group_anchor: str = "") -> str:
    """首页单篇文档卡片。"""

    anchor = f' id="{group_anchor}"' if group_anchor else ""
    return f"""
  <article class="article-card help-search-item"{anchor}>
    <h2 class="article-head"><a href="{HELP_BASE_PATH}/docs/{doc.slug}">{escape(doc.title)}</a></h2>
    <p class="article-date">来源：{escape(doc.source)} · 更新：{escape(doc.updated_at)}</p>
    <div class="tags"><span class="tag-code">{escape(doc.group)}</span></div>
    <div class="article-summary"><p>{_inline(doc.summary)}</p></div>
    <a class="more" href="{HELP_BASE_PATH}/docs/{doc.slug}">查看全文</a>
  </article>"""


def _starter_panel() -> str:
    """首页新手入口栏目。"""

    steps = (
        ("创建用户 名称", "创建角色"),
        ("签到", "领取今日气运"),
        ("修仙信息", "确认状态"),
        ("探险", "开始收益循环"),
        ("结束探险", "领取结算"),
        ("背包", "查看战利品"),
    )
    items = "\n".join(
        f"""
        <li>
          <span class="starter-command">{escape(command)}</span>
          <span class="starter-desc">{escape(desc)}</span>
        </li>"""
        for command, desc in steps
    )
    return f"""
    <section class="starter-path home-panel" id="starter-guide">
      <div class="section-heading compact-heading">
        <h2>新手入口</h2>
        <p>第一次玩先顺着这条线走，熟悉后再查组件文档。</p>
      </div>
      <ol class="starter-steps">
{items}
      </ol>
    </section>"""


def _search_results_shell() -> str:
    """首页独立搜索结果区域。"""

    return """
    <section class="search-results" id="searchResults" hidden>
      <div class="section-heading compact-heading">
        <h2>搜索结果</h2>
        <p id="searchResultCount">输入关键词后会按命令、组件和设定文档归类。</p>
      </div>
      <div class="search-result-list" id="searchResultList"></div>
    </section>"""


def _search_index(site: HelpSite) -> str:
    """渲染隐藏搜索索引，前端只负责过滤和展示。"""

    sources: list[str] = []
    for section in site.command_sections:
        for line in section.commands:
            name, desc, param = _command_parts(line)
            summary = f"{section.title} · {param}"
            if desc:
                summary = f"{summary} · {desc}"
            sources.append(_search_source("命令", section.title, name, summary, f"{HELP_BASE_PATH}#main-commands"))
    for doc in site.docs:
        kind = "设定" if doc.group == ROOT_DOC_GROUP else "组件"
        summary = f"{doc.group} · {doc.summary}"
        sources.append(_search_source(kind, doc.group, doc.title, summary, f"{HELP_BASE_PATH}/docs/{doc.slug}"))
    return f"""
<div id="searchIndex" hidden>
{''.join(sources)}
</div>"""


def _search_source(kind: str, group: str, title: str, summary: str, href: str) -> str:
    """单条隐藏搜索源。"""

    search_text = f"{kind} {group} {title} {summary}"
    return (
        f'<a class="search-source" href="{escape(href, quote=True)}" '
        f'data-kind="{escape(kind, quote=True)}" '
        f'data-group="{escape(group, quote=True)}" '
        f'data-title="{escape(title, quote=True)}" '
        f'data-summary="{escape(summary, quote=True)}">'
        f"{escape(search_text)}</a>"
    )


def _command_overview(sections: tuple[CommandSection, ...]) -> str:
    """渲染首页主要命令栏目。"""

    cards = "\n".join(_command_card(section) for section in sections)
    return f"""
  <section class="command-overview home-panel active" id="main-commands">
    <div class="section-heading">
      <h2>主要命令</h2>
      <p>打开页面先看这里；需要细查时再切到组件文档或设定文档。</p>
    </div>
    <div class="command-grid">
{cards}
    </div>
  </section>"""


def _command_card(section: CommandSection) -> str:
    """渲染一个命令分组卡片。"""

    items = "\n".join(_command_item(line) for line in section.commands)
    return f"""
      <article class="command-card help-search-item" style="--command-accent: {escape(section.color)}">
        <div class="command-card-title">
          <h3>{escape(section.title)}</h3>
          <span>{len(section.commands)} 条</span>
        </div>
        <ul>
{items}
        </ul>
      </article>"""


def _command_item(line: str) -> str:
    """渲染单条命令。"""

    command, desc, param = _command_parts(line)
    desc_html = f'<span class="command-desc">{escape(desc)}</span>' if desc else ""
    return f"""
          <li class="command-line">
            <span class="command-name">{escape(command)}</span>
            <span class="command-meta">{escape(param)}</span>
            {desc_html}
          </li>"""


def _command_parts(line: str) -> tuple[str, str, str]:
    """解析 `命令｜说明｜参数` 或单行命令说明。"""

    value = line.strip().strip("`")
    parts = [part.strip() for part in re.split(r"\s*[｜|]\s*", value) if part.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], _command_param_label(parts[0])
    return value, "", _command_param_label(value)


def _command_param_label(command: str) -> str:
    """粗略判断命令是否需要玩家补参数。"""

    fixed_words = ("开启", "关闭", "全部", "休息结束")
    if "/" in command and any(word in command for word in fixed_words):
        return "固定选项"
    param_words = (
        "ID",
        "名称",
        "名字",
        "数量",
        "等级",
        "地点",
        "源石",
        "问题",
        "物品",
        "技能书",
        "装备位",
        "孔位",
        "宝石",
        "对方",
        "玩家",
        "坐标",
        "x y",
    )
    if any(word in command for word in param_words):
        return "需要参数"
    if "/" in command:
        return "固定选项"
    if " " in command.strip():
        return "需要参数"
    return "无需参数"


def _home_catalog(command_count: int, component_count: int, setting_count: int) -> str:
    """渲染首页右侧目录。"""

    return f"""
  <aside class="home-catalog" aria-label="帮助目录">
    <div class="toc-title">目录</div>
    <a class="catalog-link" href="{HELP_BASE_PATH}#starter-guide" data-section-link="starter-guide">
      <span>新手入口</span><em>6步</em>
    </a>
    <a class="catalog-link active" href="{HELP_BASE_PATH}#main-commands" data-section-link="main-commands">
      <span>主要命令</span><em>{command_count}</em>
    </a>
    <a class="catalog-link" href="{HELP_BASE_PATH}#component-docs" data-section-link="component-docs">
      <span>组件文档</span><em>{component_count}</em>
    </a>
    <a class="catalog-link" href="{HELP_BASE_PATH}#setting-docs" data-section-link="setting-docs">
      <span>设定文档</span><em>{setting_count}</em>
    </a>
  </aside>"""


def _catalog(doc: HelpDoc) -> str:
    """详情页目录。"""

    if not doc.headings:
        return '<aside class="catalog-container"><div class="toc-title">当前文档</div><p class="toc-empty">暂无二级目录</p></aside>'
    items = "\n".join(
        f'<li class="toc-level-{level}"><a href="#{anchor}">{escape(title)}</a></li>'
        for level, title, anchor in doc.headings
    )
    return f"""
  <aside class="catalog-container">
    <div class="toc-title">当前文档</div>
    <ol class="toc-list">
{items}
    </ol>
  </aside>"""


def _render_markdown(content: str) -> str:
    """渲染轻量 Markdown 子集。"""

    html: list[str] = []
    list_open = False
    code_open = False
    code_lines: list[str] = []
    table_lines: list[str] = []
    used_anchors: set[str] = set()

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            html.append("</ul>")
            list_open = False

    def flush_code() -> None:
        if code_lines:
            html.append(f"<figure><pre><code>{escape(chr(10).join(code_lines))}</code></pre></figure>")
            code_lines.clear()

    def flush_table() -> None:
        if table_lines:
            html.append(_render_table(table_lines))
            table_lines.clear()

    for raw in content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            if code_open:
                flush_code()
                code_open = False
            else:
                flush_table()
                close_list()
                code_open = True
            continue
        if code_open:
            code_lines.append(line)
            continue
        if stripped.startswith("|"):
            close_list()
            table_lines.append(stripped)
            continue
        flush_table()
        if not stripped:
            close_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            close_list()
            level = min(len(heading.group(1)), 6)
            text = heading.group(2).strip()
            anchor = _anchor(text, used_anchors)
            html.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            continue
        if stripped.startswith(">"):
            close_list()
            html.append(f"<blockquote><p>{_inline(stripped.lstrip('>').strip())}</p></blockquote>")
            continue
        if stripped.startswith("- "):
            if not list_open:
                html.append("<ul>")
                list_open = True
            html.append(f"<li>{_inline(stripped[2:].strip())}</li>")
            continue
        close_list()
        html.append(f"<p>{_inline(stripped)}</p>")
    flush_table()
    close_list()
    if code_open:
        flush_code()
    return "\n".join(html)


def _render_table(lines: list[str]) -> str:
    """渲染 Markdown 表格。"""

    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    if len(rows) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in rows[1]):
        head = "".join(f"<th>{_inline(cell)}</th>" for cell in rows[0])
        body_rows = rows[2:]
    else:
        head = ""
        body_rows = rows
    body = "\n".join("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>" for row in body_rows)
    thead = f"<thead><tr>{head}</tr></thead>" if head else ""
    return f"<table>{thead}<tbody>{body}</tbody></table>"


def _inline(text: str) -> str:
    """渲染少量行内 Markdown。"""

    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r'<code>\1</code>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _layout(title: str, body: str, *, active: str, include_search: bool = False) -> str:
    """页面外壳。"""

    header_search = """
      <label class="header-search">
        <span>搜索</span>
        <input id="helpSearch" type="search" autocomplete="off" placeholder="输入命令、组件或规则">
      </label>""" if include_search else ""
    search_toggle = """
      <button class="search-toggle" type="button" aria-controls="helpSearch" aria-label="展开搜索">搜</button>""" if include_search else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>{STYLE}</style>
</head>
<body>
  <header class="header fixed-header">
    <div class="header-container">
      <a class="home-link" href="{HELP_BASE_PATH}" onclick="if (history.length > 1) {{ history.back(); return false; }}">
        <div class="logo back-icon">←</div>
        <span>返回</span>
      </a>
{search_toggle}
{header_search}
      <ul class="right-list">
        <li class="list-item"><a class="item-link {'active' if active == 'starter-guide' else ''}" href="{HELP_BASE_PATH}#starter-guide" data-section-link="starter-guide">新手入口</a></li>
        <li class="list-item"><a class="item-link {'active' if active == 'main-commands' else ''}" href="{HELP_BASE_PATH}#main-commands" data-section-link="main-commands">主要命令</a></li>
        <li class="list-item"><a class="item-link {'active' if active == 'component-docs' else ''}" href="{HELP_BASE_PATH}#component-docs" data-section-link="component-docs">组件文档</a></li>
        <li class="list-item"><a class="item-link {'active' if active == 'setting-docs' else ''}" href="{HELP_BASE_PATH}#setting-docs" data-section-link="setting-docs">设定文档</a></li>
      </ul>
    </div>
  </header>
{body}
  <footer class="footer">修仙帮助 · 主要命令来自组件说明，文档内容来自 Markdown</footer>
</body>
</html>"""


SEARCH_SCRIPT = """
<script>
(() => {
  const input = document.getElementById("helpSearch");
  const panels = Array.from(document.querySelectorAll(".home-panel"));
  const links = Array.from(document.querySelectorAll("[data-section-link]"));
  const results = document.getElementById("searchResults");
  const resultCount = document.getElementById("searchResultCount");
  const resultList = document.getElementById("searchResultList");
  const searchSources = Array.from(document.querySelectorAll("#searchIndex .search-source"));
  const searchToggle = document.querySelector(".search-toggle");
  const panelIds = new Set(panels.map((panel) => panel.id));
  let current = panelIds.has(location.hash.slice(1)) ? location.hash.slice(1) : "main-commands";
  let searchTimer = 0;
  let compactHeader = false;
  let compactFrame = 0;

  function on(target, eventName, handler) {
    if (!target) return;
    if (target.addEventListener) target.addEventListener(eventName, handler);
    else target["on" + eventName] = handler;
  }

  function clearResults() {
    if (resultList) resultList.innerHTML = "";
  }

  function append(parent, child) {
    if (parent && child) parent.appendChild(child);
  }

  function closestLink(target) {
    while (target && target !== results) {
      if (target.tagName && target.tagName.toLowerCase() === "a") return target;
      target = target.parentNode;
    }
    return null;
  }

  function data(target, name) {
    return target ? target.getAttribute("data-" + name) || "" : "";
  }

  function showPanel(id, updateHash) {
    if (searchTimer) {
      clearTimeout(searchTimer);
      searchTimer = 0;
    }
    current = panelIds.has(id) ? id : "main-commands";
    panels.forEach((panel) => panel.classList.toggle("active", panel.id === current));
    links.forEach((link) => link.classList.toggle("active", data(link, "section-link") === current));
    document.body.classList.remove("is-searching");
    if (results) results.hidden = true;
    clearResults();
    if (input) input.value = "";
    if (updateHash) history.replaceState(null, "", "#" + current);
  }

  function appendSearchResult(source, parent) {
    const card = document.createElement("a");
    card.className = "search-result-card";
    card.href = source.getAttribute("href");

    const head = document.createElement("span");
    head.className = "search-result-head";
    const kind = document.createElement("em");
    kind.textContent = data(source, "kind") || "文档";
    const title = document.createElement("strong");
    title.textContent = data(source, "title") || source.textContent.trim();
    append(head, kind);
    append(head, title);

    const summary = document.createElement("span");
    summary.className = "search-result-summary";
    summary.textContent = data(source, "summary");

    const group = document.createElement("span");
    group.className = "search-result-group";
    group.textContent = data(source, "group");

    append(card, head);
    append(card, summary);
    append(card, group);
    append(parent, card);
  }

  function renderSearch(query) {
    const matches = searchSources.filter((source) => source.textContent.toLowerCase().includes(query));
    const fragment = document.createDocumentFragment();
    const limit = 36;
    for (const source of matches.slice(0, limit)) appendSearchResult(source, fragment);
    if (!matches.length) {
      const empty = document.createElement("p");
      empty.className = "search-empty";
      empty.textContent = "没有找到匹配内容，可以换一个命令、组件或设定关键词。";
      append(fragment, empty);
    }

    panels.forEach((panel) => panel.classList.remove("active"));
    links.forEach((link) => link.classList.remove("active"));
    document.body.classList.add("is-searching");
    clearResults();
    append(resultList, fragment);
    results.hidden = false;
    resultCount.textContent = matches.length
      ? `找到 ${matches.length} 条结果，当前显示前 ${Math.min(matches.length, limit)} 条。`
      : "没有匹配结果。";
  }

  links.forEach((link) => {
    on(link, "click", (event) => {
      const id = data(link, "section-link");
      if (!panelIds.has(id)) return;
      event.preventDefault();
      showPanel(id, true);
    });
  });

  showPanel(current, false);
  if (!input) return;
  on(input, "input", () => {
    if (searchTimer) clearTimeout(searchTimer);
    const query = input.value.trim().toLowerCase();
    if (!query) {
      showPanel(current, false);
      return;
    }
    searchTimer = setTimeout(() => {
      searchTimer = 0;
      renderSearch(query);
    }, 80);
  });
  if (results) {
    on(results, "click", (event) => {
      const link = closestLink(event.target);
      if (!link) return;
      const id = link.hash.slice(1);
      if (!panelIds.has(id)) return;
      event.preventDefault();
      showPanel(id, true);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  function syncCompactHeader() {
    if (!window.matchMedia("(max-width: 560px)").matches) {
      compactHeader = false;
      document.body.classList.remove("compact-header", "search-open");
      return;
    }

    const scrollTop = window.scrollY || document.documentElement.scrollTop || 0;
    if (!compactHeader && scrollTop > 128) compactHeader = true;
    if (compactHeader && scrollTop < 32) compactHeader = false;
    document.body.classList.toggle("compact-header", compactHeader);
    if (!compactHeader) document.body.classList.remove("search-open");
  }

  function requestCompactSync() {
    if (compactFrame) return;
    const schedule = window.requestAnimationFrame || ((callback) => setTimeout(callback, 16));
    compactFrame = schedule(() => {
      compactFrame = 0;
      syncCompactHeader();
    });
  }

  on(window, "scroll", requestCompactSync);
  on(window, "resize", requestCompactSync);
  on(input, "focus", () => document.body.classList.add("search-open"));
  on(input, "blur", () => {
    if (!input.value.trim()) document.body.classList.remove("search-open");
  });
  if (searchToggle) {
    on(searchToggle, "click", () => {
      document.body.classList.toggle("search-open");
      if (document.body.classList.contains("search-open")) input.focus();
    });
  }
  syncCompactHeader();
})();
</script>
"""


STYLE = r"""
:root {
  --primary: #4f8f6f;
  --dark: #34495e;
  --gray: #7f8c8d;
  --border: #dfe6e9;
  --bg: #f7f8f3;
  --paper: #ffffff;
  --orange: #d37a35;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  color: var(--dark);
  background: var(--bg);
  font-family: "Source Sans Pro", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  font-size: 16px;
}
a { color: var(--primary); text-decoration: none; }
p { margin: 0; line-height: 1.65; }
.header {
  width: 100%;
  height: 64px;
  background: rgba(255,255,255,.96);
  border-bottom: 1px solid rgba(0,0,0,.05);
  z-index: 9;
}
.fixed-header { position: sticky; top: 0; }
.header-container {
  max-width: 1180px;
  height: 64px;
  margin: 0 auto;
  padding: 0 18px;
  display: flex;
  align-items: center;
  gap: 18px;
}
.home-link { display: flex; align-items: center; gap: 10px; color: var(--dark); font-weight: 700; }
.home-link span { white-space: nowrap; }
.logo {
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: #fff;
  background: var(--primary);
}
.back-icon {
  font-size: 20px;
  line-height: 1;
}
.home-link:hover .back-icon { background: #3f7d5f; }
.search-toggle {
  display: none;
  width: 30px;
  height: 30px;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: #fff;
  color: var(--primary);
  font-weight: 700;
}
.right-list { list-style: none; display: flex; gap: 18px; margin: 0; padding: 0; }
.item-link { color: var(--dark); font-size: 14px; }
.item-link.active { color: var(--primary); border-bottom: 2px solid var(--primary); }
.header-search {
  flex: 1 1 420px;
  max-width: 520px;
  min-width: 220px;
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 9px;
}
.header-search span {
  flex: 0 0 auto;
  color: var(--gray);
  font-size: 14px;
  font-weight: 700;
}
.header-search input {
  width: 100%;
  min-width: 0;
  height: 36px;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0 12px;
  background: #fff;
  color: var(--dark);
}
.app-body {
  max-width: 980px;
  margin: 0 auto;
  padding: 24px 18px 48px;
}
.home-layout {
  max-width: 1180px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 30px;
  align-items: start;
}
.home-main { min-width: 0; }
.home-catalog {
  position: sticky;
  top: 86px;
  padding: 16px 0 16px 18px;
  border-left: 1px solid var(--border);
}
.catalog-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  color: var(--gray);
  font-size: 14px;
}
.catalog-link:hover,
.catalog-link.active {
  color: var(--primary);
}
.catalog-link em {
  min-width: 28px;
  padding: 2px 6px;
  border-radius: 999px;
  background: #eef4ed;
  color: var(--orange);
  font-style: normal;
  text-align: center;
  font-size: 12px;
}
.top-tools {
  margin: 0 0 24px;
  padding: 18px 0 16px;
  border-bottom: 1px solid var(--border);
}
.top-tools p {
  margin-top: 10px;
  color: var(--gray);
  font-size: .92em;
}
.starter-path {
  margin: 0 0 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
}
.compact-heading { margin-top: 12px; }
.starter-steps {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}
.starter-steps li {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--paper);
}
.starter-command {
  display: block;
  color: var(--dark);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.starter-desc {
  display: block;
  margin-top: 4px;
  color: var(--gray);
  font-size: .86em;
}
.search-results {
  margin: 0 0 28px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
}
.search-result-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.search-result-card {
  display: block;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--paper);
}
.search-result-card:hover { border-color: var(--primary); }
.search-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.search-result-head em {
  flex: 0 0 auto;
  padding: 2px 6px;
  border-radius: 999px;
  background: #eef4ed;
  color: var(--orange);
  font-style: normal;
  font-size: 12px;
}
.search-result-head strong {
  min-width: 0;
  color: var(--dark);
  overflow-wrap: anywhere;
}
.search-result-summary,
.search-result-group {
  display: block;
  margin-top: 6px;
  color: var(--gray);
  font-size: .88em;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.search-empty { color: var(--gray); }
.is-searching .starter-path { display: none; }
.home-panel { display: none; }
.home-panel.active { display: block; }
.is-searching .section-heading { margin-top: 22px; }
.flex-box {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 230px;
  gap: 32px;
  align-items: start;
  min-width: 0;
}
.article-card {
  padding-bottom: 22px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--border);
}
.intro-card { margin-top: 28px; }
h2.article-head { font-size: 1.6em; margin: 0 0 6px; }
.article-head > a { color: var(--dark); }
.article-head > a:hover { border-bottom: 2px solid var(--primary); }
.article-date { color: var(--gray); margin: 8px 0; font-size: .9em; }
.article-summary { margin: 10px 0; }
.more { display: inline-block; font-weight: 700; transition: transform .25s ease; }
.more:hover { transform: translateX(8px); }
.section-heading {
  margin: 28px 0 16px;
  padding-top: 4px;
}
.section-heading h2 {
  margin: 0;
  font-size: 1.45em;
  color: var(--dark);
}
.section-heading p {
  margin-top: 6px;
  color: var(--gray);
}
.command-overview {
  margin: 10px 0 34px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.command-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.command-card {
  min-width: 0;
  background: var(--paper);
  border: 1px solid var(--border);
  border-top: 7px solid var(--command-accent);
  border-radius: 8px;
  padding: 14px 14px 12px;
  box-shadow: 0 8px 18px rgba(52,73,94,.06);
}
.command-card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 0 0 9px;
}
.command-card h3 {
  margin: 0;
  font-size: 1.05em;
  color: var(--dark);
}
.command-card-title span {
  flex: 0 0 auto;
  padding: 2px 7px;
  border-radius: 999px;
  background: #f4f7f1;
  color: var(--orange);
  font-size: 12px;
  font-weight: 700;
}
.command-card ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.command-card .command-line {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 8px;
  padding: 6px 0 6px 15px;
  line-height: 1.42;
  font-size: .92em;
  overflow-wrap: anywhere;
}
.command-card .command-line:before {
  content: "";
  position: absolute;
  left: 0;
  top: 15px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--primary);
}
.command-name {
  min-width: 0;
  color: var(--dark);
  font-weight: 700;
}
.command-meta {
  align-self: start;
  padding: 1px 6px;
  border-radius: 999px;
  background: #f4f7f1;
  color: var(--gray);
  font-size: 12px;
  white-space: nowrap;
}
.command-desc {
  grid-column: 1 / -1;
  color: var(--gray);
  font-size: .9em;
}
.tags { margin: 10px 0; }
.tag-code {
  font-family: Monaco, Consolas, monospace;
  font-size: .82em;
  display: inline-block;
  background: #eef4ed;
  color: var(--orange);
  padding: 3px 6px;
  margin: 0 4px 6px 0;
  border-radius: 2px;
}
#article-banner {
  width: 100%;
  padding: 104px 20px 28px;
  text-align: center;
  color: #fff;
  background:
    linear-gradient(135deg, rgba(79,143,111,.88), rgba(52,73,94,.88)),
    repeating-linear-gradient(45deg, rgba(255,255,255,.08) 0 12px, transparent 12px 24px);
}
#article-banner h2 { margin: .4em 0; font-size: 2.2em; }
.post-date { color: rgba(255,255,255,.88); }
.post-article {
  min-width: 0;
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 28px;
}
.markdown-content {
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
}
.markdown-content p { padding: 8px 0; }
.markdown-content h1 { font-size: 1.8em; }
.markdown-content h2 { font-size: 1.5em; margin-top: 1.4em; }
.markdown-content h3 {
  margin: 1em 0;
  font-size: 1.3em;
  padding-bottom: .3em;
  border-bottom: 1px solid var(--border);
}
.markdown-content h4:before { content: "#"; color: var(--primary); margin-right: 5px; }
.markdown-content code {
  background: #eef4ed;
  color: var(--orange);
  padding: 2px 5px;
  border-radius: 2px;
  overflow-wrap: anywhere;
}
.markdown-content figure {
  background: #f2f5ef;
  padding: 12px;
  border-radius: 3px;
  overflow: auto;
}
.markdown-content pre { margin: 0; }
.markdown-content blockquote {
  margin: 1em 0;
  padding: 10px 18px;
  border-left: 4px solid var(--primary);
  background: #eef4ed;
}
.markdown-content ul, .markdown-content ol { margin: 8px 0 8px 22px; padding: 0; }
.markdown-content li {
  padding: 4px 0;
  overflow-wrap: anywhere;
}
.markdown-content p { overflow-wrap: anywhere; }
.markdown-content table { width: 100%; border-collapse: collapse; margin: 14px 0; }
.markdown-content th { background: var(--primary); color: #fff; }
.markdown-content th, .markdown-content td { padding: 7px 10px; border: 1px solid var(--border); }
.markdown-content tr:nth-child(even) td { background: #f4f7f1; }
.catalog-container {
  min-width: 0;
  position: sticky;
  top: 86px;
  padding: 16px;
  border-left: 1px solid var(--border);
  color: var(--gray);
}
.toc-title { color: var(--dark); font-weight: 700; margin-bottom: 8px; }
.toc-list { margin: 0; padding-left: 18px; }
.toc-list li { padding: 4px 0; }
.toc-level-3 { margin-left: 12px; font-size: .92em; }
.article-nav { margin-top: 24px; padding-top: 16px; border-top: 1px dashed var(--border); }
.footer {
  max-width: 980px;
  margin: 0 auto;
  padding: 24px 18px 40px;
  color: var(--gray);
  font-size: 13px;
}
@media (max-width: 1080px) {
  .home-layout { display: block; }
  .home-catalog { display: none; }
}
@media (max-width: 820px) {
  .flex-box { display: block; }
  .catalog-container {
    position: static;
    border-left: 0;
    border-top: 1px solid var(--border);
    margin-top: 16px;
  }
  .right-list { gap: 12px; }
  .command-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .starter-steps { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  #article-banner { padding-top: 86px; }
  .post-article { padding: 20px; }
}
@media (max-width: 560px) {
  body { font-size: 15px; }
  .header { height: auto; }
  .header-container {
    height: auto;
    min-height: 0;
    padding: 8px 12px;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    grid-template-areas:
      "brand nav"
      "search search";
    align-items: center;
    gap: 7px 10px;
  }
  .home-link {
    grid-area: brand;
    min-width: 0;
  }
  .home-link span { display: none; }
  .logo {
    width: 30px;
    height: 30px;
    flex-basis: 30px;
  }
  .header-search {
    grid-area: search;
    width: 100%;
    max-width: none;
    min-width: 0;
    margin-left: 0;
    display: block;
  }
  .header-search span { display: none; }
  .header-search input {
    height: 32px;
    padding: 0 10px;
    font-size: 14px;
  }
  .search-toggle { grid-area: searchButton; }
  .right-list {
    grid-area: nav;
    width: auto;
    min-width: 0;
    gap: 10px;
    justify-content: flex-end;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .right-list::-webkit-scrollbar { display: none; }
  .item-link {
    display: block;
    padding-bottom: 3px;
    white-space: nowrap;
  }
  .app-body { padding: 14px 12px 36px; }
  .top-tools { padding: 12px 0 14px; margin-bottom: 18px; }
  .intro-card { margin-top: 12px; }
  h2.article-head { font-size: 1.35em; }
  .starter-steps,
  .search-result-list {
    grid-template-columns: 1fr;
  }
  .command-grid { grid-template-columns: 1fr; gap: 12px; }
  .command-card { padding: 13px 13px 11px; }
  .compact-header .header-container {
    grid-template-columns: auto auto minmax(0, 1fr);
    grid-template-areas: "brand searchButton nav";
    padding: 7px 12px;
    gap: 7px 8px;
  }
  .compact-header .search-toggle {
    display: grid;
    place-items: center;
  }
  .compact-header .header-search { display: none; }
  .compact-header.search-open .header-container {
    grid-template-areas:
      "brand searchButton nav"
      "search search search";
  }
  .compact-header.search-open .header-search { display: block; }
  #article-banner { padding: 76px 12px 22px; }
  #article-banner h2 { font-size: 1.65em; }
  .post-article { padding: 16px 14px; }
  .markdown-content h1 { font-size: 1.55em; }
  .markdown-content h2 { font-size: 1.32em; }
  .markdown-content h3 { font-size: 1.16em; }
  .footer { padding: 18px 12px 30px; }
}
"""
