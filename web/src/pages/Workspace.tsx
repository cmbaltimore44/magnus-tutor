// The Workspace: problem + chat on the left; Scratchpad, Code and Sources on the right.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, onEvent, postStream } from '../api';
import { CodePanel } from '../components/CodePanel';
import { HintLadder } from '../components/HintLadder';
import { QuizPanel } from '../components/QuizPanel';
import { Check, Cloud, Code, Image as ImageIcon, Lock, Pages, Quill, Send, Spark, X } from '../components/Icons';
import { Markdown } from '../components/Markdown';
import { Modal } from '../components/Modal';
import { SourcesPanel } from '../components/SourcesPanel';
import { setWorkspaceFocusLabel } from '../components/TimerPill';
import { Link } from '../router';
import { useApp } from '../state';
import type { Message, Problem, Session, Source } from '../types';

type Tab = 'scratch' | 'code' | 'sources';
type Pending = { id: string; url: string; uploadId?: string };

const MODE_LABEL: Record<string, string> = { office_hours: 'Office hours', ask: 'Ask', code: 'Code', quiz: 'Quiz' };

export function Workspace({ id }: { id: number }) {
  const { courseBySlug, setCurrentCourse, timer, settings } = useApp();
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [state, setState] = useState<any>({});
  const [draft, setDraft] = useState('');
  const [images, setImages] = useState<Pending[]>([]);
  const [streaming, setStreaming] = useState<{ text: string; note?: string; status?: string; sources?: Source[] } | null>(null);
  const [hintLevel, setHintLevel] = useState(0);
  const [tab, setTab] = useState<Tab>(() => (localStorage.getItem('tutor.tab') as Tab) || 'scratch');
  const [activeSources, setActiveSources] = useState<Source[]>([]);
  const [focusSource, setFocusSource] = useState<number | null>(null);
  const [askUnlock, setAskUnlock] = useState(false);
  const [nextIsNewProblem, setNextIsNewProblem] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [codeContext, setCodeContext] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const autoSent = useRef(false);

  const course = courseBySlug(session?.course);

  const load = useCallback(async () => {
    const r = await api.get(`/sessions/${id}`);
    setSession(r.session);
    setMessages(r.messages);
    setProblem(r.problem);
    setState(r.state);
    const lastMeta = [...r.messages].reverse().find((m: Message) => m.role === 'assistant')?.meta;
    setHintLevel(r.state.solution_unlocked || r.state.solved ? 4 : (lastMeta?.hint_level ?? r.state.hint_level ?? 0));
    const srcs = [...r.messages].reverse().find((m: Message) => m.meta?.sources?.length)?.meta?.sources;
    if (srcs) setActiveSources(srcs);
    return r;
  }, [id]);

  useEffect(() => {
    autoSent.current = false;
    load()
      .then((r) => {
        if (r.session.course) setCurrentCourse(r.session.course);
        const q = new URLSearchParams(location.search).get('q');
        if (q && !r.messages.length && !autoSent.current) {
          autoSent.current = true;
          history.replaceState(null, '', location.pathname);
          send(q);
        }
      })
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // Live solver status for this problem.
  useEffect(
    () =>
      onEvent('solver', (d) => {
        setProblem((p) => (p && p.id === d.problem_id ? { ...p, solver_status: d.status, confidence: d.confidence ?? p.confidence } : p));
      }),
    [],
  );

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: streaming ? 'auto' : 'smooth' });
  }, [messages, streaming?.text]);

  // What "Start focus" / "switch to this problem" means here.
  useEffect(() => {
    if (!course) return setWorkspaceFocusLabel(null);
    const what = session?.title ? ` ${session.title}` : '';
    setWorkspaceFocusLabel(`office hours: ${course.name}${what}`.slice(0, 120));
    return () => setWorkspaceFocusLabel(null);
  }, [course, session?.title]);

  useEffect(() => localStorage.setItem('tutor.tab', tab), [tab]);

  // Leaving the page mid-reply stops the generation (so the model isn't left running for nobody).
  useEffect(() => () => abortRef.current?.abort(), []);

  // "Open in editor" on a code block in a reply switches to the Code tab.
  useEffect(() => {
    const open = () => setTab('code');
    window.addEventListener('tutor:open-code', open);
    return () => window.removeEventListener('tutor:open-code', open);
  }, []);

  const send = async (textArg?: string, action?: string) => {
    const text = (textArg ?? draft).trim();
    const ready = images.filter((i) => i.uploadId);
    if (!text && !ready.length && !action) return;
    if (streaming) return;
    setError(null);
    const act = action ?? (nextIsNewProblem ? 'new_problem' : undefined);
    const optimistic: Message = { id: -Date.now(), role: 'user', content: text || (action === 'unlock_now' ? 'Show me the full solution now.' : ''), images: ready.map((i) => i.uploadId!), meta: {}, created_at: Date.now() / 1000 };
    if (optimistic.content || optimistic.images.length) setMessages((m) => [...m, optimistic]);
    setDraft('');
    setImages([]);
    setNextIsNewProblem(false);
    setStreaming({ text: '' });
    const ctl = new AbortController();
    abortRef.current = ctl;
    let sources: Source[] = [];
    try {
      await postStream(
        `/sessions/${id}/messages`,
        { text, images: ready.map((i) => i.uploadId), action: act, code_context: codeContext ?? undefined },
        (ev) => {
          if (ev.type === 'meta') {
            if (typeof ev.hint_level === 'number') setHintLevel(ev.hint_level);
            sources = ev.sources || [];
            if (sources.length) setActiveSources(sources);
            setStreaming((s) => ({ ...(s || { text: '' }), sources }));
            if (ev.problem_id) setProblem((p) => (p && p.id === ev.problem_id ? { ...p, solver_status: ev.solver_status } : p));
          } else if (ev.type === 'status') setStreaming((s) => ({ ...(s || { text: '' }), status: ev.message }));
          else if (ev.type === 'token') setStreaming((s) => ({ ...(s || { text: '' }), text: (s?.text || '') + ev.text, status: undefined }));
          else if (ev.type === 'reset') setStreaming((s) => ({ ...(s || { text: '' }), text: '', note: ev.reason }));
          else if (ev.type === 'error') setError(ev.message);
        },
        ctl.signal,
      );
    } catch (e: any) {
      if (e.name !== 'AbortError') setError(e.message);
    } finally {
      abortRef.current = null;
      setCodeContext(null);
      setStreaming(null);
      load().catch(() => {});
    }
  };

  const addFiles = async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      if (!f.type.startsWith('image/')) continue;
      const pid = `${Date.now()}-${Math.random()}`;
      const url = URL.createObjectURL(f);
      setImages((xs) => [...xs, { id: pid, url }]);
      try {
        const uploadId = await api.upload(f, f.name || 'paste.png');
        setImages((xs) => xs.map((x) => (x.id === pid ? { ...x, uploadId } : x)));
      } catch (e: any) {
        setError(`Upload failed: ${e.message}`);
        setImages((xs) => xs.filter((x) => x.id !== pid));
      }
    }
  };

  const onPaste = (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData.files);
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  };

  const isOfficeHours = session?.mode === 'office_hours' || session?.mode === 'code';
  const calm = timer.active && timer.status === 'running' && timer.phase !== 'focus';
  const unlocked = state.solution_unlocked || state.solved;
  const hasProblem = !!problem;

  const placeholder = useMemo(() => {
    if (!session) return '';
    if (session.mode === 'ask') return `Ask about ${course?.name ?? 'anything'}…`;
    if (!hasProblem || nextIsNewProblem) return course?.is_writing ? 'What are you writing? Paste the prompt or your thesis…' : 'Paste a problem, or drop a screenshot of it…';
    return course?.is_writing ? 'Share your thinking or a draft paragraph…' : 'Your attempt, a question, or “I’m stuck”…';
  }, [session, course, hasProblem, nextIsNewProblem]);

  const sendCodeToTutor = (summary: string) => {
    setCodeContext(summary);
    setDraft((d) => d || 'Here is my code and what happened when I ran it.');
    inputRef.current?.focus();
  };

  if (error && !session) return <div className="page"><div className="error">{error}</div></div>;
  if (!session) return <div className="page loading">Loading…</div>;

  return (
    <div className={`workspace ${calm ? 'calm' : ''}`}>
      <section className="ws-left">
        <div className="ws-head">
          <div>
            <div className="crumbs">
              {course ? <Link to={`/course/${course.slug}`}>{course.name}</Link> : <span>General</span>}
              <span className="sep">/</span>
              <span>{MODE_LABEL[session.mode]}</span>
            </div>
            <input
              className="title-input"
              defaultValue={session.title ?? ''}
              placeholder={session.mode === 'ask' ? 'Question' : session.mode === 'quiz' ? 'Quiz' : 'Problem set / title'}
              onBlur={(e) => {
                if (e.target.value !== (session.title ?? '')) api.patch(`/sessions/${id}`, { title: e.target.value }).then((r) => setSession(r.session));
              }}
            />
          </div>
          {isOfficeHours && hasProblem && (
            <button className="btn ghost small" onClick={() => setNextIsNewProblem(true)} title="The next message starts a new problem">
              <Spark size={14} /> New problem
            </button>
          )}
        </div>

        {isOfficeHours && problem && (
          <div className="problem-card">
            <div className="problem-meta">
              <span className="label">Problem</span>
              <SolverBadge problem={problem} />
            </div>
            <Markdown text={problem.text} className="problem-text" />
            {!course?.is_writing && (
              <div className="ladder-row">
                <HintLadder level={hintLevel} solved={state.solved} />
                {!unlocked ? (
                  <button className="btn ghost small solution-btn" onClick={() => setAskUnlock(true)} title="Unlock the full worked solution">
                    <Lock size={14} /> Show me the solution
                  </button>
                ) : (
                  <span className="muted small">{state.solved ? 'Solved. Walk through it any way you like.' : 'Full solution unlocked'}</span>
                )}
              </div>
            )}
          </div>
        )}

        {session.mode === 'quiz' ? (
          <QuizPanel sessionId={id} course={course} onSource={(s) => { setActiveSources(s); setFocusSource(0); setTab('sources'); }} />
        ) : (<>
        <div className="chat" ref={listRef}>
          {!messages.length && !streaming && (
            <div className="empty-chat">
              {session.mode === 'ask' ? (
                <p>Ask anything about {course?.name ?? 'your courses'}. Answers come from your notes and textbooks first, with page citations.</p>
              ) : course?.is_writing ? (
                <p>Bring the essay prompt or your working thesis. I’ll ask questions and point at your sources. I won’t write it for you.</p>
              ) : (
                <p>Paste a problem (LaTeX is fine) or drop a screenshot. I’ll ask how you’d start before giving any hints, and I’ll prepare a private reference solution in the background while you think.</p>
              )}
            </div>
          )}
          {messages.filter((m, i) => !(problem && m.role === 'user' && i === messages.findIndex((x) => x.role === 'user') && m.content.trim() === problem.text.trim())).map((m) => (
            <MessageView key={m.id} m={m} onCite={(i) => { setActiveSources(m.meta?.sources || []); setFocusSource(i); setTab('sources'); }} cloud={settings?.cloud?.enabled} sessionId={id} onEscalated={load} />
          ))}
          {streaming && (
            <div className="msg assistant">
              {streaming.note && <div className="rephrase">{streaming.note}</div>}
              {streaming.status && <div className="muted small">{streaming.status}</div>}
              {streaming.text ? <Markdown text={streaming.text} sources={streaming.sources} /> : <div className="typing"><i /><i /><i /></div>}
            </div>
          )}
        </div>

        <div className={`composer ${nextIsNewProblem ? 'new-problem' : ''}`} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }}>
          {nextIsNewProblem && (
            <div className="composer-flag">
              New problem <button className="icon-btn" onClick={() => setNextIsNewProblem(false)} aria-label="Cancel"><X size={12} /></button>
            </div>
          )}
          {codeContext && (
            <div className="composer-flag">
              <Code size={13} /> Code and run result attached <button className="icon-btn" onClick={() => setCodeContext(null)} aria-label="Remove"><X size={12} /></button>
            </div>
          )}
          {!!images.length && (
            <div className="thumbs">
              {images.map((im) => (
                <div key={im.id} className={`thumb ${im.uploadId ? '' : 'uploading'}`}>
                  <img src={im.url} alt="" />
                  <button className="icon-btn" onClick={() => setImages((xs) => xs.filter((x) => x.id !== im.id))} aria-label="Remove image"><X size={12} /></button>
                </div>
              ))}
            </div>
          )}
          <textarea
            ref={inputRef}
            value={draft}
            rows={Math.min(8, Math.max(2, draft.split('\n').length))}
            placeholder={placeholder}
            onChange={(e) => setDraft(e.target.value)}
            onPaste={onPaste}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="composer-bar">
            <label className="icon-btn" title="Attach a photo or screenshot">
              <ImageIcon size={17} />
              <input type="file" accept="image/*" multiple hidden onChange={(e) => e.target.files && addFiles(e.target.files)} />
            </label>
            <span className="muted tiny">Enter to send · Shift+Enter for a new line · paste images</span>
            {streaming ? (
              <button className="btn small" onClick={() => abortRef.current?.abort()}>Stop</button>
            ) : (
              <button className="btn primary small" onClick={() => send()} disabled={!draft.trim() && !images.some((i) => i.uploadId)}>
                <Send size={14} /> Send
              </button>
            )}
          </div>
          {error && <div className="error small">{error}</div>}
        </div>
        </>)}
      </section>

      <aside className="ws-right">
        <div className="tabs">
          <button className={tab === 'scratch' ? 'active' : ''} onClick={() => setTab('scratch')}><Quill size={15} /> Scratchpad</button>
          <button className={tab === 'code' ? 'active' : ''} onClick={() => setTab('code')}><Code size={15} /> Code</button>
          <button className={tab === 'sources' ? 'active' : ''} onClick={() => setTab('sources')}><Pages size={15} /> Sources{activeSources.length ? <span className="count">{activeSources.length}</span> : null}</button>
        </div>
        <div className="tab-body">
          {tab === 'scratch' && <Scratchpad sessionId={id} onSendPhoto={addFiles} />}
          {tab === 'code' && <CodePanel sessionId={id} course={course} onAskTutor={sendCodeToTutor} />}
          {tab === 'sources' && <SourcesPanel sources={activeSources} focus={focusSource} />}
        </div>
      </aside>

      <Modal open={askUnlock} onClose={() => setAskUnlock(false)} title="See the full solution?">
        <p className="modal-text">You can have the worked solution now, or take one more shot first. Trying once more is usually where it clicks.</p>
        <div className="modal-actions">
          <button className="btn" onClick={() => { setAskUnlock(false); send('', 'unlock_after'); }}>One more attempt first</button>
          <button className="btn primary" onClick={() => { setAskUnlock(false); send('', 'unlock_now'); }}>Show it now</button>
        </div>
      </Modal>
    </div>
  );
}

function SolverBadge({ problem }: { problem: Problem }) {
  const s = problem.solver_status;
  if (s === 'skipped') return null;
  if (s === 'pending' || s === 'running' || s === 'paused')
    return (
      <span className="solver-badge preparing" title="A private reference solution is being prepared and checked. You won't see it unless you unlock the solution.">
        <span className="pulse" /> {s === 'paused' ? 'reference paused while we talk' : 'preparing a reference'}
      </span>
    );
  if (s === 'failed') return <span className="solver-badge uncertain" title="The hidden solver couldn't produce a reference. I'll check your work step by step instead.">no reference</span>;
  const c = problem.confidence;
  const tip = c === 'verified' ? 'Reference checked with SymPy / units / tests' : c === 'agreed' ? 'Independent solver runs agree' : 'Not fully sure: we’ll verify steps together';
  return (
    <span className={`solver-badge ${c}`} title={tip}>
      {c === 'verified' ? <Check size={12} /> : null} reference {c}
    </span>
  );
}

function MessageView({ m, onCite, cloud, sessionId, onEscalated }: { m: Message; onCite: (i: number) => void; cloud?: boolean; sessionId: number; onEscalated: () => void }) {
  const [busy, setBusy] = useState(false);
  if (m.role === 'user') {
    return (
      <div className="msg user">
        {!!m.images?.length && (
          <div className="msg-images">
            {m.images.map((u) => (
              <a key={u} href={`/api/uploads/${u}`} target="_blank" rel="noreferrer"><img src={`/api/uploads/${u}`} alt="attached" /></a>
            ))}
          </div>
        )}
        {m.content && <Markdown text={m.content} />}
      </div>
    );
  }
  const meta = m.meta || {};
  const escalate = async () => {
    setBusy(true);
    try {
      let failed: string | null = null;
      await postStream(`/sessions/${sessionId}/escalate`, { message_id: m.id }, (ev) => {
        if (ev.type === 'error') failed = ev.message;
      });
      if (failed) alert(`Escalation failed: ${failed}`);
      onEscalated();
    } catch (e: any) {
      alert(`Escalation failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`msg assistant ${meta.provider === 'anthropic' ? 'cloud' : ''}`}>
      {meta.provider === 'anthropic' && <div className="cloud-note"><Cloud size={13} /> answered by {meta.model || 'the cloud model'} (this left your Mac)</div>}
      <Markdown text={m.content} sources={meta.sources} onCite={onCite} codeEditor />
      <div className="msg-foot">
        {typeof meta.hint_level === 'number' && <span title="Hint level for this reply">hint {meta.hint_level}</span>}
        {meta.stats?.tokens_per_s && <span title={`first token ${meta.stats.ttft_s}s · ${meta.stats.total_s}s total`}>{meta.stats.model} · {meta.stats.tokens_per_s} tok/s</span>}
        {meta.leak_retries ? <span title="The first draft revealed too much and was rewritten">rephrased</span> : null}
        {cloud && meta.provider !== 'anthropic' && (
          <button className="link-btn" disabled={busy} onClick={escalate} title="Ask the stronger cloud model to redo this reply (sends this conversation to Anthropic)">
            <Cloud size={12} /> {busy ? 'asking…' : 'escalate'}
          </button>
        )}
      </div>
    </div>
  );
}

function Scratchpad({ sessionId, onSendPhoto }: { sessionId: number; onSendPhoto: (f: File[]) => void }) {
  const key = `tutor.scratch.${sessionId}`;
  const [text, setText] = useState(() => localStorage.getItem(key) || '');
  const [preview, setPreview] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => localStorage.setItem(key, text), 300);
    return () => clearTimeout(t);
  }, [text, key]);
  return (
    <div className="scratch">
      <div className="scratch-bar">
        <label className="btn small" title="Attach a photo of handwritten work to your next message">
          <ImageIcon size={14} /> Photo of my work
          <input type="file" accept="image/*" capture="environment" hidden onChange={(e) => e.target.files && onSendPhoto(Array.from(e.target.files))} />
        </label>
        <span className="grow" />
        {text.trim() && <button className="btn small ghost" onClick={() => setPreview((p) => !p)}>{preview ? 'Hide preview' : 'Show preview'}</button>}
      </div>
      <textarea className="scratch-input" value={text} onChange={(e) => setText(e.target.value)} placeholder={'E.g.  $\\oint \\vec E \\cdot d\\vec A = Q_{enc}/\\varepsilon_0$'} />
      {preview && text.trim() && <Markdown text={text} className="scratch-preview" />}
      <p className="muted tiny">Markdown + LaTeX, saved on this Mac only. The tutor doesn't see it unless you paste it into the chat.</p>
    </div>
  );
}
