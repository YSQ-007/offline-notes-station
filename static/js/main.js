/* 本地学习笔记站 · 前端交互 */
const API = "/api";
let treeData = { folders: [], documents: [] };
let currentDocId = null;

// ---------- 工具 ----------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const esc = (s) => String(s || "").replace(/[&<>"']/g, c => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

async function api(path, opts = {}) {
  const res = await fetch(API + path, opts);
  if (res.status === 401) {
    // 未登录或需初始化密码：跳登录页
    try {
      const b = await res.clone().json();
      if (b.need_setup || b.code === "UNAUTHORIZED") {
        location.href = "/login";
        throw new Error("请先登录");
      }
    } catch (e) {
      if (e.message === "请先登录") throw e;
    }
  }
  if (!res.ok) {
    // 尽量把后端写回的 error 字段取出来
    let msg = null;
    try {
      const body = await res.text();
      try { msg = JSON.parse(body).error; } catch (_) { msg = body; }
    } catch (_) {}
    msg = msg || `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2200);
}

function fileIcon(ft) {
  return { ".md": "📝", ".markdown": "📝", ".txt": "📄", ".html": "🌐", ".htm": "🌐", ".pdf": "📕" }[ft] || "📄";
}

// ---------- 目录树构建 ----------
function buildTree() {
  const root = $("#tree");
  root.innerHTML = "";

  // 未分类文档
  const unfiled = treeData.documents.filter(d => !d.folder_id);
  const ul = document.createElement("ul");
  if (unfiled.length) {
    const li = makeFolderNode({ id: 0, name: "未分类" }, true);
    ul.appendChild(li);
  }
  // 根目录
  const roots = treeData.folders.filter(f => !f.parent_id);
  roots.forEach(f => ul.appendChild(makeFolderNode(f, false)));
  // 未分类挂在最后
  // 已在上面处理
  root.appendChild(ul);
}

function makeFolderNode(folder, isVirtual) {
  const li = document.createElement("li");
  const children = treeData.folders.filter(f => f.parent_id === folder.id);
  // 虚拟「未分类」(id=0) 收纳所有 folder_id 为空或 0 的文档；
  // 空值(null/undefined/0) 与 id=0 严格 === 不相等，必须放宽条件，否则未分类目录永远显示为空
  const docs = treeData.documents.filter(d =>
    isVirtual ? (!d.folder_id || d.folder_id === 0) : d.folder_id === folder.id
  );

  const node = document.createElement("div");
  node.className = "node folder" + (isVirtual ? " virtual" : "");
  node.innerHTML = `
    <span class="caret">${children.length || docs.length ? "▶" : ""}</span>
    <span class="icon">📁</span>
    <span class="label">${esc(folder.name)}</span>
    ${isVirtual ? "" : `<span class="node-ops">
      <button title="新建子目录" data-act="subfolder">＋</button>
      <button title="重命名" data-act="rename">✎</button>
      <button title="删除" data-act="delfolder">🗑</button>
    </span>`}
  `;
  li.appendChild(node);

  // 折叠
  const childUl = document.createElement("ul");
  childUl.style.display = "none";
  li.appendChild(childUl);

  node.querySelector(".caret").addEventListener("click", (e) => {
    e.stopPropagation();
    const open = childUl.style.display === "none";
    childUl.style.display = open ? "block" : "none";
    node.querySelector(".caret").textContent = open ? "▼" : "▶";
  });
  if (!isVirtual) {
    node.addEventListener("click", () => {});
    node.querySelectorAll(".node-ops button").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const act = btn.dataset.act;
        if (act === "subfolder") openFolderModal(folder.id);
        if (act === "rename") {
          const name = prompt("新目录名：", folder.name);
          if (name && name.trim()) renameFolder(folder.id, name.trim(), folder.parent_id);
        }
        if (act === "delfolder") {
          if (confirm(`删除目录「${folder.name}」？其下资料会变成未分类。`)) deleteFolder(folder.id);
        }
      });
    });
  }

  children.forEach(c => childUl.appendChild(makeFolderNode(c, false)));
  docs.forEach(d => childUl.appendChild(makeDocNode(d)));
  return li;
}

function makeDocNode(doc) {
  const li = document.createElement("li");
  const node = document.createElement("div");
  node.className = "node doc";
  node.dataset.did = doc.id;
  node.innerHTML = `
    <span class="caret"></span>
    <span class="icon">${fileIcon(doc.file_type)}</span>
    <span class="label">${esc(doc.title)}</span>
  `;
  node.addEventListener("click", () => openDoc(doc.id));
  li.appendChild(node);
  return li;
}

// ---------- 加载树 ----------
async function loadTree() {
  try {
    treeData = await api("/tree");
    buildTree();
    refreshFolderSelects();
  } catch (e) {
    toast("加载目录失败：" + e.message);
  }
}

async function loadTags() {
  try {
    const { tags } = await api("/tags");
    const cloud = $("#tagCloud");
    cloud.innerHTML = tags.length
      ? tags.map(t =>
          `<span class="tag-chip" data-tag="${esc(t.name)}" data-id="${t.id}">` +
            `${esc(t.name)} (${t.count})<span class="chip-x" data-del="${t.id}" title="删除标签">×</span>` +
          `</span>`).join(" ")
      : '<span class="tag-empty">暂无标签</span>';
    cloud.querySelectorAll(".tag-chip[data-tag]").forEach(c => {
      c.addEventListener("click", () => {
        $("#searchInput").value = c.dataset.tag;
        doSearch();
      });
    });
    // 删除标签：tags 表删除后关联自动级联清除
    cloud.querySelectorAll(".chip-x[data-del]").forEach(x => {
      x.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = +x.dataset.del;
        const name = x.closest(".tag-chip").dataset.tag;
        if (!confirm(`删除标签「${name}」？将同时移除该标签与所有文档的关联。`)) return;
        try {
          await api(`/tags/${id}`, { method: "DELETE" });
          toast("标签已删除");
          loadTags();
          loadTree();
        } catch (err) { toast(err.message); }
      });
    });
  } catch (e) {}
}

// ---------- 标签 chips 编辑器（弹窗表单通用） ----------
const tagEditors = {
  upload: { chips: "#chipsUpload", hidden: "#uploadTags", input: "#tagInputUpload" },
  edit:   { chips: "#chipsEdit",   hidden: "#editTags",   input: "#tagInputEdit" },
  note:   { chips: "#chipsNote",   hidden: "#noteTags",   input: "#tagInputNote" },
};
function initTagEditor(key, initialNames = []) {
  const cfg = tagEditors[key];
  const chipsEl = $(cfg.chips), hiddenEl = $(cfg.hidden), inputEl = $(cfg.input);
  const getNames = () => [...chipsEl.querySelectorAll(".tag-chip")].map(c => c.dataset.name);
  const sync = () => { hiddenEl.value = getNames().join(", "); };
  const add = (raw) => {
    raw.split(/[,，、]+/).map(s => s.trim()).filter(Boolean).forEach(n => {
      if (!getNames().includes(n)) {
        const span = document.createElement("span");
        span.className = "tag-chip";
        span.dataset.name = n;
        span.innerHTML = `${esc(n)}<span class="chip-x" title="移除标签">×</span>`;
        span.querySelector(".chip-x").onclick = (ev) => { ev.stopPropagation(); span.remove(); sync(); };
        chipsEl.appendChild(span);
      }
    });
    sync();
  };
  // 已初始化过则只重置内容（避免重复绑定事件）
  if (cfg.bound) { chipsEl.innerHTML = ""; initialNames.forEach(add); return; }
  cfg.bound = true;
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(inputEl.value); inputEl.value = ""; }
  });
  inputEl.addEventListener("blur", () => {
    if (inputEl.value.trim()) { add(inputEl.value); inputEl.value = ""; }
  });
  const wrap = chipsEl.closest(".tag-editor");
  if (wrap) wrap.addEventListener("click", (e) => {
    if (!e.target.closest(".chip-x") && e.target !== inputEl) inputEl.focus();
  });
  initialNames.forEach(add);
}

function refreshFolderSelects() {
  const opts = ['<option value="">（未分类 / 根目录）</option>'];
  function walk(parentId, depth) {
    treeData.folders.filter(f => f.parent_id === parentId || (parentId === null && !f.parent_id))
      .forEach(f => {
        opts.push(`<option value="${f.id}">${"　".repeat(depth)}📁 ${esc(f.name)}</option>`);
        walk(f.id, depth + 1);
      });
  }
  walk(null, 0);
  ["#uploadFolderSel", "#editFolderSel", "#folderParentSel", "#noteFolderSel"].forEach(sel => {
    const cur = $(sel).value;
    $(sel).innerHTML = opts.join("");
    if (cur) $(sel).value = cur;
  });
}

// ---------- 文档打开/渲染 ----------
async function openDoc(did) {
  currentDocId = did;
  $$(".node.doc").forEach(n => n.classList.toggle("active", +n.dataset.did === did));
  try {
    const doc = await api(`/documents/${did}`);
    $("#readerEmpty").style.display = "none";
    $("#readerContent").style.display = "block";
    closeDrawers();   // 移动端：选中文档后收起目录抽屉
    $("#docTitle").textContent = doc.title;
    $("#docType").textContent = doc.file_type;
    $("#docDate").textContent = "更新于 " + (doc.updated_at || "").slice(0, 16);
    $("#docTags").innerHTML = (doc.tags || []).map(t =>
      `<span class="tag-chip" data-ftag="${esc(t.name)}">${esc(t.name)}</span>`
    ).join(" ");
    // 阅读区的标签也可点击按标签筛选
    $$("#docTags .tag-chip").forEach(c => {
      c.addEventListener("click", () => {
        $("#searchInput").value = c.dataset.ftag;
        doSearch();
      });
    });

    const body = $("#docBody");
    // 清空已有的工具栏/舞台按钮（每次打开文档重建）
    $("#readerToolbar")?.remove();
    document.body.classList.remove("docs-responsive", "docs-stage");
    $("#editContentBtn").style.display = "none";
    $("#noteToggleBtn").style.display = "inline-block";
    const reloadOp = () => openDoc(did); // 编辑内容保存后回读
    window.__reloadOp = reloadOp;

    if (doc.file_type === ".pdf") {
      // 本地 pdf.js 优先，否则浏览器原生 PDF 预览
      const pdfjsViewer = "/static/pdfjs/web/viewer.html?file=" + encodeURIComponent("/uploads/" + doc.stored_filename);
      const hasPdfjs = await fetch("/static/pdfjs/web/viewer.html", { method: "HEAD" })
        .then(r => r.ok).catch(() => false);
      const src = hasPdfjs ? pdfjsViewer : "/uploads/" + doc.stored_filename;
      body.innerHTML = `<iframe class="pdf-frame" src="${src}"></iframe>`;
    } else if (doc.file_type === ".html" || doc.file_type === ".htm") {
      // HTML 笔记：本页面 iframe。iterator: render_mode=sanitize走preview；raw直接加载原始文件(SPA完整)
      const src = doc.render_mode === "raw"
        ? "/uploads/" + doc.stored_filename
        : `/api/documents/${doc.id}/preview.html`;
      body.innerHTML =
        `<div class="reader-toolbar" id="readerToolbar">
           <label>页面高度
             <select id="frameHeightSel">
               <option value="72vh">约占窗口</option>
               <option value="1600px">较高(1600)</option>
               <option value="100%">占满阅读区</option>
             </select>
           </label>
           <button id="stageBtn">全屏查看</button>
         </div>
         <iframe class="html-frame" id="htmlFrame" src="${src}"></iframe>`;
      $("#frameHeightSel").addEventListener("change", () => {
        const f = $("#htmlFrame");
        body.style.padding = "0";
        if (f) {
          const v = $("#frameHeightSel").value;
          f.style.height = v === "100%" ? "calc(100vh - 52px)" : v;
          f.style.minHeight = "0";
          document.body.classList.add("docs-responsive");   // 解除 .doc-body 860px 限制，撑满阅读区
        }
      });
      // 全宽/还原
      $("#stageBtn").onclick = (e) => {
        e.stopPropagation();
        const r = $("#reader");
        const staged = document.body.classList.toggle("docs-stage");
        r.classList.toggle("docs-stage");
        $("#stageBtn").textContent = staged ? "退出全屏" : "全屏查看";
      };
      document.body.classList.add("docs-responsive");   // 默认全宽显示，消除右侧大块空白
    } else {
      // txt / md：加字体字号工具条，仅 txt 需要字体选择
      const isTxt = doc.file_type === ".txt";
      const toolbar = document.createElement("div");
      toolbar.className = "reader-toolbar";
      toolbar.id = "readerToolbar";
      toolbar.innerHTML = `
        <label>字号
          <input type="range" min="12" max="24" step="1" id="txtSizeRange">
          <span id="txtSizeLabel"></span>
        </label>
        ${isTxt ? `<label>字体
          <select id="txtFontSel">
            <option value="mono" ${txtFont==='mono'?'selected':''}>等宽 (代码/速查)</option>
            <option value="sans" ${txtFont==='sans'?'selected':''}>无衬线 (正文)</option>
            <option value="serif" ${txtFont==='serif'?'selected':''}>衬线</option>
          </select>
        </label>` : ""}
      `;
      $("#docBody").insertBefore(toolbar, $("#docBody").firstChild);
      const sizeEl = $("#txtSizeRange");
      sizeEl.value = txtSize;
      $("#txtSizeLabel").textContent = txtSize + "px";
      sizeEl.addEventListener("input", () => {
        txtSize = +sizeEl.value;
        localStorage.setItem("txtSize", txtSize);
        document.documentElement.style.setProperty("--txt-size", txtSize + "px");
        $("#txtSizeLabel").textContent = txtSize + "px";
      });
      if (isTxt) $("#txtFontSel").addEventListener("change", e => setTxtFont(e.target.value));

      body.innerHTML = doc.rendered || "（无内容）";
      // markdown 也要支持字号
      document.documentElement.style.setProperty("--txt-size", txtSize + "px");
      $("#editContentBtn").style.display = "inline-block";
    }
    // 编辑/删除绑定
    $("#editDocBtn").onclick = () => openEditModal(doc);
    $("#editContentBtn").onclick = () => enterContentEditor(doc);
    $("#noteToggleBtn").onclick = () => toggleNotePanel(doc);
    $("#delDocBtn").onclick = async () => {
      if (confirm(`删除「${doc.title}」？此操作不可恢复。`)) {
        await api(`/documents/${did}`, { method: "DELETE" });
        toast("已删除");
        currentDocId = null;
        $("#readerEmpty").style.display = "flex";
        $("#readerContent").style.display = "none";
        loadTree();
        loadTags();
      }
    };
  } catch (e) {
    toast("打开失败：" + e.message);
  }
}

// ---------- 上传 ----------
function bindUpload() {
  initTagEditor("upload");
  $("#uploadBtn").onclick = () => { $("#uploadModal").style.display = "flex"; };
  $("#uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api("/upload", { method: "POST", body: fd });
      toast("上传成功");
      $("#uploadModal").style.display = "none";
      e.target.reset();
      loadTree();
      loadTags();
    } catch (e) {
      toast("上传失败：" + e.message);
    }
  });
}

// ---------- 新建笔记 ----------
function bindNote() {
  initTagEditor("note");
  $("#newNoteBtn").onclick = () => {
    $("#noteModal").style.display = "flex";
    $("#noteType").value = ".md";
    $("#noteTitle").focus();
  };
  $("#noteForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = $("#noteTitle").value.trim();
    const content = $("#noteContent").value;
    const file_type = $("#noteType").value;
    const folder_id = $("#noteFolderSel").value || null;
    const tags = $("#noteTags").value.trim();
    if (!title) { toast("标题不能为空"); return; }
    try {
      await api("/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, content, file_type, folder_id, tags }),
      });
      toast("新建成功");
      $("#noteModal").style.display = "none";
      e.target.reset();
      loadTree();
      loadTags();
    } catch (e) {
      toast("新建失败：" + e.message);
    }
  });
}

// ---------- 目录增删改 ----------
function bindFolder() {
  $("#newFolderBtn").onclick = () => openFolderModal(null);
  $("#newFolderBtn2").onclick = () => openFolderModal(null);
  $("#folderForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const name = fd.get("name").trim();
    const pid = fd.get("parent_id") || null;
    if (!name) return;
    try {
      await api("/folders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, parent_id: pid ? +pid : null }),
      });
      toast("目录已创建");
      $("#folderModal").style.display = "none";
      e.target.reset();
      loadTree();
    } catch (e) {
      toast("创建失败：" + e.message);
    }
  });
}

function openFolderModal(parentId) {
  $("#folderModalTitle").textContent = parentId ? "新建子目录" : "新建目录";
  $("#folderParentSel").value = parentId || "";
  $("#folderModal").style.display = "flex";
  $("#folderForm").querySelector('[name="name"]').focus();
}

async function renameFolder(fid, name, parentId) {
  try {
    await api(`/folders/${fid}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parent_id: parentId }),
    });
    toast("已重命名");
    loadTree();
  } catch (e) { toast(e.message); }
}

async function deleteFolder(fid) {
  try {
    await api(`/folders/${fid}`, { method: "DELETE" });
    toast("目录已删除");
    loadTree();
    loadTags();
  } catch (e) { toast(e.message); }
}

// ---------- 编辑文档 ----------
function openEditModal(doc) {
  $("#editDid").value = doc.id;
  $("#editTitle").value = doc.title;
  $("#editFolderSel").value = doc.folder_id || "";
  initTagEditor("edit", (doc.tags || []).map(t => t.name));
  $("#editRenderMode").value = doc.render_mode === "raw" ? "raw" : "sanitize";
  $("#editModal").style.display = "flex";
}

function bindEdit() {
  $("#editForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const did = +$("#editDid").value;
    const title = $("#editTitle").value.trim();
    const folder_id = $("#editFolderSel").value || null;
    const tags = $("#editTags").value.split(",").map(s => s.trim()).filter(Boolean);
    const render_mode = $("#editRenderMode").value;
    try {
      await api(`/documents/${did}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, folder_id: folder_id ? +folder_id : null, tags, render_mode }),
      });
      toast("已保存");
      $("#editModal").style.display = "none";
      loadTree();
      loadTags();
      openDoc(did);
    } catch (e) { toast(e.message); }
  });
}

// ---------- 搜索 ----------
function bindSearch() {
  let timer;
  $("#searchInput").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(doSearch, 300);
  });
  $("#searchBtn").onclick = doSearch;
  $("#searchInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSearch();
  });
}

async function doSearch() {
  const q = $("#searchInput").value.trim();
  if (!q) { loadTree(); return; }
  try {
    const { results } = await api(`/search?q=${encodeURIComponent(q)}`);
    if (!results.length) {
      toast("未找到匹配资料");
      return;
    }
    // 搜索结果统一挂到「未分类」根节点下展示（folder_id 置 0 以匹配
    // 虚拟「未分类」folder.id=0，避免结果被 buildTree 过滤掉而看不到任何变化）
    treeData = { folders: [], documents: results.map(d => ({ ...d, folder_id: 0 })) };
    buildTree();
    // 展开未分类
    const unfiled = $("#tree > ul > li");
    if (unfiled) {
      const caret = unfiled.querySelector(".caret");
      const child = unfiled.querySelector("ul");
      if (caret && child) {
        child.style.display = "block";
        caret.textContent = "▼";
      }
    }
    toast(`找到 ${results.length} 条`);
  } catch (e) { toast(e.message); }
}

// ---------- 退出登录 ----------
function bindLogout() {
  const btn = $("#logoutBtn");
  if (!btn) return;
  btn.onclick = async () => {
    try {
      await api("/logout", { method: "POST" });
    } catch (e) {}
    location.href = "/login";
  };
}

// ---------- 导出 ----------
function bindExport() {
  $("#exportBtn").onclick = () => {
    window.location.href = API + "/export";
  };
}

// ---------- 修改密码与密保（登录态内） ----------
function resolveSecQuestion(selId, customId) {
  let q = $(selId).value;
  if (q === "__custom__") q = ($(customId).value || "").trim();
  return q;
}

function bindSecurity() {
  const secModal = $("#securitySetModal");
  if (!secModal) return;
  // 补录/修改密保弹窗：自定义问题切换
  $("#secSetQuestion").addEventListener("change", () => {
    const show = $("#secSetQuestion").value === "__custom__";
    $("#secSetCustomField").style.display = show ? "block" : "none";
  });
  // 补录/修改密保提交（原密码验证；已有密保时还需旧密保答案）
  $("#secSetSubmit").onclick = async () => {
    const q = resolveSecQuestion("#secSetQuestion", "#secSetQuestionCustom");
    const ans = $("#secSetAnswer").value.trim();
    if (!q) return toast("请选择或填写密保问题");
    if (ans.length < 2 || ans.length > 128) return toast("密保答案长度需 2-128 字符");
    const oldSec = $("#secSetOldSec").value.trim();
    const needOldSec = $("#secSetOldSecField").style.display !== "none";
    if (needOldSec && !oldSec) return toast("请回答旧密保答案");
    try {
      await api("/security/set", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          old_password: $("#secSetOld").value,
          old_security_answer: oldSec,
          security_question: q,
          security_answer: ans,
        }),
      });
      toast("密保已保存");
      secModal.style.display = "none";
      checkSecurityBanner();   // 重新检查引导条
    } catch (e) { toast(e.message); }
  };
  // 补录引导条
  $("#secBannerGo").onclick = () => openSecuritySet();
  $("#secBannerLater").onclick = () => {
    $("#secBanner").style.display = "none";
    localStorage.setItem("sec_later", String(Date.now()));
  };

  // 修改密码弹窗
  $("#modifyPwBtn").onclick = () => {
    $("#pwOld").value = $("#pwNew").value = $("#pwNew2").value = $("#pwSecAnswer").value = "";
    $("#modifyPwModal").style.display = "flex";
    api("/security/status").then(s => {
      if (s.configured) {
        $("#secQShow").textContent = s.question || "";
        $("#pwSecHint").textContent = "";
      } else {
        $("#secQShow").textContent = "（未设置）";
        $("#pwSecHint").textContent = "尚未设置密保，请先补录后再修改密码。";
      }
    }).catch(() => {});
  };
  $("#goSetSecBtn").onclick = () => {
    $("#modifyPwModal").style.display = "none";
    openSecuritySet();
  };
  $("#pwSubmit").onclick = async () => {
    const oldPw = $("#pwOld").value;
    const pw = $("#pwNew").value, pw2 = $("#pwNew2").value;
    const sec = $("#pwSecAnswer").value.trim();
    if (pw.length < 4 || pw.length > 128) return toast("新密码长度需 4-128 字符");
    if (pw !== pw2) return toast("两次输入的新密码不一致");
    if (!sec) return toast("请回答密保问题");
    try {
      await api("/password/change", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_password: oldPw, new_password: pw, security_answer: sec }),
      });
      toast("密码已修改，请使用新密码重新登录");
      setTimeout(() => { location.href = "/login"; }, 900);
    } catch (e) { toast(e.message); }
  };
}

// 打开补录/修改密保弹窗（清空表单；已有密保则要求验证旧密保答案）
function openSecuritySet() {
  $("#secSetOld").value = "";
  $("#secSetOldSec").value = "";
  $("#secSetQuestion").value = "";
  $("#secSetQuestionCustom").value = "";
  $("#secSetCustomField").style.display = "none";
  $("#secSetAnswer").value = "";
  $("#securitySetModal").style.display = "flex";
  api("/security/status").then(s => {
    $("#secSetOldSecField").style.display = s.configured ? "block" : "none";
  }).catch(() => {
    $("#secSetOldSecField").style.display = "block";
  });
  $("#secSetOld").focus();
}

// 登录后检查密保状态：未设置则显示引导条（“稍后”24 小时内不再提示）
async function checkSecurityBanner() {
  const banner = $("#secBanner");
  if (!banner) return;
  try {
    const s = await api("/security/status");
    const later = localStorage.getItem("sec_later");
    const hidden = later && (Date.now() - +later < 24 * 3600 * 1000);
    banner.style.display = s.configured || hidden ? "none" : "flex";
  } catch (e) {}
}

// ---------- 主题 ----------
function bindTheme() {
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
  };
  const saved = localStorage.getItem("theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
}

// ---------- 关闭对话框 ----------
function bindModalClose() {
  $$("[data-close]").forEach(btn => {
    btn.addEventListener("click", () => {
      btn.closest(".modal").style.display = "none";
    });
  });
  $$(".modal").forEach(m => {
    m.addEventListener("click", (e) => {
      if (e.target === m) m.style.display = "none";
    });
  });
}

// =========================================================
// 新增：布局折叠 / 拖拽 / 备注栏 / 内容编辑器 / 字体控制
// =========================================================
// 移动端判定与抽屉辅助
function isMobile() {
  return window.matchMedia("(max-width: 768px)").matches;
}
function closeDrawers() {
  document.body.classList.remove("sidebar-open");
}
// 触屏（或 CodeMirror 未加载）时正文/备注编辑器降级为普通 textarea
function usePlainEditor() {
  return isMobile() || !window.CodeMirror;
}

const FONT_STACKS = {
  mono: `"JetBrains Mono","Cascadia Mono",Consolas,"Microsoft YaHei Mono",monospace`,
  sans: `-apple-system,"Segoe UI Variable","PingFang SC","Microsoft YaHei UI","Noto Sans CJK SC",sans-serif`,
  serif: `"Source Serif 4","Noto Serif SC","Songti SC",SimSun,serif`,
};
let txtSize = +(localStorage.getItem("txtSize") || 15);
let txtFont = localStorage.getItem("txtFont") || "mono";
let noteOpen = localStorage.getItem("noteOpen") !== "0";
let sidebarHidden = localStorage.getItem("sidebarHidden") === "1";
let noteEditors = {};     // did -> CodeMirror view
let notePlainEditors = {}; // did -> textarea（触屏降级）
let contentEditor = null; // 当前正文编辑 view

function setTxtFont(f) {
  txtFont = f;
  localStorage.setItem("txtFont", f);
  document.documentElement.style.setProperty("--txt-font", FONT_STACKS[f]);
  const tv = document.querySelector(".txt-view");
  if (tv) tv.style.fontFamily = FONT_STACKS[f];
}
function applyTxtState() {
  document.documentElement.style.setProperty("--txt-size", txtSize + "px");
  setTxtFont(txtFont);
}

// ---- 布局折叠（左目录栏；移动端为抽屉） ----
function bindSidebarFold() {
  $("#foldSidebarBtn").onclick = () => {
    if (isMobile()) {
      // 移动端：开关全高抽屉（CSS 控制 transform + 遮罩显示）
      document.body.classList.toggle("sidebar-open");
    } else {
      sidebarHidden = !sidebarHidden;
      localStorage.setItem("sidebarHidden", sidebarHidden ? "1" : "0");
      $("#layout").classList.toggle("sidebar-hidden", sidebarHidden);
    }
  };
  // 点击遮罩收起抽屉
  $("#drawerMask").addEventListener("click", closeDrawers);
  $("#layout").classList.toggle("sidebar-hidden", sidebarHidden);
}

// ---- 拖拽把手（侧栏/备注栏调宽；Pointer Events，兼容触屏） ----
function bindResizers() {
  $$(".resizer").forEach(rz => {
    const onDown = (e) => {
      e.preventDefault();
      rz.classList.add("dragging");
      const which = rz.dataset.resize;
      const startX = e.clientX;
      const startW = which === "sidebar"
        ? $(".layout").classList.contains("sidebar-hidden") ? 0 : $("#sidebar").getBoundingClientRect().width
        : $("#notePanel").getBoundingClientRect().width;
      const onMove = (ev) => {
        let w = Math.max(180, Math.min(480, startW + (which === "sidebar" ? ev.clientX - startX : startX - ev.clientX)));
        if (which === "sidebar") {
          w = Math.max(180, Math.min(Math.max(180, window.innerWidth * 0.4), startW + (ev.clientX - startX)));
          $("#sidebar").style.flexBasis = w + "px";
          $("#sidebar").style.width = w + "px";
        } else {
          $("#notePanel").style.flexBasis = w + "px";
          $("#notePanel").style.width = w + "px";
          $("#layout").style.setProperty("--note-w", w + "px");
          localStorage.setItem("noteW", w);
        }
      };
      const onUp = () => {
        rz.classList.remove("dragging");
        rz.removeEventListener("pointermove", onMove);
        rz.removeEventListener("pointerup", onUp);
        rz.removeEventListener("pointercancel", onUp);
      };
      rz.addEventListener("pointermove", onMove);
      rz.addEventListener("pointerup", onUp);
      rz.addEventListener("pointercancel", onUp);
    };
    rz.addEventListener("pointerdown", onDown);
  });
}

// ---- 备注栏 ----
function bindNotePanel() {
  const panel = $("#notePanel");
  $("#noteCloseBtn").onclick = () => {
    noteOpen = false;
    localStorage.setItem("noteOpen", "0");
    $("#layout").classList.remove("show-note");
  };
  // 移动端默认收起备注抽屉，避免刷新后遮挡正文
  if (isMobile()) noteOpen = false;
  const savedW = localStorage.getItem("noteW");
  if (savedW) {
    panel.style.flexBasis = savedW + "px";
    panel.style.width = savedW + "px";
    $("#layout").style.setProperty("--note-w", savedW + "px");
  }
  if (noteOpen) $("#layout").classList.add("show-note");
}
async function toggleNotePanel(doc) {
  noteOpen = !noteOpen;
  localStorage.setItem("noteOpen", noteOpen ? "1" : "0");
  $("#layout").classList.toggle("show-note", noteOpen);
  if (noteOpen) {
    await loadNoteForDoc(doc);
  }
}
async function loadNoteForDoc(doc) {
  const body = $("#notePanelBody");
  const did = doc.id;
  if (usePlainEditor()) {
    // 触屏降级：普通 textarea（带防抖自动保存，并按文档缓存）
    let ta = notePlainEditors[did];
    if (!ta || !ta.isConnected) {
      const host = document.createElement("div");
      host.className = "editor-host";
      body.innerHTML = "";
      body.appendChild(host);
      ta = document.createElement("textarea");
      ta.className = "note-plain-editor";
      ta.placeholder = "在此记录备注…保存是自动的";
      const r = await api(`/documents/${did}/note`).catch(() => ({ content: "" }));
      ta.value = r.content || "";
      host.appendChild(ta);
      notePlainEditors[did] = ta;
      let t;
      ta.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(async () => {
          try {
            await api(`/documents/${did}/note`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ content: ta.value }),
            });
          } catch (e) {}
        }, 800);
      });
    } else {
      body.innerHTML = "";
      body.appendChild(ta.closest(".editor-host"));
    }
    ta.focus();
    return;
  }
  if (noteEditors[did]) {
    body.innerHTML = "";
    body.appendChild(noteEditors[did].dom.closest(".editor-host"));
    noteEditors[did].focus();
    return;
  }
  let content = "";
  try {
    const r = await api(`/documents/${did}/note`);
    content = r.content || "";
  } catch (e) { content = ""; }
  // 重建面板载体
  body.innerHTML = "";
  const host = document.createElement("div");
  host.className = "editor-host";
  body.appendChild(host);
  const darkCol = (document.documentElement.getAttribute("data-theme") === "dark");
  const view = window.CodeMirror.build({
    host,
    value: content,
    meta: "markdown",
    dark: darkCol,
  });
  view.dom.style.height = "100%";
  noteEditors[did] = view;
  // 防抖自动保存备注
  let t;
  view.dom.addEventListener("keyup", () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      try {
        await api(`/documents/${did}/note`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: view.state.doc.toString() }),
        });
      } catch (e) {}
    }, 800);
  });
}

// ---- 内容编辑器（正文编辑） ----
async function enterContentEditor(doc) {
  const did = doc.id;
  if (doc.file_type !== ".txt" && doc.file_type !== ".md" && doc.file_type !== ".markdown") {
    toast("仅支持编辑 txt / md 正文");
    return;
  }
  let raw = "";
  try {
    const r = await api(`/documents/${did}`);
    raw = r.raw || "";
  } catch (e) { toast("读取失败：" + e.message); return; }
  const body = $("#docBody");
  const meta = doc.file_type === ".txt" ? "text" : (doc.file_type === ".md" ? "markdown" : "text");
  body.innerHTML = `<div class="editor-wrap">
      <div class="editor-toolbar">
        <span>✍ 编辑：${esc(doc.title)}</span>
        <span class="spacer"></span>
        <span class="editor-meta" id="editorMeta">已载入</span>
        <button class="btn-ghost" id="editorCancel">取消</button>
        <button class="btn-primary" id="editorSave">保存</button>
      </div>
      ${usePlainEditor()
        ? `<textarea id="editorTextarea" wrap="off" placeholder="在此编辑内容…">${esc(raw)}</textarea>`
        : `<div class="editor-host" id="editorHost"></div>`}
    </div>`;
  if (usePlainEditor()) {
    // 触屏降级：直接体编辑 textarea
    contentEditor = null;
    $("#editorCancel").onclick = () => { window.__reloadOp(); };
    $("#editorSave").onclick = async () => {
      const content = $("#editorTextarea").value;
      $("#editorMeta").textContent = "保存中…";
      try {
        await api(`/documents/${did}/content`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        });
        toast("已保存");
        window.__reloadOp();
      } catch (e) {
        $("#editorMeta").textContent = "保存失败：" + e.message;
        toast("保存失败：" + e.message);
      }
    };
    return;
  }
  const darkCol = (document.documentElement.getAttribute("data-theme") === "dark");
  contentEditor = window.CodeMirror.build({
    host: $("#editorHost"),
    value: raw,
    meta,
    dark: darkCol,
  });
  $("#editorCancel").onclick = () => { window.__reloadOp(); };
  $("#editorSave").onclick = async () => {
    const content = contentEditor.state.doc.toString();
    $("#editorMeta").textContent = "保存中…";
    try {
      const r = await api(`/documents/${did}/content`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      toast("已保存");
      window.__reloadOp();
    } catch (e) {
      $("#editorMeta").textContent = "保存失败：" + e.message;
      toast("保存失败：" + e.message);
    }
  };
}

// ---------- 初始化 ----------
window.addEventListener("DOMContentLoaded", () => {
  bindTheme();
  bindModalClose();
  bindUpload();
  bindNote();
  bindFolder();
  bindEdit();
  bindSearch();
  bindLogout();
  bindExport();
  bindSecurity();
  checkSecurityBanner();
  applyTxtState();
  bindSidebarFold();
  bindResizers();
  bindNotePanel();
  loadTree();
  loadTags();
});
