// Prompt editor: every prompt is a Markdown file you can change any time.
// Live preview of the fully rendered prompt, save, reset, history with restore,
// and a "test this prompt" run of scripted conversations.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, postStream } from '../api';
import { Play, Refresh } from '../components/Icons';
import { Markdown } from '../components/Markdown';
import { useApp } from '../state';
import { ago } from './Home';

type Entry = { name: string; description: string; where: string; chars: number };
type Line = { type: string; text?: string; name?: string; hint_level?: number };

export function Prompts() {
  const { courses } = useApp();
  const [course, setCourse] = useState<string>('');
  const [list, setList] = useState<Entry[]>([]);
  const [vars, setVars] = useState<Record<string, string>>({});
  const [name, setName] = useState('office_hours');
  const [loaded, setLoaded] = useState<any>(null);
  const [text, setText] = useState('');
  const [preview, setPreview] = useState('');
  const [showPreview, setShowPreview] = useState(true);
  const [test, setTest] = useState<Line[] | null>(null);
  const [testing, setTesting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const qs = course ? `?course=${course}` : '';
  const loadList = useCallback(() => api.get(`/prompts${qs}`).then((r) => { setList(r.prompts); setVars(r.variables); }), [qs]);
  const load = useCallback(() => api.get(`/prompts/${name}${qs}`).then((r) => { setLoaded(r); setText(r.text); }), [name, qs]);
  useEffect(() => { loadList(); }, [loadList]);
  useEffect(() => { load(); setTest(null); }, [load]);

  // Live preview of the rendered prompt (debounced).
  useEffect(() => {
    const t = setTimeout(() => {
      api.post('/prompts/preview', { name, text, course: course || null }).then((r) => setPreview(r.text)).catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [name, text, course]);

  const dirty = loaded && text !== loaded.text;
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 1800); };

  const save = async () => {
    await api.put(`/prompts/${name}`, { text, course: course || null });
    await Promise.all([load(), loadList()]);
    flash(course ? `Saved as an override for ${courses.find((c) => c.slug === course)?.name}` : 'Saved · takes effect on the next turn');
  };
  const reset = async () => {
    if (!confirm(course ? 'Remove this course override (fall back to the global prompt)?' : 'Reset to the default prompt? (Your current version stays in the history.)')) return;
    await api.post(`/prompts/${name}/reset`, { course: course || null });
    await Promise.all([load(), loadList()]);
    flash('Reset');
  };
  const restore = async (vid: number) => {
    await api.post(`/prompts/versions/${vid}/restore`);
    await Promise.all([load(), loadList()]);
    flash('Restored');
  };
  const runTest = async () => {
    setTesting(true);
    setTest([]);
    try {
      await postStream('/prompts/test', { name, text, course: course || (courses.find((c) => c.kind.includes('physics'))?.slug ?? null) }, (ev) => setTest((t) => [...(t || []), ev as Line]));
    } finally {
      setTesting(false);
    }
  };

  const used = useMemo(() => new Set((text.match(/\{\{\s*([a-z_]+)\s*\}\}/g) || []).map((m) => m.replace(/[{}\s]/g, ''))), [text]);

  return (
    <div className="page prompts">
      <div className="section-head">
        <h1>Prompts {msg && <span className="saved">{msg}</span>}</h1>
        <select value={course} onChange={(e) => setCourse(e.target.value)} aria-label="Scope">
          <option value="">Global prompts</option>
          {courses.map((c) => <option key={c.slug} value={c.slug}>Override for {c.name}</option>)}
        </select>
      </div>
      <p className="muted small">Plain Markdown files in <code>~/.config/magnus-tutor/prompts/</code> (course overrides in each course’s <code>prompts/</code> folder). Edits apply on the next turn. The engine’s gates (Settings) enforce attempt-first and the hint ladder separately, so a prompt edit never fights hidden behavior.</p>

      <div className="prompt-layout">
        <ul className="prompt-list">
          {list.map((p) => (
            <li key={p.name} className={p.name === name ? 'active' : ''} onClick={() => (!dirty || confirm('Discard unsaved changes?')) && setName(p.name)}>
              <div className="pl-name">{p.name}</div>
              <div className="muted tiny">{p.description}</div>
              <span className={`where w-${p.where.split(':')[0]}`}>{p.where.startsWith('course') ? 'course override' : p.where}</span>
            </li>
          ))}
        </ul>

        <div className="prompt-editor">
          <div className="editor-bar">
            <b>{name}.md</b>
            <span className="muted small">{loaded?.where === 'default' ? 'packaged default' : loaded?.where?.startsWith('course') ? 'course override' : 'global'}{dirty ? ' · unsaved' : ''}</span>
            <span className="grow" />
            <button className="btn small ghost" onClick={reset}><Refresh size={13} /> {course ? 'Remove override' : 'Reset to default'}</button>
            <button className="btn small" onClick={runTest} disabled={testing}><Play size={13} /> {testing ? 'Testing…' : 'Test this prompt'}</button>
            <button className="btn small primary" onClick={save} disabled={!dirty}>Save</button>
          </div>
          <textarea className="prompt-text" value={text} onChange={(e) => setText(e.target.value)} spellCheck />
          <div className="vars">
            {Object.entries(vars).map(([k, d]) => (
              <button key={k} className={`var ${used.has(k) ? 'used' : ''}`} title={d} onClick={() => setText((t) => `${t}{{${k}}}`)}>{`{{${k}}}`}</button>
            ))}
          </div>
          {test && (
            <div className="test-out card">
              <div className="muted small">Scripted conversations with your draft (not saved anywhere). The engine’s gates still apply.</div>
              {test.map((l, i) =>
                l.type === 'scenario' ? <h3 key={i}>{l.name}</h3> :
                l.type === 'student' ? <div key={i} className="t-student">{l.text}</div> :
                l.type === 'tutor' ? <div key={i} className="t-tutor"><span className="pill tiny">hint {l.hint_level ?? '–'}</span><Markdown text={l.text || ''} /></div> : null,
              )}
              {testing && <div className="typing"><i /><i /><i /></div>}
            </div>
          )}
        </div>

        <aside className="prompt-side">
          <div className="side-head">
            <button className={`btn small ghost ${showPreview ? 'active' : ''}`} onClick={() => setShowPreview(true)}>Rendered preview</button>
            <button className={`btn small ghost ${!showPreview ? 'active' : ''}`} onClick={() => setShowPreview(false)}>History</button>
          </div>
          {showPreview ? (
            <pre className="preview">{preview}</pre>
          ) : (
            <ul className="history">
              {(loaded?.history || []).map((h: any) => (
                <li key={h.id}>
                  <div>{h.note || 'saved'} <span className="muted tiny">· {ago(h.created_at)} · {h.chars} chars</span></div>
                  <button className="btn small ghost" onClick={() => restore(h.id)}>Restore</button>
                </li>
              ))}
              {!loaded?.history?.length && <li className="muted small">No saved versions yet. Every save here is kept.</li>}
            </ul>
          )}
        </aside>
      </div>
    </div>
  );
}
