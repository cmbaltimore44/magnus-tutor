// Code tab: editor, sandboxed Run, test cases, and "ask the tutor about this run".
// Real execution results are the ground truth the tutor sees.
import { java } from '@codemirror/lang-java';
import { python } from '@codemirror/lang-python';
import { yaml } from '@codemirror/lang-yaml';
import { StreamLanguage } from '@codemirror/language';
import { dockerFile } from '@codemirror/legacy-modes/mode/dockerfile';
import { oCaml } from '@codemirror/legacy-modes/mode/mllike';
import { ruby } from '@codemirror/legacy-modes/mode/ruby';
import CodeMirror from '@uiw/react-codemirror';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { Course } from '../types';
import { Play, Plus, X } from './Icons';

type Lang = { key: string; highlight: string; mode: string; ok: boolean; filename?: string; extensions: string[] };
type Test = { name: string; stdin: string; expected: string };

const HIGHLIGHT: Record<string, () => any> = {
  python: () => python(),
  java: () => java(),
  yaml: () => yaml(),
  sml: () => StreamLanguage.define(oCaml),
  ruby: () => StreamLanguage.define(ruby),
  dockerfile: () => StreamLanguage.define(dockerFile),
};

export function CodePanel({ sessionId, course, onAskTutor }: { sessionId: number; course?: Course; onAskTutor: (summary: string) => void }) {
  const [langs, setLangs] = useState<Lang[]>([]);
  const key = `tutor.code.${sessionId}`;
  const saved = useMemo(() => {
    try {
      return JSON.parse(localStorage.getItem(key) || '{}');
    } catch {
      return {};
    }
  }, [key]);
  const [lang, setLang] = useState<string>(saved.lang || course?.languages?.[0] || 'python');
  const [code, setCode] = useState<string>(saved.code || '');
  const [stdin, setStdin] = useState<string>(saved.stdin || '');
  const [tests, setTests] = useState<Test[]>(saved.tests || []);
  const [result, setResult] = useState<any>(null);
  const [running, setRunning] = useState(false);
  const [dark, setDark] = useState(document.documentElement.dataset.mode === 'dark');

  useEffect(() => {
    api.get<Lang[]>('/languages').then(setLangs).catch(() => {});
    const obs = new MutationObserver(() => setDark(document.documentElement.dataset.mode === 'dark'));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-mode'] });
    return () => obs.disconnect();
  }, []);
  useEffect(() => {
    const t = setTimeout(() => localStorage.setItem(key, JSON.stringify({ lang, code, stdin, tests })), 300);
    return () => clearTimeout(t);
  }, [key, lang, code, stdin, tests]);

  const spec = langs.find((l) => l.key === lang);
  const ext = useMemo(() => {
    const h = spec?.highlight || lang;
    return HIGHLIGHT[h] ? [HIGHLIGHT[h]()] : [];
  }, [spec, lang]);
  const isCheck = spec?.mode === 'check';
  const filename = spec?.filename || `main${spec?.extensions?.[0] || '.txt'}`;

  const run = async () => {
    setRunning(true);
    setResult(null);
    try {
      setResult(await api.post('/code/run', { language: lang, files: { [filename]: code }, stdin, tests: tests.filter((t) => t.expected || t.stdin) }));
    } catch (e: any) {
      setResult({ stderr: e.message, exit_code: -1 });
    } finally {
      setRunning(false);
    }
  };

  const summary = () => {
    const lines = [`[Student's ${lang} code]`, '```' + (spec?.highlight || lang), code, '```'];
    if (result?.summary) lines.push(result.summary);
    return lines.join('\n');
  };

  const ordered = [...langs].sort((a, b) => (course?.languages?.includes(b.key) ? 1 : 0) - (course?.languages?.includes(a.key) ? 1 : 0));

  return (
    <div className="code-panel">
      <div className="code-bar">
        <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Language">
          {(ordered.length ? ordered : [{ key: lang } as Lang]).map((l) => (
            <option key={l.key} value={l.key} disabled={l.ok === false}>
              {l.key}{l.ok === false ? ' (not installed)' : ''}{l.mode === 'check' ? ' · check' : ''}
            </option>
          ))}
        </select>
        <span className="muted tiny">{filename} · sandboxed: no network, time and memory capped</span>
        <button className="btn primary small" onClick={run} disabled={running || !code.trim()}>
          <Play size={13} /> {running ? 'Running…' : isCheck ? 'Check' : 'Run'}
        </button>
      </div>
      <div className="editor">
        <CodeMirror value={code} height="100%" theme={dark ? 'dark' : 'light'} extensions={ext} onChange={setCode} basicSetup={{ foldGutter: false, highlightActiveLine: true }} placeholder={`Write or paste your ${lang} here…`} />
      </div>
      {!isCheck && (
        <details className="io">
          <summary>Input and tests {tests.length ? `(${tests.length})` : ''}</summary>
          <label className="small muted">stdin for Run</label>
          <textarea value={stdin} onChange={(e) => setStdin(e.target.value)} rows={2} />
          {tests.map((t, i) => (
            <div className="test-row" key={i}>
              <input value={t.name} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} placeholder="name" />
              <textarea value={t.stdin} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, stdin: e.target.value } : x)))} placeholder="stdin" rows={1} />
              <textarea value={t.expected} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, expected: e.target.value } : x)))} placeholder="expected output" rows={1} />
              <button className="icon-btn" onClick={() => setTests(tests.filter((_, j) => j !== i))} aria-label="Remove test"><X size={12} /></button>
            </div>
          ))}
          <button className="btn small ghost" onClick={() => setTests([...tests, { name: `test ${tests.length + 1}`, stdin: '', expected: '' }])}><Plus size={12} /> Add test</button>
        </details>
      )}
      {result && (
        <div className={`run-result ${result.ok ? 'ok' : 'bad'}`}>
          <div className="run-head">
            <span>{result.killed ? `stopped: ${result.killed}` : `exit ${result.exit_code}`}</span>
            {result.duration_s != null && <span className="muted small">{result.duration_s}s</span>}
            <button className="btn small" onClick={() => onAskTutor(summary())}>Ask the tutor about this</button>
          </div>
          {!!result.tests?.length && (
            <ul className="test-results">
              {result.tests.map((t: any) => (
                <li key={t.name} className={t.passed ? 'pass' : 'fail'}>
                  {t.passed ? '✓' : '✗'} {t.name}
                  {!t.passed && <span className="muted small"> expected {JSON.stringify(t.expected)}, got {JSON.stringify(t.actual)}</span>}
                </li>
              ))}
            </ul>
          )}
          {result.stdout && <pre className="out">{result.stdout}</pre>}
          {result.stderr && <pre className="err">{result.stderr}</pre>}
        </div>
      )}
      {!result && code.trim() && (
        <button className="link-btn small" onClick={() => onAskTutor(summary())}>Ask the tutor about this code without running it</button>
      )}
    </div>
  );
}
