import { useApp } from '../state';
import { Link, navigate, usePath } from '../router';
import { Cloud, Feather, Search } from './Icons';
import { TimerPill } from './TimerPill';

export function Header({ onAsk }: { onAsk: () => void }) {
  const { courses, currentCourse, setCurrentCourse, connected, resources, settings } = useApp();
  const path = usePath();
  const nav = [
    ['/', 'Home'],
    ['/library', 'Library'],
    ['/prompts', 'Prompts'],
    ['/settings', 'Settings'],
  ];
  const cloud = settings?.cloud?.enabled;
  return (
    <header className="topbar">
      <Link to="/" className="brand" title="Magnus Tutor">
        <Feather size={18} />
        <span>Magnus <em>Tutor</em></span>
      </Link>
      <nav className="nav">
        {nav.map(([to, label]) => (
          <Link key={to} to={to} className={(to === '/' ? path === '/' : path.startsWith(to)) ? 'active' : ''}>
            {label}
          </Link>
        ))}
      </nav>
      <div className="topbar-mid">
        <select
          className="course-select"
          value={currentCourse ?? ''}
          onChange={(e) => {
            const v = e.target.value || null;
            setCurrentCourse(v);
            if (v) navigate(`/course/${v}`);
          }}
          aria-label="Current course"
        >
          <option value="">All courses</option>
          {courses.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name}
            </option>
          ))}
        </select>
        <button className="ask-btn" onClick={onAsk} title="Ask a question (⌘K)">
          <Search size={15} />
          <span>Ask anything</span>
          <kbd>⌘K</kbd>
        </button>
      </div>
      <div className="topbar-right">
        <TimerPill />
        {cloud && (
          <span className="cloud-flag" title="The cloud provider is enabled. Messages you escalate leave this Mac.">
            <Cloud size={15} /> cloud on
          </span>
        )}
        <span className={`conn ${connected ? 'ok' : 'down'}`} title={connected ? `Local · ${resources?.loaded_models?.map((m: any) => m.name).join(', ') || 'no model loaded'}` : 'Backend not reachable: run `tutor start`'} />
      </div>
    </header>
  );
}
