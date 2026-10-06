import { useEffect, useState } from 'react';
import { api, postStream } from '../api';
import { Cloud, Refresh, X } from '../components/Icons';
import { Modal } from '../components/Modal';
import { PALETTES } from '../palettes';
import { useApp } from '../state';

const ROLES = ['tutor', 'solver', 'coder', 'vision', 'embedding'] as const;
const GATES: [string, string][] = [
  ['attempt_gate', 'Attempt gate: the first reply to a new problem asks for your attempt'],
  ['hint_ladder', 'Hint ladder: at most one hint level per stuck turn'],
  ['solution_gate', 'Full-solution gate: only on explicit request, after asking “now or after one more try?”'],
  ['output_check', 'Output check: rewrite replies that leak the answer before you unlock it'],
];

export function applyAppearance(palette: string, mode: string) {
  localStorage.setItem('tutor.palette', palette);
  localStorage.setItem('tutor.mode', mode);
  const dark = mode === 'dark' || (mode === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.palette = palette;
  if (dark) document.documentElement.dataset.mode = 'dark';
  else delete document.documentElement.dataset.mode;
}

export function Settings() {
  const { settings, reloadSettings, resources, refreshResources } = useApp();
  const [models, setModels] = useState<any>(null);
  const [hw, setHw] = useState<any>(null);
  const [pull, setPull] = useState<{ name: string; size?: number | null } | null>(null);
  const [pullProgress, setPullProgress] = useState<string>('');
  const [palette, setPalette] = useState(localStorage.getItem('tutor.palette') || 'heather');
  const [mode, setMode] = useState(localStorage.getItem('tutor.mode') || 'auto');
  const [saved, setSaved] = useState<string | null>(null);

  const loadModels = () => api.get('/models').then(setModels).catch(() => {});
  useEffect(() => {
    loadModels();
    api.get('/hardware').then(setHw).catch(() => {});
    refreshResources();
    const id = setInterval(refreshResources, 5000);
    return () => clearInterval(id);
  }, [refreshResources]);

  useEffect(() => {
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, []);

  const patch = async (changes: any, note = 'Saved') => {
    await api.patch('/settings', changes);
    await reloadSettings();
    setSaved(note);
    setTimeout(() => setSaved(null), 1600);
  };

  if (!settings) return <div className="page loading">Loading…</div>;
  const installed: any[] = models?.installed || [];

  const startPull = async (name: string) => {
    const r = await api.post('/models/size', { name }).catch(() => ({ size: null }));
    setPull({ name, size: r.size });
  };
  const confirmPull = async () => {
    if (!pull) return;
    const name = pull.name;
    setPull(null);
    setPullProgress(`Pulling ${name}…`);
    await postStream('/models/pull', { name }, (ev) => {
      if (ev.total && ev.completed) setPullProgress(`Pulling ${name}: ${Math.round((100 * ev.completed) / ev.total)}%`);
      else if (ev.status) setPullProgress(`${name}: ${ev.status}`);
    }).catch((e) => setPullProgress(`Pull failed: ${e.message}`));
    setPullProgress('');
    loadModels();
  };

  return (
    <div className="page settings">
      <h1>Settings {saved && <span className="saved">{saved}</span>}</h1>

      <section className="card" id="resources">
        <h2>What the tutor is using</h2>
        <div className="res-grid">
          <div><div className="muted small">Loaded model</div><div>{resources?.loaded_models?.length ? resources.loaded_models.map((m: any) => `${m.name} (${(m.size / 2 ** 30).toFixed(1)} GB)`).join(', ') : 'none'}</div></div>
          <div><div className="muted small">Memory available</div><div>{resources?.memory?.available_gb ?? '–'} of {resources?.memory?.total_gb ?? '–'} GB</div></div>
          <div><div className="muted small">Backend</div><div>{resources?.backend_mb ?? '–'} MB</div></div>
          <div><div className="muted small">Background jobs</div><div>{resources?.jobs?.length ? resources.jobs.map((j: any) => `${j.kind} ${j.status}`).join(', ') : 'none'}</div></div>
        </div>
        {resources?.warnings?.map((w: string, i: number) => <div key={i} className="warn small">{w}</div>)}
        <div className="row">
          <button className="btn" onClick={() => api.post('/models/unload').then(refreshResources)}>Unload models now</button>
          <label className="toggle">
            <input type="checkbox" checked={!!settings.background?.paused} onChange={(e) => patch({ background: { paused: e.target.checked } })} />
            Pause all background work
          </label>
          <label className="toggle">
            <input type="checkbox" checked={!!settings.background?.only_when_plugged_in} onChange={(e) => patch({ background: { only_when_plugged_in: e.target.checked } })} />
            Heavy jobs only when plugged in
          </label>
        </div>
        <p className="muted tiny">Models unload after {settings.ollama.keep_alive} idle. The backend stops itself after {settings.server.idle_shutdown_minutes} minutes without use; <code>tutor stop</code> stops everything now; <code>tutor doctor</code> proves nothing is left running.</p>
      </section>

      <section className="card" id="appearance">
        <h2>Appearance</h2>
        <div className="row">
          {PALETTES.map((p) => (
            <button key={p.key} className={`swatch-btn ${palette === p.key ? 'active' : ''}`} data-palette={p.key} onClick={() => { setPalette(p.key); applyAppearance(p.key, mode); patch({ appearance: { theme: p.key } }); }}>
              <span className="swatch" data-palette={p.key} />
              {p.label}
            </button>
          ))}
        </div>
        <div className="row">
          {['auto', 'light', 'dark'].map((m) => (
            <label key={m} className="radio"><input type="radio" checked={mode === m} onChange={() => { setMode(m); applyAppearance(palette, m); patch({ appearance: { mode: m } }); }} /> {m === 'auto' ? 'Follow macOS' : m}</label>
          ))}
        </div>
        <p className="muted tiny">The same four theme families as Magnus (Heather, Lakeglow, Beacon, Hearth).</p>
      </section>

      <section className="card" id="models">
        <h2>Models</h2>
        {hw && (
          <p className="small">
            {hw.hardware.chip} · {hw.hardware.ram_gb} GB · recommended preset <b>{hw.recommendation.preset}</b> (models up to {hw.recommendation.budget_gb} GB, half your RAM).
          </p>
        )}
        <div className="row">
          {['light', 'standard', 'full'].map((p) => (
            <label key={p} className="radio" title={PRESET_HELP[p]}>
              <input type="radio" checked={settings.preset === p} onChange={() => patch(presetChanges(p, settings), `Preset: ${p}`)} /> {p}
            </label>
          ))}
        </div>
        <table className="roles">
          <tbody>
            {ROLES.map((r) => (
              <tr key={r}>
                <td>{r}</td>
                <td>
                  <select value={settings.models[r]} onChange={(e) => patch({ models: { [r]: e.target.value } })}>
                    {[...new Set([settings.models[r], ...installed.map((m) => m.name)])].map((n) => (
                      <option key={n} value={n}>{n}{installed.some((m) => m.name === n) ? '' : ' (not installed)'}</option>
                    ))}
                  </select>
                </td>
                <td className="muted small">{ROLE_HELP[r]}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3>Installed <span className="muted small">{models ? `${(installed.reduce((a, m) => a + m.size, 0) / 2 ** 30).toFixed(1)} GB on disk` : ''}</span></h3>
        <ul className="model-list">
          {installed.map((m) => (
            <li key={m.name}>
              <span>{m.name}</span>
              <span className="muted small">{(m.size / 2 ** 30).toFixed(1)} GB</span>
              <span className="muted small">{Object.entries(settings.models).filter(([, v]) => v === m.name).map(([k]) => k).join(', ') || 'unused'}</span>
              {!Object.values(settings.models).includes(m.name) && (
                <button className="icon-btn" title="Delete this model from disk" onClick={() => confirm(`Delete ${m.name} (${(m.size / 2 ** 30).toFixed(1)} GB)?`) && api.del(`/models/${encodeURIComponent(m.name)}`).then(loadModels)}><X size={13} /></button>
              )}
            </li>
          ))}
        </ul>
        <div className="row">
          <input placeholder="model tag, e.g. qwen3.5:9b" id="pull-name" />
          <button className="btn" onClick={() => { const v = (document.getElementById('pull-name') as HTMLInputElement).value.trim(); if (v) startPull(v); }}>Pull…</button>
          {pullProgress && <span className="muted small">{pullProgress}</span>}
        </div>
        {hw?.recommendation?.experimental?.length ? <p className="warn tiny">Experimental (opt-in): {hw.recommendation.experimental.join(', ')}. Bigger models cause memory pressure and slower replies on this Mac.</p> : null}
      </section>

      <section className="card" id="gates">
        <h2>Office hours behavior</h2>
        {GATES.map(([k, label]) => (
          <label key={k} className="toggle block">
            <input type="checkbox" checked={!!settings.gates[k]} onChange={(e) => patch({ gates: { [k]: e.target.checked } })} /> {label}
          </label>
        ))}
        <label className="toggle block">
          <input type="checkbox" checked={!!settings.solver.enabled} onChange={(e) => patch({ solver: { enabled: e.target.checked } })} /> Hidden solver: prepare a checked reference solution in the background
        </label>
        <div className="row">
          <label>Solver thinking budget <input type="number" min={500} max={8000} step={500} value={settings.solver.thinking_budget} onChange={(e) => patch({ solver: { thinking_budget: Number(e.target.value) } })} /> tokens</label>
          <label>Runs per problem <input type="number" min={1} max={3} value={settings.solver.self_consistency} onChange={(e) => patch({ solver: { self_consistency: Number(e.target.value) } })} /></label>
        </div>
        <label className="toggle block">
          <input type="checkbox" checked={!!settings.solver.run_reference_code} onChange={(e) => patch({ solver: { run_reference_code: e.target.checked } })} /> Run the solver's reference code in the sandbox to verify code problems (off by default)
        </label>
        <p className="muted tiny">The engine enforces these in code, so editing a prompt never fights hidden behavior. Prompts themselves are on the <a href="/prompts">Prompts</a> page.</p>
      </section>

      <section className="card" id="timer">
        <h2>Focus timer</h2>
        <p className="small">Magnus owns the timer; the tutor shows it and controls it with <code>magnus timer</code>.</p>
        <div className="row">
          <span className="small">When a phase ends, alert in:</span>
          {['auto', 'terminal', 'web', 'both'].map((a) => (
            <label key={a} className="radio"><input type="radio" checked={settings.alerts === a} onChange={() => patch({ alerts: a })} /> {a}</label>
          ))}
        </div>
        <p className="muted tiny">Auto: the terminal rings if Magnus is open, otherwise this page chimes and posts a notification. The page always shows a banner.</p>
      </section>

      <CloudSection settings={settings} patch={patch} />
      <ProfileSection />

      <Modal open={!!pull} onClose={() => setPull(null)} title={`Download ${pull?.name}?`}>
        <p className="modal-text">This model is {pull?.size ? `${(pull.size / 2 ** 30).toFixed(1)} GB` : 'an unknown size'} on disk. It downloads once and is stored by Ollama in ~/.ollama/models.</p>
        <div className="modal-actions">
          <button className="btn" onClick={() => setPull(null)}>Cancel</button>
          <button className="btn primary" onClick={confirmPull}>Download</button>
        </div>
      </Modal>
    </div>
  );
}

const PRESET_HELP: Record<string, string> = {
  light: 'One small model for every role; the hidden solver runs only when you ask; no self-consistency.',
  standard: 'One mid-size model for tutor and solver; vision only during ingestion.',
  full: 'Separate solver and coder models, self-consistency on (needs lots of RAM).',
};
const ROLE_HELP: Record<string, string> = {
  tutor: 'conversation and hints (thinking off, fast)',
  solver: 'the hidden reference pass (thinking on)',
  coder: 'code turns',
  vision: 'handwriting and photos; loaded only when needed',
  embedding: 'retrieval; small, may stay loaded',
};

function presetChanges(preset: string, s: any) {
  if (preset === 'light') return { preset, solver: { enabled: false, self_consistency: 1, extra_run_if_unverified: false } };
  if (preset === 'full') return { preset, solver: { enabled: true, self_consistency: 3 } };
  return { preset, solver: { enabled: true, self_consistency: 1, extra_run_if_unverified: true }, models: s.models };
}

function CloudSection({ settings, patch }: { settings: any; patch: (c: any, n?: string) => Promise<void> }) {
  const [key, setKey] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const c = settings.cloud;
  const saveKey = async () => {
    try {
      await api.post('/cloud/key', { key });
      setKey('');
      setMsg('Key saved in the macOS Keychain.');
      patch({});
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  return (
    <section className="card" id="cloud">
      <h2><Cloud size={16} /> Optional cloud model</h2>
      <p className="small">Off by default. When on, an “escalate” button appears on replies; only what you escalate is sent to Anthropic, and those replies are marked. Nothing else ever leaves this Mac.</p>
      <div className="row">
        <span className="small">API key: {c.key_present ? 'saved in Keychain' : 'none'}</span>
        <input type="password" placeholder="sk-ant-…" value={key} onChange={(e) => setKey(e.target.value)} />
        <button className="btn" disabled={!key} onClick={saveKey}>Save key</button>
        {c.key_present && <button className="btn ghost" onClick={() => api.del('/cloud/key').then(() => patch({ cloud: { enabled: false } }))}>Remove key</button>}
      </div>
      <div className="row">
        <label className="toggle"><input type="checkbox" checked={!!c.enabled} disabled={!c.key_present} onChange={(e) => patch({ cloud: { enabled: e.target.checked } })} /> Enable escalation</label>
        <label>Model <input value={c.model} onChange={(e) => patch({ cloud: { model: e.target.value } })} /></label>
      </div>
      {msg && <p className="small">{msg}</p>}
    </section>
  );
}

function ProfileSection() {
  const [p, setP] = useState<any>(null);
  const [text, setText] = useState('');
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    api.get('/profile').then((r) => {
      setP(r);
      setText(toYamlish(r.profile));
    }).catch(() => {});
  }, []);
  const save = async () => {
    const obj: any = {};
    let cur: string | null = null;
    for (const line of text.split('\n')) {
      const m = line.match(/^([a-z_]+):\s*(.*)$/);
      if (m) {
        cur = m[1];
        obj[cur] = m[2] || '';
        if (cur === 'learning_preferences') obj[cur] = [];
      } else if (cur === 'learning_preferences' && line.trim().startsWith('-')) obj[cur].push(line.trim().slice(1).trim());
      else if (cur && line.trim()) obj[cur] = `${obj[cur]}\n${line.trim()}`.trim();
    }
    await api.put('/profile', obj);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };
  return (
    <section className="card" id="profile">
      <h2>About you {saved && <span className="saved">Saved</span>}</h2>
      <p className="small muted">The tutor reads this before every conversation. Also a file: {p?.path}</p>
      <textarea className="profile-edit" rows={10} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row">
        <button className="btn primary" onClick={save}>Save</button>
        <button className="btn ghost" onClick={() => api.post('/profile/resume').then((r) => { setText(toYamlish(r.profile)); }).catch((e) => alert(e.message))} title="Read resume.pdf from the config folder and summarize it into your profile">
          <Refresh size={14} /> Read my resume
        </button>
      </div>
    </section>
  );
}

function toYamlish(p: any): string {
  const keys = ['name', 'school', 'program', 'year', 'background', 'learning_preferences', 'resume', 'resume_summary'];
  return keys
    .map((k) => {
      const v = p?.[k];
      if (k === 'learning_preferences') return `${k}:\n${(Array.isArray(v) ? v : []).map((x: string) => `  - ${x}`).join('\n')}`;
      return `${k}: ${v ?? ''}`;
    })
    .join('\n');
}
