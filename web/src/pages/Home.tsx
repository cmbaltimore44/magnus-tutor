import { useEffect, useState } from 'react';
import { api, onEvent } from '../api';
import { Book, Code, Plus, Quill, Spark } from '../components/Icons';
import { Link, navigate } from '../router';
import { useApp } from '../state';
import type { Course, Session } from '../types';

export function startSession(course: Course | undefined | null, mode: string, q?: string) {
  return api.post('/sessions', { course: course?.slug ?? null, mode }).then((r) => navigate(`/session/${r.session.id}${q ? `?q=${encodeURIComponent(q)}` : ''}`));
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 5 ? 'Late night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

export function ago(ts: number): string {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function Home() {
  const { courses, settings } = useApp();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [docs, setDocs] = useState<any[]>([]);
  const [review, setReview] = useState<any[]>([]);
  const [profile, setProfile] = useState<any>(null);

  useEffect(() => {
    api.get<Session[]>('/sessions?limit=8').then(setSessions).catch(() => {});
    api.get('/library/documents').then(setDocs).catch(() => setDocs([]));
    api.get('/review').then(setReview).catch(() => setReview([]));
    api.get('/profile').then(setProfile).catch(() => {});
    return onEvent('job', () => api.get('/library/documents').then(setDocs).catch(() => {}));
  }, []);

  const name = profile?.profile?.name;
  const ingesting = docs.filter((d) => ['queued', 'ingesting', 'paused', 'needs_confirmation'].includes(d.status));
  const term = courses[0]?.term;

  return (
    <div className="page home">
      <section className="hero">
        <h1>{greeting()}{name ? `, ${String(name).split(' ')[0]}` : ''}.</h1>
        <p className="muted">{term ? `${term} · ` : ''}{courses.length} course{courses.length === 1 ? '' : 's'} · everything runs on this Mac{settings?.cloud?.enabled ? ' (cloud escalation on)' : ''}.</p>
        {!name && <p className="small"><Link to="/settings#profile">Tell the tutor about yourself</Link> so explanations fit your background.</p>}
      </section>

      <section>
        <div className="section-head">
          <h2>This term</h2>
          <Link to="/courses/new" className="btn ghost small"><Plus size={14} /> New course</Link>
        </div>
        <div className="course-grid">
          {courses.map((c) => (
            <div key={c.slug} className={`course-card k-${c.kind[0] || 'general'}`}>
              <Link to={`/course/${c.slug}`} className="course-card-main">
                <div className="course-short">{c.name}</div>
                <div className="course-title">{c.title}</div>
                <div className="course-kind muted small">{c.kind.join(' · ')}{c.languages.length ? ` · ${c.languages.join(', ')}` : ''}</div>
              </Link>
              <div className="course-actions">
                <button className="btn small" onClick={() => startSession(c, 'office_hours')}>
                  {c.is_writing ? <Quill size={14} /> : c.kind.includes('code') ? <Code size={14} /> : <Spark size={14} />} {c.is_writing ? 'Writing help' : 'Problem set'}
                </button>
                <button className="btn small ghost" onClick={() => startSession(c, 'ask')}>Ask</button>
              </div>
            </div>
          ))}
          {!courses.length && <p className="muted">No courses yet. <Link to="/courses/new">Add your first course</Link>.</p>}
        </div>
      </section>

      <div className="home-cols">
        <section>
          <h2>Recent sessions</h2>
          {sessions.length ? (
            <ul className="session-list">
              {sessions.map((s) => (
                <li key={s.id}>
                  <Link to={`/session/${s.id}`}>
                    <span className="sess-course">{courses.find((c) => c.slug === s.course)?.name ?? 'General'}</span>
                    <span className="sess-title">{s.title || s.first_message?.slice(0, 80) || 'Untitled'}</span>
                    <span className="muted small">{ago(s.started_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">Nothing yet. Start with a problem set above.</p>
          )}
        </section>
        <section>
          <h2>To review</h2>
          {review.length ? (
            <ul className="review-list">
              {review.slice(0, 6).map((r: any, i: number) => (
                <li key={i}>
                  <span className="pill">{courses.find((c) => c.slug === r.course)?.name ?? r.course}</span> {r.concept}
                  <span className="muted small"> · {r.reason}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">Concepts you needed big hints on, or unlocked solutions for, show up here.</p>
          )}
          <h2 className="mt">Materials</h2>
          {ingesting.length ? (
            <ul className="ingest-mini">
              {ingesting.map((d) => (
                <li key={d.id}><Book size={14} /> {d.title} <span className="muted small">{d.status} {Math.round(d.progress * 100)}%</span></li>
              ))}
            </ul>
          ) : (
            <p className="muted small">{docs.length ? `${docs.length} document${docs.length === 1 ? '' : 's'} ingested.` : 'No notes or textbooks ingested yet.'} <Link to="/library">Library</Link></p>
          )}
        </section>
      </div>
    </div>
  );
}
