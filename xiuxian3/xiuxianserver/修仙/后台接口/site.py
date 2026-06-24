"""修仙后台 WebUI 与 HTTP API。"""

from __future__ import annotations

import json
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .service import ADMIN_SESSION_COOKIE, AdminOperationError, service

router = APIRouter()


@router.get("/xiuxian/admin", response_class=HTMLResponse)
async def admin_index(request: Request) -> HTMLResponse:
    """后台首页。"""

    status = service.bootstrap_status()
    admin = _current_admin(request)
    if admin:
        return HTMLResponse(_render_dashboard(admin, status, active_tab="overview"))
    if not status["bootstrapped"]:
        return HTMLResponse(_render_auth(status, mode="bootstrap", notice="首次初始化，请先创建管理员账号。"))
    return HTMLResponse(_render_auth(status, mode="login", notice="后台已初始化，请先登录。"))


@router.get("/xiuxian/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request) -> HTMLResponse:
    """后台登录页。"""

    if _current_admin(request):
        return RedirectResponse("/xiuxian/admin", status_code=303)
    status = service.bootstrap_status()
    mode = "bootstrap" if not status["bootstrapped"] else "login"
    notice = "当前系统还没有管理员，请先初始化。" if mode == "bootstrap" else "请输入管理员账号和密码。"
    return HTMLResponse(_render_auth(status, mode=mode, notice=notice))


@router.get("/xiuxian/admin/bootstrap", response_class=HTMLResponse)
async def admin_bootstrap_page(request: Request) -> HTMLResponse:
    """首次初始化页。"""

    if _current_admin(request):
        return RedirectResponse("/xiuxian/admin", status_code=303)
    status = service.bootstrap_status()
    if status["bootstrapped"]:
        return HTMLResponse(_render_auth(status, mode="login", notice="后台已经初始化，不能重复创建管理员。"))
    return HTMLResponse(_render_auth(status, mode="bootstrap", notice="请输入初始化一次性密钥。"))


@router.get("/xiuxian/admin/operations", response_class=HTMLResponse)
async def admin_operations_page(request: Request) -> HTMLResponse:
    """后台操作页。"""

    admin = _require_admin(request)
    return HTMLResponse(_render_dashboard(admin, service.bootstrap_status(), active_tab="operations"))


@router.get("/xiuxian/admin/api/bootstrap-status")
async def api_bootstrap_status() -> dict[str, Any]:
    """读取初始化状态。"""

    return service.bootstrap_status()


@router.post("/xiuxian/admin/api/bootstrap")
async def api_bootstrap(request: Request) -> JSONResponse:
    """首次创建管理员。"""

    payload = await _request_payload(request)
    try:
        result = service.bootstrap_admin(
            _payload_value(payload, "username"),
            _payload_value(payload, "password"),
            _payload_value(payload, "ADMIN_BOOTSTRAP_TOKEN", _payload_value(payload, "token")),
            operator_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except AdminOperationError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=400)

    return JSONResponse({"ok": True, "message": result["message"], "admin": result["admin"], "redirect_to": "/xiuxian/admin/login"})


@router.post("/xiuxian/admin/api/login")
async def api_login(request: Request) -> JSONResponse:
    """管理员登录。"""

    payload = await _request_payload(request)
    try:
        result = service.login_admin(
            _payload_value(payload, "username"),
            _payload_value(payload, "password"),
            operator_ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except AdminOperationError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=400)

    response = JSONResponse(
        {
            "ok": True,
            "message": result["message"],
            "admin": result["admin"],
            "expires_at": result["expires_at"],
            "redirect_to": "/xiuxian/admin",
        }
    )
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=result["session_token"],
        max_age=7 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/xiuxian/admin/api/logout")
async def api_logout(request: Request) -> JSONResponse:
    """退出登录。"""

    service.logout(request.cookies.get(ADMIN_SESSION_COOKIE, ""))
    response = JSONResponse({"ok": True, "message": "已退出登录。", "redirect_to": "/xiuxian/admin/login"})
    response.delete_cookie(key=ADMIN_SESSION_COOKIE, path="/")
    return response


@router.get("/xiuxian/admin/api/me")
async def api_me(request: Request) -> dict[str, Any]:
    """读取当前管理员。"""

    admin = _require_admin(request)
    return {"ok": True, "admin": admin}


@router.get("/xiuxian/admin/api/session")
async def api_session(request: Request) -> dict[str, Any]:
    """读取当前会话。"""

    admin = _require_admin(request)
    return {"ok": True, "admin": admin}


@router.get("/xiuxian/admin/api/search")
async def api_search(
    request: Request,
    q: str = Query("", alias="q"),
    scope: str = Query("", alias="scope"),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    """聚合检索玩家和物品。"""

    _require_admin(request)
    return {"ok": True, "players": service.search_players(q, limit=limit), "items": service.search_items(q, scope=scope, limit=limit)}


@router.get("/xiuxian/admin/api/players")
async def api_players(
    request: Request,
    q: str = Query("", alias="q"),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    """玩家模糊搜索。"""

    _require_admin(request)
    return {"ok": True, "items": service.search_players(q, limit=limit)}


@router.get("/xiuxian/admin/api/players/{client_id}")
async def api_player_detail(request: Request, client_id: str) -> dict[str, Any]:
    """玩家详情。"""

    _require_admin(request)
    try:
        return {"ok": True, **service.get_player_detail(client_id)}
    except AdminOperationError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.get("/xiuxian/admin/api/items")
async def api_items(
    request: Request,
    q: str = Query("", alias="q"),
    scope: str = Query("", alias="scope"),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    """物品模糊搜索。"""

    _require_admin(request)
    return {"ok": True, "items": service.search_items(q, scope=scope, limit=limit)}


@router.get("/xiuxian/admin/api/operations")
async def api_operations(request: Request, limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """后台操作列表。"""

    _require_admin(request)
    return {"ok": True, "items": service.list_operations(limit=limit)}


@router.get("/xiuxian/admin/api/operations/latest")
async def api_operations_latest(request: Request, limit: int = Query(10, ge=1, le=20)) -> dict[str, Any]:
    """最新后台操作。"""

    _require_admin(request)
    return {"ok": True, "items": service.list_operations(limit=limit)}


@router.get("/xiuxian/admin/api/operations/{operation_id}")
async def api_operation_detail(request: Request, operation_id: int) -> dict[str, Any]:
    """后台操作详情。"""

    _require_admin(request)
    try:
        return {"ok": True, "operation": service.get_operation_detail(operation_id)}
    except AdminOperationError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.get("/xiuxian/admin/api/operations/{operation_id}/audit")
async def api_operation_audit(request: Request, operation_id: int) -> dict[str, Any]:
    """读取单条操作的审计日志。"""

    _require_admin(request)
    rows = service.log_db.fetch_all("SELECT * FROM admin_operation_logs WHERE operation_id = ? ORDER BY log_id ASC", (int(operation_id),))
    return {"ok": True, "items": [_audit_log_row(row) for row in rows]}


@router.get("/xiuxian/admin/api/audit-logs")
async def api_audit_logs(request: Request, limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """审计日志列表。"""

    _require_admin(request)
    return {"ok": True, "items": [_audit_log_row(row) for row in service.log_db.recent_logs(limit=limit)]}


@router.get("/xiuxian/admin/api/audit-logs/latest")
async def api_audit_logs_latest(request: Request, limit: int = Query(10, ge=1, le=20)) -> dict[str, Any]:
    """最新审计日志。"""

    _require_admin(request)
    return {"ok": True, "items": [_audit_log_row(row) for row in service.log_db.recent_logs(limit=limit)]}


@router.get("/xiuxian/admin/api/audit-logs/{log_id}")
async def api_audit_log_detail(request: Request, log_id: int) -> dict[str, Any]:
    """单条审计日志。"""

    _require_admin(request)
    rows = service.log_db.fetch_all("SELECT * FROM admin_operation_logs WHERE log_id = ?", (int(log_id),))
    if not rows:
        raise HTTPException(status_code=404, detail="没有找到这条审计日志。")
    return {"ok": True, "item": _audit_log_row(rows[0])}


@router.get("/xiuxian/admin/api/bootstrap-token")
async def api_bootstrap_token(request: Request) -> dict[str, Any]:
    """只返回密钥状态，不直接泄露密钥。"""

    _require_admin(request)
    status = service.bootstrap_status()
    return {"ok": True, "token_ready": status["token_ready"], "token_length": status["token_length"], "token_file": status["token_file"]}


@router.get("/xiuxian/admin/api/health")
async def api_health(request: Request) -> dict[str, Any]:
    """后台健康检查。"""

    return {"ok": True, "bootstrap": service.bootstrap_status(), "logged_in": bool(_current_admin(request))}


@router.post("/xiuxian/admin/api/operations/preview")
async def api_operation_preview(request: Request) -> JSONResponse:
    """生成发放预览。"""

    admin = _require_admin(request)
    payload = await _request_payload(request)
    try:
        result = service.preview_operation(admin, payload, operator_ip=_client_ip(request), user_agent=_user_agent(request))
    except AdminOperationError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=400)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/xiuxian/admin/api/operations/confirm")
async def api_operation_confirm(request: Request) -> JSONResponse:
    """确认执行发放。"""

    admin = _require_admin(request)
    payload = await _request_payload(request)
    try:
        result = service.confirm_operation(admin, payload, operator_ip=_client_ip(request), user_agent=_user_agent(request))
    except AdminOperationError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=400)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/xiuxian/admin/api/operations/{operation_id}/cancel")
async def api_operation_cancel(request: Request, operation_id: int) -> JSONResponse:
    """取消一条待执行记录。"""

    _require_admin(request)
    try:
        service._mark_operation_status(operation_id, "canceled", "管理员手动取消。", operator_ip=_client_ip(request), user_agent=_user_agent(request))
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "message": "已取消。", "operation_id": operation_id})


@router.post("/xiuxian/admin/api/operations/{operation_id}/retry")
async def api_operation_retry(request: Request, operation_id: int) -> JSONResponse:
    """后台预留接口：重新查看记录。"""

    _require_admin(request)
    try:
        operation = service.get_operation_detail(operation_id)
    except AdminOperationError as exc:
        return JSONResponse({"ok": False, "message": exc.message}, status_code=404)
    return JSONResponse({"ok": True, "operation": operation})


@router.post("/xiuxian/admin/api/bootstrap/recreate-token")
async def api_recreate_token(request: Request) -> JSONResponse:
    """重新生成一次性密钥：仅供管理员排查使用。"""

    _require_admin(request)
    token = service.ensure_bootstrap_token()
    return JSONResponse({"ok": True, "message": "一次性密钥已同步。", "token_length": len(token), "token_file": service.bootstrap_status()["token_file"]})


@router.get("/xiuxian/admin/api/operations/{operation_id}/detail")
async def api_operation_detail_alias(request: Request, operation_id: int) -> dict[str, Any]:
    """操作详情别名。"""

    return await api_operation_detail(request, operation_id)


@router.get("/xiuxian/admin/api/ping")
async def api_ping() -> dict[str, Any]:
    """基础连通性测试。"""

    return {"ok": True, "service": "admin"}


@router.post("/xiuxian/admin/api/refresh")
async def api_refresh(request: Request) -> dict[str, Any]:
    """刷新后台状态。"""

    _require_admin(request)
    return {"ok": True, "bootstrap": service.bootstrap_status(), "admin": _current_admin(request)}


@router.post("/xiuxian/admin/api/bootstrap/check")
async def api_bootstrap_check(request: Request) -> dict[str, Any]:
    """检查初始化状态。"""

    return service.bootstrap_status()


@router.get("/xiuxian/admin/api/bootstrap/check")
async def api_bootstrap_check_get() -> dict[str, Any]:
    """检查初始化状态（GET）。"""

    return service.bootstrap_status()


@router.post("/xiuxian/admin/api/audit-logs/clear")
async def api_audit_clear(request: Request) -> JSONResponse:
    """保留接口：清空日志不开放，直接拒绝。"""

    _require_admin(request)
    return JSONResponse({"ok": False, "message": "审计日志不支持在线清空。"}, status_code=400)


@router.get("/xiuxian/admin/api/summary")
async def api_summary(request: Request) -> dict[str, Any]:
    """后台概览。"""

    admin = _require_admin(request)
    status = service.bootstrap_status()
    return {
        "ok": True,
        "admin": admin,
        "status": status,
        "counts": {
            "players": len(service.search_players("", limit=1)),
            "items": len(service.search_items("", limit=1)),
            "operations": len(service.list_operations(limit=1)),
        },
    }


@router.post("/xiuxian/admin/api/bootstrap/ping")
async def api_bootstrap_ping(request: Request) -> dict[str, Any]:
    """初始化状态连通性。"""

    return service.bootstrap_status()


def _current_admin(request: Request) -> dict[str, Any] | None:
    """按 cookie 读取当前管理员。"""

    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    if not token:
        return None
    return service.current_admin(token)


def _require_admin(request: Request) -> dict[str, Any]:
    """读取当前管理员；未登录则抛出 401。"""

    admin = _current_admin(request)
    if not admin:
        raise HTTPException(status_code=401, detail="请先登录。")
    return admin


async def _request_payload(request: Request) -> dict[str, Any]:
    """读取 JSON 或表单数据。"""

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            return {}
        return dict(payload) if isinstance(payload, dict) else {}
    try:
        form = await request.form()
    except Exception:
        return {}
    return dict(form)


def _payload_value(payload: dict[str, Any], key: str, default: str = "") -> str:
    """从载荷读取字符串。"""

    value = payload.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _client_ip(request: Request) -> str:
    """读取客户端 IP。"""

    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return getattr(request.client, "host", "") or ""


def _user_agent(request: Request) -> str:
    """读取 UA。"""

    return request.headers.get("user-agent", "")


def _audit_log_row(row: dict[str, Any]) -> dict[str, Any]:
    """整理审计日志行。"""

    data = dict(row)
    data["detail"] = _json_load(row.get("detail_json", "{}"), {})
    return data


def _json_load(value: object, default: Any) -> Any:
    """安全解析 JSON。"""

    if not value:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _json_for_script(data: Any) -> str:
    """安全嵌入到 script 标签的 JSON。"""

    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _render_auth(status: dict[str, Any], *, mode: str, notice: str) -> str:
    """渲染登录/初始化页。"""

    initial = {"mode": mode, "notice": notice, "status": status}
    body = f"""
    <section class="auth-shell">
      <div class="card auth-card">
        <div class="card-head">
          <div>
            <h1>{'后台初始化' if mode == 'bootstrap' else '后台登录'}</h1>
            <p>同服 HTTP WebUI。初始化密钥会写入 <code>xiuxian.db</code> 和 <code>{escape(status['token_file'])}</code>。</p>
          </div>
          <div class="badge">{ '未初始化' if not status['bootstrapped'] else '已初始化' }</div>
        </div>
        <div id="banner" class="banner {'warning' if mode == 'bootstrap' else ''}">{escape(notice)}</div>
        <form id="auth-form" class="form-grid">
          {('<label class="field"><span>初始化一次性密钥 ADMIN_BOOTSTRAP_TOKEN</span><input id="auth-token" type="text" placeholder="请从 txt 文件复制" /></label>' if mode == 'bootstrap' else '')}
          <label class="field"><span>管理员账号</span><input id="auth-username" type="text" autocomplete="username" placeholder="请输入管理员账号" /></label>
          <label class="field"><span>管理员密码</span><input id="auth-password" type="password" autocomplete="current-password" placeholder="请输入管理员密码" /></label>
          {('<label class="field"><span>管理员密码确认</span><input id="auth-password-confirm" type="password" autocomplete="new-password" placeholder="再次输入管理员密码" /></label>' if mode == 'bootstrap' else '')}
          <div class="form-actions">
            <button class="primary" type="submit">{'创建管理员' if mode == 'bootstrap' else '登录后台'}</button>
            <a class="ghost" href="/xiuxian/help" target="_blank" rel="noreferrer">打开帮助站</a>
          </div>
        </form>
        <section class="mini-panel">
          <h2>状态信息</h2>
          <ul class="bullet-list">
            <li>管理员数量：{int(status['admin_count'])}</li>
            <li>初始化状态：{'已完成' if status['bootstrapped'] else '未完成'}</li>
            <li>一次性密钥长度：{int(status['token_length'])}</li>
            <li>密钥文件：{escape(status['token_file'])}</li>
          </ul>
        </section>
      </div>
    </section>
    """
    data = {"mode": mode, "notice": notice, "status": status}
    return _render_document("修仙后台", body, data, AUTH_SCRIPT)


def _render_dashboard(admin: dict[str, Any], status: dict[str, Any], *, active_tab: str = "overview") -> str:
    """渲染后台主页。"""

    admin_name = escape(str(admin.get("username") or "未知管理员"))
    body = f"""
    <section class="hero card">
      <div>
        <div class="eyebrow">修仙后台 WebUI</div>
        <h1>欢迎回来，{admin_name}</h1>
        <p>可按玩家名或物品名直接检索；当玩家名或物品名不正确时，页面会直接显示错误提示。</p>
      </div>
      <div class="hero-meta">
        <div class="stat"><span>管理员账号</span><strong>{admin_name}</strong></div>
        <div class="stat"><span>初始化状态</span><strong>{'已完成' if status['bootstrapped'] else '未完成'}</strong></div>
        <div class="stat"><span>后台日志库</span><strong>xiuxiangmrizhi.db</strong></div>
      </div>
    </section>
    <div id="banner" class="banner"></div>
    <section class="toolbar card">
      <div class="toolbar-group">
        <button type="button" class="ghost" onclick="scrollToCard('players-card')">玩家检索</button>
        <button type="button" class="ghost" onclick="scrollToCard('items-card')">物品检索</button>
        <button type="button" class="ghost" onclick="scrollToCard('operations-card')">发放面板</button>
        <button type="button" class="ghost" onclick="scrollToCard('records-card')">记录列表</button>
      </div>
      <div class="toolbar-group">
        <button type="button" class="ghost" onclick="loadAllData()">刷新数据</button>
        <button type="button" class="danger" onclick="logout()">退出登录</button>
      </div>
    </section>
    <div class="grid">
      <section id="players-card" class="card panel">
        <div class="card-head"><div><h2>玩家检索</h2><p>支持 client_id、展示名的精确和模糊搜索。</p></div></div>
        <div class="search-row"><input id="player-query" type="text" placeholder="输入玩家名或玩家 ID" /><button type="button" class="primary" onclick="searchPlayers()">搜索</button></div>
        <div id="player-results" class="result-list empty-state">请先搜索玩家。</div>
      </section>
      <section id="player-detail-card" class="card panel">
        <div class="card-head"><div><h2>玩家详情</h2><p>选中某个玩家后，会在这里展示详情与最近日志。</p></div></div>
        <div id="player-detail" class="detail-box empty-state">请选择一位玩家。</div>
      </section>
      <section id="items-card" class="card panel">
        <div class="card-head"><div><h2>物品检索</h2><p>背包物品和纳戒物品都支持搜索。</p></div></div>
        <div class="search-row split">
          <input id="item-query" type="text" placeholder="输入物品名或物品 ID" />
          <select id="item-scope"><option value="">全部来源</option><option value="backpack">背包</option><option value="ring">纳戒</option></select>
          <button type="button" class="primary" onclick="searchItems()">搜索</button>
        </div>
        <div id="item-results" class="result-list empty-state">请先搜索物品。</div>
      </section>
      <section id="operations-card" class="card panel">
        <div class="card-head"><div><h2>发放面板</h2><p>先预览，再确认执行。</p></div></div>
        <form id="operation-form" class="form-grid compact">
          <label class="field"><span>请求编号</span><input id="request-id" type="text" readonly /></label>
          <label class="field"><span>操作类型</span><select id="action-type" onchange="syncActionForm()"><option value="grant_stones">发放源石</option><option value="grant_item">发放物品</option></select></label>
          <label class="field full-width"><span>目标玩家名 / ID</span><input id="target-player" type="text" placeholder="输入玩家名或玩家 ID" /></label>
          <label class="field stones-field"><span>源石数量</span><input id="stones-amount" type="number" min="0" step="1" placeholder="例如 10000" /></label>
          <div class="item-fields full-width">
            <label class="field"><span>物品来源</span><select id="item-scope-field"><option value="">自动识别</option><option value="backpack">背包</option><option value="ring">纳戒</option></select></label>
            <label class="field grow"><span>物品名 / ID</span><input id="item-id" type="text" placeholder="输入物品名或物品 ID" /></label>
            <label class="field"><span>数量</span><input id="item-quantity" type="number" min="0" step="1" placeholder="例如 5" /></label>
          </div>
          <label class="field full-width"><span>发放原因</span><input id="reason" type="text" value="后台发放" placeholder="例如：活动补发" /></label>
          <label class="field full-width"><span>备注</span><textarea id="note" rows="3" placeholder="可填写补充说明"></textarea></label>
          <div class="form-actions full-width">
            <button type="button" class="primary" onclick="previewOperation()">生成预览</button>
            <button type="button" class="ghost" onclick="confirmOperation()">确认执行</button>
            <button type="button" class="ghost" onclick="resetRequestId()">刷新请求编号</button>
          </div>
        </form>
        <div id="preview-panel" class="preview-box empty-state">还没有生成预览。</div>
      </section>
      <section id="records-card" class="card panel wide">
        <div class="card-head"><div><h2>操作记录</h2><p>显示最近后台发放记录和独立审计日志。</p></div></div>
        <div class="subgrid">
          <div><h3>后台操作列表</h3><div id="operation-list" class="table-list empty-state">正在加载操作记录…</div></div>
          <div><h3>审计日志</h3><div id="audit-log-list" class="table-list empty-state">正在加载审计日志…</div></div>
        </div>
      </section>
    </div>
    """
    data = {"admin": admin, "status": status, "active_tab": active_tab}
    return _render_document("修仙后台", body, data, DASHBOARD_SCRIPT)


def _render_document(title: str, body: str, data: dict[str, Any], script: str) -> str:
    """渲染完整文档。"""

    topbar = '<div class="topbar-left"><div class="brand">修仙后台</div><div class="sub-brand">WebUI 管理面板</div></div><div class="topbar-right"><a class="badge" href="/xiuxian/help" target="_blank" rel="noreferrer">帮助站</a></div>'
    return _DOCUMENT_TEMPLATE.replace("%%TITLE%%", escape(title)).replace("%%TOPBAR%%", topbar).replace("%%BODY%%", body).replace("%%INITIAL_DATA%%", _json_for_script(data)).replace("%%SCRIPT%%", script)


_DOCUMENT_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>%%TITLE%%</title>
  <style>
    :root{color-scheme:dark;--bg:#0b1020;--panel:rgba(17,23,43,.92);--panel-2:rgba(21,30,54,.9);--line:rgba(148,163,184,.18);--text:#e5eefb;--muted:#94a3b8;--brand:#7dd3fc;--success:#4ade80;--warning:#fbbf24;--danger:#fb7185;--shadow:0 18px 60px rgba(2,6,23,.45);font-family:"Inter","PingFang SC","Microsoft YaHei",sans-serif;}
    *{box-sizing:border-box;} body{margin:0;min-height:100vh;background:linear-gradient(180deg,#08101f 0%,#0b1020 100%);color:var(--text);} a{color:inherit;text-decoration:none;} code,pre{font-family:"JetBrains Mono",Consolas,monospace;}
    .topbar{position:sticky;top:0;z-index:10;display:flex;justify-content:space-between;align-items:center;gap:16px;padding:16px 22px;backdrop-filter:blur(16px);background:rgba(8,12,24,.72);border-bottom:1px solid var(--line);} .brand{font-size:1.15rem;font-weight:800;letter-spacing:.08em;} .sub-brand{color:var(--muted);font-size:.85rem;margin-top:4px;} .topbar-left,.topbar-right{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
    .page{width:min(1500px,calc(100vw - 28px));margin:18px auto 40px;} .card{background:linear-gradient(180deg,var(--panel),var(--panel-2));border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);padding:18px;} .hero{display:grid;grid-template-columns:1.7fr 1fr;gap:18px;margin-bottom:14px;} .hero h1{margin:6px 0 10px;font-size:2rem;} .hero p{margin:0;color:var(--muted);line-height:1.75;} .eyebrow{display:inline-flex;align-items:center;gap:8px;color:#c7d2fe;background:rgba(99,102,241,.12);padding:6px 12px;border-radius:999px;font-size:.85rem;border:1px solid rgba(99,102,241,.28);} .hero-meta{display:grid;gap:10px;} .stat{border-radius:16px;border:1px solid var(--line);padding:12px 14px;background:rgba(15,23,42,.56);} .stat span{display:block;color:var(--muted);font-size:.82rem;} .stat strong{display:block;margin-top:6px;font-size:1rem;}
    .toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap;} .toolbar-group{display:flex;gap:10px;flex-wrap:wrap;} .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start;} .panel{min-height:220px;} .wide{grid-column:1/-1;} .card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:14px;} .card-head h2,.card-head h3{margin:0;} .card-head p{margin:6px 0 0;color:var(--muted);} .search-row{display:grid;grid-template-columns:1fr auto;gap:10px;margin-bottom:12px;} .search-row.split{grid-template-columns:1fr 180px auto;} input,select,textarea,button{font:inherit;border-radius:14px;border:1px solid rgba(148,163,184,.22);background:rgba(15,23,42,.8);color:var(--text);outline:none;transition:border-color .15s ease,transform .15s ease,background .15s ease;} input,select,textarea{width:100%;padding:12px 14px;} textarea{resize:vertical;min-height:88px;} input:focus,select:focus,textarea:focus{border-color:rgba(125,211,252,.75);background:rgba(15,23,42,.95);} button{cursor:pointer;padding:11px 16px;font-weight:700;white-space:nowrap;} button:hover{transform:translateY(-1px);} .primary{border-color:rgba(125,211,252,.35);background:linear-gradient(135deg,rgba(59,130,246,.95),rgba(168,85,247,.95));color:#fff;} .ghost{background:rgba(15,23,42,.82);color:var(--text);} .danger{background:rgba(251,113,133,.15);border-color:rgba(251,113,133,.35);color:#fecdd3;} .badge{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:6px 11px;background:rgba(148,163,184,.12);border:1px solid rgba(148,163,184,.2);color:#dbeafe;font-size:.82rem;} .banner{min-height:44px;display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:16px;border:1px solid rgba(125,211,252,.22);background:rgba(37,99,235,.08);color:#dbeafe;margin-bottom:14px;white-space:pre-wrap;line-height:1.65;} .banner.success{border-color:rgba(74,222,128,.28);background:rgba(16,185,129,.12);color:#bbf7d0;} .banner.warning{border-color:rgba(251,191,36,.3);background:rgba(251,191,36,.12);color:#fde68a;} .banner.error{border-color:rgba(248,113,113,.34);background:rgba(248,113,113,.12);color:#fecaca;} .empty-state{padding:14px;border-radius:16px;border:1px dashed rgba(148,163,184,.28);color:var(--muted);background:rgba(15,23,42,.45);line-height:1.7;} .result-list,.detail-box,.preview-box,.table-list{display:grid;gap:10px;} .result-card,.info-card,.preview-card,.record-card{border:1px solid rgba(148,163,184,.18);border-radius:16px;background:rgba(15,23,42,.72);padding:12px 14px;} .result-card .title,.record-card .title{display:flex;justify-content:space-between;gap:10px;align-items:center;} .result-card h4,.record-card h4{margin:0;font-size:1rem;} .result-card p,.record-card p{margin:6px 0 0;color:var(--muted);line-height:1.65;} .meta-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;} .meta-row .badge{font-size:.76rem;} .form-grid{display:grid;gap:12px;} .form-grid.compact{grid-template-columns:repeat(2,minmax(0,1fr));} .field{display:grid;gap:8px;} .field span{color:var(--muted);font-size:.86rem;} .field.full-width{grid-column:1/-1;} .field.grow{min-width:0;} .form-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;} .form-actions.full-width{grid-column:1/-1;} .item-fields{display:grid;grid-template-columns:170px 1fr 110px;gap:10px;align-items:end;} .item-fields.hidden,.stones-field.hidden{display:none;} .preview-json{display:grid;gap:12px;grid-template-columns:repeat(2,minmax(0,1fr));} .preview-json pre{margin:0;padding:12px;border-radius:14px;border:1px solid rgba(148,163,184,.18);background:rgba(2,6,23,.6);overflow:auto;max-height:280px;white-space:pre-wrap;word-break:break-word;} .subgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;} .table-grid{display:grid;gap:8px;} .table-head,.table-row{display:grid;grid-template-columns:84px 130px 1.2fr 1.2fr 1fr 116px;gap:8px;align-items:center;} .table-head{color:var(--muted);font-size:.8rem;padding:0 4px;} .table-row{padding:10px 12px;border:1px solid rgba(148,163,184,.16);border-radius:14px;background:rgba(15,23,42,.72);} .table-row code{color:#bfdbfe;} .table-row .mini{color:var(--muted);font-size:.85rem;} .link-button{padding:8px 12px;border-radius:12px;font-size:.83rem;} .note{color:var(--muted);line-height:1.7;} .auth-shell{width:min(760px,calc(100vw - 28px));margin:28px auto;} .auth-card{padding:24px;} .mini-panel{margin-top:16px;border-top:1px solid var(--line);padding-top:16px;} .bullet-list{margin:10px 0 0;padding-left:20px;color:var(--muted);line-height:1.8;} .hidden{display:none !important;}
    @media (max-width:1100px){.hero,.grid,.subgrid,.preview-json,.form-grid.compact{grid-template-columns:1fr}.table-head,.table-row{grid-template-columns:1fr}.item-fields,.search-row.split{grid-template-columns:1fr}.page{width:min(100vw - 20px,100vw);}}
  </style>
</head>
<body>
  <header class="topbar">%%TOPBAR%%</header>
  <main class="page">%%BODY%%</main>
  <script id="initial-data" type="application/json">%%INITIAL_DATA%%</script>
  <script>%%SCRIPT%%</script>
</body>
</html>
"""


AUTH_SCRIPT = r"""
const INITIAL = JSON.parse(document.getElementById('initial-data').textContent || '{}');
function qs(id){return document.getElementById(id);}
function showBanner(message, kind='info'){const banner=qs('banner')||qs('auth-banner');if(!banner)return;banner.className=`banner ${kind==='error'?'error':kind==='success'?'success':kind==='warning'?'warning':''}`.trim();banner.textContent=message||'';}
async function fetchJson(url, options={}){const response=await fetch(url,{credentials:'same-origin',headers:{'Accept':'application/json','Content-Type':'application/json',...(options.headers||{})},...options});const text=await response.text();let data={};try{data=text?JSON.parse(text):{};}catch{data={ok:false,message:text||`HTTP ${response.status}`};}if(!response.ok||data.ok===false)throw new Error(data.message||data.detail||`HTTP ${response.status}`);return data;}
function val(id){const el=qs(id);return el?String(el.value||'').trim():'';}
async function submitAuth(event){event.preventDefault();try{const mode=INITIAL.mode||'login';const payload={username:val('auth-username'),password:val('auth-password')};if(mode==='bootstrap'){if(val('auth-password')!==val('auth-password-confirm')){showBanner('两次输入的管理员密码不一致。','error');return;}payload.ADMIN_BOOTSTRAP_TOKEN=val('auth-token');const data=await fetchJson('/xiuxian/admin/api/bootstrap',{method:'POST',body:JSON.stringify(payload)});showBanner(data.message||'初始化成功。','success');setTimeout(()=>window.location.href=data.redirect_to||'/xiuxian/admin/login',700);return;}const data=await fetchJson('/xiuxian/admin/api/login',{method:'POST',body:JSON.stringify(payload)});showBanner(data.message||'登录成功。','success');setTimeout(()=>window.location.href=data.redirect_to||'/xiuxian/admin',300);}catch(error){showBanner(error.message||String(error),'error');}}
document.addEventListener('DOMContentLoaded',()=>{const form=qs('auth-form');if(form)form.addEventListener('submit',submitAuth);if(INITIAL.mode==='bootstrap'){const token=qs('auth-token');if(token)token.focus();}else{const username=qs('auth-username');if(username)username.focus();}if(INITIAL.notice)showBanner(INITIAL.notice,INITIAL.mode==='bootstrap'?'warning':'info');});
"""


DASHBOARD_SCRIPT = r"""
const INITIAL = JSON.parse(document.getElementById('initial-data').textContent || '{}');
let currentPreview = null;
function qs(id){return document.getElementById(id);}
function escapeHtml(value){return String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');}
function escapeJsString(value){return JSON.stringify(String(value ?? '')).slice(1, -1).replaceAll("'", "\\'");}
function showBanner(message, kind='info'){const banner=qs('banner');if(!banner)return;banner.className=`banner ${kind==='error'?'error':kind==='success'?'success':kind==='warning'?'warning':''}`.trim();banner.textContent=message||'';}
function scrollToCard(id){const card=qs(id);if(card)card.scrollIntoView({behavior:'smooth',block:'start'});}
function generateRequestId(){if(window.crypto&&crypto.randomUUID)return crypto.randomUUID();return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;}
function resetRequestId(){const el=qs('request-id');if(el)el.value=generateRequestId();showBanner('请求编号已刷新。','info');}
function syncActionForm(){const actionType=qs('action-type').value;const stonesField=qs('stones-amount').closest('.field');const itemFields=qs('item-id').closest('.item-fields');if(actionType==='grant_stones'){stonesField.classList.remove('hidden');itemFields.classList.add('hidden');}else{stonesField.classList.add('hidden');itemFields.classList.remove('hidden');}}
async function fetchJson(url, options={}){const response=await fetch(url,{credentials:'same-origin',headers:{'Accept':'application/json','Content-Type':'application/json',...(options.headers||{})},...options});const text=await response.text();let data={};try{data=text?JSON.parse(text):{};}catch{data={ok:false,message:text||`HTTP ${response.status}`};}if(!response.ok||data.ok===false)throw new Error(data.message||data.detail||`HTTP ${response.status}`);return data;}
function fieldValue(id){const el=qs(id);return el?String(el.value||'').trim():'';}
function fieldNumber(id){const value=Number.parseInt(fieldValue(id),10);return Number.isFinite(value)?value:0;}
function selectPlayer(clientId, displayName=''){const target=qs('target-player');if(target)target.value=clientId||displayName||'';showBanner(`已选择玩家：${displayName||clientId}`,'success');if(clientId)loadPlayerDetail(clientId);}
function selectItem(scope, itemId, name){const scopeEl=qs('item-scope-field');const itemEl=qs('item-id');if(scopeEl)scopeEl.value=scope||'';if(itemEl)itemEl.value=itemId||name||'';showBanner(`已选择物品：${name||itemId}`,'success');}
function renderPlayerResults(items){const box=qs('player-results');if(!items||!items.length){box.className='result-list empty-state';box.textContent='没有找到匹配的玩家。';return;}box.className='result-list';box.innerHTML=items.map((item)=>`<article class="result-card"><div class="title"><h4>${escapeHtml(item.display_name||item.client_id||'未知玩家')}</h4><button type="button" class="link-button ghost" onclick="selectPlayer('${escapeJsString(item.client_id)}','${escapeJsString(item.display_name||'')}')">选择</button></div><p>玩家ID：<code>${escapeHtml(item.client_id)}</code> · 等级 ${escapeHtml(item.level ?? 0)} · 源石 ${escapeHtml(item.source_stones ?? 0)}</p><div class="meta-row"><span class="badge">状态：${escapeHtml(item.status||'')}</span><span class="badge">地点：${escapeHtml(item.location_name||'')}</span><span class="badge">坐标：(${escapeHtml(item.x ?? 0)}, ${escapeHtml(item.y ?? 0)})</span></div></article>`).join('');}
function renderItemResults(items){const box=qs('item-results');if(!items||!items.length){box.className='result-list empty-state';box.textContent='没有找到匹配的物品。';return;}box.className='result-list';box.innerHTML=items.map((item)=>`<article class="result-card"><div class="title"><h4>${escapeHtml(item.name||item.item_id||'未知物品')}</h4><button type="button" class="link-button ghost" onclick="selectItem('${escapeJsString(item.scope||'')}','${escapeJsString(item.item_id||'')}','${escapeJsString(item.name||'')}')">使用</button></div><p><code>${escapeHtml(item.item_id||'')}</code> · 来源：${escapeHtml(item.scope||'')} · 品级 ${escapeHtml(item.quality||'')}</p><div class="meta-row"><span class="badge">分类：${escapeHtml(item.category||'')}</span><span class="badge">用途：${escapeHtml(item.usable ?? 0)}</span>${item.stack_limit?`<span class="badge">堆叠：${escapeHtml(item.stack_limit)}</span>`:''}${item.target_type?`<span class="badge">目标：${escapeHtml(item.target_type)}</span>`:''}</div></article>`).join('');}
function renderPlayerDetail(data){const box=qs('player-detail');if(!data||!data.player){box.className='detail-box empty-state';box.textContent='请选择一位玩家。';return;}const player=data.player;const backpack=data.backpack||[];const ring=data.ring||[];const logs=data.recent_logs||[];const mount=data.mount||{};box.className='detail-box';box.innerHTML=`<article class="info-card"><div class="title"><h4>${escapeHtml(player.display_name||'')}</h4><span class="badge">${escapeHtml(player.client_id||'')}</span></div><p>等级 ${escapeHtml(player.level ?? 0)} · 经验 ${escapeHtml(player.exp ?? 0)} · 下一级：${escapeHtml(player.next_level_text||'')}</p><div class="meta-row"><span class="badge">血气 ${escapeHtml(player.hp ?? 0)}/${escapeHtml(player.max_hp ?? 0)}</span><span class="badge">精神 ${escapeHtml(player.mp ?? 0)}/${escapeHtml(player.max_mp ?? 0)}</span><span class="badge">源石 ${escapeHtml(player.source_stones ?? 0)}</span><span class="badge">状态 ${escapeHtml(player.status||'')}</span><span class="badge">地点 ${escapeHtml(player.location_name||'')}</span></div></article><article class="info-card"><h4>库存摘要</h4><div class="meta-row"><span class="badge">背包格：${escapeHtml(data.summary?.backpack_count ?? 0)}</span><span class="badge">纳戒格：${escapeHtml(data.summary?.ring_count ?? 0)}</span><span class="badge">背包负重：${escapeHtml(data.summary?.backpack_weight ?? 0)}</span><span class="badge">源库：${escapeHtml(data.source_vault?.balance ?? 0)}</span></div></article>${mount.mount_id?`<article class="info-card"><h4>坐骑</h4><div class="title"><h4>${escapeHtml(mount.tier_text||'')}${mount.direction?' · '+escapeHtml(mount.direction):''} · ${escapeHtml(mount.name||'')}</h4><span class="badge">⭐${escapeHtml(mount.stars??0)}/${escapeHtml(mount.max_stars??0)}</span></div><p>${escapeHtml(mount.lore||'')}</p>${mount.blessing_value?`<p>进阶祝福：${escapeHtml(mount.blessing_value)}${mount.blessing_expires_at?' · 过期 '+escapeHtml(mount.blessing_expires_at):''}</p>`:''}</article>`:''}<article class="info-card"><h4>背包</h4><div class="table-grid">${backpack.length?backpack.map((row)=>`<div class="record-card"><div class="title"><h4>${escapeHtml(row.name||row.item_id||'')}</h4><span class="badge">x${escapeHtml(row.quantity ?? 0)}</span></div><p><code>${escapeHtml(row.item_id||'')}</code> · ${escapeHtml(row.category||'')} · 品级 ${escapeHtml(row.quality||'')}</p></div>`).join(''):'<div class="empty-state">背包为空。</div>'}</div></article><article class="info-card"><h4>纳戒</h4><div class="table-grid">${ring.length?ring.map((row)=>`<div class="record-card"><div class="title"><h4>${escapeHtml(row.name||row.ring_item_id||row.item_id||'')}</h4><span class="badge">x${escapeHtml(row.quantity ?? 0)}</span></div><p><code>${escapeHtml(row.ring_item_id||row.item_id||'')}</code> · ${escapeHtml(row.category||'')} · 品级 ${escapeHtml(row.quality||'')}</p></div>`).join(''):'<div class="empty-state">纳戒为空。</div>'}</div></article><article class="info-card"><h4>最近游戏日志</h4><div class="table-grid">${logs.length?logs.map((row)=>`<div class="record-card"><div class="title"><h4>${escapeHtml(row.action||'')}</h4><span class="badge">#${escapeHtml(row.log_id ?? 0)}</span></div><p>${escapeHtml(row.detail||'')}</p><div class="meta-row"><span class="badge">${escapeHtml(row.created_at||'')}</span></div></div>`).join(''):'<div class="empty-state">暂无日志。</div>'}</div></article>`;}
function renderOperations(items){const box=qs('operation-list');if(!items||!items.length){box.className='table-list empty-state';box.textContent='暂无后台操作记录。';return;}box.className='table-grid';box.innerHTML=`<div class="table-head"><div>ID</div><div>状态</div><div>目标</div><div>物品/源石</div><div>时间</div><div>操作</div></div>${items.map((row)=>`<div class="table-row"><div><code>#${escapeHtml(row.operation_id ?? 0)}</code></div><div><span class="badge ${row.status==='success'?'success':row.status==='failed'?'warning':''}">${escapeHtml(row.status||'')}</span></div><div><div>${escapeHtml(row.target_name_snapshot||row.target_client_id||'')}</div><div class="mini">${escapeHtml(row.target_client_id||'')}</div></div><div><div>${row.action_type==='grant_stones'?`源石 ${escapeHtml(row.stones_amount ?? 0)}`:`${escapeHtml(row.item_name_snapshot||row.item_id_snapshot||'')} x${escapeHtml(row.quantity ?? 0)}`}</div><div class="mini">${escapeHtml(row.reason||'')}</div></div><div class="mini">${escapeHtml(row.created_at||'')}</div><div><button type="button" class="link-button ghost" onclick="viewOperation(${Number(row.operation_id||0)})">查看</button></div></div>`).join('')}`;}
function renderAuditLogs(items){const box=qs('audit-log-list');if(!items||!items.length){box.className='table-list empty-state';box.textContent='暂无审计日志。';return;}box.className='table-grid';box.innerHTML=`<div class="table-head"><div>日志</div><div>阶段</div><div>结果</div><div>目标/物品</div><div>原因</div><div>时间</div></div>${items.map((row)=>`<div class="table-row"><div><code>#${escapeHtml(row.log_id ?? 0)}</code></div><div><span class="badge">${escapeHtml(row.stage||'')}</span></div><div><span class="badge ${row.status==='success'?'success':row.status==='failed'?'warning':''}">${escapeHtml(row.status||'')}</span></div><div><div>${escapeHtml(row.target_name_snapshot||row.target_client_id||'')}</div><div class="mini">${escapeHtml(row.item_name_snapshot||row.item_id_snapshot||'')}</div></div><div class="mini">${escapeHtml(row.error_message||'')}</div><div class="mini">${escapeHtml(row.created_at||'')}</div></div>`).join('')}`;}
function renderPreview(operation){const box=qs('preview-panel');if(!operation){box.className='preview-box empty-state';box.textContent='还没有生成预览。';return;}currentPreview=operation;box.className='preview-box';box.innerHTML=`<article class="preview-card"><div class="title"><h4>预览 #${escapeHtml(operation.operation_id ?? 0)}</h4><span class="badge">${escapeHtml(operation.status||'')}</span></div><div class="meta-row"><span class="badge">请求编号 ${escapeHtml(operation.request_id||'')}</span><span class="badge">管理员 ${escapeHtml(operation.admin_username||'')}</span><span class="badge">目标 ${escapeHtml(operation.target_name_snapshot||operation.target_client_id||'')}</span><span class="badge">物品 ${escapeHtml(operation.item_name_snapshot||operation.item_id_snapshot||'源石')}</span></div><p>原因：${escapeHtml(operation.reason||'')} · 备注：${escapeHtml(operation.note||'')}</p><div class="preview-json"><div><h5>执行前</h5><pre>${escapeHtml(JSON.stringify(operation.before||{}, null, 2))}</pre></div><div><h5>执行后</h5><pre>${escapeHtml(JSON.stringify(operation.after||{}, null, 2))}</pre></div></div><div><h5>警告</h5>${(operation.warnings||[]).length?`<ul>${(operation.warnings||[]).map((item)=>`<li>${escapeHtml(typeof item==='string'?item:JSON.stringify(item))}</li>`).join('')}</ul>`:'<div class="empty-state">没有警告。</div>'}</div></article>`;}
function previewError(message){showBanner(message,'error');const box=qs('preview-panel');if(box){box.className='preview-box empty-state';box.textContent=message;}}
function buildOperationPayload(){const actionType=fieldValue('action-type');const target=fieldValue('target-player');const payload={request_id:fieldValue('request-id'),action_type:actionType,target_client_id:target,target_query:target,reason:fieldValue('reason')||'后台发放',note:fieldValue('note')};if(actionType==='grant_stones'){payload.stones_amount=fieldNumber('stones-amount');}else{const item=fieldValue('item-id');payload.item_scope=fieldValue('item-scope-field');payload.item_id=item;payload.item_query=item;payload.quantity=fieldNumber('item-quantity');}return payload;}
async function previewOperation(){try{const data=await fetchJson('/xiuxian/admin/api/operations/preview',{method:'POST',body:JSON.stringify(buildOperationPayload())});renderPreview(data.operation);showBanner(data.message||'预览已生成。','success');await loadOperations();await loadAuditLogs();}catch(error){previewError(error.message||String(error));}}
async function confirmOperation(){try{if(!currentPreview){showBanner('请先生成预览，再确认执行。','warning');return;}const data=await fetchJson('/xiuxian/admin/api/operations/confirm',{method:'POST',body:JSON.stringify({preview_id:currentPreview.operation_id,request_id:currentPreview.request_id})});renderPreview(data.operation);showBanner(data.message||'发放成功。','success');await loadOperations();await loadAuditLogs();const target=fieldValue('target-player');if(target)await loadPlayerDetail(target);}catch(error){previewError(error.message||String(error));}}
async function searchPlayers(){try{const data=await fetchJson(`/xiuxian/admin/api/players?q=${encodeURIComponent(fieldValue('player-query'))}`);renderPlayerResults(data.items||[]);showBanner(`找到 ${data.items ? data.items.length : 0} 个玩家结果。`,'success');}catch(error){showBanner(error.message||String(error),'error');}}
async function loadPlayerDetail(clientId){if(!clientId)return;try{const data=await fetchJson(`/xiuxian/admin/api/players/${encodeURIComponent(clientId)}`);renderPlayerDetail(data);}catch(error){showBanner(error.message||String(error),'error');const box=qs('player-detail');if(box){box.className='detail-box empty-state';box.textContent=error.message||String(error);}}}
async function searchItems(){try{const data=await fetchJson(`/xiuxian/admin/api/items?q=${encodeURIComponent(fieldValue('item-query'))}&scope=${encodeURIComponent(fieldValue('item-scope'))}`);renderItemResults(data.items||[]);showBanner(`找到 ${data.items ? data.items.length : 0} 个物品结果。`,'success');}catch(error){showBanner(error.message||String(error),'error');}}
async function viewOperation(operationId){try{const data=await fetchJson(`/xiuxian/admin/api/operations/${operationId}`);renderPreview(data.operation);scrollToCard('operations-card');}catch(error){showBanner(error.message||String(error),'error');}}
async function loadOperations(){try{const data=await fetchJson('/xiuxian/admin/api/operations?limit=20');renderOperations(data.items||[]);}catch(error){const box=qs('operation-list');if(box){box.className='table-list empty-state';box.textContent=error.message||String(error);}}}
async function loadAuditLogs(){try{const data=await fetchJson('/xiuxian/admin/api/audit-logs?limit=20');renderAuditLogs(data.items||[]);}catch(error){const box=qs('audit-log-list');if(box){box.className='table-list empty-state';box.textContent=error.message||String(error);}}}
async function loadAllData(){await Promise.all([loadOperations(), loadAuditLogs()]);const target=fieldValue('target-player');if(target)await loadPlayerDetail(target);showBanner('数据已刷新。','success');}
async function logout(){try{const data=await fetchJson('/xiuxian/admin/api/logout',{method:'POST',body:'{}'});showBanner(data.message||'已退出登录。','success');setTimeout(()=>window.location.href=data.redirect_to||'/xiuxian/admin/login',200);}catch(error){showBanner(error.message||String(error),'error');}}
document.addEventListener('DOMContentLoaded',()=>{const requestId=qs('request-id');if(requestId)requestId.value=generateRequestId();syncActionForm();const actionType=qs('action-type');if(actionType)actionType.addEventListener('change',syncActionForm);const playerQuery=qs('player-query');if(playerQuery)playerQuery.addEventListener('keydown',(event)=>{if(event.key==='Enter'){event.preventDefault();searchPlayers();}});const itemQuery=qs('item-query');if(itemQuery)itemQuery.addEventListener('keydown',(event)=>{if(event.key==='Enter'){event.preventDefault();searchItems();}});loadAllData();if(INITIAL.active_tab==='operations')setTimeout(()=>scrollToCard('records-card'),120);});
"""


__all__ = ["router"]
