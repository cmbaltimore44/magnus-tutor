// A tiny history router: routes are plain paths like /course/em or /session/12.
import { useEffect, useState } from 'react';

const subs = new Set<() => void>();

export function navigate(to: string, replace = false) {
  if (to === location.pathname + location.search) return;
  if (replace) history.replaceState(null, '', to);
  else history.pushState(null, '', to);
  subs.forEach((f) => f());
}

window.addEventListener('popstate', () => subs.forEach((f) => f()));

export function usePath(): string {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const f = () => setPath(location.pathname);
    subs.add(f);
    return () => {
      subs.delete(f);
    };
  }, []);
  return path;
}

export function match(pattern: string, path: string): Record<string, string> | null {
  const a = pattern.split('/').filter(Boolean);
  const b = path.split('/').filter(Boolean);
  if (a.length !== b.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < a.length; i++) {
    if (a[i].startsWith(':')) params[a[i].slice(1)] = decodeURIComponent(b[i]);
    else if (a[i] !== b[i]) return null;
  }
  return params;
}

export function Link(props: { to: string; className?: string; children: React.ReactNode; title?: string }) {
  return (
    <a
      href={props.to}
      className={props.className}
      title={props.title}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        navigate(props.to);
      }}
    >
      {props.children}
    </a>
  );
}
