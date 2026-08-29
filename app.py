#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地离线学习笔记站 · Flask 后端
单进程、单机、纯本地，零外部网络请求。
"""
import os
import io
import re
import sys
import socket
import zipfile
import sqlite3
import secrets
import hashlib
import hmac
import time
import datetime
import shutil
import html
from pathlib import Path

from werkzeug.utils import secure_filename
from flask import (
    Flask, request, jsonify, render_template, send_from_directory,
    abort, g, Response, session, redirect,
)
from functools import wraps

import bleach
import markdown as markdown_lib

# ============ 配置 ============
# 程序目录（代码/静态资源所在；PyInstaller 打包时指向 _MEIPASS 解包目录）
if getattr(sys, "frozen", False):
    _bundle_dir = Path(sys._MEIPASS)
else:
    _bundle_dir = Path(__file__).resolve().parent
BASE_DIR = _bundle_dir

# 资源目录（templates / static / sample_files）。
# 打包后（frozen）指向 PyInstaller 的 _MEIPASS 解包目录；开发时与程序目录相同。
RES_DIR = BASE_DIR
STATIC_DIR = RES_DIR / "static"
SAMPLE_DIR = RES_DIR / "sample_files"

# 数据目录（可写持久化：notes.db / uploads/）。
# frozen 时 __file__ 位于临时解包目录（_MEIPASS），不可持久化，
# 故改为 exe 所在目录（与 exe 同目录的"便携模式"）；开发时即程序目录。
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
else:
    _exe_dir = Path(__file__).resolve().parent
DATA_DIR = _exe_dir
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "notes.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".html", ".htm", ".pdf"}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB

# bleach 白名单：仅文档类标签，剥除 script / 事件属性 / javascript: 协议
ALLOWED_TAGS = [
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "u", "s", "del", "sub", "sup",
    "a", "code", "pre", "blockquote",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "img", "div", "span", "caption", "colgroup", "col",
    "dl", "dt", "dd",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "name"],
    "img": ["src", "alt", "title", "width", "height"],
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
    "code": ["class"],
    "pre": ["class"],
    "span": ["class"],
    "div": ["class"],
    "style": [],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto", "data"]

# HTML 渲染用：保留页面结构和样式，但坚决剥掉脚本/事件
HTML_PREVIEW_TAGS = list(ALLOWED_TAGS) + [
    "style", "title", "meta", "link",
    "html", "head", "body", "section", "article", "aside", "header", "footer", "nav",
    "figure", "figcaption",
]
HTML_PREVIEW_ATTRS = dict(ALLOWED_ATTRIBUTES)
HTML_PREVIEW_ATTRS["meta"] = ["charset", "name", "content"]
HTML_PREVIEW_ATTRS["link"] = ["rel", "href"]
HTML_PREVIEW_ATTRS["style"] = ["media"]
HTML_PREVIEW_ATTRS["*"] = ["class", "id", "style"]

# 允许 style 属性和 style 标签，这不会执行脚本，只影响外观
CSS_SANITIZER = None  # 暂不做 CSS 白名单，依赖 sandbox 禁止行为

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static",
            template_folder=str(RES_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.config["JSON_AS_ASCII"] = False

# ============ 局域网模式（可选） ============
# 默认仅监听 127.0.0.1；`python app.py --lan` 时允许局域网内其它设备访问
LAN_MODE = False
LAN_PORT = 8848


def get_lan_ip() -> str:
    """获取本机局域网地址：UDP connect 技巧（连接不发送任何字节，保持零外联）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        pass
    # 回退：枚举本机非回环 IPv4 接口
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if not ip.startswith("127.") and ":" not in ip:
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def lan_display_url() -> str:
    """LAN 模式下返回本机可被局域网访问的地址，否则返回空字符串"""
    if not LAN_MODE:
        return ""
    return f"http://{get_lan_ip()}:{LAN_PORT}"

# ============ 授权登录 ============
# 进程级随机 SECRET_KEY：签名 session cookie，重启后登录态失效（可接受）
app.config["SECRET_KEY"] = secrets.token_hex(32)
app.permanent_session_lifetime = datetime.timedelta(days=7)  # “记住登录”有效期

MAX_FAILS = 3
LOCK_SECONDS = 300  # 5 分钟
# 内存锁定状态：{源地址: {"fails": [...时间戳], "lock_until": epoch}}
_auth_locks = {}


def _hmac_hash(password: str, salt: str) -> str:
    """PBKDF2-HMAC-SHA256 加盐哈希，标准库实现，纯离线可用"""
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations=120_000)
    return dk.hex()


def get_setting(key, default=None):
    row = get_db().execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        """INSERT INTO app_settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (key, value))
    db.commit()


def has_password() -> bool:
    return bool(get_setting("password_salt")) and bool(get_setting("password_hash"))


def set_password(plain: str) -> None:
    salt = secrets.token_hex(16)
    set_setting("password_salt", salt)
    set_setting("password_hash", _hmac_hash(plain, salt))


def verify_password(plain: str) -> bool:
    salt = get_setting("password_salt")
    expected = get_setting("password_hash")
    if not salt or not expected:
        return False
    got = _hmac_hash(plain, salt)
    return hmac.compare_digest(got, expected)


# ---- 密保问题（登录后修改密码需原密码 + 密保答案双验证） ----
SECURITY_QUESTIONS = [
    "你的小学名称是？",
    "你第一个宠物的名字是？",
    "你的故乡城市是？",
    "你最喜欢的一部电影是？",
    "你的学号或工号后四位是？",
    "你小时候的昵称是？",
]


def get_security_question() -> str:
    return get_setting("security_question") or ""


def has_security() -> bool:
    return bool(get_setting("security_question")) and bool(get_setting("security_answer_hash"))


def verify_security(plain: str) -> bool:
    salt = get_setting("security_answer_salt")
    expected = get_setting("security_answer_hash")
    if not salt or not expected:
        return False
    got = _hmac_hash(plain.strip(), salt)
    return hmac.compare_digest(got, expected)


def set_security(question: str, answer: str):
    """设置/更新密保：问题文本直接存，答案与密码同等强度加盐哈希存储"""
    salt = secrets.token_hex(16)
    set_setting("security_question", question.strip())
    set_setting("security_answer_salt", salt)
    set_setting("security_answer_hash", _hmac_hash(answer.strip(), salt))


def client_ip() -> str:
    """本机回环应用，取远端地址即足够"""
    return request.remote_addr or "local"


def _record_fail(ip: str, now=None) -> int:
    """记录一次失败，返回当前 5 分钟内失败次数"""
    now = now or time.time()
    state = _auth_locks.setdefault(ip, {"fails": [], "lock_until": 0})
    # 滑动窗口：清掉 5 分钟前的旧记录
    state["fails"] = [t for t in state["fails"] if now - t < LOCK_SECONDS]
    state["fails"].append(now)
    # 若达到阈值则触发锁定
    if len(state["fails"]) >= MAX_FAILS:
        state["lock_until"] = now + LOCK_SECONDS
    return len(state["fails"])


def _check_locked(ip: str, now=None) -> int:
    """返回剩余锁定秒数；0 表示未锁定"""
    now = now or time.time()
    state = _auth_locks.get(ip)
    if not state:
        return 0
    remain = state.get("lock_until", 0) - now
    if remain > 0:
        return int(remain) + 1
    # 锁已过期：清掉计数
    if remain <= 0 and state["lock_until"]:
        state["fails"] = []
        state["lock_until"] = 0
    return 0


def _clear_locks(ip: str) -> None:
    state = _auth_locks.get(ip)
    if state:
        state["fails"] = []
        state["lock_until"] = 0


AUTH_OPEN_PATHS = {"/api/login", "/api/logout", "/api/setup-password", "/api/auth/status", "/api/lan-info"}
AUTH_OPEN_PREFIXES = ("/static/",)


@app.before_request
def auth_gate():
    """全局限流：未登录拦截所有页面与 API（静态资源与 auth 接口放行）"""
    path = request.path
    if path == "/login" or path.startswith(AUTH_OPEN_PREFIXES) or path in AUTH_OPEN_PATHS:
        return None
    # 未设置密码：任何非 auth 请求都去设置页（引导首次设置）
    if not has_password():
        if path.startswith("/api/"):
            return jsonify({"error": "NEED_SETUP", "need_setup": True}), 401
        return redirect("/login")
    if not session.get("authed"):
        if path.startswith("/api/"):
            return jsonify({"error": "未登录", "code": "UNAUTHORIZED"}), 401
        return redirect("/login")
    return None

# ============ 数据库 ============
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_id INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        stored_filename TEXT NOT NULL,
        file_type TEXT NOT NULL,
        folder_id INTEGER,
        content_text TEXT DEFAULT '',
        render_mode TEXT DEFAULT 'sanitize',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS document_tags (
        document_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        PRIMARY KEY (document_id, tag_id),
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
    CREATE INDEX IF NOT EXISTS idx_documents_folder ON documents(folder_id);

    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
        title,
        content_text,
        document_id UNINDEXED,
        tokenize = "unicode61 remove_diacritics 2"
    );
    """)
    # 迁移：老库没有 render_mode 列则补上（默认 sanitize）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
    if "render_mode" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN render_mode TEXT DEFAULT 'sanitize'")

    # 个人备注表（每篇文档最多一条"我的备注"）
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS document_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL UNIQUE,
        note_content TEXT DEFAULT '',
        updated_at TEXT NOT NULL,
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    );
    -- 应用级配置（密码盐/哈希等）
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    # 兼容老库：早期 seed 未同步 FTS，启动自动补齐索引
    conn.row_factory = sqlite3.Row
    ensure_fts_index(conn)
    conn.commit()
    conn.close()


# ============ 工具函数 ============
def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def make_stored_filename(filename):
    """secure_filename + 随机前缀，防目录穿越与重名覆盖"""
    safe = secure_filename(filename)
    if not safe or safe in (".", ""):
        safe = "untitled"
    prefix = secrets.token_hex(8)
    ext = Path(filename).suffix.lower()
    # safe 可能已带扩展名，去掉再统一追加，避免 .pdf.pdf 这类重复
    if safe.lower().endswith(ext):
        safe = safe[: -len(ext)]
    return f"{prefix}_{safe}{ext}"


def extract_text(filepath, file_type):
    """抽取纯文本用于全文索引。PDF 本版本不抽取（保持简单）。"""
    try:
        if file_type in (".txt", ".md", ".markdown"):
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        if file_type in (".html", ".htm"):
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            # 剥标签留文本
            return bleach.clean(raw, tags=[], strip=True)
    except Exception:
        return ""
    return ""


def render_markdown(text):
    """Markdown -> HTML，带代码高亮"""
    md = markdown_lib.Markdown(extensions=[
        "extra", "codehilite", "tables", "fenced_code", "toc", "sane_lists",
    ])
    return md.convert(text)


def sanitize_html(raw_html):
    """bleach 严格消毒：剥除脚本 / 事件属性 / javascript: 协议"""
    return bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )


def sanitize_html_for_preview(raw_html):
    """保留样式/布局（style 标签、html/body 结构），但剥除脚本和事件属性。
    说明：bleach 默认不会解析 style 属性的 CSS 内容，我们依赖 iframe sandbox + CSP
    （script-src=none, form-action=none, sandbox）作为安全兜底，不把 style 属性剥除。
    """
    try:
        import warnings as _w
        from bleach.sanitizer import NoCssSanitizerWarning
        _w.filterwarnings("ignore", category=NoCssSanitizerWarning)
    except Exception:
        pass
    return bleach.clean(
        raw_html,
        tags=HTML_PREVIEW_TAGS,
        attributes=HTML_PREVIEW_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=None,
        strip=False,
        strip_comments=True,
    )


def update_fts(db, doc_id, title, content_text):
    """同步 FTS5 索引"""
    db.execute("DELETE FROM documents_fts WHERE document_id = ?", (doc_id,))
    db.execute(
        "INSERT INTO documents_fts (title, content_text, document_id) VALUES (?, ?, ?)",
        (title, content_text or "", doc_id),
    )


def ensure_fts_index(conn=None):
    """保证 FTS5 索引覆盖 documents 全部文档（有任一文档缺失时全量重建）。
    兼容老库：早期版本 seed_samples() 插样例时未同步 FTS，导致全文检索永远为空，
    启动时检测到缺失则自动补齐。
    注意：不能用 COUNT(*) 对比判断——FTS5 的 COUNT 会把"已标记删除未合并"的行
    计入，正常编辑过的库会误判为不一致、每次都全量重建。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        missing = conn.execute(
            """SELECT COUNT(*) AS c FROM documents d
               WHERE NOT EXISTS (SELECT 1 FROM documents_fts f WHERE f.document_id = d.id)"""
        ).fetchone()["c"]
        if not missing:
            return
        conn.execute("DELETE FROM documents_fts")
        rows = conn.execute("SELECT id, title, content_text FROM documents").fetchall()
        conn.executemany(
            "INSERT INTO documents_fts (title, content_text, document_id) VALUES (?, ?, ?)",
            [(r["id"], r["title"], r["content_text"] or "") for r in rows],
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def _fts_query(q: str):
    """把用户输入转成安全的 FTS5 MATCH 表达式：按空白/标点分词、AND 连接，
    避免 `"` `*` `-` `:` 等语法字符导致 MATCH 报错（异常会被 search() 吞掉）。
    中文场景下不强制短语连续，命中率远高于当年带引号的短语模式。"""
    tokens = [t for t in re.split(r'[\s"\'()*:+^#@\-]+', q) if t]
    if not tokens:
        return None
    return " AND ".join(tokens)


def get_doc_tags(db, doc_id):
    rows = db.execute(
        """SELECT t.id, t.name FROM tags t
           JOIN document_tags dt ON t.id = dt.tag_id
           WHERE dt.document_id = ? ORDER BY t.name""",
        (doc_id,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def set_doc_tags(db, doc_id, tag_names):
    """覆盖式设置文档标签"""
    db.execute("DELETE FROM document_tags WHERE document_id = ?", (doc_id,))
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        row = db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            db.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?, ?)",
                (doc_id, row["id"]),
            )


def doc_to_dict(db, row):
    return {
        "id": row["id"],
        "title": row["title"],
        "original_filename": row["original_filename"],
        "stored_filename": row["stored_filename"],
        "file_type": row["file_type"],
        "render_mode": row["render_mode"] or "sanitize",
        "folder_id": row["folder_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "tags": get_doc_tags(db, row["id"]),
    }


# ============ 页面路由 ============
@app.route("/")
def index():
    return render_template("index.html", security_questions=SECURITY_QUESTIONS)


# ============ 授权登录路由 ============
@app.route("/login")
def login_page():
    """登录页（未设置密码时兼作设置密码引导页）"""
    return render_template("login.html", has_password=has_password(),
                           security_questions=SECURITY_QUESTIONS,
                           lan_url=lan_display_url())


@app.route("/api/lan-info")
def api_lan_info():
    """局域网模式信息（供前端提示手机访问地址，默认模式返回 disabled）"""
    return jsonify({
        "enabled": LAN_MODE,
        "url": lan_display_url(),
    })


@app.route("/api/auth/status")
def auth_status():
    ip = client_ip()
    return jsonify({
        "has_password": has_password(),
        "authed": bool(session.get("authed")),
        "locked_seconds": _check_locked(ip),
    })


@app.route("/api/setup-password", methods=["POST"])
def setup_password():
    """首次设置密码（仅未设置过时允许），同时设置密保问题与答案"""
    if has_password():
        return jsonify({"error": "已设置过密码"}), 400
    data = request.get_json(force=True) or {}
    pw = data.get("password") or ""
    if len(pw) < 4 or len(pw) > 128:
        return jsonify({"error": "密码长度需 4-128 字符"}), 400
    question = (data.get("security_question") or "").strip()
    answer = (data.get("security_answer") or "").strip()
    if not question:
        return jsonify({"error": "请选择或填写密保问题"}), 400
    if len(answer) < 2 or len(answer) > 128:
        return jsonify({"error": "密保答案长度需 2-128 字符"}), 400
    set_password(pw)
    set_security(question, answer)
    session.permanent = True
    session["authed"] = True
    return jsonify({"ok": True})


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True) or {}
    pw = data.get("password") or ""
    ip = client_ip()
    # 锁定中：直接拒绝，并返回剩余秒数
    remain = _check_locked(ip)
    if remain > 0:
        return jsonify({"error": f"尝试次数过多，请 {remain} 秒后再试",
                        "locked_seconds": remain}), 429
    if not has_password():
        return jsonify({"error": "NEED_SETUP", "need_setup": True}), 401
    if verify_password(pw):
        _clear_locks(ip)
        session.permanent = True
        session["authed"] = True
        return jsonify({"ok": True})
    fails = _record_fail(ip)
    locked = _check_locked(ip)
    if locked > 0:
        return jsonify({"error": f"尝试次数过多，已锁定 {locked} 秒",
                        "locked_seconds": locked}), 429
    return jsonify({"error": f"密码错误，还可尝试 {MAX_FAILS - fails} 次"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("authed", None)
    return jsonify({"ok": True})


# ============ 密保与修改密码 API ============
@app.route("/api/security/status")
def api_security_status():
    """查询是否已设置密保及其问题（登录态内；问题文本非敏感，可回显）"""
    return jsonify({
        "configured": has_security(),
        "question": get_security_question(),
        "question_options": SECURITY_QUESTIONS,
    })


@app.route("/api/security/set", methods=["POST"])
def api_security_set():
    """补录/修改密保：需验证原密码；已有密保时（更换场景）还需验证旧密保答案"""
    data = request.get_json(force=True) or {}
    old_pw = data.get("old_password") or ""
    if not verify_password(old_pw):
        return jsonify({"error": "原密码错误"}), 401
    if has_security():
        old_sec = (data.get("old_security_answer") or "").strip()
        if not verify_security(old_sec):
            return jsonify({"error": "旧密保答案错误"}), 401
    question = (data.get("security_question") or "").strip()
    answer = (data.get("security_answer") or "").strip()
    if not question:
        return jsonify({"error": "请选择或填写密保问题"}), 400
    if len(answer) < 2 or len(answer) > 128:
        return jsonify({"error": "密保答案长度需 2-128 字符"}), 400
    set_security(question, answer)
    return jsonify({"ok": True})


@app.route("/api/password/change", methods=["POST"])
def api_password_change():
    """登录态内修改密码：需原密码 + 密保答案双双验证通过；成功后强制重新登录"""
    data = request.get_json(force=True) or {}
    old_pw = data.get("old_password") or ""
    sec = (data.get("security_answer") or "").strip()
    new_pw = (data.get("new_password") or "").strip()
    if len(new_pw) < 4 or len(new_pw) > 128:
        return jsonify({"error": "新密码长度需 4-128 字符"}), 400
    if not has_security():
        return jsonify({"error": "未设置密保，请先补录密保问题", "code": "NEED_SECURITY"}), 400
    if not verify_password(old_pw):
        return jsonify({"error": "原密码错误"}), 401
    if not verify_security(sec):
        return jsonify({"error": "密保答案错误"}), 401
    set_password(new_pw)
    session.clear()  # 清空登录态，强制用新密码重新登录
    resp = jsonify({"ok": True})
    # 显式让客户端删除旧 session cookie（仍在有效期内，否则旧会话可继续使用）
    resp.delete_cookie("session", path="/")
    return resp


# ============ 目录树 API ============
@app.route("/api/tree")
def get_tree():
    """返回所有目录与文档，前端组装成树"""
    db = get_db()
    folders = db.execute(
        "SELECT * FROM folders ORDER BY name COLLATE NOCASE"
    ).fetchall()
    docs = db.execute("SELECT * FROM documents ORDER BY title COLLATE NOCASE").fetchall()
    return jsonify({
        "folders": [dict(f) for f in folders],
        "documents": [doc_to_dict(db, d) for d in docs],
    })


@app.route("/api/folders", methods=["POST"])
def create_folder():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    parent_id = data.get("parent_id")
    if not name:
        return jsonify({"error": "目录名不能为空"}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO folders (name, parent_id, created_at) VALUES (?, ?, ?)",
        (name, parent_id, now()),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "name": name, "parent_id": parent_id})


@app.route("/api/folders/<int:fid>", methods=["PUT"])
def update_folder(fid):
    data = request.get_json(force=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone()
    if not row:
        abort(404)
    name = (data.get("name") or row["name"]).strip()
    parent_id = data.get("parent_id", row["parent_id"])
    if parent_id == fid:
        return jsonify({"error": "目录不能是自己的父目录"}), 400
    db.execute(
        "UPDATE folders SET name = ?, parent_id = ? WHERE id = ?",
        (name, parent_id, fid),
    )
    db.commit()
    return jsonify({"id": fid, "name": name, "parent_id": parent_id})


@app.route("/api/folders/<int:fid>", methods=["DELETE"])
def delete_folder(fid):
    db = get_db()
    db.execute("DELETE FROM folders WHERE id = ?", (fid,))
    db.commit()
    return jsonify({"ok": True})


# ============ 文档 API ============
@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "缺少文件"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "文件名为空"}), 400
    if not allowed_file(f.filename):
        return jsonify({"error": f"不支持的类型，仅支持 {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    file_type = Path(f.filename).suffix.lower()
    stored = make_stored_filename(f.filename)
    fpath = UPLOAD_DIR / stored
    f.save(str(fpath))

    title = (request.form.get("title") or Path(f.filename).stem).strip()
    folder_id = request.form.get("folder_id") or None
    if folder_id == "":
        folder_id = None
    tag_str = request.form.get("tags") or ""
    tag_names = [t.strip() for t in tag_str.split(",") if t.strip()]
    render_mode = request.form.get("render_mode") or "sanitize"
    if render_mode not in ("sanitize", "raw"):
        render_mode = "sanitize"

    content_text = extract_text(fpath, file_type)

    db = get_db()
    cur = db.execute(
        """INSERT INTO documents
           (title, original_filename, stored_filename, file_type, folder_id, content_text, render_mode, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, f.filename, stored, file_type, folder_id, content_text, render_mode, now(), now()),
    )
    doc_id = cur.lastrowid
    set_doc_tags(db, doc_id, tag_names)
    update_fts(db, doc_id, title, content_text)
    db.commit()

    row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return jsonify(doc_to_dict(db, row))


@app.route("/api/notes", methods=["POST"])
def create_note():
    """站内直接新建 txt / md 笔记，支持 Markdown，入库前做安全处理"""
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    content = data.get("content") or ""
    file_type = (data.get("file_type") or ".md").lower().strip()
    if file_type not in (".md", ".markdown", ".txt"):
        file_type = ".md"
    if file_type == ".markdown":
        file_type = ".md"
    folder_id = data.get("folder_id")
    if folder_id == "":
        folder_id = None
    tag_names = [t.strip() for t in (data.get("tags") or "").split(",") if t.strip()]
    render_mode = data.get("render_mode") or "sanitize"
    if render_mode not in ("sanitize", "raw"):
        render_mode = "sanitize"

    if not title:
        return jsonify({"error": "标题不能为空"}), 400

    # 文件名：标题 sanitize 后的 stem + 类型；仍走 make_stored_filename 防穿越/重名
    raw_filename = title + file_type
    stored = make_stored_filename(raw_filename)
    fpath = UPLOAD_DIR / stored
    fpath.write_text(content, encoding="utf-8")

    content_text = content  # txt/md 全文即索引文本
    db = get_db()
    cur = db.execute(
        """INSERT INTO documents
           (title, original_filename, stored_filename, file_type, folder_id, content_text, render_mode, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, f"{title}{file_type}", stored, file_type, folder_id, content_text, render_mode, now(), now()),
    )
    doc_id = cur.lastrowid
    set_doc_tags(db, doc_id, tag_names)
    update_fts(db, doc_id, title, content_text)
    db.commit()

    row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return jsonify(doc_to_dict(db, row))


@app.route("/api/documents/<int:did>")
def get_document(did):
    db = get_db()
    row = db.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    if not row:
        abort(404)
    data = doc_to_dict(db, row)
    ft = row["file_type"]
    stored = row["stored_filename"]
    fpath = UPLOAD_DIR / stored
    try:
        # 兜底：若磁盘上找不到 stored_filename，尝试通过 original_filename 在 uploads/ 里匹配
        if not fpath.exists():
            alt_name = None
            for cand in UPLOAD_DIR.iterdir():
                if cand.is_file() and cand.name.endswith(ft):
                    # 取最匹配的一个文件（通常 uploads/ 数量少，直接选首个未被其它 doc 占用的）
                    used = {r[0] for r in db.execute("SELECT stored_filename FROM documents").fetchall()}
                    if cand.name not in used:
                        alt_name = cand.name
                        break
            if alt_name:
                stored = alt_name
                fpath = UPLOAD_DIR / stored
                data["stored_filename"] = stored
                db.execute("UPDATE documents SET stored_filename = ? WHERE id = ?", (stored, did))
                db.commit()
        # 根据类型渲染
        if ft in (".md", ".markdown"):
            try:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                raw = f"（读取失败：{e}）"
            # markdown 渲染后必须过 bleach 消毒：markdown 允许内嵌原始 HTML，防止 XSS
            data["rendered"] = sanitize_html(render_markdown(raw))
            data["raw"] = raw
        elif ft in (".html", ".htm"):
            try:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                raw = f"（读取失败：{e}）"
            data["rendered"] = sanitize_html(raw)
            data["raw"] = raw
        elif ft == ".txt":
            try:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                raw = f"（读取失败：{e}）"
            data["rendered"] = f"<pre class='txt-view'>{html.escape(raw)}</pre>"
            data["raw"] = raw
        elif ft == ".pdf":
            data["rendered"] = None
        else:
            data["rendered"] = f"<pre class='txt-view'>（不支持的类型 {html.escape(ft)}）</pre>"
        return jsonify(data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"打开失败：{e}"}), 500


@app.route("/api/documents/<int:did>/preview.html")
def preview_document_html(did):
    """HTML 类型专用：sandbox iframe 内预览，保留样式但禁止脚本/弹窗/跳转/表单"""
    db = get_db()
    row = db.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    if not row or row["file_type"] not in (".html", ".htm"):
        abort(404)
    fpath = UPLOAD_DIR / row["stored_filename"]
    try:
        raw = fpath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"<pre style='color:#933'>read failed: {html.escape(str(e))}</pre>", 500
    cleaned = sanitize_html_for_preview(raw)
    resp = Response(cleaned, mimetype="text/html; charset=utf-8")
    # 严格 sandbox：禁止脚本、表单、弹窗、跳转、同源、top 导航
    resp.headers["Content-Security-Policy"] = (
        "sandbox allow-same-origin; "
        "default-src 'self' data: blob:; "
        "script-src 'none'; "
        "style-src 'self' 'unsafe-inline' data: blob:; "
        "img-src 'self' data: blob:; "
        "frame-src 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'self'; "
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/api/documents/<int:did>", methods=["PUT"])
def update_document(did):
    data = request.get_json(force=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    if not row:
        abort(404)
    title = (data.get("title") or row["title"]).strip()
    folder_id = data.get("folder_id", row["folder_id"])
    if folder_id == "":
        folder_id = None
    tags = data.get("tags")
    render_mode = data.get("render_mode", row["render_mode"] or "sanitize")
    if render_mode not in ("sanitize", "raw"):
        render_mode = "sanitize"
    db.execute(
        "UPDATE documents SET title = ?, folder_id = ?, render_mode = ?, updated_at = ? WHERE id = ?",
        (title, folder_id, render_mode, now(), did),
    )
    if tags is not None:
        set_doc_tags(db, did, [t.strip() for t in tags if t.strip()])
    update_fts(db, did, title, row["content_text"])
    db.commit()
    new = db.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    return jsonify(doc_to_dict(db, new))


@app.route("/api/documents/<int:did>", methods=["DELETE"])
def delete_document(did):
    db = get_db()
    row = db.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    if not row:
        abort(404)
    fpath = UPLOAD_DIR / row["stored_filename"]
    try:
        fpath.unlink(missing_ok=True)
    except Exception:
        pass
    db.execute("DELETE FROM documents WHERE id = ?", (did,))
    db.execute("DELETE FROM documents_fts WHERE document_id = ?", (did,))
    db.commit()
    return jsonify({"ok": True})


# ============ 个人备注 API ============
@app.route("/api/documents/<int:did>/note")
def get_note(did):
    """读取一篇文档的"我的备注"（无则返回空）"""
    db = get_db()
    row = db.execute("SELECT id FROM documents WHERE id = ?", (did,)).fetchone()
    if not row:
        abort(404)
    note = db.execute(
        "SELECT note_content, updated_at FROM document_notes WHERE document_id = ?",
        (did,),
    ).fetchone()
    if not note:
        return jsonify({"content": "", "updated_at": None})
    return jsonify({"content": note["note_content"] or "", "updated_at": note["updated_at"]})


@app.route("/api/documents/<int:did>/note", methods=["PUT"])
def save_note(did):
    """保存一篇文档的"我的备注"（覆盖式）"""
    data = request.get_json(force=True) or {}
    content = data.get("content") or ""
    if isinstance(content, str):
        # 限制单条备注体积，避免恶意撑爆数据库
        content = content[:100000]
    else:
        content = ""
    db = get_db()
    row = db.execute("SELECT id FROM documents WHERE id = ?", (did,)).fetchone()
    if not row:
        abort(404)
    ts = now()
    db.execute(
        """INSERT INTO document_notes (document_id, note_content, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(document_id) DO UPDATE SET
             note_content = excluded.note_content,
             updated_at = excluded.updated_at""",
        (did, content, ts),
    )
    db.commit()
    return jsonify({"ok": True, "content": content, "updated_at": ts})


@app.route("/api/documents/<int:did>/content", methods=["PUT"])
def update_document_content(did):
    """写回 txt/md 的正文内容（源文件 + 全文索引同步写）"""
    db = get_db()
    row = db.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    if not row:
        abort(404)
    if row["file_type"] not in (".txt", ".md", ".markdown"):
        return jsonify({"error": "仅支持编辑 .txt / .md / .markdown 文档内容"}), 400
    data = request.get_json(force=True) or {}
    content = data.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "content 必须是字符串"}), 400
    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        return jsonify({"error": "内容过大（限 5MB）"}), 413

    fpath = UPLOAD_DIR / row["stored_filename"]
    # 原子写回：先写临时文件再 rename，避免中途断写把源文件写空
    tmp = UPLOAD_DIR / f".{row['stored_filename']}.tmp"
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(fpath)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return jsonify({"error": f"写入失败：{e}"}), 500

    ts = now()
    db.execute(
        "UPDATE documents SET content_text = ?, updated_at = ? WHERE id = ?",
        (content, ts, did),
    )
    update_fts(db, did, row["title"], content)
    db.commit()

    # 返回新的渲染结果，前端直接刷新预览
    ft = row["file_type"]
    if ft in (".md", ".markdown"):
        rendered = sanitize_html(render_markdown(content))
    elif ft == ".txt":
        rendered = f"<pre class='txt-view'>{html.escape(content)}</pre>"
    else:
        rendered = None
    return jsonify({"ok": True, "updated_at": ts, "rendered": rendered})


# ============ 搜索 API ============
@app.route("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": [], "note": "空查询"})
    db = get_db()
    # 1) FTS5 主检索：token 间为 AND（不强制短语连续），中文按单字命中，
    #    彻底取代旧版带引号的短语模式（对中文按单字分词的 unicode61 几乎永远匹配不上）
    fts_ids = set()
    fts_expr = _fts_query(q)
    if fts_expr:
        try:
            rows = db.execute(
                "SELECT document_id FROM documents_fts WHERE documents_fts MATCH ? ORDER BY rank",
                (fts_expr,),
            ).fetchall()
            fts_ids = {r["document_id"] for r in rows}
        except Exception:
            pass
    # 2) LIKE 兜底补充召回（标题 + 内容）
    like_rows = db.execute(
        """SELECT id FROM documents
           WHERE title LIKE ? OR content_text LIKE ?""",
        (f"%{q}%", f"%{q}%"),
    ).fetchall()
    all_ids = fts_ids | {r["id"] for r in like_rows}
    # 3) 标签匹配（标签名存在）
    tag_rows = db.execute(
        """SELECT DISTINCT dt.document_id AS id
           FROM document_tags dt JOIN tags t ON dt.tag_id = t.id
           WHERE t.name LIKE ?""",
        (f"%{q}%",),
    ).fetchall()
    all_ids |= {r["id"] for r in tag_rows}
    if not all_ids:
        return jsonify({"results": []})
    placeholders = ",".join("?" for _ in all_ids)
    docs = db.execute(
        f"SELECT * FROM documents WHERE id IN ({placeholders}) ORDER BY title COLLATE NOCASE",
        tuple(all_ids),
    ).fetchall()
    return jsonify({"results": [doc_to_dict(db, d) for d in docs], "total": len(docs)})


# ============ 标签 API ============
@app.route("/api/tags")
def list_tags():
    db = get_db()
    rows = db.execute(
        """SELECT t.id, t.name, COUNT(dt.document_id) AS count
           FROM tags t LEFT JOIN document_tags dt ON t.id = dt.tag_id
           GROUP BY t.id ORDER BY t.name COLLATE NOCASE"""
    ).fetchall()
    return jsonify({"tags": [dict(r) for r in rows]})


@app.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def delete_tag(tag_id):
    """删除标签：tags 行删除后，document_tags 通过外键级联移除所有关联"""
    db = get_db()
    db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    db.commit()
    return jsonify({"ok": True})


# ============ 文件服务（供 PDF 预览用） ============
@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    # 仅允许文件名，防目录穿越
    safe = secure_filename(filename)
    return send_from_directory(str(UPLOAD_DIR), safe)


# ============ 备份导出 ============
@app.route("/api/export")
def export_backup():
    """zip 打包 notes.db + uploads/，随机器迁移"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if DB_PATH.exists():
            zf.write(DB_PATH, "notes.db")
        for fp in UPLOAD_DIR.iterdir():
            if fp.is_file():
                zf.write(fp, f"uploads/{fp.name}")
    buf.seek(0)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="notes_backup_{ts}.zip"'},
    )


# ============ 导入备份（可选，便于迁移恢复） ============
@app.route("/api/import", methods=["POST"])
def import_backup():
    if "file" not in request.files:
        return jsonify({"error": "缺少 zip 文件"}), 400
    f = request.files["file"]
    buf = io.BytesIO(f.read())
    db = get_db()
    db.close()
    # 关闭后替换
    conn = sqlite3.connect(DB_PATH)
    with zipfile.ZipFile(buf) as zf:
        for name in zf.namelist():
            if name == "notes.db":
                with zf.open(name) as src, open(DB_PATH, "wb") as dst:
                    dst.write(src.read())
            elif name.startswith("uploads/") and not name.endswith("/"):
                target = UPLOAD_DIR / Path(name).name
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
    conn.close()
    return jsonify({"ok": True})


# ============ 初始化样例（首次启动可选） ============
def seed_samples():
    """若库为空且 sample_files 存在，自动导入样例"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cnt = conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
    if cnt > 0 or not SAMPLE_DIR.exists():
        conn.close()
        return
    # 建一个根目录
    cur = conn.execute(
        "INSERT INTO folders (name, parent_id, created_at) VALUES (?, ?, ?)",
        ("样例目录", None, now()),
    )
    fid = cur.lastrowid
    for fp in SAMPLE_DIR.iterdir():
        if not fp.is_file():
            continue
        ext = fp.suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue
        stored = make_stored_filename(fp.name)
        shutil.copy2(fp, UPLOAD_DIR / stored)
        content_text = extract_text(UPLOAD_DIR / stored, ext)
        title = fp.stem
        cur = conn.execute(
            """INSERT INTO documents
               (title, original_filename, stored_filename, file_type, folder_id, content_text, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, fp.name, stored, ext, fid, content_text, now(), now()),
        )
        doc_id = cur.lastrowid
        update_fts(conn, doc_id, title, content_text)
    conn.commit()
    conn.close()


# ============ 启动 ============
if __name__ == "__main__":
    # 命令行参数：--lan 允许局域网其它设备（如安卓手机）访问
    LAN_MODE = "--lan" in sys.argv
    host = "0.0.0.0" if LAN_MODE else "127.0.0.1"

    init_db()
    seed_samples()
    print("=" * 54)
    print("本地学习笔记站已启动")
    print("访问地址: http://127.0.0.1:8848")
    if LAN_MODE:
        print("局域网模式已开启，同一 WiFi 下的设备（如安卓手机）可访问：")
        print(f"  → {lan_display_url()}")
        print("⚠ 安全提示：局域网为明文 HTTP，请在家庭等可信网络使用，")
        print("  避免在公共 WiFi 中开启本模式；笔记数据仍仅存本机，不上传。")
    print("数据目录:", BASE_DIR)
    print("=" * 54)
    # 默认仅监听本地；--lan 时监听 0.0.0.0（防火墙/路由器需放行 8848 端口）
    app.run(host=host, port=LAN_PORT, debug=False)
