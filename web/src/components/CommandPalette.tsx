// ⌘K: ask a quick question, start a problem, or jump anywhere.
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { navigate } from '../router';
import { useApp } from '../state';

type Item = { id: string; label: string; hint?: string; run: () => void | Promise<void> };

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { courses, currentCourse } = useApp();
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ('');
      setSel(0);
      setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  const course = courses.find((c) => c.slug === currentCourse);
  const items = useMemo<Item[]>(() => {
    const text = q.trim();
    const out: Item[] = [];
    const start = async (mode: string, slug: string | null, message?: string) => {
      const r = await api.post('/sessions', { course: slug, mode });
      navigate(`/session/${r.session.id}${message ? `?q=${encodeURIComponent(message)}` : ''}`);
    };
    if (text) {
      out.push({ id: 'ask', label: `Ask: ${text}`, hint: course ? `in ${course.name}` : 'all courses', run: () => start('ask', course?.slug ?? null, text) });
      out.push({ id: 'problem', label: `Office hours: ${text.slice(0, 60)}`, hint: course?.name || 'no course', run: () => start('office_hours', course?.slug ?? null, text) });
    }
    for (const c of courses) {
      out.push({ id: `p-${c.slug}`, label: `New problem · ${c.name}`, hint: c.title, run: () => start(c.is_writing ? 'office_hours' : 'office_hours', c.slug) });
      out.push({ id: `c-${c.slug}`, label: `Open ${c.name}`, hint: 'course page', run: () => navigate(`/course/${c.slug}`) });
    }
    out.push({ id: 'lib', label: 'Library', run: () => navigate('/library') });
    out.push({ id: 'prompts', label: 'Prompts', run: () => navigate('/prompts') });
    out.push({ id: 'settings', label: 'Settings', run: () => navigate('/settings') });
    out.push({ id: 'newcourse', label: 'New course…', run: () => navigate('/courses/new') });
    out.push({ id: 'unload', label: 'Unload models now', hint: 'free memory', run: () => api.post('/models/unload').then(() => undefined) });
    if (!text) return out;
    const needle = text.toLowerCase();
    return out.filter((i) => i.id === 'ask' || i.id === 'problem' || i.label.toLowerCase().includes(needle));
  }, [q, courses, course]);

  if (!open) return null;
  const run = async (i: Item) => {
    onClose();
    await i.run();
  };
  return (
    <div className="overlay top" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-label="Command palette">
        <input
          ref={input}
          value={q}
          placeholder={course ? `Ask about ${course.name}, or type a command…` : 'Ask anything, or type a command…'}
          onChange={(e) => {
            setQ(e.target.value);
            setSel(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose();
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setSel((s) => Math.min(s + 1, items.length - 1));
            }
            if (e.key === 'ArrowUp') {
              e.preventDefault();
              setSel((s) => Math.max(s - 1, 0));
            }
            if (e.key === 'Enter' && items[sel]) run(items[sel]);
          }}
        />
        <ul>
          {items.slice(0, 12).map((it, i) => (
            <li key={it.id} className={i === sel ? 'sel' : ''} onMouseEnter={() => setSel(i)} onMouseDown={() => run(it)}>
              <span>{it.label}</span>
              {it.hint && <span className="muted small">{it.hint}</span>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
