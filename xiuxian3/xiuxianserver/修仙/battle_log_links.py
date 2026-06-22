"""战斗日志链接生成。"""

from __future__ import annotations

from launch import config


LOG_BASE_PATH = "/xiuxian/zhandou-rizhi"


def battle_log_url(kind: str, record_id: int, *, client_id: str = "", detail: bool = False) -> str:
    """生成可直接打开的完整战斗日志地址。"""

    query: list[str] = []
    if client_id:
        query.append(f"player={client_id}")
    if detail:
        query.append("detail=1")
    suffix = f"?{'&'.join(query)}" if query else ""
    return f"{_base_url()}{LOG_BASE_PATH}/{kind}/{int(record_id)}{suffix}"


def battle_log_markdown(label: str, kind: str, record_id: int, *, client_id: str = "", detail: bool = False) -> str:
    """生成消息里使用的 Markdown 改名链接。"""

    return f"[{label}]({battle_log_url(kind, record_id, client_id=client_id, detail=detail)})"


def _base_url() -> str:
    """按项目公开域名和端口生成访问基地址。"""

    port = str(config.server.port)
    domain = (config.project.domain or "127.0.0.1").strip().rstrip("/")
    if domain.startswith(("http://", "https://")):
        scheme, rest = domain.split("://", 1)
    else:
        scheme, rest = "http", domain

    host, _, path = rest.partition("/")
    hostname = host
    explicit_port = ""
    if ":" in host and not host.startswith("["):
        hostname, explicit_port = host.rsplit(":", 1)

    final_port = explicit_port or port
    netloc = hostname if final_port == "80" else f"{hostname}:{final_port}"
    suffix = f"/{path.strip('/')}" if path else ""
    return f"{scheme}://{netloc}{suffix}".rstrip("/")
