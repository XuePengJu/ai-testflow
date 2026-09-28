/**
 * V4.0 知识库管理台（RAG 知识库 · 文档可见可审可干预）。
 *
 * 左栏：知识库列表（可见性徽标 + 文档/分块统计）+ 新建
 * 右栏：选中库 → 文档 Tab（上传/列表/详情/分块编辑/修订/回滚/重建/删除）
 *        + 检索 Tab（RAG 检索测试台，展示相似度与来源）
 * 权限：global 库非创建者只读（写操作按 403 由后端拦截，前端隐藏编辑按钮）
 * V5.8：AI 问答 Tab 下线（问答统一走首页 AI 会话的知识库多选检索）
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileText, RefreshCw, Search, Trash2, Upload, Plus,
  History, Undo2, X, Pencil, Check,
  BookOpen, Network,
} from "lucide-react";
import { api, apiJson, toast } from "../api/client";
import ReactECharts from "echarts-for-react";
import KbSelector from "../components/knowledge/KbSelector";
import { useAuth } from "../hooks/useAuth";
import { parseServerTime } from "../utils/time";

interface KB {
  id: string; name: string; description: string; visibility: string;
  type: string; user_id: number; doc_count: number; chunk_count: number;
  created_at: string | null;
}
interface Doc {
  id: string; knowledge_base_id: string; title: string; file_name: string;
  file_type: string; file_size: number; parse_status: string;
  error_message: string | null; chunk_count: number; created_at: string | null;
  processed_at: string | null; wiki_summary?: string;
  custom_meta?: Record<string, string>;
}
interface ChunkRow {
  id: string; chunk_index: number; content: string; context_header: string;
  chunk_type: string; content_revision: number; is_enabled: boolean;
}
interface Hit { id: string; score: number; document: string; metadata: Record<string, unknown>; }

const cleanTitle = (t: string) => (t || "").replace(/^\d+[_\-\s]*/, "").replace(/\.[^.]+$/, "");

/**
 * 摘要展示兜底：存量数据里 wiki_summary 可能存的是 {"summary": "..."} 形状的
 * 原始 JSON（LLM 返回整段入库的历史脏数据），展示前解析取纯文本。
 * 与后端 app/services/knowledge/ingest.py:extract_summary_text 同口径。
 */
function extractSummaryText(raw?: string | null): string {
  const s = (raw || "").trim();
  if (!s.startsWith("{")) return s;
  try {
    const data = JSON.parse(s) as { summary?: unknown };
    if (data && typeof data === "object" && "summary" in data) {
      const v = data.summary;
      if (typeof v === "string") return extractSummaryText(v);
      if (v == null) return "";
      if (typeof v === "object") return extractSummaryText(JSON.stringify(v));
      return String(v);
    }
  } catch {
    /* 截断/非法 JSON：剥 {"summary": " 前缀做尽力修复，失败原文展示 */
    const m = s.match(/^\{\s*"summary"\s*:\s*"(.*)$/s);
    if (m) {
      const body = m[1].replace(/\s*\}\s*$/, "").replace(/"\s*$/, "")
        .replace(/\\n/g, "\n").replace(/\\"/g, '"').replace(/\\\\/g, "\\");
      if (body.trim()) return body;
    }
  }
  return s;
}

const STATUS_META: Record<string, { label: string; cls: string }> = {
  ready: { label: "已入库", cls: "ok" },
  failed: { label: "失败", cls: "err" },
  unprocessed: { label: "待处理", cls: "" },
  parsing: { label: "解析中", cls: "" },
  chunking: { label: "分块中", cls: "" },
  processing: { label: "向量化中", cls: "" },
};

export default function KnowledgePage() {
  const { role } = useAuth();
  const readOnly = role === "guest"; // V4.2：访客只读（仅共享库，无新建/上传/删除，写操作后端 403 兜底）
  const [bases, setBases] = useState<KB[]>([]);
  const [sel, setSel] = useState<KB | null>(null);
  const [tab, setTab] = useState<"docs" | "wiki" | "graph" | "search">(
    () => {
      const saved = localStorage.getItem("aitf_kb_tab");
      // V5.8："chat" Tab 已下线，老记忆值回落到文档 Tab
      return saved && saved !== "chat" ? (saved as "docs" | "wiki" | "graph" | "search") : "docs";
    }
  );
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    const data = await apiJson<{ items: KB[] }>("/api/knowledge/bases");
    setBases(data?.items ?? []);
    setSel((prev) => {
      const items = data?.items ?? [];
      if (prev) {
        const next = items.find((b) => b.id === prev.id);
        if (next) return next;
      }
      const savedId = localStorage.getItem("aitf_kb_sel");
      const restored = savedId ? items.find((b) => b.id === savedId) : null;
      return restored ?? (items[0] ?? null);
    });
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { localStorage.setItem("aitf_kb_tab", tab); }, [tab]);

  // Embedding 生效状态（打开页面 toast 一次，不常驻）
  const [embShown, setEmbShown] = useState(false);
  useEffect(() => {
    if (embShown) return;
    void apiJson<{ embedding_source: string; embedding: { model: string } | null }>("/api/llm/effective")
      .then((d) => {
        if (d) {
          const src = d.embedding_source;
          const model = d.embedding?.model ?? "";
          if (src === "mock") {
            toast("⚠️ 当前为 mock 向量（未配置 Embedding），相似度排序参考价值有限");
          } else {
            const srcLabel = src === "personal" ? "我的配置" : src === "platform" ? "平台默认" : "环境变量";
            toast(`✓ Embedding 生效：${model || "已配置"}（${srcLabel}）`);
          }
          setEmbShown(true);
        }
      });
  }, [embShown]);

  return (
    <main className="app-main kb-page kb-no-aside">
      {/* V4.4：知识库列表移到顶栏下拉选择器（KbSelector），左侧不再占用一列 */}
      <section className="kb-main">
        {!sel ? (
          <div className="kb-empty-col muted">
            {readOnly ? "暂无可访问的共享知识库" : (
              <>
                <div style={{ marginBottom: 12 }}>还没有知识库，先创建一个开始入库文档</div>
                <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
                  <Plus size={14} /> 创建知识库
                </button>
              </>
            )}
          </div>
        ) : (
          <>
            <header className="kb-topbar">
              <KbSelector
                bases={bases}
                sel={sel}
                readOnly={readOnly}
                onSelect={(b) => { setSel(b); localStorage.setItem("aitf_kb_sel", b.id); }}
                onNew={() => setShowCreate(true)}
              />
              <nav className="pill-tabs" role="tablist" aria-label="视图切换">
                <button className={"ptab " + (tab === "docs" ? "on" : "")} onClick={() => setTab("docs")}>
                  <FileText size={14} /> 文档
                </button>
                <button className={"ptab " + (tab === "wiki" ? "on" : "")} onClick={() => setTab("wiki")}>
                  <BookOpen size={14} /> Wiki
                </button>
                <button className={"ptab " + (tab === "graph" ? "on" : "")} onClick={() => setTab("graph")}>
                  <Network size={14} /> 图谱
                </button>
                <button className={"ptab " + (tab === "search" ? "on" : "")} onClick={() => setTab("search")}>
                  <Search size={14} /> 检索测试
                </button>
              </nav>
              <div className="kb-top-actions">
                {!readOnly && <KbActions kb={sel} onChanged={load} />}
              </div>
            </header>
            {tab === "docs" && <DocsTab kb={sel} readOnly={readOnly} />}
            {tab === "wiki" && <WikiTab kb={sel} />}
            {tab === "graph" && <GraphTab kb={sel} />}
            {tab === "search" && <SearchTab kb={sel} />}
          </>
        )}
      </section>

      {showCreate && <CreateDialog onClose={() => setShowCreate(false)} onCreated={load} />}
    </main>
  );
}

/* ---------------- 库级操作（权限/改名/删除） ---------------- */
function KbActions({ kb, onChanged }: { kb: KB; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(kb.name);
  const [desc, setDesc] = useState(kb.description);

  const save = async () => {
    const r = await apiJson(`/api/knowledge/bases/${kb.id}`, {
      method: "PATCH",
      body: JSON.stringify({ name: name.trim() || kb.name, description: desc }),
    });
    if (r) { toast("已保存"); setEditing(false); onChanged(); }
  };
  const toggleVis = async () => {
    const next = kb.visibility === "global" ? "private" : "global";
    const r = await apiJson(`/api/knowledge/bases/${kb.id}`, {
      method: "PATCH", body: JSON.stringify({ visibility: next }),
    });
    if (r) { toast(next === "global" ? "已设为全局共享（只读）" : "已设为私有"); onChanged(); }
  };
  const del = async () => {
    if (!window.confirm(`删除知识库「${kb.name}」？将同时清空其全部文档与向量，不可恢复。`)) return;
    const r = await api(`/api/knowledge/bases/${kb.id}`, { method: "DELETE" });
    if (r.ok) { toast("已删除"); onChanged(); }
  };

  return (
    <>
      <button className="btn ghost btn-sm" onClick={() => setEditing(!editing)} title="重命名/描述">
        <Pencil size={13} />
      </button>
      <button className="btn ghost btn-sm" onClick={toggleVis} title={kb.visibility === "global" ? "当前全局共享 → 改为私有" : "当前私有 → 改为全局共享"}>
        {kb.visibility === "global" ? "设为私有" : "设为共享"}
      </button>
      <button className="btn ghost btn-sm danger" onClick={del} title="删除知识库"><Trash2 size={13} /></button>
      {editing && (
        <div className="kb-edit-pop">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="名称" />
          <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="描述" />
          <div><button className="btn btn-sm btn-primary" onClick={save}><Check size={13} /> 保存</button></div>
        </div>
      )}
    </>
  );
}

/* ---------------- 文档 Tab ---------------- */
function DocsTab({ kb, readOnly = false }: { kb: KB; readOnly?: boolean }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [detail, setDetail] = useState<{ doc: Doc; chunks: ChunkRow[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const data = await apiJson<{ items: Doc[] }>(`/api/knowledge/bases/${kb.id}/documents`);
    setDocs(data?.items ?? []);
  }, [kb.id]);
  useEffect(() => { void load(); }, [load]);

  const upload = async (f: File) => {
    setBusy(true);
    toast(`正在入库「${f.name}」…（解析+分块+向量化，请稍候）`);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await api(`/api/knowledge/bases/${kb.id}/documents`, { method: "POST", body: fd });
      if (r.ok) { toast(`「${f.name}」入库完成`); void load(); }
      else {
        let msg = `入库失败（HTTP ${r.status}）`;
        try { const d = await r.json(); if (d?.detail) msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail); } catch {}
        toast(msg);
      }
    } catch {
      toast("入库请求失败：网络错误或后端无响应，请检查后端日志");
    } finally { setBusy(false); }
  };

  const createTextDoc = async () => {
    if (!newTitle.trim() || !newContent.trim()) { toast("标题与正文不能为空"); return; }
    setBusy(true);
    try {
      toast(`正在入库「${newTitle.trim()}」…`);
      const r = await api(`/api/knowledge/bases/${kb.id}/documents/text`, {
        method: "POST",
        body: JSON.stringify({ title: newTitle.trim(), content: newContent.trim() }),
      });
      if (r.ok) { toast(`「${newTitle.trim()}」入库完成`); setShowNew(false); setNewTitle(""); setNewContent(""); void load(); }
      else {
        let msg = `入库失败（HTTP ${r.status}）`;
        try { const d = await r.json(); if (d?.detail) msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail); } catch {}
        toast(msg);
      }
    } catch {
      toast("入库请求失败：网络错误或后端无响应");
    } finally { setBusy(false); }
  };

  const openDetail = async (doc: Doc) => {
    const data = await apiJson<Record<string, unknown>>(`/api/knowledge/documents/${doc.id}`);
    if (data) {
      const { chunks: ck, ...info } = data as { chunks?: ChunkRow[] } & Partial<Doc>;
      setDetail({ doc: { ...doc, ...info } as Doc, chunks: ck ?? [] });
    }
  };
  const reindex = async (doc: Doc) => {
    setBusy(true);
    toast(`正在重建「${doc.title}」索引…（分块+向量化，请稍候）`);
    try {
      const r = await api(`/api/knowledge/documents/${doc.id}/reindex`, { method: "POST" });
      if (r.ok) { toast("已重建索引"); void load(); }
      else {
        let msg = `重建失败（HTTP ${r.status}）`;
        try { const d = await r.json(); if (d?.detail) msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail); } catch {}
        toast(msg);
      }
    } catch {
      toast("重建请求失败：网络错误或后端无响应");
    } finally { setBusy(false); }
  };
  const delDoc = async (doc: Doc) => {
    if (!window.confirm(`删除文档「${doc.title}」？其分块与向量将一并清除。`)) return;
    const r = await api(`/api/knowledge/documents/${doc.id}`, { method: "DELETE" });
    if (r.ok) { toast("已删除"); void load(); if (detail?.doc.id === doc.id) setDetail(null); }
  };

  return (
    <div className="kb-panel">
      <div className="kb-toolbar">
        {!readOnly ? (
          <>
            <button className="btn btn-sm btn-primary" disabled={busy}
              onClick={() => fileRef.current?.click()} title="上传文档（md/txt/pdf/docx/xlsx/xls/xmind/csv/json）">
              <Upload size={14} /> 上传文档
            </button>
            <button className="btn btn-sm ghost" disabled={busy} onClick={() => setShowNew(true)} title="在线新建文本文档">
              <FileText size={14} /> 新建文档
            </button>
          </>
        ) : (
          <span className="muted" style={{ fontSize: 12.5 }}>👤 访客只读模式：仅可浏览与检索，不支持上传/修改</span>
        )}
        <input ref={fileRef} type="file" hidden
          accept=".md,.txt,.pdf,.docx,.xlsx,.xls,.xmind,.csv,.json"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
        <span className="muted" style={{ fontSize: 12 }}>支持 md/txt/pdf/docx/xlsx/xls/xmind/csv/json（≤10MB）</span>
      </div>

      {showNew && (
        <div className="kb-mask" onClick={() => setShowNew(false)}>
          <div className="kb-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>新建文档</h3>
            <input className="input" placeholder="标题（必填）" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} />
            <textarea className="input" rows={8} placeholder="正文内容（必填，将自动分块入库）"
              value={newContent} onChange={(e) => setNewContent(e.target.value)} style={{ resize: "vertical", minHeight: 160 }} />
            <div className="kb-dialog-ops">
              <button className="btn ghost" onClick={() => setShowNew(false)}>取消</button>
              <button className="btn btn-primary" disabled={busy} onClick={() => void createTextDoc()}><Check size={14} /> 创建并入库</button>
            </div>
          </div>
        </div>
      )}

      <div className="doc-cards">
        {docs.length === 0 && <div className="muted" style={{ padding: 32, textAlign: "center" }}>{readOnly ? "该库暂无文档" : "暂无文档，点上方「上传文档」开始入库"}</div>}
        {docs.map((d) => {
          const sm = STATUS_META[d.parse_status] ?? { label: d.parse_status, cls: "" };
          return (
            <article key={d.id} className="doc-card" data-doc-id={d.id} onClick={() => void openDetail(d)}>
              <div className="dc-top">
                <h3>{cleanTitle(d.file_name || d.title)}</h3>
                <span className={"status-pill " + sm.cls}>{sm.label}</span>
                <span className="dc-ops" onClick={(e) => e.stopPropagation()}>
                  <button className="btn ghost btn-sm" onClick={() => void openDetail(d)} title={readOnly ? "查看分块" : "查看/编辑分块"}><FileText size={13} /></button>
                  {!readOnly && (
                    <>
                      <button className="btn ghost btn-sm" onClick={() => void reindex(d)} title="重建索引"><RefreshCw size={13} /></button>
                      <button className="btn ghost btn-sm danger" onClick={() => void delDoc(d)} title="删除"><Trash2 size={13} /></button>
                    </>
                  )}
                </span>
              </div>
              {d.parse_status === "failed" && d.error_message
                ? <div className="dc-summary err-text" title={d.error_message}>{d.error_message.slice(0, 90)}</div>
                : d.wiki_summary && <div className="dc-summary">{extractSummaryText(d.wiki_summary)}</div>}
              <div className="dc-meta">
                <span>{d.chunk_count} 片段</span>
                <span>{(d.file_type || "—").toUpperCase()}</span>
                <span>{fmtSize(d.file_size)}</span>
                <span>入库于 {fmtDate(d.created_at)}</span>
              </div>
            </article>
          );
        })}
      </div>

      {detail && <ChunkPanel detail={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

/* ---------------- 文档元信息（基本信息/自定义元数据/摘要） ---------------- */
function DocMetaPanel({ doc }: { doc: Doc }) {
  const toFields = (d: Doc) =>
    Object.entries(d.custom_meta || {}).map(([key, value]) => ({ key, value: String(value) }));
  const [metaFields, setMetaFields] = useState<{ key: string; value: string }[]>(() => toFields(doc));
  const [metaEditing, setMetaEditing] = useState(false);
  const [summary, setSummary] = useState(() => extractSummaryText(doc.wiki_summary));
  const [summaryEditing, setSummaryEditing] = useState(false);
  const [summaryText, setSummaryText] = useState(() => extractSummaryText(doc.wiki_summary));
  const [busy, setBusy] = useState(false);

  const fmtTime = (t?: string | null) => (t ? t.replace("T", " ").slice(0, 16) : "—");
  const fmtSize = (n?: number) =>
    !n ? "—" : n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;

  const saveMeta = async () => {
    setBusy(true);
    const r = await apiJson(`/api/knowledge/documents/${doc.id}/meta`,
      { method: "PUT", body: JSON.stringify({ fields: metaFields }) });
    setBusy(false);
    if (r) { setMetaEditing(false); toast("元数据已保存"); }
  };
  const regenSummary = async () => {
    setBusy(true); toast("正在用文本模型生成摘要…");
    const r = await apiJson<{ wiki_summary?: string }>(`/api/knowledge/documents/${doc.id}/summary`, { method: "POST" });
    setBusy(false);
    if (r) { const t = extractSummaryText(r.wiki_summary); setSummary(t); setSummaryText(t); toast("摘要已生成"); }
  };
  const saveSummary = async () => {
    setBusy(true);
    const r = await apiJson<{ wiki_summary?: string }>(`/api/knowledge/documents/${doc.id}/summary`,
      { method: "PUT", body: JSON.stringify({ summary: summaryText }) });
    setBusy(false);
    if (r) { setSummary(extractSummaryText(r.wiki_summary) || summaryText); setSummaryEditing(false); toast("摘要已保存"); }
  };

  return (
    <>
      <section className="kb-info-section">
        <h4 className="kb-sec-title"><span className="kb-sec-bar" />基本信息</h4>
        <div className="kb-info-grid">
          <div className="kb-info-row"><span>上传时间</span><b>{fmtTime(doc.created_at)}</b></div>
          <div className="kb-info-row"><span>类型</span><b>{(doc.file_type || "—").toUpperCase()}</b></div>
          <div className="kb-info-row"><span>大小</span><b>{fmtSize(doc.file_size)}</b></div>
        </div>
      </section>

      <section className="kb-info-section">
        <h4 className="kb-sec-title">
          <span className="kb-sec-bar" />自定义元数据
          {!metaEditing && (
            <button className="icon-btn kb-title-ops" title="编辑元数据" onClick={() => setMetaEditing(true)}><Pencil size={13} /></button>
          )}
        </h4>
        {metaEditing ? (
          <div className="kb-meta-edit">
            {metaFields.map((f, i) => (
              <div key={i} className="kb-meta-row">
                <input className="input" value={f.key} placeholder="字段名"
                  onChange={(e) => setMetaFields((p) => p.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
                <input className="input" value={f.value} placeholder="字段值"
                  onChange={(e) => setMetaFields((p) => p.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
                <button className="icon-btn danger" title="删除"
                  onClick={() => setMetaFields((p) => p.filter((_, j) => j !== i))}><Trash2 size={13} /></button>
              </div>
            ))}
            <button className="btn btn-sm ghost kb-add-btn"
              onClick={() => setMetaFields((p) => [...p, { key: "", value: "" }])}><Plus size={13} /> 添加元数据字段</button>
            <div className="kb-meta-ops">
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void saveMeta()}><Check size={13} /> 保存</button>
              <button className="btn btn-sm ghost" onClick={() => { setMetaEditing(false); setMetaFields(toFields(doc)); }}>取消</button>
            </div>
          </div>
        ) : metaFields.length === 0 ? (
          <button className="btn btn-sm ghost kb-add-btn" onClick={() => setMetaEditing(true)}><Plus size={13} /> 添加元数据字段</button>
        ) : (
          <div className="kb-meta-list">
            {metaFields.map((f, i) => (
              <div key={i} className="kb-info-row"><span>{f.key}</span><b>{f.value || "—"}</b></div>
            ))}
          </div>
        )}
      </section>

      <section className="kb-info-section">
        <h4 className="kb-sec-title">
          <span className="kb-sec-bar" />摘要
          {!summaryEditing && (
            <span className="kb-title-ops">
              <button className="icon-btn" title="编辑摘要"
                onClick={() => { setSummaryText(summary); setSummaryEditing(true); }}><Pencil size={13} /></button>
              <button className="icon-btn" title="AI 重新生成" disabled={busy}
                onClick={() => void regenSummary()}><RefreshCw size={13} /></button>
            </span>
          )}
        </h4>
        {summaryEditing ? (
          <div className="kb-meta-edit">
            <textarea className="input kb-textarea" rows={6} value={summaryText} onChange={(e) => setSummaryText(e.target.value)} />
            <div className="kb-meta-ops">
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void saveSummary()}><Check size={13} /> 保存</button>
              <button className="btn btn-sm ghost" onClick={() => setSummaryEditing(false)}>取消</button>
            </div>
          </div>
        ) : summary ? (
          <div className="kb-summary-box">{summary}</div>
        ) : (
          <button className="btn btn-sm ghost kb-add-btn" disabled={busy} onClick={() => void regenSummary()}><RefreshCw size={13} /> 生成 AI 摘要</button>
        )}
      </section>
    </>
  );
}

/* ---------------- 分块面板（可审可干预） ---------------- */
function ChunkPanel({ detail, onClose }: {
  detail: { doc: Doc; chunks: ChunkRow[] };
  onClose: () => void;
}) {
  const [chunks, setChunks] = useState<ChunkRow[]>(detail.chunks);
  const [editId, setEditId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [revs, setRevs] = useState<Record<string, { revision: number; content: string; edited_at: string | null }[]>>({});
  const [showRevs, setShowRevs] = useState<Record<string, boolean>>({});
  const [drawerW, setDrawerW] = useState<number>(() => {
    const saved = Number(localStorage.getItem("aitf_drawer_w"));
    return saved >= 320 && saved <= 1200 ? saved : 460;
  });

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const onMove = (ev: MouseEvent) => {
      const w = Math.min(1200, Math.max(320, window.innerWidth - ev.clientX));
      setDrawerW(w);
      localStorage.setItem("aitf_drawer_w", String(w));
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  const saveChunk = async (c: ChunkRow) => {
    const r = await apiJson<{ content: string; content_revision: number }>(
      `/api/knowledge/chunks/${c.id}`, { method: "PUT", body: JSON.stringify({ content: editText }) });
    if (r) {
      setChunks((prev) => prev.map((x) => (x.id === c.id ? { ...x, content: r.content, content_revision: r.content_revision } : x)));
      setEditId(null); toast("分块已更新并同步向量");
    }
  };
  const loadRevs = async (c: ChunkRow) => {
    const data = await apiJson<{ items: { revision: number; content: string; edited_at: string | null }[] }>(
      `/api/knowledge/chunks/${c.id}/revisions`);
    setRevs((prev) => ({ ...prev, [c.id]: data?.items ?? [] }));
    setShowRevs((prev) => ({ ...prev, [c.id]: !prev[c.id] }));
  };
  const rollback = async (c: ChunkRow, rev: number) => {
    if (!window.confirm(`回滚到修订 #${rev}？当前内容将入档为新修订。`)) return;
    const r = await apiJson<{ content: string; content_revision: number }>(
      `/api/knowledge/chunks/${c.id}/rollback`, { method: "POST", body: JSON.stringify({ revision: rev }) });
    if (r) {
      setChunks((prev) => prev.map((x) => (x.id === c.id ? { ...x, content: r.content, content_revision: r.content_revision } : x)));
      toast("已回滚");
    }
  };

  return (
    <div className="kb-drawer" style={{ width: drawerW }}>
      <div className="kb-drawer-resize" onMouseDown={startResize} title="拖动调整宽度" />
      <header className="kb-drawer-head">
        <strong title={detail.doc.file_name || detail.doc.title}>{detail.doc.file_name || detail.doc.title}</strong>
        <span className="muted" style={{ fontSize: 12, flex: "none" }}>共 {chunks.length} 块</span>
        <button className="icon-btn" onClick={onClose} title="关闭"><X size={16} /></button>
      </header>
      <div className="kb-drawer-scroll">
        <DocMetaPanel doc={detail.doc} />
        <section className="kb-info-section">
          <h4 className="kb-sec-title"><span className="kb-sec-bar" />文件内容<span className="kb-chip">共 {chunks.length} 个片段</span></h4>
          <div className="kb-chunks-inner">
        {chunks.length === 0 && <div className="muted">该文档没有分块</div>}
        {chunks.map((c) => (
          <div key={c.id} className="kb-chunk">
            <div className="kb-chunk-head">
              <span className="kb-chunk-idx">{c.chunk_index + 1}</span>
              {c.context_header && <span className="muted" style={{ fontSize: 12, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.context_header}</span>}
              <span className={"status-pill " + (c.chunk_type === "table" ? "ok" : "")}>{c.chunk_type}</span>
            </div>
            <div className="kb-chunk-body">
              {editId === c.id ? (
                <>
                  <textarea className="input kb-textarea" rows={6} value={editText} onChange={(e) => setEditText(e.target.value)} />
                  <div className="kb-chunk-ops">
                    <button className="btn btn-sm btn-primary" onClick={() => void saveChunk(c)}><Check size={13} /> 保存</button>
                    <button className="btn btn-sm ghost" onClick={() => setEditId(null)}>取消</button>
                  </div>
                </>
              ) : (
                <>
                  <div className="kb-chunk-pre">{c.content || "（空分块）"}</div>
                  <div className="kb-chunk-ops">
                    <button className="btn btn-sm ghost" onClick={() => { setEditId(c.id); setEditText(c.content); }}><Pencil size={13} /> 编辑</button>
                    <button className="btn btn-sm ghost" onClick={() => void loadRevs(c)}><History size={13} /> 修订</button>
                  </div>
                  {showRevs[c.id] && (
                    <div className="kb-revs">
                      {(revs[c.id] ?? []).length === 0 && <div className="muted" style={{ fontSize: 12 }}>暂无修订记录</div>}
                      {(revs[c.id] ?? []).map((r) => (
                        <div key={r.revision} className="kb-rev">
                          <span className="kb-chunk-idx">#{r.revision}</span>
                          <pre className="kb-rev-pre">{r.content}</pre>
                          <button className="btn btn-sm ghost" onClick={() => void rollback(c, r.revision)}><Undo2 size={13} /> 回滚</button>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
          </div>
        </section>
      </div>
    </div>
  );
}

/* ---------------- 检索 Tab ---------------- */
function SearchTab({ kb }: { kb: KB }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try {
      const data = await apiJson<{ items: Hit[] }>(
        `/api/knowledge/search?q=${encodeURIComponent(q)}&kb_id=${encodeURIComponent(kb.id)}`);
      setHits(data?.items ?? []);
    } finally { setBusy(false); }
  };

  return (
    <div className="kb-panel">
      <div className="kb-toolbar">
        <input className="input" style={{ flex: 1 }} value={q} placeholder={`在「${kb.name}」中检索…`}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void run()} />
        <button className="btn btn-primary btn-sm" onClick={() => void run()} disabled={busy || !q.trim()}>
          <Search size={14} /> 检索
        </button>
      </div>
      {hits !== null && hits.length === 0 && <div className="muted">未命中，试试换个说法</div>}
      {hits?.map((h, i) => {
        const m = (h.metadata ?? {}) as Record<string, unknown>;
        return (
          <div key={h.id} className="kb-hit">
            <div className="kb-hit-head">
              <span className="kb-hit-score">{(h.score * 100).toFixed(1)}%</span>
              <span className="muted" style={{ fontSize: 12 }}>
                {String(m.file_name ?? "")}{m.context_header ? ` · ${String(m.context_header)}` : ""}
              </span>
            </div>
            <pre className="kb-chunk-pre">{h.document}</pre>
            {i < (hits?.length ?? 0) - 1 && <hr className="kb-hr" />}
          </div>
        );
      })}
    </div>
  );
}

/* ---------------- Wiki Tab（AI 摘要导航） ---------------- */
type WikiItem = {
  doc_id: string; title: string; summary: string; category: string;
  chunk_count: number; file_type: string; processed_at: string | null; updated_at: string | null;
};
const CAT_COLORS = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

function WikiTab({ kb }: { kb: KB }) {
  const [items, setItems] = useState<WikiItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string>("全部");
  const [detail, setDetail] = useState<{ doc: Doc; chunks: ChunkRow[] } | null>(null);

  const load = useCallback(() => {
    apiJson<{ items: WikiItem[] }>(`/api/knowledge/bases/${kb.id}/wiki`).then((d) => {
      if (d?.items) setItems(d.items);
    });
  }, [kb.id]);
  useEffect(() => { load(); }, [load]);

  const indexWiki = async () => {
    setBusy(true);
    toast("正在用 AI 生成 Wiki 索引（摘要 + 主题分类）…");
    try {
      const r = await apiJson<{ items: WikiItem[] }>(`/api/knowledge/bases/${kb.id}/wiki/index`, { method: "POST" });
      if (r?.items) { setItems(r.items); toast(`Wiki 索引生成完成，共 ${r.items.length} 篇`); }
    } finally { setBusy(false); }
  };

  const openDoc = async (docId: string) => {
    const data = await apiJson<Record<string, unknown>>(`/api/knowledge/documents/${docId}`);
    if (data) {
      const { chunks: ck, ...info } = data as { chunks?: ChunkRow[] } & Partial<Doc>;
      setDetail({ doc: info as Doc, chunks: ck ?? [] });
    }
  };

  const normCat = (c?: string) => c && c !== "未分类" ? c : "未分类";
  const catCounts = items.reduce<Record<string, number>>((acc, it) => {
    const c = normCat(it.category); acc[c] = (acc[c] || 0) + 1; return acc;
  }, {});
  const cats = ["全部", ...Object.keys(catCounts).filter((c) => c !== "未分类").sort((a, b) => catCounts[b] - catCounts[a]), ...(catCounts["未分类"] ? ["未分类"] : [])];
  const catColor = (c: string) => { const i = cats.indexOf(c); return CAT_COLORS[(i - 1 + CAT_COLORS.length) % CAT_COLORS.length]; };
  const kw = q.trim().toLowerCase();
  const filtered = items.filter((s) =>
    (cat === "全部" || normCat(s.category) === cat) &&
    (!kw || (s.title || "").toLowerCase().includes(kw) || (s.summary || "").toLowerCase().includes(kw))
  );
  const totalChunks = items.reduce((a, s) => a + (s.chunk_count || 0), 0);
  const themeCount = Object.keys(catCounts).filter((c) => c !== "未分类").length;
  const fmtDate = (t?: string | null) => t ? t.replace("T", " ").slice(0, 10) : "—";

  return (
    <div className="wiki-layout">
      <aside className="wiki-cats">
        <div className="wiki-cats-head">主题分类</div>
        {cats.map((c) => (
          <button key={c} className={"wiki-cat " + (cat === c ? "on" : "")} onClick={() => setCat(c)}>
            {c !== "全部" && <span className="wiki-cat-dot" style={{ background: catColor(c) }} />}
            <span className="wiki-cat-name">{c}</span>
            <span className="muted">{c === "全部" ? items.length : catCounts[c]}</span>
          </button>
        ))}
        <div style={{ padding: "12px 4px 0" }}>
          <button className="btn btn-sm btn-primary" style={{ width: "100%" }} onClick={() => void indexWiki()} disabled={busy}>
            {busy ? "生成中…" : "重新生成 Wiki 索引"}
          </button>
        </div>
      </aside>
      <section className="wiki-main">
        <div className="kb-toolbar">
          <input className="input" style={{ flex: 1 }} placeholder="搜索 Wiki 页面（标题或摘要）…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="wiki-index">
          <div className="wiki-overview">
            <h3>Wiki Index</h3>
            <div className="wiki-stats">
              <span><b>{items.length}</b> 篇文档</span>
              <span><b>{totalChunks}</b> 个知识块</span>
              <span><b>{themeCount}</b> 个主题分类</span>
            </div>
          </div>
          {items.length === 0 && (
            <div className="muted" style={{ padding: 40, textAlign: "center" }}>
              还没有 Wiki 索引。点左侧「重新生成 Wiki 索引」，AI 会自动生成摘要并按主题分类。
            </div>
          )}
          {items.length > 0 && filtered.length === 0 && (
            <div className="muted" style={{ padding: 40, textAlign: "center" }}>没有匹配「{q}」的 Wiki 页面</div>
          )}
          {filtered.map((s) => {
            const c = normCat(s.category);
            return (
              <div key={s.doc_id} className="wiki-item" onClick={() => void openDoc(s.doc_id)} title="点击查看文档与分块">
                <div className="wiki-item-top">
                  <div className="wiki-item-title">{cleanTitle(s.title)}</div>
                  <span className="wiki-cat-tag" style={{ background: (catColor(c)) + "1a", color: catColor(c) }}>{c}</span>
                </div>
                <div className="wiki-item-summary">{extractSummaryText(s.summary) || "（暂无摘要，点「重新生成 Wiki 索引」生成）"}</div>
                <div className="wiki-item-foot">
                  <span>{s.chunk_count} 个片段</span>
                  {s.file_type && <span className="ft">{s.file_type.toUpperCase()}</span>}
                  <span className="muted">更新于 {fmtDate(s.updated_at || s.processed_at)}</span>
                </div>
              </div>
            );
          })}
        </div>
      </section>
      {detail && <ChunkPanel detail={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

/* ---------------- 图谱 Tab（知识图谱） ---------------- */
function GraphTab({ kb }: { kb: KB }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [chartData, setChartData] = useState<{ nodes: any[]; links: any[] } | null>(null);

  useEffect(() => {
    apiJson<{ items: Doc[] }>(`/api/knowledge/bases/${kb.id}/documents`).then((d) => {
      const items = d?.items ?? [];
      setDocs(items);
      buildGraph(items);
    });
  }, [kb.id]);

  const buildGraph = async (items: Doc[]) => {
    const nodes: any[] = [];
    const links: any[] = [];
    const seen = new Set<string>();

    // 文档节点
    items.forEach((d) => {
      const name = d.title || d.file_name;
      nodes.push({ name, symbolSize: 40, category: 0, value: `${d.chunk_count} 块` });
      seen.add(name);
    });

    // 拉分块抽关键词
    for (const d of items.slice(0, 6)) {
      const data = await apiJson<{ chunks: ChunkRow[] }>(`/api/knowledge/documents/${d.id}`);
      const chunks = data?.chunks ?? [];
      const docName = d.title || d.file_name;
      chunks.forEach((c) => {
        const text = c.content || "";
        // 抽 2-4 字高频词
        const words = text.match(/[一-龥]{2,4}/g) || [];
        const freq: Record<string, number> = {};
        words.forEach((w) => { freq[w] = (freq[w] || 0) + 1; });
        const top = Object.entries(freq).sort((a, b) => b[1] - a[1]).slice(0, 3);
        top.forEach(([w, cnt]) => {
          if (!seen.has(w) && cnt >= 2) {
            nodes.push({ name: w, symbolSize: 10 + cnt * 2, category: 1, value: `${cnt} 次` });
            links.push({ source: docName, target: w });
            seen.add(w);
          }
        });
      });
    }

    setChartData({ nodes, links });
  };

  const option = chartData ? {
    tooltip: {},
    legend: [{ data: ["文档", "关键词"], bottom: 10 }],
    series: [{
      type: "graph",
      layout: "force",
      data: chartData.nodes,
      links: chartData.links,
      categories: [{ name: "文档" }, { name: "关键词" }],
      roam: true,
      label: { show: true, position: "right", fontSize: 11 },
      force: { repulsion: 80, edgeLength: 30 },
      lineStyle: { color: "source", curveness: 0.1 },
    }],
  } : null;

  return (
    <div className="kb-panel">
      <div className="kb-toolbar">
        <span className="muted">知识图谱（{docs.length} 篇文档 · {docs.reduce((a, d) => a + (d.chunk_count || 0), 0)} 个知识块）</span>
      </div>
      {option && <ReactECharts option={option} style={{ height: 500, width: "100%" }} />}
    </div>
  );
}

/* ---------------- 新建知识库弹窗 ---------------- */
function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [vis, setVis] = useState("private");
  const [created, setCreated] = useState(false);

  const create = async () => {
    if (!name.trim()) { toast("请输入知识库名称"); return; }
    const r = await apiJson("/api/knowledge/bases", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), description: desc, visibility: vis }),
    });
    if (r) { toast("知识库已创建"); onCreated(); setCreated(true); }
  };

  const goConfig = () => {
    window.dispatchEvent(new CustomEvent("nav-to", { detail: "settings" }));
    onClose();
  };

  return (
    <div className="kb-mask" onClick={onClose}>
      <div className="kb-dialog" onClick={(e) => e.stopPropagation()}>
        {created ? (
          <>
            <h3>知识库已创建</h3>
            <div className="kb-guide">
              <p>下一步建议：配置 <b>Embedding 向量模型</b>（API Key / 模型 / 连接地址），
                否则上传的文档将使用 mock 向量——流程可用但检索相似度参考价值有限。</p>
              <p className="muted">配置后，已入库文档需「重建索引」切换为真实向量。</p>
            </div>
            <div className="kb-dialog-ops">
              <button className="btn ghost" onClick={onClose}>稍后再说</button>
              <button className="btn btn-primary" onClick={goConfig}>去配置向量模型</button>
            </div>
          </>
        ) : (
          <>
            <h3>新建知识库</h3>
            <input className="input" placeholder="名称（必填）" value={name} onChange={(e) => setName(e.target.value)} />
            <input className="input" placeholder="描述（可选）" value={desc} onChange={(e) => setDesc(e.target.value)} />
            <div className="kb-vis-radio">
              <label>
                <input type="radio" checked={vis === "private"} onChange={() => setVis("private")} />
                <span><b>私有</b> <span className="muted">仅自己可见、可检索、可管理</span></span>
              </label>
              <label>
                <input type="radio" checked={vis === "global"} onChange={() => setVis("global")} />
                <span><b>全局共享</b> <span className="muted">全平台可读可检索，仅创建者/管理员可管理</span></span>
              </label>
            </div>
            <div className="kb-dialog-ops">
              <button className="btn ghost" onClick={onClose}>取消</button>
              <button className="btn btn-primary" onClick={create}><Check size={14} /> 创建</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ---------------- 工具 ---------------- */
function fmtSize(n?: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function fmtDate(s: string | null): string {
  const d = parseServerTime(s);
  return d ? `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` : "—";
}
