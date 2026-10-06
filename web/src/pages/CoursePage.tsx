import { useEffect, useState } from 'react';
import { api } from '../api';
import { Archive, Book, Code, Quill, Spark } from '../components/Icons';
import { Link, navigate } from '../router';
import { useApp } from '../state';
import type { Session } from '../types';
import { ago, startSession } from './Home';

export function CoursePage({ slug }: { slug: string }) {
  const { courseBySlug, setCurrentCourse, reloadCourses } = useApp();
  const course = courseBySlug(slug);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [docs, setDocs] = useState<any[]>([]);
  const [focus, setFocus] = useState<any>(null);
  const [mastery, setMastery] = useState<any[]>([]);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    setCurrentCourse(slug);
    api.get<Session[]>(`/sessions?course=${slug}&limit=40`).then(setSessions).catch(() => {});
    api.get(`/library/documents?course=${slug}`).then(setDocs).catch(() => setDocs([]));
    api.get(`/timer/minutes?course=${slug}`).then(setFocus).catch(() => setFocus(null));
    api.get(`/mastery?course=${slug}`).then(setMastery).catch(() => setMastery([]));
  }, [slug, setCurrentCourse]);

  if (!course) return <div className="page muted">No course “{slug}”.</div>;

  const archive = async () => {
    await api.patch(`/courses/${slug}`, { status: course.status === 'archived' ? 'active' : 'archived' });
    await reloadCourses();
    navigate('/');
  };

  return (
    <div className="page course-page">
      <section className="course-hero">
        <div>
          <div className="eyebrow">{course.term}{course.code ? ` · ${course.code}` : ''}{course.instructor ? ` · ${course.instructor}` : ''}</div>
          <h1>{course.title}</h1>
          {course.description && <p className="muted">{course.description}</p>}
        </div>
        <div className="hero-actions">
          <button className="btn primary" onClick={() => startSession(course, 'office_hours')}>
            {course.is_writing ? <Quill size={15} /> : <Spark size={15} />} {course.is_writing ? 'Writing office hours' : 'Start a problem set'}
          </button>
          {course.kind.includes('code') && <button className="btn" onClick={() => startSession(course, 'code')}><Code size={15} /> Code session</button>}
          <button className="btn" onClick={() => startSession(course, 'ask')}>Ask a question</button>
          <button className="btn ghost" onClick={() => startSession(course, 'quiz')}>Quiz me</button>
        </div>
      </section>

      <div className="course-cols">
        <section>
          <h2>Sessions</h2>
          {sessions.length ? (
            <ul className="session-list">
              {sessions.map((s) => (
                <li key={s.id}>
                  <Link to={`/session/${s.id}`}>
                    <span className="sess-course">{s.mode.replace('_', ' ')}</span>
                    <span className="sess-title">{s.title || s.first_message?.slice(0, 90) || 'Untitled'}</span>
                    <span className="muted small">{ago(s.started_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">No sessions yet.</p>
          )}
        </section>
        <section>
          <h2>Materials</h2>
          {docs.length ? (
            <ul className="doc-list">
              {docs.map((d) => (
                <li key={d.id}>
                  <Book size={14} /> <span>{d.title}</span> <span className={`status s-${d.status}`}>{d.status === 'done' ? `${d.pages} pages` : `${d.status} ${Math.round(d.progress * 100)}%`}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">Put PDFs in <code>{course.folders.notes}</code> (GoodNotes exports) or <code>{course.folders.textbooks}</code>, then ingest from the <Link to="/library">Library</Link>.</p>
          )}
          <h2 className="mt">Topics</h2>
          {course.topics.length ? <div className="topic-cloud">{course.topics.map((t) => <span key={t} className="pill">{t}</span>)}</div> : <p className="muted small">No topics yet.</p>}
          {!!mastery.length && (
            <>
              <h2 className="mt">Mastery</h2>
              <ul className="mastery">
                {mastery.slice(0, 12).map((m: any) => (
                  <li key={m.concept}>
                    <span>{m.concept}</span>
                    <span className="bar"><i style={{ width: `${Math.round(m.score * 100)}%` }} /></span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {focus && (
            <>
              <h2 className="mt">Focus time</h2>
              <p className="small">{focus.minutes} min logged in Magnus with “{course.name}” labels{focus.sessions ? ` across ${focus.sessions} tutor sessions` : ''}.</p>
            </>
          )}
          <div className="course-tools">
            <button className="btn small ghost" onClick={() => setEditing((e) => !e)}>{editing ? 'Close' : 'Edit course'}</button>
            <button className="btn small ghost" onClick={archive}><Archive size={14} /> {course.status === 'archived' ? 'Unarchive' : 'Archive'}</button>
          </div>
          {editing && <CourseEditor slug={slug} onSaved={() => { setEditing(false); reloadCourses(); }} />}
        </section>
      </div>
    </div>
  );
}

function CourseEditor({ slug, onSaved }: { slug: string; onSaved: () => void }) {
  const { courseBySlug } = useApp();
  const c = courseBySlug(slug)!;
  const [form, setForm] = useState({
    title: c.title, short: c.short, code: c.code, instructor: c.instructor, term: c.term, description: c.description,
    topics: c.topics.join('\n'), notation_conventions: c.notation_conventions, languages: c.languages.join(', '), kind: c.kind.join(', '),
  });
  const set = (k: string) => (e: any) => setForm({ ...form, [k]: e.target.value });
  const save = async () => {
    await api.patch(`/courses/${slug}`, {
      ...form,
      topics: form.topics.split('\n').map((s) => s.trim()).filter(Boolean),
      languages: form.languages.split(',').map((s) => s.trim()).filter(Boolean),
      kind: form.kind.split(',').map((s) => s.trim()).filter(Boolean),
    });
    onSaved();
  };
  return (
    <div className="form card">
      {(['title', 'short', 'code', 'instructor', 'term'] as const).map((k) => (
        <label key={k}>{k}<input value={(form as any)[k]} onChange={set(k)} /></label>
      ))}
      <label>kind (math, physics, code, writing)<input value={form.kind} onChange={set('kind')} /></label>
      <label>languages<input value={form.languages} onChange={set('languages')} /></label>
      <label>description<textarea value={form.description} onChange={set('description')} rows={2} /></label>
      <label>topics (one per line)<textarea value={form.topics} onChange={set('topics')} rows={5} /></label>
      <label>notation conventions<textarea value={form.notation_conventions} onChange={set('notation_conventions')} rows={2} /></label>
      <p className="muted tiny">Also editable as a file: ~/.config/magnus-tutor/courses/{slug}/course.yaml</p>
      <button className="btn primary" onClick={save}>Save</button>
    </div>
  );
}
