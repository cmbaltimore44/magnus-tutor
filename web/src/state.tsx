// App-wide state: settings, courses, the Magnus focus timer, resources, and
// the backend connection. Everything else is loaded per page.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api, onConnection, onEvent } from './api';
import type { Course, Timer } from './types';

type AppState = {
  settings: any | null;
  courses: Course[];
  timer: Timer;
  connected: boolean;
  currentCourse: string | null;
  setCurrentCourse: (slug: string | null) => void;
  reloadCourses: () => Promise<void>;
  reloadSettings: () => Promise<void>;
  courseBySlug: (slug: string | null | undefined) => Course | undefined;
  resources: any | null;
  refreshResources: () => void;
};

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettings] = useState<any | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [timer, setTimer] = useState<Timer>({ active: false });
  const [connected, setConnected] = useState(false);
  const [resources, setResources] = useState<any | null>(null);
  const [currentCourse, setCurrent] = useState<string | null>(() => localStorage.getItem('tutor.course'));

  const reloadCourses = useCallback(async () => setCourses(await api.get<Course[]>('/courses')), []);
  const reloadSettings = useCallback(async () => setSettings(await api.get('/settings')), []);
  const refreshResources = useCallback(() => {
    api.get('/resources').then(setResources).catch(() => {});
  }, []);

  useEffect(() => {
    reloadCourses().catch(() => {});
    reloadSettings().catch(() => {});
    refreshResources();
    api.get<Timer>('/timer').then(setTimer).catch(() => {});
    const offs = [
      onEvent('timer', (t) => setTimer(t)),
      onEvent('settings', () => reloadSettings().catch(() => {})),
      onEvent('job', () => refreshResources()),
      onConnection((c) => {
        setConnected(c);
        if (c) {
          reloadCourses().catch(() => {});
          api.get<Timer>('/timer').then(setTimer).catch(() => {});
        }
      }),
    ];
    return () => offs.forEach((f) => f());
  }, [reloadCourses, reloadSettings, refreshResources]);

  // A visible tab counts as activity, so the backend doesn't idle-stop under you.
  useEffect(() => {
    const beat = () => {
      if (document.visibilityState === 'visible') api.post('/heartbeat').catch(() => {});
    };
    beat();
    const id = setInterval(beat, 60_000);
    document.addEventListener('visibilitychange', beat);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', beat);
    };
  }, []);

  const setCurrentCourse = useCallback((slug: string | null) => {
    setCurrent(slug);
    if (slug) localStorage.setItem('tutor.course', slug);
    else localStorage.removeItem('tutor.course');
  }, []);

  const value = useMemo<AppState>(
    () => ({
      settings,
      courses,
      timer,
      connected,
      currentCourse,
      setCurrentCourse,
      reloadCourses,
      reloadSettings,
      courseBySlug: (slug) => courses.find((c) => c.slug === slug),
      resources,
      refreshResources,
    }),
    [settings, courses, timer, connected, currentCourse, setCurrentCourse, reloadCourses, reloadSettings, resources, refreshResources],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApp outside AppProvider');
  return v;
}

/** Re-render every `ms` (for live clocks). */
export function useTick(ms: number, enabled = true): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => setN((x) => x + 1), ms);
    return () => clearInterval(id);
  }, [ms, enabled]);
  return n;
}
