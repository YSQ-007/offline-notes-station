#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地离线学习笔记站 · tkinter 桌面版
- 左侧目录树/文档列表、顶部搜索、右侧阅读区
- 阅读区用 tkinterweb.HtmlFrame 渲染（只渲染 HTML/CSS、不执行 JS，天然防脚本）
- 未安装 tkinterweb 时自动降级为 tkinter.Text 平文本显示
- PDF / 原始模式 SPA html 通过"用系统浏览器打开"查看
"""
import os
import sys
import re
import io
import sqlite3
import secrets
import html
import datetime
import shutil
import subprocess
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import bleach
import markdown as markdown_lib

# ---- 数据/路径（与 Flask 版共用同一套持久化） ----
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "notes.db"
SAMPLE_DIR = BASE_DIR / "sample_files"
ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".html", ".htm", ".pdf"}
UPLOAD_DIR.mkdir(exist_ok=True)

# markdown 渲染用（无 flask 依赖的独立实现）
_ALLOWED_TAGS = [
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "strong", "em", "b", "i", "u", "s", "del",
    "a", "code", "pre", "blockquote", "table", "thead", "tbody",
    "tr", "th", "td", "img", "div", "span", "dl", "dt", "dd",
]
_ALLOWED_ATTRS = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
    "code": ["class"],
    "pre": ["class"],
    "div": ["class"],
    "span": ["class"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto", "data"]


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
        title, content_text, document_id UNINDEXED,
        tokenize = "unicode61 remove_diacritics 2"
    );
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
    if "render_mode" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN render_mode TEXT DEFAULT 'sanitize'")
    conn.commit()
    conn.close()


def make_stored_filename(filename):
    """随机前缀防重名 + 清理路径符号，防目录穿越"""
    base = Path(filename).name
    base = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", base)
    base = base.strip("._")
    if not base:
        base = "untitled"
    ext = Path(filename).suffix.lower()
    if ext and base.lower().endswith(ext):
        base = base[: -len(ext)]
    return f"{secrets.token_hex(8)}_{base}{ext}"


def extract_text(filepath, file_type):
    try:
        if file_type in (".txt", ".md", ".markdown"):
            return Path(filepath).read_text(encoding="utf-8", errors="replace")
        if file_type in (".html", ".htm"):
            return bleach.clean(
                Path(filepath).read_text(encoding="utf-8", errors="replace"),
                tags=[], strip=True)
    except Exception:
        return ""
    return ""


def render_markdown(text):
    md = markdown_lib.Markdown(extensions=[
        "extra", "codehilite", "tables", "fenced_code", "toc", "sane_lists"])
    return md.convert(text)


def sanitize_html(raw_html):
    return bleach.clean(
        raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS, strip=True, strip_comments=True)


def set_doc_tags(db, doc_id, tag_names):
    db.execute("DELETE FROM document_tags WHERE document_id=?", (doc_id,))
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        row = db.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row:
            db.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?,?)",
                (doc_id, row["id"]))


def update_fts(db, doc_id, title, content_text):
    db.execute("DELETE FROM documents_fts WHERE document_id=?", (doc_id,))
    db.execute(
        "INSERT INTO documents_fts (title, content_text, document_id) VALUES (?,?,?)",
        (title, content_text or "", doc_id))


def seed_samples():
    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    if cnt > 0:
        conn.close()
        return
    cur = conn.execute(
        "INSERT INTO folders (name, parent_id, created_at) VALUES (?,?,?)",
        ("样例资料", None, now()))
    fid = cur.lastrowid
    if SAMPLE_DIR.exists():
        for fp in SAMPLE_DIR.iterdir():
            if not fp.is_file():
                continue
            ext = fp.suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            stored = make_stored_filename(fp.name)
            shutil.copy2(fp, UPLOAD_DIR / stored)
            content_text = extract_text(UPLOAD_DIR / stored, ext)
            conn.execute(
                """INSERT INTO documents
                   (title, original_filename, stored_filename, file_type, folder_id,
                    content_text, render_mode, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (fp.stem, fp.name, stored, ext, fid, content_text, "sanitize",
                 now(), now()))
    conn.commit()
    conn.close()


try:
    import tkinterweb
    HAVE_TKINTERWEB = True
except Exception:
    HAVE_TKINTERWEB = False


def open_in_system(path):
    """用系统默认程序打开（PDF 阅读器 / 浏览器）"""
    p = str(path)
    if sys.platform.startswith("win"):
        os.startfile(p)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


class DesktopApp:
    def __init__(self, root):
        self.root = root
        root.title("本地离线学习笔记站")
        root.geometry("1100x680")
        root.minsize(820, 520)

        init_db()
        seed_samples()

        self.current_doc_id = None
        self._build_ui()
        self._load_tags_box()
        self.refresh_tree()

    # ---------- UI ----------
    def _build_ui(self):
        root = self.root

        # 顶栏：搜索 + 新建目录 + 上传 + 新建笔记 + 用系统打开
        top = ttk.Frame(root, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="搜索:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(top, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="left", padx=(2, 6))
        self.search_entry.bind("<Return>", lambda e: self.do_search())
        ttk.Button(top, text="检索", command=self.do_search).pack(side="left", padx=(0, 12))

        ttk.Button(top, text="＋目录", command=self.add_folder).pack(side="left")
        ttk.Button(top, text="上传", command=self.upload_doc).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="＋笔记", command=self.add_note).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="编辑", command=lambda: self.edit_current()).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="删除", command=lambda: self.delete_current()).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="系统打开", command=lambda: self.open_current_system()).pack(side="left", padx=(4, 0))

        # 标签过滤
        self.tag_var = tk.StringVar(value="")
        self.tag_combo = ttk.Combobox(top, textvariable=self.tag_var, state="readonly", width=14)
        self.tag_combo.pack(side="right")
        self.tag_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_tree())
        ttk.Label(top, text="标签:").pack(side="right", padx=(0, 4))

        # 主体：左树 + 右阅读区
        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        left = ttk.Frame(body)
        self.tree = ttk.Treeview(left, selectmode="browse")
        ys = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        body.add(left, weight=1)

        right = ttk.Frame(body)
        self.reader = self._make_reader(right)
        self.reader.pack(fill="both", expand=True)
        body.add(right, weight=3)

        # 状态栏
        self.status = tk.StringVar(value="就绪")
        ttk.Label(root, textvariable=self.status, anchor="w", relief="sunken").pack(
            fill="x", side="bottom")

    def _make_reader(self, parent):
        if HAVE_TKINTERWEB:
            from tkinterweb import HtmlFrame
            return HtmlFrame(parent)
        # 降级：平文本显示
        txt = tk.Text(parent, wrap="word", state="disabled")
        txt.pack(fill="both", expand=True)
        return txt

    def _set_reader_html(self, html_content):
        if HAVE_TKINTERWEB:
            self.reader.load_html(html_content or "<p style='color:#888'>（无内容）</p>")
        else:
            self.reader.config(state="normal")
            self.reader.delete("1.0", "end")
            self.reader.insert("1.0", bleach.clean(html_content or "", tags=[], strip=True))
            self.reader.config(state="disabled")

    def _load_tags_box(self):
        try:
            rows = get_db().execute(
                "SELECT name FROM tags ORDER BY name COLLATE NOCASE").fetchall()
            vals = ["（全部）"] + [r["name"] for r in rows]
        except Exception:
            vals = ["（全部）"]
        self.tag_var.set("（全部）")
        self.tag_combo["values"] = vals

    # ---------- 目录树 ----------
    def refresh_tree(self):
        self._load_tags_box()
        self.tree.delete(*self.tree.get_children())
        db = get_db()
        tag_filter = self.tag_var.get()
        tag_id = None
        if tag_filter and tag_filter != "（全部）":
            trow = db.execute("SELECT id FROM tags WHERE name=?", (tag_filter,)).fetchone()
            tag_id = trow["id"] if trow else -1

        folders = db.execute("SELECT * FROM folders ORDER BY name COLLATE NOCASE").fetchall()
        docs = db.execute("SELECT * FROM documents ORDER BY title COLLATE NOCASE").fetchall()
        # 只看目录的文档树（不按标签过滤目录）
        self.folder_nodes = {}
        for f in folders:
            nid = self.tree.insert(self.folder_nodes.get(f["parent_id"], ""), "end",
                                   text=f"📁 {f['name']}", open=True)
            self.folder_nodes[f["id"]] = nid
        for d in docs:
            if tag_id is not None:
                hit = db.execute(
                    "SELECT 1 FROM document_tags WHERE document_id=? AND tag_id=?",
                    (d["id"], tag_id)).fetchone()
                if not hit:
                    continue
            pid = self.folder_nodes.get(d["folder_id"], "")
            self.tree.insert(
                pid, "end", iid=f"doc-{d['id']}",
                text=self._doc_label(d), values=(d["id"],))

    def _doc_label(self, d):
        icon = {"md": "📄", "txt": "📝", "html": "🌐", "pdf": "📕"}.get(d["file_type"].lstrip("."), "📄")
        return f"{icon} {d['title']}"

    def on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("doc-"):
            try:
                did = int(self.tree.item(iid, "values")[0])
            except Exception:
                return
            self.open_doc(did)

    # ---------- 文档读取 ----------
    def open_doc(self, did):
        db = get_db()
        row = db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
        if not row:
            return
        self.current_doc_id = did
        ft = row["file_type"]
        fpath = UPLOAD_DIR / row["stored_filename"]
        tags = db.execute(
            "SELECT t.name FROM tags t JOIN document_tags dt ON t.id=dt.tag_id "
            "WHERE dt.document_id=? ORDER BY t.name", (did,)).fetchall()
        tag_str = ", ".join(t["name"] for t in tags)
        self.status.set(f"{row['title']}  {tag_str}")

        if not fpath.exists():
            self._set_reader_html("<p style='color:#c00'>文件缺失</p>")
            return
        if ft in (".md", ".markdown"):
            raw = fpath.read_text(encoding="utf-8", errors="replace")
            self._set_reader_html(render_markdown(raw))
        elif ft == ".txt":
            raw = fpath.read_text(encoding="utf-8", errors="replace")
            self._set_reader_html(
                "<pre style='white-space:pre-wrap;font-family:monospace'>"
                + html.escape(raw) + "</pre>")
        elif ft in (".html", ".htm"):
            if row["render_mode"] == "raw":
                self._set_reader_html(
                    f"<p style='color:#996'>该文档为原始 HTML。SPA/脚本页面请在系统浏览器中查看："
                    f"点击右上「系统打开」。</p>"
                    + (fpath.read_text(encoding="utf-8", errors="replace")
                       if self._is_plain_enough(fpath) else ""))
            else:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
                self._set_reader_html(sanitize_html(raw))
        elif ft == ".pdf":
            self._set_reader_html(
                "<p style='color:#886'>PDF 文档请在系统浏览器/阅读器中打开。<br>"
                "点击右上角「系统打开」查看。</p>")

    def _is_plain_enough(self, fpath):
        """粗略判断 HTML 是否是纯静态文档（不含成对脚本/大量 JS）"""
        try:
            raw = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        # tkinterweb 不执行 JS，直接嵌入小文件比丢空提示更友好
        return len(raw) < 300 * 1024

    def open_current_system(self):
        if not self.current_doc_id:
            messagebox.showinfo("提示", "请先在左侧选择一篇文档")
            return
        db = get_db()
        row = db.execute("SELECT * FROM documents WHERE id=?", (self.current_doc_id,)).fetchone()
        if row:
            open_in_system(UPLOAD_DIR / row["stored_filename"])

    # ---------- 搜索 ----------
    def do_search(self):
        q = self.search_var.get().strip()
        if not q:
            self.refresh_tree()
            return
        db = get_db()
        like = db.execute(
            "SELECT id FROM documents WHERE title LIKE ? OR content_text LIKE ?",
            (f"%{q}%", f"%{q}%")).fetchall()
        self.tree.delete(*self.tree.get_children())
        for r in like:
            d = db.execute("SELECT * FROM documents WHERE id=?", (r["id"],)).fetchone()
            if d:
                self.tree.insert("", "end", iid=f"doc-{d['id']}",
                                 text=self._doc_label(d), values=(d["id"],))
        self.status.set(f"检索「{q}」：找到 {len(like)} 篇")

    # ---------- 目录 ----------
    def add_folder(self):
        parent_id = self._selected_folder_id()
        name = simpledialog.askstring("新建目录", "目录名称：", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        db = get_db()
        db.execute("INSERT INTO folders (name, parent_id, created_at) VALUES (?,?,?)",
                   (name, parent_id, now()))
        db.commit()
        self.refresh_tree()

    def _selected_folder_id(self):
        sel = self.tree.selection()
        if sel and not sel[0].startswith("doc-"):
            for fid, nid in self.folder_nodes.items():
                if nid == sel[0]:
                    return fid
        return None

    # ---------- 上传 ----------
    def upload_doc(self):
        path = filedialog.askopenfilename(
            title="选择要上传的资料",
            filetypes=[("资料文件", "*.txt *.md *.markdown *.html *.htm *.pdf"), ("所有文件", "*.*")])
        if not path:
            return
        src = Path(path)
        if src.suffix.lower() not in ALLOWED_EXTENSIONS:
            messagebox.showwarning("不支持", "仅支持 .txt / .md / .html / .pdf")
            return
        title = simpledialog.askstring(
            "上传资料", "标题（留空用文件名，HTML 可下方设渲染模式）：",
            initialvalue=src.stem, parent=self.root)
        if title is None:
            return
        title = title.strip() or src.stem
        render_mode = "sanitize"
        if src.suffix.lower() in (".html", ".htm"):
            if messagebox.askyesno(
                    "渲染模式", "该 HTML 是否完整显示 SPA/脚本页面？\n是=原始模式(信任文件) 否=安全消毒模式"):
                render_mode = "raw"
        tag_input = simpledialog.askstring("标签", "标签（逗号分隔，可空）：", parent=self.root)
        tags = [t.strip() for t in (tag_input or "").split(",") if t.strip()]

        stored = make_stored_filename(src.name)
        dst = UPLOAD_DIR / stored
        try:
            import shutil
            shutil.copy2(src, dst)
        except Exception as e:
            messagebox.showerror("失败", f"复制失败：{e}")
            return
        content_text = extract_text(dst, src.suffix.lower())
        db = get_db()
        cur = db.execute(
            """INSERT INTO documents
               (title, original_filename, stored_filename, file_type, folder_id,
                content_text, render_mode, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (title, src.name, stored, src.suffix.lower(), None, content_text,
             render_mode, now(), now()))
        set_doc_tags(db, cur.lastrowid, tags)
        update_fts(db, cur.lastrowid, title, content_text)
        db.commit()
        self.refresh_tree()
        self.status.set(f"已上传：{title}")

    # ---------- 新建笔记 ----------
    def add_note(self):
        win = tk.Toplevel(self.root)
        win.title("新建笔记")
        win.geometry("520x420")
        win.transient(self.root)
        ttk.Label(win, text="标题：").pack(anchor="w", padx=10, pady=(10, 2))
        title_var = tk.StringVar()
        ttk.Entry(win, textvariable=title_var).pack(fill="x", padx=10)
        ttk.Label(win, text="格式：").pack(anchor="w", padx=10, pady=(8, 2))
        fmt_var = tk.StringVar(value=".md")
        ttk.Combobox(win, textvariable=fmt_var, state="readonly",
                     values=[".md", ".txt"]).pack(anchor="w", padx=10)
        ttk.Label(win, text="标签（逗号分隔）：").pack(anchor="w", padx=10, pady=(8, 2))
        tag_var = tk.StringVar()
        ttk.Entry(win, textvariable=tag_var).pack(fill="x", padx=10)
        ttk.Label(win, text="内容（支持 Markdown 语法）：").pack(anchor="w", padx=10, pady=(8, 2))
        txt = tk.Text(win, wrap="word", height=10)
        txt.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        def save():
            t = title_var.get().strip()
            if not t:
                messagebox.showwarning("提示", "标题不能为空", parent=win)
                return
            fmt = fmt_var.get()
            content = txt.get("1.0", "end")
            tags = [x.strip() for x in tag_var.get().split(",") if x.strip()]
            stored = make_stored_filename(t + fmt)
            fpath = UPLOAD_DIR / stored
            fpath.write_text(content, encoding="utf-8")
            db = get_db()
            cur = db.execute(
                """INSERT INTO documents
                   (title, original_filename, stored_filename, file_type, folder_id,
                    content_text, render_mode, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (t, t + fmt, stored, fmt, None, content, "sanitize",
                 now(), now()))
            set_doc_tags(db, cur.lastrowid, tags)
            update_fts(db, cur.lastrowid, t, content)
            db.commit()
            self.refresh_tree()
            win.destroy()
            self.status.set(f"已新建：{t}")

        ttk.Button(win, text="保存", command=save).pack(pady=(0, 10))

    # ---------- 编辑（改标题/标签/渲染模式） ----------
    def edit_current(self):
        if not self.current_doc_id:
            messagebox.showinfo("提示", "请先选择文档")
            return
        db = get_db()
        row = db.execute("SELECT * FROM documents WHERE id=?", (self.current_doc_id,)).fetchone()
        if not row:
            return
        title = simpledialog.askstring("编辑标题", "标题：", initialvalue=row["title"],
                                       parent=self.root)
        if title is None:
            return
        title = title.strip() or row["title"]
        tag_rows = db.execute(
            "SELECT t.name FROM tags t JOIN document_tags dt ON t.id=dt.tag_id "
            "WHERE dt.document_id=?", (self.current_doc_id,)).fetchall()
        cur_tags = ", ".join(t["name"] for t in tag_rows)
        tag_input = simpledialog.askstring("编辑标签", "标签（逗号分隔）：",
                                           initialvalue=cur_tags, parent=self.root)
        if tag_input is None:
            tag_input = cur_tags
        tags = [t.strip() for t in tag_input.split(",") if t.strip()]

        render_mode = row["render_mode"] or "sanitize"
        if row["file_type"] in (".html", ".htm"):
            if messagebox.askyesno(
                    "渲染模式",
                    f"当前：{'原始' if render_mode=='raw' else '消毒'}模式\n"
                    "切换为完整显示(原始)模式？\n是=原始 否=消毒"):
                render_mode = "raw"
            else:
                render_mode = "sanitize"

        db.execute(
            "UPDATE documents SET title=?, render_mode=?, updated_at=? WHERE id=?",
            (title, render_mode, now(), self.current_doc_id))
        set_doc_tags(db, self.current_doc_id, tags)
        update_fts(db, self.current_doc_id, title, row["content_text"])
        db.commit()
        self.refresh_tree()
        self.open_doc(self.current_doc_id)

    # ---------- 删除 ----------
    def delete_current(self):
        if not self.current_doc_id:
            messagebox.showinfo("提示", "请先选择文档")
            return
        db = get_db()
        row = db.execute("SELECT * FROM documents WHERE id=?", (self.current_doc_id,)).fetchone()
        if not row:
            return
        if not messagebox.askyesno("确认删除", f"删除「{row['title']}」？不可恢复。"):
            return
        fpath = UPLOAD_DIR / row["stored_filename"]
        try:
            fpath.unlink(missing_ok=True)
        except Exception:
            pass
        db.execute("DELETE FROM documents WHERE id=?", (self.current_doc_id,))
        db.execute("DELETE FROM documents_fts WHERE document_id=?", (self.current_doc_id,))
        db.commit()
        self.current_doc_id = None
        self.refresh_tree()
        self._set_reader_html("<p style='color:#888'>（已删除，选择其它文档阅读）</p>")
        self.status.set("已删除")


def main():
    root = tk.Tk()
    if not HAVE_TKINTERWEB:
        messagebox.showwarning(
            "提示",
            "未安装 tkinterweb，阅读区将用纯文本显示（无格式/无 PDF 内嵌）。\n"
            "离线安装：python -m pip install tkinterweb")
    DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()