// The Magnus focus timer, always visible in the header. Magnus owns the timer;
// this pill only displays it (computed from timestamps) and sends commands
// through the backend, which runs `magnus timer …`.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useApp, useTick } from '../state';
import type { Timer } from '../types';
import { Pause, Play, Plus, Skip, Stop, X } from './Icons';

export function remainingMs(t: Timer): number {
  if (!t.active) return 0;
  if (t.status === 'ended') return 0;
  if (t.paused || !t.ends_at) return t.remaining_ms ?? 0;
  return Math.max(0, t.ends_at - Date.now());
}

export function clock(ms: number): string {
  const s = Math.ceil(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

let focusLabel: string | null = null;
/** The Workspace registers what "switch to this problem" means. */
export function setWorkspaceFocusLabel(label: string | null) {
  focusLabel = label;
}

function chime() {
  try {
    const ctx = new AudioContext();
    const now = ctx.currentTime;
    [659.25, 987.77].forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = 'sine';
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, now + i * 0.22);
      g.gain.exponentialRampToValueAtTime(0.18, now + i * 0.22 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.22 + 0.9);
      o.connect(g).connect(ctx.destination);
      o.start(now + i * 0.22);
      o.stop(now + i * 0.22 + 1);
    });
  } catch {
    /* audio unavailable */
  }
}

export function TimerPill() {
  const { timer, courseBySlug, currentCourse } = useApp();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const prev = useRef<Timer | null>(null);
  useTick(1000, timer.active && timer.status === 'running' && !timer.paused);

  const ms = remainingMs(timer);
  const label = timer.title || timer.label || '';

  // Live tab title, visible even when the tab is in the background.
  useEffect(() => {
    if (!timer.active) {
      document.title = 'Magnus Tutor';
      return;
    }
    const icon = timer.status === 'ended' ? '⏰' : timer.paused ? '⏸' : timer.phase === 'focus' ? '▶' : '☕';
    const what = label ? ` · ${label}` : '';
    document.title = timer.status === 'ended' ? `⏰ ${timer.phase_label} done${what}` : `${icon} ${clock(ms)}${what}`;
  });

  // Phase end: the in-page banner always; chime + notification when the web app owns alerts.
  useEffect(() => {
    const was = prev.current;
    prev.current = timer;
    if (!was || !timer.active) return;
    if (was.status === 'running' && timer.status === 'ended') {
      const msg = timer.phase === 'focus' ? `${timer.phase_label} done${label ? ` · ${label}` : ''}. Time for a break.` : 'Break over. Ready for the next round?';
      setBanner(msg);
      if (timer.alert_owner === 'web') {
        chime();
        if ('Notification' in window && Notification.permission === 'granted') new Notification('Magnus Tutor', { body: msg });
      }
    }
    if (timer.status === 'running') setBanner(null);
  }, [timer, label]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, [open]);

  const cmd = async (c: string, body?: object) => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/timer/${c}`, body);
      if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const course = courseBySlug(currentCourse);
  const startLabel = focusLabel || (course ? `office hours: ${course.name}` : null);

  if (timer.available === false) {
    return <div className="timer-pill off" title={timer.error || 'Magnus timer unavailable'}>timer offline</div>;
  }

  const dots = Array.from({ length: timer.every || 4 }, (_, i) => i < (timer.round || 0));
  const phaseClass = !timer.active ? 'idle' : timer.status === 'ended' ? 'ended' : timer.phase === 'focus' ? 'focus' : 'break';

  return (
    <div className="timer-wrap" ref={ref}>
      <button className={`timer-pill ${phaseClass} ${timer.paused ? 'paused' : ''}`} onClick={() => setOpen((o) => !o)} aria-expanded={open} title="Focus timer (Magnus)">
        {timer.active ? (
          <>
            <span className="tp-phase">{timer.status === 'ended' ? '⏰' : timer.paused ? '⏸' : timer.phase === 'focus' ? '●' : '☕'}</span>
            <span className="tp-clock">{timer.status === 'ended' ? 'done' : clock(ms)}</span>
            {label && <span className="tp-label">{label}</span>}
            <span className="tp-dots">{dots.map((d, i) => <i key={i} className={d ? 'on' : ''} />)}</span>
          </>
        ) : (
          <>
            <span className="tp-phase">○</span>
            <span className="tp-label">Focus</span>
          </>
        )}
      </button>
      {open && (
        <div className="popover timer-pop">
          {timer.active ? (
            <>
              <div className="tp-head">
                <div className="tp-big">{timer.status === 'ended' ? 'Time' : clock(ms)}</div>
                <div className="muted small">{timer.phase_label}{label ? ` · ${label}` : ''}</div>
              </div>
              <div className="tp-actions">
                {timer.status === 'ended' ? (
                  <button className="btn primary" disabled={busy} onClick={() => cmd('skip')}><Play size={15} /> Start next</button>
                ) : timer.paused ? (
                  <button className="btn primary" disabled={busy} onClick={() => cmd('resume')}><Play size={15} /> Resume</button>
                ) : (
                  <button className="btn" disabled={busy} onClick={() => cmd('pause')}><Pause size={15} /> Pause</button>
                )}
                {timer.status !== 'ended' && <button className="btn" disabled={busy} onClick={() => cmd('skip')}><Skip size={15} /> Skip</button>}
                <button className="btn" disabled={busy} onClick={() => cmd('add5')}><Plus size={15} /> 5 min</button>
                <button className="btn" disabled={busy} onClick={() => cmd('stop')}><Stop size={15} /> Stop</button>
                <button className="btn ghost danger" disabled={busy} onClick={() => cmd('discard')} title="Stop without logging"><X size={15} /> Discard</button>
              </div>
              {startLabel && startLabel !== label && (
                <button className="btn wide" disabled={busy} onClick={() => cmd('switch', { label: startLabel })}>
                  Switch to “{startLabel}” <span className="muted small">keeps the clock</span>
                </button>
              )}
            </>
          ) : (
            <>
              <div className="tp-head">
                <div className="muted small">No focus round running</div>
              </div>
              <button className="btn primary wide" disabled={busy} onClick={() => cmd('start', startLabel ? { label: startLabel } : {})}>
                <Play size={15} /> Start focus{startLabel ? ` · ${startLabel}` : ''}
              </button>
            </>
          )}
          {error && <div className="error small">{error}</div>}
          <div className="muted tiny">Shared with Magnus · minutes are logged once, by Magnus</div>
        </div>
      )}
      {banner && (
        <div className="timer-banner" role="status">
          <span>{banner}</span>
          <button className="btn small" onClick={() => cmd('skip')}>Start next</button>
          <button className="btn small ghost" onClick={() => cmd('add5')}>+5 min</button>
          <button className="icon-btn" onClick={() => setBanner(null)} aria-label="Dismiss"><X size={14} /></button>
        </div>
      )}
    </div>
  );
}
