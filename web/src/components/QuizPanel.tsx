// Quiz mode: one question at a time from your own materials, graded against
// the source page (with a real math check for numeric answers).
import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Course, Source } from '../types';
import { Check, Spark } from './Icons';
import { Markdown } from './Markdown';

type Item = { id: number; question: string; concept: string; answer?: string | null; student_answer?: string; grade?: number | null; feedback?: string; source?: Source | null };

export function QuizPanel({ sessionId, course, onSource }: { sessionId: number; course?: Course; onSource: (s: Source[]) => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [topic, setTopic] = useState('');
  const [answer, setAnswer] = useState('');
  const [busy, setBusy] = useState<'next' | 'grade' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<Item[]>(`/sessions/${sessionId}/quiz`).then(setItems).catch(() => {});
  }, [sessionId]);

  const current = items.length && items[items.length - 1].grade == null ? items[items.length - 1] : null;
  const graded = items.filter((i) => i.grade != null);
  const avg = graded.length ? graded.reduce((a, i) => a + (i.grade || 0), 0) / graded.length : null;

  const next = async () => {
    setBusy('next');
    setError(null);
    try {
      const q = await api.post(`/sessions/${sessionId}/quiz/next`, { topic: topic || undefined });
      setItems((xs) => [...xs, q]);
      setAnswer('');
      if (q.source) onSource([q.source]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };
  const submit = async () => {
    if (!current || !answer.trim()) return;
    setBusy('grade');
    setError(null);
    try {
      const g = await api.post(`/sessions/${sessionId}/quiz/answer`, { item_id: current.id, answer });
      setItems((xs) => xs.map((x) => (x.id === current.id ? { ...x, ...g, student_answer: answer } : x)));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="quiz">
      <div className="quiz-bar">
        <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder={course?.topics?.length ? `Topic (e.g. ${course.topics[0]}), or leave blank for your weak spots` : 'Topic (optional)'} />
        <button className="btn primary small" onClick={next} disabled={!!busy || !!current}>
          <Spark size={14} /> {busy === 'next' ? 'Writing a question…' : items.length ? 'Next question' : 'Start the quiz'}
        </button>
        {avg != null && <span className="muted small">{graded.length} answered · {Math.round(avg * 100)}%</span>}
      </div>
      <div className="quiz-list">
        {!items.length && !busy && <div className="empty-chat"><p>Questions come only from your notes and textbooks, and each answer is graded against the page it came from. Leave the topic blank to target the concepts you’re weakest on.</p></div>}
        {items.map((it, i) => (
          <div key={it.id} className={`quiz-item ${it.grade != null ? 'done' : ''}`}>
            <div className="qi-head">
              <span className="pill tiny">Q{i + 1} · {it.concept}</span>
              {it.source && <button className="link-btn small" onClick={() => onSource([it.source!])}>{it.source.label}</button>}
            </div>
            <Markdown text={it.question} />
            {it.grade != null && (
              <div className="qi-result">
                <div className="qi-answer"><span className="muted small">You:</span> <Markdown text={it.student_answer || ''} /></div>
                <div className="grade-row">
                  <span className="bar"><i style={{ width: `${Math.round((it.grade || 0) * 100)}%`, background: (it.grade || 0) >= 0.8 ? 'var(--success)' : (it.grade || 0) >= 0.5 ? 'var(--warning)' : 'var(--danger)' }} /></span>
                  <b>{Math.round((it.grade || 0) * 100)}%</b> {(it.grade || 0) >= 0.8 && <Check size={14} />}
                </div>
                <Markdown text={it.feedback || ''} />
                {it.answer && <div className="muted small">Expected: <Markdown text={it.answer} className="inline-md" /></div>}
              </div>
            )}
          </div>
        ))}
      </div>
      {current && (
        <div className="composer">
          <textarea value={answer} rows={3} placeholder="Your answer (LaTeX is fine)…" onChange={(e) => setAnswer(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }} />
          <div className="composer-bar">
            <span className="muted tiny">Enter to submit · Shift+Enter for a new line</span>
            <button className="btn primary small" onClick={submit} disabled={!!busy || !answer.trim()}>{busy === 'grade' ? 'Grading…' : 'Submit'}</button>
          </div>
        </div>
      )}
      {error && <div className="error small">{error}</div>}
    </div>
  );
}
