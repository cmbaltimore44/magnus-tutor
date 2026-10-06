// Library: every document with ingestion status, re-ingest, confirmation for
// big handwriting jobs, and fixing transcriptions against the page image.
import { useCallback, useEffect, useState } from 'react';
import { api, onEvent } from '../api';
import { Book, Pages, Refresh, Search, X } from '../components/Icons';
import { Markdown } from '../components/Markdown';
import { Modal } from '../components/Modal';
import { useApp } from '../state';

type Doc = {
  id: number; course: string; kind: string; path: string; title: string; status: string; progress: number;
  pages: number; error?: string; meta: any; chunks: number; exists: boolean;
};

const STATUS: Record<string, string> = {
  queued: 'queued', ingesting: 'reading', done: 'ready', failed: 'failed', paused: 'paused', needs_confirmation: 'needs your OK',
};

export function Library() {
  const { courses, settings, reloadSettings } = useApp();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [folders, setFolders] = useState<any[]>([]);
  const [open, setOpen] = useState<Doc | null>(null);
  const [q, setQ] = useState('');
  const [allCourses, setAllCourses] = useState(false);
  const [results, setResults] = useState<any[] | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<Doc[]>('/library/documents').then(setDocs).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    api.get('/library/folders').then(setFolders).catch(() => {});
    return onEvent('job', (d) => {
      if (d.document) setDocs((xs) => xs.map((x) => (x.id === d.document.id ? { ...x, ...d.document } : x)));
      if (d.status) setStatus(d.status);
      if (d.document?.status === 'done') load();
    });
  }, [load]);

  const search = async () => {
    if (!q.trim()) return setResults(null);
    const course = localStorage.getItem('tutor.course');
    setResults(await api.get(`/library/search?q=${encodeURIComponent(q)}${course && !allCourses ? `&course=${course}` : ''}&all_courses=${allCourses}`));
  };

  const paused = !!settings?.background?.paused;
  const byCourse = courses.map((c) => ({ c, docs: docs.filter((d) => d.course === c.slug) }));

  return (
    <div className="page library">
      <div className="section-head">
        <h1>Library</h1>
        <div className="row">
          <button className="btn small" onClick={() => api.post('/library/scan').then(load)}><Refresh size={14} /> Scan folders</button>
          <button className="btn small ghost" onClick={() => api.patch('/settings', { background: { paused: !paused } }).then(reloadSettings)}>
            {paused ? 'Resume background work' : 'Pause background work'}
          </button>
        </div>
      </div>
      <p className="muted small">
        PDFs are read in place from each course’s folders; only extracted text, embeddings and a capped page-image cache are stored.
        {status && <> · <span className="warn">{status}</span></>}
      </p>

      <div className="search-row card">
        <Search size={16} />
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} placeholder="Search your notes and textbooks…" />
        <label className="toggle small"><input type="checkbox" checked={allCourses} onChange={(e) => setAllCourses(e.target.checked)} /> all courses, incl. archived</label>
        <button className="btn small" onClick={search}>Search</button>
      </div>
      {results && (
        <ul className="results">
          {results.length ? results.map((r, i) => (
            <li key={i} className="card">
              <div className="res-head"><b>{r.label}</b><span className="muted small">{courses.find((c) => c.slug === r.course)?.name} · {r.section_path}</span></div>
              <Markdown text={r.text.slice(0, 600) + (r.text.length > 600 ? '…' : '')} className="res-text" />
            </li>
          )) : <li className="muted">No matches.</li>}
        </ul>
      )}

      {byCourse.map(({ c, docs }) => (
        <section key={c.slug} className="lib-course">
          <h2>{c.name} <span className="muted small">{c.title}</span></h2>
          {docs.length ? (
            <table className="docs">
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id} className={`s-${d.status}`}>
                    <td className="doc-kind">{d.kind === 'textbook' ? <Book size={15} /> : <Pages size={15} />}</td>
                    <td>
                      <button className="link-btn doc-title" onClick={() => setOpen(d)}>{d.title}</button>
                      <div className="muted tiny">{d.path.split('/').slice(-1)[0]}{d.exists ? '' : ' · file missing'}</div>
                    </td>
                    <td className="doc-status">
                      <span className={`status s-${d.status}`}>{STATUS[d.status] || d.status}</span>
                      {['ingesting', 'queued'].includes(d.status) && <div className="bar"><i style={{ width: `${Math.round(d.progress * 100)}%` }} /></div>}
                      {d.status === 'failed' && <div className="error tiny">{d.error}</div>}
                    </td>
                    <td className="muted small">{d.pages ? `${d.pages} pp` : ''}{d.chunks ? ` · ${d.chunks} passages` : ''}{d.meta?.gap_pages ? ` · ${d.meta.gap_pages} pages with equations read by OCR` : ''}</td>
                    <td className="doc-actions">
                      {d.status === 'needs_confirmation' && (
                        <button className="btn small primary" onClick={() => api.post(`/library/documents/${d.id}/confirm`).then(load)} title="Transcribe handwriting with the vision model">
                          Transcribe {d.meta.vision_pages} pages (~{Math.max(1, Math.round(d.meta.estimate_s / 60))} min)
                        </button>
                      )}
                      <button className="icon-btn" title="Re-ingest" onClick={() => api.post(`/library/documents/${d.id}/reingest`).then(load)}><Refresh size={14} /></button>
                      <button className="icon-btn" title="Forget derived data (the PDF stays)" onClick={() => confirm('Remove this document’s extracted text and embeddings? The PDF itself is not touched.') && api.del(`/library/documents/${d.id}`).then(load)}><X size={14} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted small">No documents yet.</p>
          )}
          <div className="folders muted tiny">
            {folders.filter((f) => f.course === c.slug).map((f) => <div key={f.path}><code>{f.path}</code> ({f.kind === 'notes' ? 'GoodNotes exports' : f.kind === 'textbook' ? 'textbook PDFs' : 'syllabus, slides, problem sets'})</div>)}
          </div>
        </section>
      ))}
      {open && <DocViewer doc={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function DocViewer({ doc, onClose }: { doc: Doc; onClose: () => void }) {
  const [pages, setPages] = useState<any[]>([]);
  const [sel, setSel] = useState(0);
  const [text, setText] = useState('');
  const [method, setMethod] = useState('');
  const [edit, setEdit] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    api.get(`/documents/${doc.id}/pages`).then((ps) => {
      setPages(ps);
      const firstHand = ps.findIndex((p: any) => p.method === 'vision' || p.method === 'ocr');
      setSel(firstHand >= 0 ? firstHand : 0);
    });
  }, [doc.id]);
  useEffect(() => {
    if (!pages.length) return;
    api.get(`/documents/${doc.id}/pages/${pages[sel].page_index}/text`).then((r) => { setText(r.text || ''); setMethod(r.method); setEdit(false); });
  }, [sel, pages, doc.id]);
  const p = pages[sel];
  const save = async () => {
    setSaving(true);
    await api.put(`/documents/${doc.id}/pages/${p.page_index}/text`, { text });
    setSaving(false);
    setEdit(false);
    setMethod('edited');
  };
  return (
    <Modal open onClose={onClose} title={doc.title} wide>
      <div className="viewer">
        <div className="viewer-nav">
          <button className="btn small" disabled={sel === 0} onClick={() => setSel(sel - 1)}>‹</button>
          <span className="small">Page {p?.printed_page || (p ? p.page_index + 1 : '')} <span className="muted">({sel + 1} of {pages.length})</span></span>
          <button className="btn small" disabled={sel >= pages.length - 1} onClick={() => setSel(sel + 1)}>›</button>
          <span className={`pill tiny m-${method}`}>{({ embedded: 'text layer', 'embedded-gaps': 'text layer (equations missing)', ocr: 'macOS OCR', vision: 'vision transcription', edited: 'your edit', none: 'no text' } as any)[method] || method}</span>
          {method === 'embedded-gaps' || method === 'ocr' ? (
            <button className="btn small ghost" onClick={() => api.post(`/library/documents/${doc.id}/repair`, { from: p.page_index, to: p.page_index })} title="Read this page's equations with the vision model (≈20 s, when idle)">Repair equations</button>
          ) : null}
        </div>
        <div className="viewer-body">
          {p && <img className="page-img" src={`/api/documents/${doc.id}/pages/${p.page_index}.png`} alt="" />}
          <div className="viewer-text">
            {edit ? (
              <>
                <textarea value={text} onChange={(e) => setText(e.target.value)} />
                <div className="row">
                  <button className="btn primary small" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save fix'}</button>
                  <button className="btn small ghost" onClick={() => setEdit(false)}>Cancel</button>
                </div>
              </>
            ) : (
              <>
                <Markdown text={text || '_No text on this page._'} />
                <button className="btn small" onClick={() => setEdit(true)}>Fix transcription</button>
              </>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
