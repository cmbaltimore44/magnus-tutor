import { useEffect, useState } from 'react';
import { CommandPalette } from './components/CommandPalette';
import { Header } from './components/Header';
import { CoursePage } from './pages/CoursePage';
import { Home } from './pages/Home';
import { Library } from './pages/Library';
import { NewCourse } from './pages/NewCourse';
import { Prompts } from './pages/Prompts';
import { applyAppearance, Settings } from './pages/Settings';
import { Workspace } from './pages/Workspace';
import { match, usePath } from './router';
import { AppProvider, useApp } from './state';

function Routes() {
  const path = usePath();
  let m;
  if ((m = match('/session/:id', path))) return <Workspace key={m.id} id={Number(m.id)} />;
  if ((m = match('/course/:slug', path))) return <CoursePage key={m.slug} slug={m.slug} />;
  if (path === '/courses/new') return <NewCourse />;
  if (path.startsWith('/library')) return <Library />;
  if (path.startsWith('/prompts')) return <Prompts />;
  if (path.startsWith('/settings')) return <Settings />;
  return <Home />;
}

function Shell() {
  const [palette, setPalette] = useState(false);
  const { connected } = useApp();
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener('keydown', k);
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const onScheme = () => applyAppearance(localStorage.getItem('tutor.palette') || 'heather', localStorage.getItem('tutor.mode') || 'auto');
    mq.addEventListener('change', onScheme);
    return () => {
      window.removeEventListener('keydown', k);
      mq.removeEventListener('change', onScheme);
    };
  }, []);
  return (
    <>
      <Header onAsk={() => setPalette(true)} />
      {!connected && <div className="offline-bar">The tutor backend isn’t running. Start it with <code>tutor start</code> (or <code>magnus tutor</code>).</div>}
      <main>
        <Routes />
      </main>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </>
  );
}

export function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
