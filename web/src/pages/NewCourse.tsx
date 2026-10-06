// New course wizard: basics, then drop the syllabus; the tutor proposes topics,
// schedule, grading and notation; you confirm or edit; then add materials.
import { useState } from 'react';
import { api } from '../api';
import { Archive, Pages, Spark } from '../components/Icons';
import { navigate } from '../router';
import { useApp } from '../state';

const EMPTY = { title: '', code: '', short: '', instructor: '', term: '', description: '', kind: [] as string[], languages: [] as string[], topics: [] as string[], schedule: '', grading: '', notation_conventions: '' };
const KINDS = ['math', 'physics', 'code', 'writing', 'general'];

export function NewCourse() {
  const { courses, reloadCourses } = useApp();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [form, setForm] = useState({ ...EMPTY });
  const [syllabusId, setSyllabusId] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<any>(null);
  const terms = [...new Set(courses.map((c) => c.term).filter(Boolean))];

  const set = (k: string) => (e: any) => setForm({ ...form, [k]: e.target.value });
  const readSyllabus = async (file: File) => {
    setReading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      const r = await fetch('/api/courses/from-syllabus', { method: 'POST', body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const j = await r.json();
      const p = j.proposal;
      // Keep what you typed; fill the rest from the syllabus.
      setForm((f) => ({ ...p, ...Object.fromEntries(Object.entries(f).filter(([, v]) => (Array.isArray(v) ? v.length : v))) }));
      setSyllabusId(j.syllabus_id);
      setStep(2);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setReading(false);
    }
  };
  const create = async () => {
    setError(null);
    try {
      const r = await api.post('/courses/wizard', { proposal: form, syllabus_id: syllabusId });
      await reloadCourses();
      setCreated(r);
      setStep(3);
    } catch (e: any) {
      setError(e.message);
    }
  };
  const archiveTerm = async (term: string) => {
    if (!confirm(`Archive every ${term} course? They stay searchable and keep their history.`)) return;
    await api.post('/courses/archive-term', { term });
    await reloadCourses();
  };

  return (
    <div className="page new-course">
      <h1>New course</h1>
      <ol className="steps">
        <li className={step === 1 ? 'on' : ''}>Basics & syllabus</li>
        <li className={step === 2 ? 'on' : ''}>Confirm details</li>
        <li className={step === 3 ? 'on' : ''}>Add materials</li>
      </ol>

      {step === 1 && (
        <div className="card wizard">
          <div className="grid2">
            <label>Course name<input value={form.title} onChange={set('title')} placeholder="Electricity and Magnetism" autoFocus /></label>
            <label>Code<input value={form.code} onChange={set('code')} placeholder="PHYS 3110" /></label>
            <label>Nickname<input value={form.short} onChange={set('short')} placeholder="E&M" /></label>
            <label>Term<input value={form.term} onChange={set('term')} placeholder="Spring 2027" list="terms" /></label>
            <datalist id="terms">{terms.map((t) => <option key={t} value={t} />)}</datalist>
            <label>Instructor<input value={form.instructor} onChange={set('instructor')} /></label>
          </div>
          <label className="drop">
            <Pages size={22} />
            <span>{reading ? 'Reading the syllabus…' : 'Drop the syllabus here (PDF or text), or click to choose'}</span>
            <input type="file" accept=".pdf,.txt,.md" hidden onChange={(e) => e.target.files?.[0] && readSyllabus(e.target.files[0])} disabled={reading} />
          </label>
          <div className="row">
            <button className="btn ghost" onClick={() => setStep(2)} disabled={!form.title}>Skip the syllabus</button>
          </div>
          {error && <div className="error small">{error}</div>}
        </div>
      )}

      {step === 2 && (
        <div className="card wizard">
          <p className="small muted">{syllabusId ? 'Proposed from your syllabus. Edit anything before saving.' : 'Fill in what you know; everything can be edited later.'}</p>
          <div className="grid2">
            {(['title', 'code', 'short', 'term', 'instructor'] as const).map((k) => (
              <label key={k}>{k}<input value={(form as any)[k]} onChange={set(k)} /></label>
            ))}
            <label>languages<input value={form.languages.join(', ')} onChange={(e) => setForm({ ...form, languages: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} /></label>
          </div>
          <div className="row">
            {KINDS.map((k) => (
              <label key={k} className="toggle"><input type="checkbox" checked={form.kind.includes(k)} onChange={(e) => setForm({ ...form, kind: e.target.checked ? [...form.kind, k] : form.kind.filter((x) => x !== k) })} /> {k}</label>
            ))}
          </div>
          <label>Description<textarea rows={2} value={form.description} onChange={set('description')} /></label>
          <label>Topics (one per line)<textarea rows={7} value={form.topics.join('\n')} onChange={(e) => setForm({ ...form, topics: e.target.value.split('\n') })} /></label>
          <div className="grid2">
            <label>Schedule<textarea rows={3} value={form.schedule} onChange={set('schedule')} /></label>
            <label>Grading<textarea rows={3} value={form.grading} onChange={set('grading')} /></label>
          </div>
          <label>Notation conventions<textarea rows={2} value={form.notation_conventions} onChange={set('notation_conventions')} /></label>
          <div className="row">
            <button className="btn" onClick={() => setStep(1)}>Back</button>
            <button className="btn primary" onClick={create} disabled={!form.title}><Spark size={14} /> Create course</button>
          </div>
          {error && <div className="error small">{error}</div>}
        </div>
      )}

      {step === 3 && created && (
        <div className="card wizard">
          <h2>Done. Now the materials</h2>
          <p className="small">Put PDFs in these folders and the tutor picks them up on its own (your files are read in place, never copied):</p>
          <ul className="folder-list">
            <li><b>GoodNotes exports</b> <code>{created.folders.notes}</code></li>
            <li><b>Textbooks</b> <code>{created.folders.textbooks}</code></li>
            <li><b>Syllabus, slides, problem sets</b> <code>{created.folders.other}</code>{syllabusId ? ' (your syllabus is already there)' : ''}</li>
          </ul>
          <div className="row">
            <button className="btn primary" onClick={() => navigate(`/course/${created.slug}`)}>Open the course</button>
            <button className="btn" onClick={() => navigate('/library')}>Library</button>
          </div>
        </div>
      )}

      {!!terms.length && (
        <section className="mt">
          <h2>New semester</h2>
          <p className="small muted">Archive last term’s courses in one go. Archived courses are hidden from Home but stay searchable (“didn’t I cover this last year?”) and keep their history.</p>
          <div className="row">
            {terms.map((t) => <button key={t} className="btn small" onClick={() => archiveTerm(t)}><Archive size={14} /> Archive {t}</button>)}
          </div>
        </section>
      )}
    </div>
  );
}
