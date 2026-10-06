// The cited page, shown as the real page image with the passage highlighted
// (rendered by the backend from the PDF, so it matches the physical book).
import { useEffect, useState } from 'react';
import type { Source } from '../types';
import { Expand, Pages } from './Icons';

export function SourcesPanel({ sources, focus }: { sources: Source[]; focus: number | null }) {
  const [sel, setSel] = useState(0);
  useEffect(() => {
    if (focus !== null && focus < sources.length) setSel(focus);
  }, [focus, sources]);
  if (!sources.length)
    return (
      <div className="empty-panel">
        <Pages size={28} />
        <p>Pages from your notes and textbooks show up here when the tutor cites them.</p>
        <p className="muted small">Add PDFs to a course’s notes or textbooks folder, then ingest them from the Library.</p>
      </div>
    );
  const s = sources[Math.min(sel, sources.length - 1)];
  const img = s.document_id != null && s.page_index != null ? `/api/documents/${s.document_id}/pages/${s.page_index}.png?highlight=${s.chunk_id ?? ''}` : null;
  return (
    <div className="sources">
      <ul className="source-list">
        {sources.map((x, i) => (
          <li key={i} className={i === sel ? 'active' : ''} onClick={() => setSel(i)}>
            <span className="src-n">S{i + 1}</span>
            <span className="src-label">{x.label}</span>
          </li>
        ))}
      </ul>
      <div className="source-view">
        <div className="source-head">
          <div>
            <div className="src-title">{s.label}</div>
            {s.section_path && <div className="muted small">{s.section_path}</div>}
          </div>
          {s.document_id != null && (
            <a className="icon-btn" href={`/api/documents/${s.document_id}/file#page=${(s.page_index ?? 0) + 1}`} target="_blank" rel="noreferrer" title="Open the PDF at this page">
              <Expand size={16} />
            </a>
          )}
        </div>
        {img && <img className="page-img" src={img} alt={s.label} loading="lazy" />}
        {s.text && <blockquote className="src-text">{s.text.slice(0, 900)}{s.text.length > 900 ? '…' : ''}</blockquote>}
      </div>
    </div>
  );
}
